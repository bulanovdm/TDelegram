"""Request shapes from the pinned td_api.tl, and a check against them.

TDLib is forgiving in exactly the wrong way. A field it does not recognise is
ignored and a missing one takes its default, so a request built with a wrong or
outdated parameter name does not fail: it runs with that argument silently
unset. Only a JSON type it cannot convert is refused. `msg react` sent `emoji`
where TDLib reads `reaction_type`, and every media send wrapped its file in the
object TDLib used to expect -- and the test fake, which answers anything, let
both through.

`validate()` rejects what TDLib would drop without a word. FakeTransport runs it
on every request a test sends, and `tdelegram call` runs it before anything
reaches TDLib. The shapes come from `schema.json`, generated from the same
td_api.tl as `methods.json`.
"""

from __future__ import annotations

import difflib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_INTEGERS = frozenset({"int32", "int53", "int64"})
# Routing fields TDLib strips before parsing a request.
_ENVELOPE = frozenset({"@type", "@extra", "@client_id"})
_NUMERIC = re.compile(r"-?\d+")
_MAX_DEPTH = 64
DOCS_URL = "https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1{}.html"


@lru_cache(maxsize=1)
def _shapes() -> dict[str, Any]:
    path = Path(__file__).with_name("schema.json")
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"TDLib schema unavailable at {path}: {exc}") from exc
    classes: dict[str, list[str]] = {}
    for name, entry in data["constructors"].items():
        classes.setdefault(entry["type"], []).append(name)
    data["classes"] = {name: sorted(members) for name, members in classes.items()}
    return data


def function(name: str) -> dict[str, Any] | None:
    """`{"params": {name: type}, "returns": type}` for a TDLib function."""
    entry: dict[str, Any] | None = _shapes()["functions"].get(name)
    return entry


def constructor(name: str) -> dict[str, Any] | None:
    """`{"fields": {name: type}, "type": abstract type}` for a TDLib object."""
    entry: dict[str, Any] | None = _shapes()["constructors"].get(name)
    return entry


def constructors_of(type_name: str) -> list[str]:
    """The concrete objects that may stand in for an abstract type."""
    members: list[str] = _shapes()["classes"].get(type_name, [])
    return list(members)


def docs_url(name: str) -> str:
    """Where TDLib documents a function, object or type.

    Doxygen escapes an upper-case letter as `_` plus the lower-case one, which
    is how `editMessageText` becomes `edit_message_text`.
    """
    return DOCS_URL.format(re.sub(r"[A-Z]", lambda m: "_" + m.group(0).lower(), name))


def describe(name: str) -> dict[str, Any] | None:
    """What the schema says about a function, an object, or an abstract type."""
    entry = function(name)
    if entry is not None:
        return {
            "kind": "function",
            "name": name,
            "params": dict(entry["params"]),
            "returns": entry["returns"],
            "docs": docs_url(name),
        }
    entry = constructor(name)
    if entry is not None:
        return {
            "kind": "object",
            "name": name,
            "fields": dict(entry["fields"]),
            "type": entry["type"],
            "docs": docs_url(name),
        }
    members = constructors_of(name)
    if members:
        return {"kind": "type", "name": name, "constructors": members, "docs": docs_url(name)}
    return None


def suggest(name: str, *, limit: int = 10) -> list[str]:
    """Names close to one the schema does not know, for a helpful refusal."""
    shapes = _shapes()
    known = [*shapes["functions"], *shapes["constructors"], *shapes["classes"]]
    folded = name.casefold()
    containing = sorted(k for k in known if folded and folded in k.casefold())
    close = difflib.get_close_matches(name, known, n=limit, cutoff=0.6)
    return list(dict.fromkeys([*close, *containing]))[:limit]


