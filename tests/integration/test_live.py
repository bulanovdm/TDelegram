"""Opt-in integration tests against real libtdjson (skipped without it).

Run with: TDELEGRAM_INTEGRATION=1 pytest tests/integration -q
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("TDELEGRAM_INTEGRATION") != "1", reason="opt-in live TDLib test"
)


def test_tdjson_loads_if_present() -> None:
    from tdelegram.config import discover_library

    try:
        lib = discover_library()
    except RuntimeError:
        pytest.skip("libtdjson not installed")
    from tdelegram.tdjson import TdJsonLib

    handle = TdJsonLib(lib)
    cid = handle.create_client_id()
    assert isinstance(cid, int)
