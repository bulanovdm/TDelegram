"""Date parsing: relative (7d/24h/2w) and ISO-8601."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_RELATIVE_RE = re.compile(r"(\d+(?:\.\d+)?)([hdw])", re.IGNORECASE)


def parse_date(value: str | None, *, end_of_day: bool = False) -> int | None:
    if not value:
        return None
    text = value.strip().lower()
    match = re.fullmatch(r"\d+(?:\.\d+)?[hdw]", text)
    if match:
        amount = float(text[:-1])
        unit = text[-1]
        seconds = amount * {"h": 3600, "d": 86400, "w": 604800}[unit]
        return int((datetime.now(timezone.utc) - timedelta(seconds=seconds)).timestamp())
    try:
        parsed = datetime.fromisoformat(text.replace("z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid date {value!r}; use 7d, 24h, or ISO-8601.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if end_of_day and len(text) == 10:
        parsed += timedelta(days=1) - timedelta(microseconds=1)
    return int(parsed.timestamp())
