import os
import yaml
from pathlib import Path

from jarvis.security import grants, paths

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"


def _load_config():
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _check_allowed(path: str) -> Path:
    """Return the canonical path if it is in a free folder or was just approved; else raise."""
    try:
        target = paths.canonicalize(path)
    except paths.UnsafePath as e:
        raise PermissionError(f"Unsafe path {path}: {e}")
    allowed = _load_config()["tools"]["allowed_paths"]
    # Component-wise containment — immune to startswith prefix attacks
    if paths.is_within(target, allowed) or grants.is_granted(target):
        return target
    raise PermissionError(f"Path not in allowed list: {path}")


def read_file(path: str) -> str:
    target = _check_allowed(path)
    with open(target, encoding="utf-8") as f:
        return f.read()


def write_file(path: str, content: str) -> str:
    target = _check_allowed(path)
    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Written to {path}."


def list_files(path: str) -> str:
    target = _check_allowed(path)
    return "\n".join(os.listdir(target))
