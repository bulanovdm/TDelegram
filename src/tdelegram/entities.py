"""Outbound formatting via td_execute parseTextEntities (pure, testable)."""

from __future__ import annotations

import json
from typing import Any

from tdelegram.transport import Transport


def parse_entities(transport: Transport, text: str, parse_mode: str | None) -> dict[str, Any]:
    """Parse markdown|html -> formattedText via synchronous td_execute."""
    if not parse_mode:
        return {"@type": "formattedText", "text": text, "entities": []}
    mode = parse_mode.lower()
    if mode not in ("markdown", "html"):
        raise ValueError(f"Unknown parse mode {parse_mode!r}; use markdown|html.")
    td_mode = {"markdown": "textParseModeMarkdown", "html": "textParseModeHTML"}[mode]
    request = {
        "@type": "parseTextEntities",
        "text": text,
        "parse_mode": {"@type": td_mode, "version": 2 if mode == "markdown" else 0},
    }
    raw = transport.execute(json.dumps(request, separators=(",", ":")))
    if not raw:
        return {"@type": "formattedText", "text": text, "entities": []}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return {"@type": "formattedText", "text": text, "entities": []}
    if not isinstance(obj, dict) or obj.get("@type") == "error":
        return {"@type": "formattedText", "text": text, "entities": []}
    return obj


def render_entities(formatted: dict[str, Any], mode: str = "markdown") -> str:
    """Reverse renderer: formattedText -> markdown|html (best effort)."""
    text = str(formatted.get("text", ""))
    entities = formatted.get("entities") or []
    if not entities:
        return text

    # Apply from the end so offsets stay valid.
    def _offset(e: dict[str, Any]) -> int:
        try:
            return int(e.get("offset", 0))
        except (ValueError, TypeError):
            return -1

    ordered = sorted(entities, key=_offset, reverse=True)
    chars = list(text)
    for entity in ordered:
        try:
            offset = int(entity.get("offset", 0))
            length = int(entity.get("length", 0))
        except (ValueError, TypeError):
            continue
        etype = (entity.get("type") or {}).get("@type", "")
        start, end = offset, offset + length
        if start < 0 or end > len(chars) or start >= end:
            continue
        inner = "".join(chars[start:end])
        if etype == "textEntityTypeBold":
            wrapped = f"**{inner}**" if mode == "markdown" else f"<b>{inner}</b>"
        elif etype == "textEntityTypeItalic":
            wrapped = f"_{inner}_" if mode == "markdown" else f"<i>{inner}</i>"
        elif etype == "textEntityTypeCode":
            wrapped = f"`{inner}`" if mode == "markdown" else f"<code>{inner}</code>"
        elif etype == "textEntityTypePre":
            wrapped = f"```{inner}```" if mode == "markdown" else f"<pre>{inner}</pre>"
        elif etype == "textEntityTypeTextUrl":
            url = (entity.get("type") or {}).get("url", "")
            wrapped = f"[{inner}]({url})" if mode == "markdown" else f'<a href="{url}">{inner}</a>'
        elif etype == "textEntityTypeMention":
            wrapped = inner
        else:
            continue
        chars[start:end] = list(wrapped)
    return "".join(chars)
