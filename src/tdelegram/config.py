"""Client profiles, library discovery, and session locking."""

from __future__ import annotations

import ctypes.util
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path


def default_base_dir() -> Path:
    return Path(os.environ.get("TDELEGRAM_HOME", str(Path.home() / ".tdelegram")))


@dataclass
class ClientConfig:
    api_id: int = 0
    api_hash: str = ""
    profile: str = "default"
    base_dir: Path = field(default_factory=default_base_dir)
    library_path: str = ""
    log_verbosity: int = 0
    use_test_dc: bool = False

    @property
    def session_dir(self) -> Path:
        return self.base_dir / "profiles" / self.profile

    @property
    def db_dir(self) -> Path:
        return self.session_dir / "tdlib"

    @property
    def files_dir(self) -> Path:
        return self.session_dir / "files"


INSTALL_HINTS = {
    "Darwin": "brew install tdlib",
    "Linux": "apt install tdlib (or build from source: https://github.com/tdlib/td)",
    "Windows": "download a tdlib build and set TDELEGRAM_TDJSON to libtdjson.dll",
}


def discover_library(explicit: str = "", config_value: str = "") -> str:
    """Resolve libtdjson in order: explicit -> env -> config -> find_library -> candidates."""
    for candidate in (explicit, os.environ.get("TDELEGRAM_TDJSON", ""), config_value):
        if candidate and Path(candidate).expanduser().exists():
            return str(Path(candidate).expanduser())
        if candidate and Path(candidate).exists():
            return candidate
    found = ctypes.util.find_library("tdjson")
    if found:
        return found
    system = platform.system()
    candidates: list[str] = []
    if system == "Darwin":
        candidates = [
            "/opt/homebrew/opt/tdlib/lib/libtdjson.dylib",
            "/usr/local/opt/tdlib/lib/libtdjson.dylib",
            "/opt/homebrew/lib/libtdjson.dylib",
        ]
    elif system == "Linux":
        candidates = [
            "/usr/local/lib/libtdjson.so",
            "/usr/lib/libtdjson.so",
            "/usr/lib/x86_64-linux-gnu/libtdjson.so",
        ]
    elif system == "Windows":
        candidates = ["tdjson.dll", "libtdjson.dll"]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    hint = INSTALL_HINTS.get(system, "install tdlib for your OS")
    raise RuntimeError(
        f"Could not find libtdjson. Tried find_library and {candidates}. Hint: {hint}."
    )


class SessionLock:
    """Exclusive lockfile per profile directory. Refuses a second process."""

    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir
        self.lockfile = session_dir / ".lock"
        self._fd: int | None = None

    def acquire(self) -> None:
        import fcntl

        self.session_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.lockfile), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            os.close(fd)
            raise RuntimeError(
                f"Another tdelegram process holds {self.lockfile}. "
                "TDLib allows one client per database directory."
            ) from exc
        self._fd = fd

    def release(self) -> None:
        import fcntl

        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
