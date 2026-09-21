"""Contract: the registry covers every TDLib function, exactly.

The schema is located the same way the generator locates it, so the two
can never disagree about what "every function" means. Set
`TDELEGRAM_TD_API` to a td_api.h or td_api.tl to run this without a
TDLib install; CI points it at a pinned td_api.tl.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_method_registry import (  # noqa: E402
    SCHEMA_ENV,
    classify,
    discover_schema,
    parse_schema,
)

# TDLib pins itself: this is the function count at the commit CI fetches.
# A bump is a real event - regenerate methods.json, classify what is new,
# and move this number in the same commit.
EXPECTED_FUNCTION_COUNT = 1022


def _schema() -> Path:
    try:
        return discover_schema()
    except SystemExit:
        pytest.skip(f"No td_api schema found; set {SCHEMA_ENV} to run this contract")


def _registry() -> dict[str, dict[str, str]]:
    path = REPO_ROOT / "src" / "tdelegram" / "methods.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_registry_covers_schema_exactly() -> None:
    names = parse_schema(_schema())
    registry = _registry()
    missing = sorted(set(names) - set(registry))
    stale = sorted(set(registry) - set(names))
    assert not missing, f"Unclassified TDLib functions: {missing[:10]}"
    assert not stale, f"Registry has stale entries: {stale[:10]}"
    assert len(names) == EXPECTED_FUNCTION_COUNT


def test_registry_is_reproducible() -> None:
    """The committed file must be exactly what the generator emits today."""
    names = parse_schema(_schema())
    regenerated = {}
    for name in names:
        verdict, reason = classify(name)
        regenerated[name] = {"verdict": verdict, "reason": reason}
    assert regenerated == _registry(), "methods.json is stale; re-run the generator"


def test_every_entry_is_well_formed() -> None:
    """Runs without a schema: guards the file shape the write gate relies on."""
    registry = _registry()
    assert registry, "registry must not be empty - safety.verdict() fails closed on every call"
    for method, entry in registry.items():
        assert entry.get("verdict") in ("read", "write", "destructive"), method
        assert entry.get("reason"), f"{method} has a verdict but no recorded reason"
