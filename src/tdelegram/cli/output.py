"""Output helpers: data on stdout as JSONL, everything else on stderr."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def emit(record: dict[str, Any], *, fmt: str = "jsonl", out: str | None = None) -> None:
    if fmt == "json":
        payload = json.dumps(record, ensure_ascii=False, indent=2)
    elif fmt == "table":
        payload = _as_table(record)
    else:
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    else:
        print(payload, flush=True)


def emit_many(
    records: Iterable[dict[str, Any]], *, fmt: str = "jsonl", out: str | None = None
) -> None:
    if fmt == "json" and out is None:
        items = list(records)
        print(json.dumps(items, ensure_ascii=False, indent=2), flush=True)
        return
    for record in records:
        emit(record, fmt=fmt, out=out)


def warn(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def error_envelope(code: int, message: str, method: str = "") -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "method": method}}


def _as_table(record: dict[str, Any]) -> str:
    parts = [f"{key}={value!r}" for key, value in record.items() if key != "raw"]
    return " | ".join(parts)


def data_file(path: str | Path) -> Path:
    return Path(path).expanduser()
