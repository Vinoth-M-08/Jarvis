"""Subagent — delegate subtasks to a local Ollama model for parallel/cheaper processing."""
from __future__ import annotations
from pathlib import Path
import ollama
import yaml

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"


def _local_model() -> str:
    """Same local model as the backup brain (llm.providers.ollama.model)."""
    try:
        with open(_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg["llm"]["providers"]["ollama"]["model"]
    except Exception:
        return "qwen3:4b-q4_K_M"


def delegate_task(task: str, context: str = "") -> str:
    """Send a subtask to the local Ollama model and return its response.

    Use for tasks that don't need cloud intelligence:
    - Summarizing text
    - Reformatting data
    - Writing boilerplate code
    - Simple Q&A from provided context
    """
    messages = []
    if context:
        messages.append({
            "role": "system",
            "content": f"You are a helper agent. Complete the task using this context:\n\n{context}",
        })
    messages.append({"role": "user", "content": task})

    try:
        response = ollama.chat(model=_local_model(), messages=messages, think=False)
        import re
        text = response.message.content or ""
        # Strip thinking tags from local model too
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        return text if text else "Subtask completed (no output)."
    except Exception as e:
        return f"Subagent failed: {e}"
