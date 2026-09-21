"""Write gate over the generated registry. Lives at TelegramClient.call()."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _registry() -> dict[str, dict[str, str]]:
    path = Path(__file__).with_name("methods.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (OSError, json.JSONDecodeError):
        return {}


def verdict(method: str) -> str:
    """Return read|write|destructive for a TDLib function. Fail closed."""
    reg = _registry()
    entry = reg.get(method)
    if entry is None:
        raise RuntimeError(f"Unknown TDLib method {method!r}: no registry verdict (fail closed).")
    return str(entry["verdict"])


def reason(method: str) -> str:
    reg = _registry()
    entry = reg.get(method, {})
    return str(entry.get("reason", ""))


def needs_confirmation(method: str) -> bool:
    return verdict(method) in ("write", "destructive")


def is_destructive(method: str) -> bool:
    return verdict(method) == "destructive"


def preview_request(method: str, params: dict[str, object]) -> dict[str, object]:
    return {"@type": method, "verdict": verdict(method), "reason": reason(method), "params": params}
