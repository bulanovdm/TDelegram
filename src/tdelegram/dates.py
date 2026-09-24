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


_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)([smhdw])", re.IGNORECASE)


def parse_duration(value: str) -> float:
    """Seconds in a span like `90s`, `30m`, `2h`, `1d`."""
    match = _DURATION_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Invalid duration {value!r}; use 90s, 30m, 2h or 1d.")
    return float(match.group(1)) * {"s": 1, **_UNIT_SECONDS}[match.group(2).lower()]


_AHEAD_RE = re.compile(r"\+?(\d+(?:\.\d+)?)([mhdw])", re.IGNORECASE)


def parse_future(value: str) -> int:
    """A moment to schedule for: `30m`, `2h`, `1d` from now, or ISO-8601.

    Offsets count forward here, unlike parse_date's `7d`, which reaches back
    for a history window. A time already past is refused rather than sent now.
    """
    text = value.strip()
    now = datetime.now(timezone.utc)
    match = _AHEAD_RE.fullmatch(text)
    if match:
        seconds = float(match.group(1)) * _UNIT_SECONDS[match.group(2).lower()]
        return int((now + timedelta(seconds=seconds)).timestamp())
    moment = parse_date(text)
    if moment is None or moment <= int(now.timestamp()):
        raise ValueError(f"{value!r} is not in the future; use 30m, 2h, 1d, or ISO-8601.")
    return moment
