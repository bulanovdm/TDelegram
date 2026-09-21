"""ctypes binding to the modern TDLib C API.

This is the only module that touches ctypes / libtdjson directly.
Everything else talks to the Transport protocol.
"""

from __future__ import annotations

import ctypes
from pathlib import Path


class TdJsonLib:
    """Thin wrapper around libtdjson's modern C API."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        try:
            self._lib = ctypes.CDLL(self.path)
        except OSError as exc:
            raise RuntimeError(f"Could not load TDLib from {self.path}: {exc}") from exc
        lib = self._lib
        lib.td_create_client_id.restype = ctypes.c_int
        lib.td_create_client_id.argtypes = []
        lib.td_send.restype = None
        lib.td_send.argtypes = [ctypes.c_int, ctypes.c_char_p]
        lib.td_receive.restype = ctypes.c_char_p
        lib.td_receive.argtypes = [ctypes.c_double]
        lib.td_execute.restype = ctypes.c_char_p
        lib.td_execute.argtypes = [ctypes.c_char_p]
        try:
            lib.td_set_log_verbosity_level.restype = None
            lib.td_set_log_verbosity_level.argtypes = [ctypes.c_int]
            lib.td_set_log_verbosity_level(0)
        except AttributeError:
            pass

    def create_client_id(self) -> int:
        return int(self._lib.td_create_client_id())

    def send(self, client_id: int, request: str) -> None:
        self._lib.td_send(client_id, request.encode("utf-8"))

    def receive(self, timeout: float) -> str | None:
        raw: bytes | None = self._lib.td_receive(timeout)
        if not raw:
            return None
        return raw.decode("utf-8")

    def execute(self, request: str) -> str | None:
        raw2: bytes | None = self._lib.td_execute(request.encode("utf-8"))
        if not raw2:
            return None
        return raw2.decode("utf-8")
