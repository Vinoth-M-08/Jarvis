"""Jarvis Web UI — runs alongside voice as a background thread."""
from __future__ import annotations
import asyncio
import json
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse

from jarvis.tts import stop_speaking, is_speaking

_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Jarvis")
_clients: list[WebSocket] = []
_loop: asyncio.AbstractEventLoop | None = None
# Only the HUD's own page may drive Jarvis (PLAN.md §5.4); set from the port at startup.
_allowed_origins: set[str] = {"http://localhost:7860", "http://127.0.0.1:7860"}


def _origin_ok(origin: str | None) -> bool:
    """Browsers always send Origin on cross-site requests and WebSockets; a missing one is a local non-browser client."""
    return origin is None or origin in _allowed_origins


@app.middleware("http")
async def _origin_guard(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _origin_ok(request.headers.get("origin")):
        return JSONResponse({"status": "error", "message": "cross-origin request blocked"}, status_code=403)
    return await call_next(request)


# ─── Capture the REAL event loop at startup ───

@app.on_event("startup")
async def _capture_loop():
    global _loop
    _loop = asyncio.get_running_loop()
    print("[Web] Event loop captured — WebSocket broadcast ready.")


# ─── Thread-safe broadcast ───

def _broadcast_to_ws(event: dict) -> None:
    """Send event to all connected WebSocket clients.
    Thread-safe — can be called from voice/keyboard threads."""
    if not _loop or _loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(_async_broadcast(event), _loop)
    except RuntimeError:
        pass  # loop closed during shutdown


async def _async_broadcast(event: dict) -> None:
    """Actually send the event — runs on the event loop thread."""
    data = json.dumps(event)
    for ws in list(_clients):
        try:
            await ws.send_text(data)
        except Exception:
            if ws in _clients:
                _clients.remove(ws)


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse((_STATIC_DIR / "index.html").read_text(encoding="utf-8"))


# ─── Auto-apply (docs/AUTO_APPLY.md) ───

def _apply():
    from jarvis.main import apply_service
    return apply_service


@app.get("/api/apply")
async def api_apply():
    return JSONResponse(await asyncio.get_running_loop().run_in_executor(None, _apply().snapshot))


@app.get("/api/apply/app")
async def api_apply_app(id: str):
    d = await asyncio.get_running_loop().run_in_executor(None, _apply().details, id)
    return JSONResponse(d) if d else JSONResponse({"status": "error", "message": "not found"}, status_code=404)


@app.post("/api/apply/answer")
async def api_apply_answer(request: Request):
    body = await request.json()
    slug, text = str(body.get("slug", "")), str(body.get("text", "")).strip()
    if not slug or not text:
        return JSONResponse({"status": "error", "message": "slug and text are required"}, status_code=400)
    try:
        done = await asyncio.get_running_loop().run_in_executor(None, _apply().answer, slug, text)
    except KeyError:
        return JSONResponse({"status": "error", "message": "that question is no longer open"}, status_code=404)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
    return JSONResponse({"status": "ok", "answer": done["answer"],
                         "rechecked": [{"company": r.get("company"), "status": r.get("status")}
                                       for r in done["rechecked"]]})


@app.post("/api/apply/run")
async def api_apply_run(request: Request):
    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    started = _apply().run_in_background(int(body.get("limit", 10)))
    return JSONResponse({"status": "ok" if started else "busy"})


# ─── Provider APIs ───

@app.get("/api/providers")
async def api_providers():
    from jarvis.main import get_providers, get_active_provider
    providers = get_providers()
    active = get_active_provider()
    result = {}
    for key, prov in providers.items():
        result[key] = {
            "label": prov.get("label", key),
            "model": prov.get("model", ""),
            "type": prov.get("type", "ollama"),
            "base_url": prov.get("base_url", ""),
            "api_key": prov.get("api_key", ""),
        }
    return JSONResponse({"providers": result, "active": active})


@app.post("/api/providers/{provider_key}")
async def api_set_provider(provider_key: str):
    from jarvis.main import set_active_provider
    ok = set_active_provider(provider_key)
    if ok:
        return JSONResponse({"status": "ok", "active": provider_key})
    return JSONResponse({"status": "error", "message": "Unknown provider"}, status_code=400)


@app.put("/api/providers/{provider_key}")
async def api_upsert_provider(provider_key: str, request: Request):
    """Add or update a provider."""
    from jarvis.main import _load_config, _save_config
    body = await request.json()
    cfg = _load_config()
    providers = cfg.setdefault("llm", {}).setdefault("providers", {})
    providers[provider_key] = {
        "type": body.get("type", "openai"),
        "label": body.get("label", provider_key),
        "model": body.get("model", ""),
        "base_url": body.get("base_url", ""),
    }
    if body.get("api_key"):
        providers[provider_key]["api_key"] = body["api_key"]
    _save_config(cfg)
    return JSONResponse({"status": "ok", "provider": provider_key})


@app.delete("/api/providers/{provider_key}")
async def api_delete_provider(provider_key: str):
    """Delete a provider."""
    from jarvis.main import _load_config, _save_config, get_active_provider
    cfg = _load_config()
    providers = cfg.get("llm", {}).get("providers", {})
    if provider_key not in providers:
        return JSONResponse({"status": "error", "message": "Not found"}, status_code=404)
    if provider_key == get_active_provider():
        return JSONResponse({"status": "error", "message": "Cannot delete active provider"}, status_code=400)
    del providers[provider_key]
    _save_config(cfg)
    return JSONResponse({"status": "ok"})


@app.get("/api/claude-model")
async def api_get_claude_model():
    from jarvis.main import get_claude_model
    return JSONResponse(get_claude_model())


@app.put("/api/claude-model")
async def api_set_claude_model(request: Request):
    """Pick the Claude brain's model from the fixed list (opus / sonnet / haiku)."""
    from jarvis.main import set_claude_model
    body = await request.json()
    model = set_claude_model(str(body.get("alias", "")))
    if model is None:
        return JSONResponse({"status": "error", "message": "Unknown model or no Claude brain"}, status_code=400)
    return JSONResponse({"status": "ok", "model": model})


# ─── Settings APIs ───

@app.get("/api/settings")
async def api_get_settings():
    """Return all configurable settings."""
    from jarvis.main import _load_config
    cfg = _load_config()
    return JSONResponse({
        "tts": cfg.get("tts", {}),
        "stt": cfg.get("stt", {}),
        "wake_word": cfg.get("wake_word", {}),
        "context": cfg.get("context", {}),
        "llm": {
            "temperature": cfg.get("llm", {}).get("temperature", 0.7),
            "active_provider": cfg.get("llm", {}).get("active_provider", "ollama"),
        },
        "tools": cfg.get("tools", {}),
    })


@app.put("/api/settings")
async def api_update_settings(request: Request):
    """Update settings. Accepts partial updates."""
    from jarvis.main import _load_config, _save_config
    body = await request.json()
    cfg = _load_config()

    if "tts" in body:
        cfg.setdefault("tts", {}).update(body["tts"])
    if "stt" in body:
        cfg.setdefault("stt", {}).update(body["stt"])
    if "wake_word" in body:
        cfg.setdefault("wake_word", {}).update(body["wake_word"])
    if "context" in body:
        cfg.setdefault("context", {}).update(body["context"])
    if "llm" in body:
        if "temperature" in body["llm"]:
            cfg.setdefault("llm", {})["temperature"] = body["llm"]["temperature"]
    if "tools" in body:
        cfg.setdefault("tools", {}).update(body["tools"])

    _save_config(cfg)
    return JSONResponse({"status": "ok"})


# ─── Usage / GPU gauges ───

@app.get("/api/usage")
async def api_usage():
    from jarvis.main import usage_snapshot
    return JSONResponse(usage_snapshot())


@app.get("/api/activity")
async def api_activity():
    from jarvis.main import recent_activity
    return JSONResponse({"items": recent_activity(8)})


# ─── Mute APIs ───

@app.get("/api/mute")
async def api_get_mute():
    from jarvis.main import is_muted
    return JSONResponse({"muted": is_muted()})


@app.post("/api/mute")
async def api_toggle_mute():
    from jarvis.main import toggle_mute, is_muted
    toggle_mute()
    return JSONResponse({"muted": is_muted()})


# ─── WebSocket ───

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if not _origin_ok(ws.headers.get("origin")):
        # A web page on another site must never be able to click "Allow" for you.
        await ws.close(code=1008)
        return
    await ws.accept()
    _clients.append(ws)
    await ws.send_text(json.dumps({"type": "connected", "message": "Jarvis online."}))
    from jarvis.main import elevation_state
    await ws.send_text(json.dumps(elevation_state()))
    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "chat":
                user_text = msg["text"]

                # "stop" typed in chat = abort everything
                from jarvis.main import _is_stop_command
                if _is_stop_command(user_text):
                    from jarvis.main import abort_all
                    abort_all()
                    await ws.send_text(json.dumps({"type": "response", "text": "Stopped."}))
                    continue

                # Run as a task so this loop keeps reading — Allow/Deny and Stop must
                # get through while the request is still running.
                task = asyncio.create_task(_handle_chat(ws, user_text))
                _tasks.add(task)
                task.add_done_callback(_tasks.discard)

            elif msg.get("type") == "interrupt":
                from jarvis.main import abort_all
                abort_all()

            elif msg.get("type") == "confirm_response":
                from jarvis.main import confirm_broker
                confirm_broker.resolve(str(msg.get("id", "")), bool(msg.get("allowed")), via="click",
                                       choice=msg.get("choice"))

            elif msg.get("type") == "elevation_revoke":
                from jarvis.main import gatekeeper
                gatekeeper.elevation.revoke()

    except WebSocketDisconnect:
        if ws in _clients:
            _clients.remove(ws)


_tasks: set[asyncio.Task] = set()  # keep references so running chat tasks aren't garbage-collected


async def _handle_chat(ws: WebSocket, user_text: str) -> None:
    from jarvis.main import _process_request, _Aborted, _new_speech_stream, _abort
    _abort.clear()  # same as a typed (F2) command: a new request starts un-aborted
    loop = asyncio.get_event_loop()
    stream = _new_speech_stream()   # Jarvis starts speaking while the reply is still being written
    try:
        await loop.run_in_executor(None, _process_request, user_text, "typed", stream)
    except _Aborted:
        await _safe_send(ws, {"type": "response", "text": "Stopped."})
    except Exception as e:
        await _safe_send(ws, {"type": "response", "text": f"Error: {e}"})
    finally:
        if stream is not None:
            threading.Thread(target=stream.finish, name="tts-finish", daemon=True).start()


async def _safe_send(ws: WebSocket, event: dict) -> None:
    try:
        await ws.send_text(json.dumps(event))
    except Exception:
        pass  # client went away


def start_web_background(port: int = 7860) -> None:
    """Launch web UI in a daemon thread. Non-blocking."""
    from jarvis.main import register_event_listener
    register_event_listener(_broadcast_to_ws)
    _allowed_origins.clear()
    _allowed_origins.update({f"http://localhost:{port}", f"http://127.0.0.1:{port}"})

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # Localhost only — never expose the HUD to the network (PLAN.md §5.4)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        loop.run_until_complete(server.serve())

    t = threading.Thread(target=_run, daemon=True)
    t.start()
