# Jarvis — Personal Voice Agent: Build Plan

> A voice-controlled personal agent for Windows that does what you say, works
> freely inside approved drives, asks before going further, and keeps your
> memory, history and logs on your own machine.

**Final decisions**

| Area | Decision |
|---|---|
| Machine | **Personal laptop** — 16 GB RAM, RTX 5050 (8 GB VRAM), 1 TB SSD. Not the work laptop. |
| Ears & mouth | Fork of [PanPenek/JarvisAi](https://github.com/PanPenek/JarvisAi): openWakeWord → faster-whisper → … → Kokoro, plus its web HUD |
| Decision maker | **TypeSafe AI Jev model** upfront (typed Choice / Score / Noul decisions, ~70–500 ms, very cheap) — **rules + local embeddings fallback** when Jev is unavailable, offline, or not yet off the waitlist |
| Brain | Router (no AI) → **Claude via Claude subscription** (Claude Agent SDK) → **local LLM backup** (Ollama 4B, loaded **only** when Claude's limit is hit or offline; private) |
| Hands | [Windows-MCP](https://github.com/CursorTouch/Windows-MCP) (desktop), Playwright (visible Chrome), drive-scoped file tools |
| Safety | **Our gatekeeper** in front of every tool + non-admin Windows account from Phase 1 |
| Later (optional) | FreeLLMAPI for general (non-personal) questions only; ElevenLabs voice; phone access |

---

## 1. Principles

| # | Principle | In practice |
|---|---|---|
| 1 | **Safety in code, not prompts** | Every tool call passes through one gatekeeper. The model can be wrong or tricked; the gatekeeper can't be talked out of its rules. |
| 2 | **Scoped by default, elevated on request** | Free access only in approved drives. Anything else → Jarvis asks. "Full access" is temporary. |
| 3 | **Stored locally, sent selectively** | Memory, history, audit and logs live on the laptop. Only what a request needs is sent to the cloud brain. |
| 4 | **Never stuck, never leaky** | Router handles common commands with 0 tokens; a private local backup brain takes over when Claude's limit is hit — personal data never goes to extra providers. |
| 5 | **Visible actions** | Browser runs in a real Chrome window you can watch and take over. |
| 6 | **Everything logged** | Tool calls, approvals, and everything sent to the cloud are recorded locally. |

---

## 2. Architecture

![Jarvis architecture](docs/architecture.jpg)

```
                    ┌──────────────────────── on your laptop ────────────────────────┐
 Mic ─► Wake word ─► VAD ─► STT ─────► INPUT GATE ── blocked / router cmd / saved routine / cached answer? ──► handle (0 tokens)
       (openWakeWord) (Silero) (faster-whisper,      │
                               GPU)                  │ no
                                                     ▼
                          ┌──────── JEV DECISION MAKER (upfront, §4.8) ────────┐
                          │ TypeSafe Jev model (cloud, command text only)       │
                          │   Choice → route · intent · tool set · routine      │
                          │   Score  → expected risk · complexity               │
                          │   Noul   → unclear? · needs plan approval?          │
                          │ fallback: rules + local embeddings (0 tokens)       │
                          └──────┬──────────────────────┬──────────────────────┘
                                 │ unclear              │ decided
                                 ▼                      ▼
                         ask you ("Which folder?")   BRAIN SELECTOR (usage meter)
                                                     + chosen tool set + plan flag
                                              │                    │
                                   Claude OK  │                    │ limit hit / offline
                                              ▼                    ▼
                                   Claude Agent SDK          Backup loop (fork's llm.py)
                                   (subscription)            → Ollama 4B (local, on demand)
                                              │                    │
                                              └───── request ──────┘
                                                        ▼
                                     ┌─── PLAN APPROVAL (jev-chat idea, §4.7) ───────┐
                                     │ 1. JUDGE   propose_plan: intent + steps + alt │
                                     │ 2. CHECK   gatekeeper checks WHOLE plan       │
                                     │            (Tier 3 step → reject, 0 actions)  │
                                     │ 3. DECIDE  Tier 0 plan → run                  │
                                     │            Tier 1–2 → HUD card [A][B][C]      │
                                     │            + one spoken sentence → you pick   │
                                     │ (skipped for simple questions / single safe   │
                                     │  actions / router / routines)                 │
                                     └──────────────────────┬────────────────────────┘
                                                            ▼ approved plan
                                                   4. EXECUTE: each tool call
                                                            ▼
                                                  ACTION GATEKEEPER (safety net)
                                                  tiers · taint gate · retry limit 2
                                                  off-plan step? ── confirm ──► HUD / voice
                                                        │
                                       ┌────────────────┼──────────────────┐
                                   File tools       Windows-MCP        Playwright Chrome
                                  (approved drives) (apps, windows,    (dedicated profile)
                                                     click/type, PS)
                                                        ▼
                                          Audit log (+ successful plans → routine cache)
 Speaker ◄─ Kokoro TTS ◄─ answer ◄─────────────────────┘
                    └───────────────────────────────┬────────────────────────────────┘
                                                    │ PRIVACY FIREWALL (selective memory,
                                                    │ sensitive-folder confirm, outbound log)
                                                    ▼
                         Anthropic (Claude)  ·  TypeSafe (Jev: command text only)  ·  websites
```

### Request flow
1. "Hey Jarvis" (or F2 to type / push-to-talk) → Silero VAD → faster-whisper on the GPU.
2. **Input gate** (§4.6), all **0 tokens**: obviously forbidden → refuse; router command ("open Chrome", "volume 50", "scroll down", "what time is it") → run; saved routine → replay through gatekeeper; cached answer → speak. Also checks the turn source (speech-only acting) and budget, and filters what context may be sent.
3. **Jev decision maker** (§4.8): typed decisions on the command text — route, intent, tool set, expected risk, complexity, "unclear?", "needs plan approval?". Unclear → Jarvis asks you before spending tokens. Fallback: rules + local embeddings.
3b. **Brain selector** picks Claude (or the local backup if Claude's limit is hit / complexity is low), passing only the **chosen tool set** and the plan-approval flag.
4. **Judge first** (§4.7): for anything beyond a simple read, the brain first submits a short judgment + plan (intent, steps). The gatekeeper checks the **whole plan** before anything runs; low-risk plans run directly, medium/high-risk plans are shown in the HUD with options ([A] do all / [B] safer variant / [C] cancel) and spoken in one sentence.
5. Execution: every real tool call is still checked by the **action gatekeeper** (Tier 0–3, taint gate, retry limit) → allow / confirm / block → audit log. Steps outside the approved plan need a fresh confirm.
6. Answer spoken by Kokoro; turn saved to local history.

---

## 3. Technology Stack

| Layer | Choice | Source | Leaves laptop? |
|---|---|---|---|
| Language | Python 3.11+ (Jarvis), Python 3.14+ via `uvx` (Windows-MCP), Node.js (Playwright) | — | — |
| Wake word | openWakeWord `hey_jarvis` | fork | No |
| End of speech | Silero VAD (replaces fork's volume threshold) | ours | No |
| Speech-to-text | faster-whisper `small` → `medium` on CUDA (GPU is free now) | fork | No |
| **Decision maker** | **TypeSafe AI Jev model** (typed Choice / Score / Noul API); fallback rules + local embeddings (small sentence-embedding model on CPU) | external + ours | Yes (Jev) — command text only; fallback: No |
| **Brain (main)** | **Claude Agent SDK**, logged in with Claude subscription (`ANTHROPIC_API_KEY` **unset**) | ours | Yes — request context |
| **Brain (backup)** | Fork's Ollama tool loop → **Ollama 4B** tool-calling model (e.g. `qwen3:4b`; ~3 GB VRAM, ~1 GB RAM **only while loaded**) | fork | **No** |
| Brain (optional later) | FreeLLMAPI — general questions only, no memory/files | — | Yes — question text only |
| Text-to-speech | Kokoro-82M on CPU (ElevenLabs optional later) | fork | No |
| Desktop control | **Windows-MCP** (accessibility tree, no vision needed) | external | No |
| Browser | **Playwright**, headed Chrome, dedicated profile | ours / Playwright MCP | Website traffic only |
| Files | Agent SDK file tools limited to approved drives + fork's `file_ops` path check | SDK + fork | No |
| Memory | **Markdown files, one fact per file** (`data/memory/*.md`, easy to read/edit/delete) + ChromaDB index for search + SQLite (history, summaries, audit, usage) | fork + ours | Only selected facts per request |
| UI | FastAPI + WebSocket HUD — bento tiles + particle-swarm object (approvals, full access, current task, Claude/GPU gauges, activity, systems, settings drawer with brain + Claude model) | fork + ours | No (localhost) |
| Secrets | Windows Credential Manager via `keyring` | ours | — |
| Tests | pytest | fork + ours | — |

### Resource budget (16 GB RAM / 8 GB VRAM)
| Component | RAM | VRAM |
|---|---|---|
| Windows 11 | 4–5 GB | ~0.3 GB |
| Jarvis (voice, Kokoro, memory, HUD) | 1.5–2.5 GB | — |
| faster-whisper `small`/`medium` | ~0.3 GB | 1–2 GB |
| Windows-MCP + Agent SDK (Claude Code CLI) | ~0.5–1 GB | — |
| Jarvis Chrome | 0.5–1.5 GB | small |
| **Free for your apps** (normal mode) | **~7–9 GB** | **~5–6 GB** (gaming OK) |
| Backup mode only: Ollama 4B loaded | +~1 GB | +~3 GB |
| **Free for your apps** (backup mode) | **~6–8 GB** | **~2–3 GB** (avoid heavy gaming) |

---

## 4. Brain Strategy & Usage Control

### 4.1 Three levels
| Level | Handles | Cost | Expected share |
|---|---|---|---|
| **Router** (regex/intent, no AI) | Open/close apps, volume, media, time/date, scroll, back, tabs, lock | 0 tokens | ~50–60 % |
| **Claude** (subscription) | Questions, file tasks, multi-step tasks, browser goals | Subscription limit | ~40–50 % |
| **Local backup** (Ollama 4B) | Same as Claude when limit hit or offline (reduced toolset, simpler tasks; long/complex tasks wait for Claude) | Free, private | Only when needed |

### 4.2 Token estimates (to verify in week 1)
| Request | Est. tokens |
|---|---|
| Simple question / command via brain | ~3–8k |
| Multi-step file task | ~20–60k |
| Browser task with page reading | ~30–100k |

### 4.3 Usage controls
- **Usage gauges for the 5-hour and 7-day windows** in the HUD (matches how subscription limits actually work), plus requests/tokens today from Agent SDK result messages.
- Warning at **80 %** of either window: "Getting close to your Claude limit."
- **Daily budget** in config (e.g. max Claude requests/day).
- **Auto-switch**: on rate-limit error or no internet → load local backup + spoken notice ("Claude limit reached — using local brain until 4 PM"). When the limit resets → switch back and **unload** the local model.
- **Ask before heavy tasks**: browser goals may say "This may use a lot — continue with Claude?"
- Keep tokens low: short system prompt, page text trimmed before sending, persistent `ClaudeSDKClient` session (one long-lived brain process, not one per request).

### 4.3a Tool trimming (token saver)
- Every exposed tool's description is sent with **every** request (reports: ~11k tokens/request of tool overhead in an Agent SDK setup; a 51-tool MCP server ≈ 37.5k tokens).
- Expose only the **~10–15 tools Jarvis uses often**: Agent SDK `allowed_tools`, Windows-MCP tool allowlist/exclude, a small browser toolset.
- **Strict MCP config:** Jarvis loads only its own MCP servers — never the user's other Claude Code MCP servers or settings.
- Deferred tool loading (Tool Search) saves tokens but adds a round trip (slower voice) and Agent SDK support is unconfirmed — revisit later; keep frequent tools always loaded.
- Measure tool overhead in Phase 1 (log input tokens of an empty "hi" request).

### 4.4 Subscription terms
- Claude subscription via Claude Code / Agent SDK is for **personal use on your own machine**. Never distribute Jarvis with subscription login; switch to an API key if it ever becomes a product.
- Ensure `ANTHROPIC_API_KEY` is **not** set, or usage is billed to the API instead.
- **Env stripping:** every Claude process Jarvis starts gets a cleaned environment — remove all `ANTHROPIC_*` and `CLAUDE_CODE_*` variables (`claude_env.child_env()` helper) so a stray key can never switch billing to the API.
- Check the Claude privacy setting for model training.

### 4.5 Local backup brain — how it stays light
- Ollama runs as a service but holds **no model in memory** in normal mode.
- On switch to backup: model loads from SSD (~2–4 s; Jarvis says "switching to local brain").
- `OLLAMA_KEEP_ALIVE=5m` unloads it after 5 min idle; "light mode" voice command unloads immediately; switching back to Claude unloads it.
- Settings: `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, context ~8k; check `ollama ps` shows `100% GPU`.
- Backup gets the same gatekeeper, taint gate and tool trimming; exposes a smaller toolset (files, apps, basic browser) suited to a 4B model.
- **Optional promotion:** if Claude limits are hit often after week 1, also use the local model as a level *between* router and Claude for simple questions (config change only).
- **Optional FreeLLMAPI:** only for general, non-personal questions, with no memory or file content attached; only providers whose data terms you accept.

### 4.6 Input gate, retry limit & local cache (token savers)

**Input gate** (before the brain — `jarvis/input_gate.py`). Runs on every request, in order:
1. **Source check** — turn began with your speech/typing? (speech-only acting flag for the gatekeeper)
2. **Forbidden check** — obvious Tier 3 requests ("format C:", "delete C:\Windows", "disable Defender") → refuse immediately, 0 tokens.
3. **Router command** → run directly.
4. **Saved routine** → replay through the gatekeeper.
5. **Cached answer** → speak it.
6. **Budget check** → brain selector picks Claude or local backup.
7. **Privacy filter** → only 2–3 relevant memories; sensitive folders hidden.

The input gate does **not** replace the action gatekeeper — it only stops what is obvious before tokens are spent. Every real action is still checked after the brain.

**Avoiding wasted tokens on blocked actions**
- **Rules up front:** compact policy (~150–200 tokens) in the system prompt — free paths, never-allowed, always-ask — so the brain rarely plans blocked actions.
- **Hide disabled tools** (`run_python`, registry) — not exposed at all.
- **Final denial messages:** "Blocked: C:\Windows is never allowed. Do not retry or use another tool for this."
- **Retry limit:** max **2 denials per turn** → stop and tell you what was blocked and why.
- **Denial tracking** in the audit log → tune prompt/policy when one kind repeats.

**Local cache** (`data/cache/`, on the encrypted drive)

| Cache | What | Expiry | Saves |
|---|---|---|---|
| **Routine cache** ⭐ | Tool steps of a successful Claude task, keyed by the command; matched by local embeddings with a strict threshold ("start my work mode" ✔, "stop work mode" ✘) | Until it fails or you delete it | High — daily repeated commands become 0 tokens |
| **Answer cache** | Answers to general questions ("what is Docker?") | 7 days; never for weather/prices/news/time-sensitive | Medium |
| **Page cache** | Clean text of pages Jarvis read | 15–60 min | Medium (browser tasks) |
| **Rule cache** | Gatekeeper classification of paths/commands | Until policy changes | Speed only |

Cache safety rules:
1. Replayed routines **always** go through the gatekeeper and taint gate — the cache is never a safety bypass.
2. **Approvals are never cached** — Tier 1/2 still ask every time (except during temporary elevation).
3. First replay of a new routine asks: "Use your saved routine for this?"
4. Replay fails (e.g. folder moved) → fall back to Claude and update the routine.
5. Voice controls: "show my routines", "forget that routine", "clear cache".

### 4.7 Plan approval / judge first (idea from the **jev-chat** app: "read first, then act — you make the final move")

> Two different "Jev"s: **jev-chat** (open-source chat-reply app, MIT) inspired this plan-approval step. The **TypeSafe AI Jev model** (§4.8) is the upfront decision maker. The decision maker *estimates* risk from your words; this step checks the brain's *real* plan.

**Flow**
```
Request (past the input gate)
   ↓
1. JUDGE   brain calls the custom tool  propose_plan(intent, steps[], alternatives[])
           e.g. intent "clean Downloads"
                steps  ① list D:\Downloads  ② move 30 PDFs → D:\Docs  ③ delete 12 .tmp files
                alt    "move only, don't delete"
   ↓
2. CHECK   gatekeeper classifies EVERY step before anything runs
           ① Tier 0 ✔  ② Tier 0 ✔  ③ Tier 2 → needs OK      (Tier 3 step → plan rejected, 0 actions run)
   ↓
3. DECIDE  risk = highest tier in the plan (computed by the gatekeeper, NOT the model's own opinion)
           Tier 0 plan   → run immediately, no interruption
           Tier 1–2 plan → HUD card + one spoken sentence:
                           "I'll move 30 PDFs and delete 12 temp files. Okay?"
                           [A] Do all   [B] Move only   [C] Cancel
   ↓
4. EXECUTE each real tool call still goes through the action gatekeeper + taint gate + retry limit
           approved plan steps → no second prompt (one confirm per plan, not per step)
           step NOT in the approved plan (plan changed mid-task) → fresh confirm
```

**Rules**
- **Skip judging for simple turns:** questions, single Tier 0 actions, router commands and cached routines don't need a plan (keeps voice fast).
- **Acting tools are locked until a plan is accepted** for multi-step or Tier ≥ 1 work (gatekeeper refuses acting calls without an active plan).
- **One confirmation per plan** instead of one per step → less confirmation fatigue. Tier 2 steps inside an approved plan count as confirmed only for the exact targets listed (e.g. those 12 files), never "any delete".
- **Blocked steps are found before execution** → no tokens or actions wasted on half-done tasks.
- Plan + decision stored in the audit log; successful plans feed the **routine cache**.
- Token cost: a few hundred tokens per judged task (same Claude request); usually saves tokens by avoiding blocked steps and retries.
- Local backup brain uses the same `propose_plan` tool (simpler plans).
- The decision maker's `needs_plan` flag (§4.8) tells the brain upfront whether a plan is required.

### 4.8 Jev decision maker (upfront, before the brain)

**What it is:** [TypeSafe AI](https://siliconangle.com/2026-09-16/typesafe-ai-exits-stealth-with-40m-to-build-ai-for-use-by-software/)'s Jev — a "System One" model that answers **typed questions** with **typed decisions**, no text generated:

| Type | Returns | 
|---|---|
| **Choice** | an option id (up to 255 options) |
| **Score** | a level, 2 to 10 |
| **Noul** | a yes/no probability, 0 to 1 |

Vendor-stated: 70–500 ms, $0.042 per M input tokens, output free; cannot generate text, count, do arithmetic, compare dates or explain itself. Launched Sept 2026, **early access / waitlist**.

**Questions Jarvis asks Jev (one batch per request)**

| Decision | Type | Options / scale | Used for |
|---|---|---|---|
| `route` | Choice | local brain · Claude · ask user · refuse | Brain selector |
| `intent` | Choice | file · app/window · system · browser · question · chat-reply · memory · routine · other | Logging, tool set |
| `tool_set` | Choice | files · desktop · browser · files+desktop · browser+files · none | **Tool trimming** — brain gets only these tools |
| `routine` | Choice | saved routines + "none" | Routine cache match (replaces embedding match when available) |
| `expected_risk` | Score | 2–10 | Early warning ("This will delete files — continue?") at ≥ 8 |
| `complexity` | Score | 2–10 | ≤ 4 → local brain allowed; ≥ 5 → Claude |
| `unclear` | Noul | p(yes) | ≥ 0.7 → Jarvis asks a clarifying question first |
| `needs_plan` | Noul | p(yes) | ≥ 0.5 → brain must call `propose_plan` first |
| `page_injection` (browser turns) | Noul | p(yes) on page text | ≥ 0.5 → mark turn tainted early (extra signal for the taint gate) |

**Rules**
- **Command text only** goes to TypeSafe — no memory, no file contents, no history. Logged in `outbound.jsonl` like every other outbound call. (Exception: `page_injection` sends trimmed page text from public websites only.)
- **Not a security boundary.** Jev estimates; the plan check (§4.7) and action gatekeeper (§5) decide. A low `expected_risk` never skips a Tier 1–3 confirm.
- **Fallback** (no Jev access yet, offline, error, or > 800 ms timeout): rules + local embeddings answer the same questions with simpler logic (keyword tables for intent/tool set/risk words, embedding similarity for routines, "unclear" when no rule matches confidently).
- **Thresholds** live in config and are tuned after week 1 using the audit log (decision vs. what actually happened).
- **Cost check:** ~500 input tokens per request → ~$0.00002; 1,000 requests/day ≈ $0.02/day. Daily cap in config.
- API key stored in Windows Credential Manager (`keyring`).

---

## 5. Security & Permission Model

### 5.1 Tiers
| Tier | Behaviour | Examples |
|---|---|---|
| **0 – Free** | Runs immediately | Read/write/create in approved drives; open apps; volume/media; search & read web pages; scroll; click links |
| **1 – Confirm** | Voice "yes" **or** click | Access outside approved drives; download; install; type into web forms; kill a process; read clipboard; send a **sensitive-folder** file's content to the cloud |
| **2 – Strong confirm** | Click (+ optional spoken PIN), every time | Delete / move / overwrite; shutdown / restart; submit, send, post, buy, accept terms; registry writes; PowerShell outside allowlist |
| **3 – Never** | Hard-blocked | `C:\Windows`, Program Files writes, credential stores, disabling Defender/firewall/UAC, `format`/`diskpart`, typing passwords/cards/OTPs, `run_python` (until real sandbox) |

### 5.2 Where the gatekeeper hooks in
- **Claude path:** Agent SDK `can_use_tool` callback **and** a `PreToolUse` hook (defence in depth). Agent SDK `allowed_tools`/`disallowed_tools` expose only what Jarvis needs; `add_dirs` limited to approved drives.
- **Backup path (local Ollama):** fork's `router.dispatch()` → schema validation → same `gatekeeper.check()`.
- **Windows-MCP:** exclude Registry tool; PowerShell tool goes through shell parser; Click/Type tiered by target window.
- **Playwright:** browser actions classified (read/scroll/click link = 0, form type = 1, submit/buy/send = 2, password/card fields = 3).

### 5.3 Policy (in `config.yaml`)
```yaml
security:
  free_paths: ["D:\\", "~/Documents", "~/Desktop"]
  sensitive_paths: ["D:\\Personal"]        # content goes to cloud only after confirm
  blocked_paths: ["C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
                  "%APPDATA%\\Microsoft\\Credentials"]
  always_confirm_ops: [delete, move, overwrite, registry_write, power]
  shell:
    allow: [Get-ChildItem, Get-Content, Get-Process, Start-Process, Test-Path, Measure-Object]
    block_patterns: ["-EncodedCommand", "Invoke-Expression", "iex ", "format ", "diskpart",
                     "Set-MpPreference", "reg delete"]
  elevation: { max_minutes: 15, require_click: true }
  disabled_tools: [run_python, registry]
brain:
  primary: claude_subscription
  backup: ollama              # local, loaded only on demand
  backup_model: qwen3:4b      # pick current best 4B tool-calling model
  freellmapi_general_only: false
  max_denials_per_turn: 2
decision:
  provider: jev                 # jev | rules
  model: jev-latest
  timeout_ms: 800               # slower → fallback to rules
  fallback: rules_embeddings
  send: command_text_only
  daily_usd_cap: 0.50
  thresholds: { unclear: 0.7, needs_plan: 0.5, page_injection: 0.5, warn_risk: 8, claude_complexity: 5 }
judge:
  enabled: true
  skip_for: [question, single_tier0_action, router, routine]
  confirm_from_tier: 1          # plans with any Tier >= 1 step are shown for approval
  approval_scope: exact_targets # approved Tier 2 steps cover only the listed files/targets
cache:
  routines: { enabled: true, match_threshold: 0.92, confirm_first_replay: true }
  answers:  { enabled: true, ttl_days: 7, never: [weather, price, news, time, stock] }
  pages:    { enabled: true, ttl_minutes: 30 }
  rules:    { enabled: true }
  daily_claude_budget: 100
  warn_at_percent: 80
```

### 5.4 Must-haves
- Canonicalize every path (resolve `..`, symlinks, junctions, env vars; reject `\\?\`, UNC, 8.3 tricks).
- Parse shell commands; classify every path argument.
- Temporary elevation ("full access for 10 minutes"): Tier 1 → 0 until expiry; Tier 2/3 unchanged; HUD countdown.
- No voice-only approval for Tier 2.
- Kill switch: `Ctrl+Alt+J` + Esc / ABORT / "stop"; PyAutoGUI failsafe back on.
- Web pages, files, tool output = data, never instructions.
- **Taint gate:** once a turn has read untrusted content (web page, OCR/screenshot text, downloaded file, MCP output from outside sources), **acting tools are refused for the rest of that turn** (write/delete/move, click/type, PowerShell, submit). Jarvis reports what it found and you start a new turn to act. Read-only tools stay allowed. Approved exceptions (e.g. "click the 2nd result") are explicit Tier 1 confirms.
- **Speech-only acting:** acting tools run only on turns that **began with your speech or typed input** — never on turns started by timers, scheduled tasks, notifications, or other sessions. Those can inform you but not act.
- **Local-only HUD/server:** bind to `127.0.0.1`; check `Origin` on WebSocket and all state-changing routes; non-browser clients need a bearer token from a local file. Stops websites and other apps from driving Jarvis.
- **Never** run Claude with `--dangerously-skip-permissions`; permissions always flow through the gatekeeper.
- **Run Jarvis as a dedicated non-admin Windows user** with NTFS rights only on approved drives — **Phase 1**, not later.

### 5.5 Privacy firewall (cloud brain edition)
- **Selective memory:** only the 2–3 facts relevant to the request are sent — never full history.
- **Sensitive folders:** content from `sensitive_paths` sent only after confirm.
- **Redaction** of emails, phone numbers, IDs in free text where possible.
- **Outbound log:** every prompt and tool result sent to Claude (or optional FreeLLMAPI) logged to `data/outbound.jsonl`.
- **Backup mode is fully private:** the local Ollama brain sends nothing out.
- **Jev decision maker** receives the **command text only** (no memory, files, history); use the rules fallback for fully private sessions ("private mode" voice command switches `decision.provider` to `rules`).
- Browser: dedicated Chrome profile (no personal logins), default search DuckDuckGo.

---

## 6. Project Structure (fork + additions)

```
Jarvis/                         # our fork of PanPenek/JarvisAi
  PLAN.md
  config.yaml                   # + security:, brain:, browser:, privacy:
  jarvis/
    main.py                     # + router, brain selector, confirm events
    input_gate.py               # NEW source/forbidden/router/routine/cache/budget/privacy checks
    judge.py                    # NEW propose_plan tool, whole-plan check, plan approval state
    decision/                   # NEW upfront decision maker
      questions.py              # typed question definitions (route, intent, tool_set, risk, …)
      jev_client.py             # TypeSafe Jev API client (timeout, cap, outbound log)
      rules_fallback.py         # keyword tables + local embeddings
      decide.py                 # try Jev → fallback → Decision object
    router_cmds.py              # NEW direct commands (0 tokens)
    cache/                      # NEW
      routines.py  answers.py  pages.py  rules.py
    wake.py
    vad.py                      # NEW Silero VAD
    stt.py                      # use vad.py; configurable language; CUDA
    tts.py
    brain/                      # NEW
      selector.py               # Claude vs local backup, auto-switch, load/unload Ollama model
      claude_agent.py           # Agent SDK client, MCP servers, can_use_tool, hooks
      backup_loop.py            # fork's llm.py loop → Ollama 4B, MCP tools via python mcp client
      usage.py                  # 5h/7d gauges, token/request tracking, budget, warnings
      claude_env.py             # child_env(): strip ANTHROPIC_* / CLAUDE_CODE_* for Claude processes
    context.py                  # + persistence, merged summaries
    memory.py                   # + delete, list, selective retrieval
    web.py, static/index.html   # + confirm dialog, usage meter, elevation timer, audit & memory viewer
    security/                   # NEW
      policy.py  paths.py  shell_parser.py  gatekeeper.py  elevation.py  audit.py
    privacy/                    # NEW
      outbound.py
    tools/
      router.py                 # backup path dispatch → gatekeeper
      file_ops.py               # + delete/move, read vs write scopes
      code_exec.py              # disabled
      system.py, app_control.py # tiered; mostly superseded by Windows-MCP
      desktop.py                # OCR fallback only; failsafe on
      browser/                  # NEW Playwright (or Playwright MCP config)
  tests/
    test_paths.py  test_shell_parser.py  test_gatekeeper.py  test_router_cmds.py  test_selector.py
  data/                         # git-ignored, on BitLocker drive
```

---

## 7. Phased Roadmap

### Phase 0 — Setup & fork (1 day)
- Install: Python 3.11+, Git, Node.js, `uv`, Ollama, Claude desktop app / Claude Code; `ollama pull qwen3:4b`.
- Claude Code: `/login` with the **subscription** account; confirm `ANTHROPIC_API_KEY` is not set.
- Fork PanPenek/JarvisAi (check LICENSE file; keep attribution), clone, copy this PLAN.md in.
- Remove `run_python`, `power_command`, `kill_process` from `TOOL_MAP` before first run.
- Test the voice loop with the fork's built-in Ollama provider using `qwen3:4b` (this later becomes the backup brain).
- **Done when:** "Hey Jarvis, what time is it?" works end to end.

### Phase 1 — Claude brain + gatekeeper + safe account (≈1–1.5 weeks) ⚠ required before daily use
- `brain/claude_agent.py`: persistent `ClaudeSDKClient`, short system prompt, file tools limited to approved drives, strict MCP config, trimmed `allowed_tools`.
- `brain/claude_env.py`: `child_env()` strips `ANTHROPIC_*` / `CLAUDE_CODE_*`.
- `security/` package; `can_use_tool` + `PreToolUse` hook; audit log; **taint gate** and **speech-only acting** in the gatekeeper.
- HUD server: `127.0.0.1` only, Origin checks, bearer token for non-browser clients.
- Measure baseline tool overhead (input tokens of a trivial request).
- `input_gate.py` v1: source check, forbidden check, budget check; compact policy in system prompt; final denial messages; **retry limit (2 denials/turn)**; denial tracking in audit log.
- Confirm flow: spoken question + HUD Allow/Deny (timeout = deny); kill switch.
- Create dedicated **non-admin Windows user**; NTFS rights only on approved drives; run Jarvis as that user.
- **Tests:** path escapes, shell bypasses, every tool's tier.
- **Done when:** file tasks work in `D:\`; outside paths ask; `C:\Windows` blocked; all security tests green.

### Phase 2 — Router, usage meter & backup brain (≈1 week)
- `router_cmds.py` for common commands (0 tokens).
- `usage.py` + HUD gauges for 5-hour and 7-day windows; 80 % warning; daily budget.
- **Notification batching:** urgent "needs you" events (confirmations, failures) spoken immediately; routine "done" notices batched into one sentence at the next pause.
- Input gate v2: router commands + **answer cache** + **rule cache**.
- **Decision maker** (`decision/`): typed questions; **rules + embeddings fallback first**; plug in the Jev client when waitlist access arrives; brain gets only the chosen `tool_set`; clarifying questions on `unclear`.
- **Done when (decision):** "delete the old stuff" → Jarvis asks "Which folder?" with 0 Claude tokens; "open Notepad and type hello" sends only desktop tools to the brain; Jev timeout falls back to rules without the user noticing.
- **Judge first** (`judge.py`): `propose_plan` tool, whole-plan gatekeeper check, HUD plan card with options ([A]/[B]/[C]) + one spoken sentence, acting tools locked until plan accepted, off-plan steps re-confirm.
- **Done when (judge):** "clean my Downloads" shows one plan card; a plan containing a Tier 3 step is rejected before any action runs.
- `backup_loop.py` (local Ollama) through the same gatekeeper; auto-switch on rate limit / no internet; load on switch, unload on return.
- **Done when:** common commands are instant; hitting the limit switches to the local brain with a spoken notice; `ollama ps` shows no model loaded in normal mode.

### Phase 3 — Windows-MCP desktop control (≈1 week)
- Add Windows-MCP as an MCP server (Claude path + backup path via mcp client).
- Exclude Registry; PowerShell via shell parser; Click/Type tiering.
- Replace fork's OCR/PyAutoGUI tools (keep OCR as fallback).
- **Done when:** "open Notepad and type hello", "snap VS Code left" work reliably.

### Phase 4 — Browser (≈1 week)
- Playwright headed Chrome, dedicated profile `data/chrome-profile` (own module or Playwright MCP).
- Commands: search, open, scroll, back, tabs, click by text/number, type, read/summarize page (trimmed text).
- Tiering per §5.2; downloads → `Downloads\Jarvis` (Tier 1).
- **Page cache** (30 min) so the same page isn't fetched and re-sent twice.
- **Done when:** "search best laptops under 80k and read me the top 3" works visibly.

### Phase 5 — Voice quality (3–4 days)
- Silero VAD, configurable language, faster-whisper `medium` on CUDA, barge-in.
- **Done when:** fewer cut-offs; direct command < 2 s end to end.

### Phase 6 — Memory, context & privacy firewall (≈1 week)
- ✅ **Task memory (done early):** one markdown file per task in `data/tasks/YYYY/MM/` (YAML metadata + asked / did / result / reply), Chroma index rebuilt from the files, read-only `search_tasks` tool (untrusted), time words → date filter, "forget that task / today", 90-day retention, secret redaction, Jarvis folder always blocked for tools. Facts memory no longer stores secrets.
- Memory: one Markdown file per fact in `data/memory/`, "remember / forget", Memory Viewer, selective retrieval (2–3 facts per request); ChromaDB only as a search index rebuilt from the files.
- Context persisted in SQLite; merged summaries.
- **Routine cache**: save successful Claude task steps, embedding match, first-replay confirm, fallback + update on failure, voice management ("show my routines", "forget that routine").
- `privacy/outbound.py`: sensitive-folder confirm, redaction, `outbound.jsonl` + HUD viewer.
- **Done when:** memory survives restarts; you can see exactly what was sent to the cloud.

### Phase 7 — Extras (optional, as needed)
- Promote local model to a level before Claude if limits are hit often (§4.5); optional FreeLLMAPI for general questions only.
- Dictation mode, task centre, temporary elevation polish.
- **Chat-reply skill (Jev-style):** "Jarvis, help me reply to this message" → read the chat window (Windows-MCP / OCR) → judge intent, tone, risk → 2–3 ranked drafts in the HUD → pick one → Jarvis **only fills the text box; you press send** (sending stays with you, Tier 2 if ever automated). ⚠ Reading/typing in third-party chat apps may conflict with their terms — use at your own risk.
- ElevenLabs voice option.
- Real sandbox for code execution → re-enable at Tier 2.
- Phone access later (e.g. OpenClaw/Hermes channel) — evaluate separately.

**Rough total:** 5–7 weeks part-time.

---

## 8. Performance Targets

| Metric | Target |
|---|---|
| Wake word → listening | < 300 ms |
| Router command end to end | < 2 s |
| Claude answer, first spoken word | < 4–5 s |
| Kill switch | < 300 ms |
| Idle CPU | < 5 % |

---

## 9. Known Cons & Mitigations

| Con | Mitigation |
|---|---|
| Requests (incl. file/page content) go to Anthropic | Selective memory, sensitive-folder confirm, redaction, outbound log, training opt-out |
| Needs internet for Claude | Router + local backup brain work offline |
| Claude subscription limits | Router (0 tokens), usage gauges, budget, auto-switch to local backup |
| Local backup brain is weaker (4B) | Reduced toolset; Tier 2 actions still confirmed; long/complex tasks wait for Claude |
| Backup mode uses ~3 GB VRAM + ~1 GB RAM | Loaded only in backup mode; auto-unload; "light mode" command |
| Gatekeeper rule gaps | One choke point, tests, `PreToolUse` hook as second layer, **non-admin account in Phase 1** |
| Confirmation fatigue | Tune tiers after week 1; temporary elevation for busy sessions |
| Windows-MCP has full system access by design | Everything behind gatekeeper; Registry excluded; non-admin account |
| Prompt injection from pages/files | Taint gate (no acting after reading untrusted content); speech-only acting; gatekeeper independent of model; dedicated Chrome profile |
| Taint gate makes some flows two-step ("read page" then "click it") | Explicit Tier 1 confirm to act in the same turn; tune after week 1 |
| Tool descriptions eat tokens every request | Tool trimming (~10–15 tools), strict MCP config, measure overhead in Phase 1 |
| Stray API key silently bills the API | Env stripping on every Claude child process |
| Tokens wasted on actions the gatekeeper blocks | Input gate (0-token refusals), rules in prompt, hidden disabled tools, final denials, retry limit 2/turn |
| Cached routine replays the wrong thing | Strict match threshold, first-replay confirm, gatekeeper on every replayed step, fallback to Claude on failure |
| Stale cached answers | TTL; never cache time-sensitive topics; "clear cache" command |
| Judge step adds a pause before tasks | Skipped for simple turns; Tier 0 plans run without asking |
| Model under-states risk in its plan | Risk is computed by the gatekeeper from each step's tier, not taken from the model |
| Plan changes mid-task (e.g. after reading a page) | Off-plan steps need a fresh confirm; taint gate still applies |
| Jev is a third cloud provider (sees command text) | Command text only; outbound log; "private mode" → rules fallback |
| Jev is waitlisted / new (Sept 2026); speed & accuracy claims are vendor-only | Build rules fallback first; Jev is a plug-in; measure decisions vs. outcomes in week 1 |
| Jev mis-routes or picks the wrong tool set | Brain can request "need more tools" → one retry with full trimmed set; thresholds tuned from audit log |
| Jev under-estimates risk | Never used for safety — plan check + gatekeeper still enforce tiers |
| Fork is young (3 commits, no LICENSE file seen) | Treat as starter kit; don't track upstream; verify license |
| Fork uses threads; adding confirms/browser may block | Run brain + confirms off the keyboard thread; consider asyncio refactor in Phase 1 |
| Voice spoofing / false wakes | Tier 2 needs a click; wake threshold tuning |
| Effort estimate may slip | Phases are independently usable; stop after Phase 4 if needed |

---

## 9a. Inspirations & Credits

| Project | Ideas taken |
|---|---|
| [PanPenek/JarvisAi](https://github.com/PanPenek/JarvisAi) | Base fork: Windows voice pipeline, tools, HUD, path allowlist check |
| [ethanplusai/jarvis](https://github.com/ethanplusai/jarvis) (macOS, Claude Code) | Persistent subscription brain, env stripping, taint gate, speech-only acting, 5h/7d usage gauges, notification batching, Markdown memory, strict MCP config, localhost + Origin + token. **Not** copied: `--dangerously-skip-permissions` |
| [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) | Desktop control via accessibility tree |
| [isair/jarvis](https://github.com/isair/jarvis) | Memory Viewer, dictation mode, task centre ideas |
| [jev-chat/jev-chat-jarvis](https://github.com/jev-chat/jev-chat-jarvis) (MIT) | Judge first → ranked options → user makes the final move; chat-reply skill idea. Idea only, no code copied |
| [TypeSafe AI – Jev model](https://thenewstack.io/typesafe-jev-system-one/) (paid API, waitlist) | Upfront decision maker: typed Choice / Score / Noul decisions (§4.8) |
| [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | Learned skills → our routine cache |

---

## 10. Open Decisions

1. Approved free drives/folders (e.g. `D:\`, `~/Documents`, `~/Desktop`) and sensitive folders.
2. Claude plan: Pro or Max (affects limits).
3. Browser module: own Playwright code or Playwright MCP.
4. Fork visibility: private or public GitHub repo.
5. Default search engine: DuckDuckGo or Google.

---

## 11. Next Steps (on the personal laptop)

1. Install Python, Git, Node.js, `uv`, Ollama, Claude desktop app; log in to Claude Code with the subscription; `ollama pull qwen3:4b`.
2. Fork + clone PanPenek/JarvisAi; copy this `PLAN.md` into the repo.
2b. Join the **TypeSafe AI Jev waitlist** (typesafe.ai); Jarvis runs on the rules fallback until access arrives.
3. Open the folder in Claude Code and say: *"Read PLAN.md and start Phase 0."*
4. After week 1 of use: review the usage gauges and decide whether to promote the local model (§4.5) and how to tune tiers.