def validate(request: dict[str, Any]) -> list[str]:
    """Every way `request` departs from the schema. Empty when it conforms.

    Missing fields are not reported: TDLib defaults them, and many requests
    rely on that. Unknown fields and values of the wrong kind are, because
    TDLib either drops them silently or refuses the whole request.
    """
    method = request.get("@type")
    if not isinstance(method, str) or not method:
        return ["the request has no @type"]
    entry = function(method)
    if entry is None:
        hint = difflib.get_close_matches(method, _shapes()["functions"], n=1)
        suffix = f"; did you mean {hint[0]}?" if hint else ""
        return [f"{method} is not a TDLib function{suffix}"]
    problems: list[str] = []
    _check_fields(method, entry["params"], request, method, problems, 0)
    return problems


def _check_fields(
    owner: str,
    fields: dict[str, str],
    obj: dict[str, Any],
    path: str,
    problems: list[str],
    depth: int,
) -> None:
    for key, value in obj.items():
        if key in _ENVELOPE:
            continue
        expected = fields.get(key)
        if expected is None:
            hint = difflib.get_close_matches(key, list(fields), n=1)
            detail = f"did you mean {hint[0]!r}?" if hint else f"it takes {_names(fields)}"
            problems.append(f"{path}: {owner} has no field {key!r}; {detail}")
            continue
        _check_value(expected, value, f"{path}.{key}", problems, depth + 1)


def _check_value(tl_type: str, value: Any, path: str, problems: list[str], depth: int) -> None:
    if value is None:
        return  # TDLib reads null as "not set", which every field allows
    if depth > _MAX_DEPTH:
        problems.append(f"{path}: nested more than {_MAX_DEPTH} levels deep")
        return
    if tl_type.startswith("vector<"):
        if not isinstance(value, list):
            problems.append(f"{path}: expected an array ({tl_type}), got {_kind(value)}")
            return
        inner = tl_type[len("vector<") : -1]
        for index, item in enumerate(value):
            _check_value(inner, item, f"{path}[{index}]", problems, depth + 1)
        return
    if tl_type in _INTEGERS:
        # TDLib also accepts integers as strings, which is how int64 travels.
        if isinstance(value, bool) or not (
            isinstance(value, int) or (isinstance(value, str) and _NUMERIC.fullmatch(value))
        ):
            problems.append(f"{path}: expected {tl_type}, got {_kind(value)}")
        return
    if tl_type == "double":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(f"{path}: expected a number, got {_kind(value)}")
        return
    if tl_type in ("string", "bytes"):
        if not isinstance(value, str):
            problems.append(f"{path}: expected a string ({tl_type}), got {_kind(value)}")
        return
    if tl_type == "Bool":
        if not isinstance(value, (bool, int)):
            problems.append(f"{path}: expected true or false, got {_kind(value)}")
        return
    if not isinstance(value, dict):
        problems.append(f"{path}: expected a {tl_type} object, got {_kind(value)}")
        return
    declared = value.get("@type")
    if tl_type[:1].islower():
        # A bare type names exactly one object, so @type is optional -- but a
        # different one is an object TDLib will refuse.
        if declared is not None and declared != tl_type:
            problems.append(f"{path}: expected {tl_type}, got @type {declared!r}")
            return
        name = tl_type
    else:
        members = constructors_of(tl_type)
        if not declared:
            problems.append(f"{path}: {tl_type} needs an @type, one of {_names(members)}")
            return
        if declared not in members:
            problems.append(
                f"{path}: {declared!r} is not a {tl_type}; use one of {_names(members)}"
            )
            return
        name = str(declared)
    entry = constructor(name)
    if entry is None:
        problems.append(f"{path}: {name!r} is not a TDLib object")
        return
    _check_fields(name, entry["fields"], value, path, problems, depth)


def _names(items: Any, limit: int = 8) -> str:
    names = list(items)
    if not names:
        return "no fields"
    shown = ", ".join(names[:limit])
    return shown + (f", ... ({len(names)} in all)" if len(names) > limit else "")


def _kind(value: Any) -> str:
    """Describe a value in JSON's vocabulary, since that is what the caller wrote."""
    if isinstance(value, bool):
        return f"boolean {str(value).lower()}"
    if isinstance(value, (int, float)):
        return f"number {value}"
    if isinstance(value, str):
        return f"string {value[:40]!r}"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__
