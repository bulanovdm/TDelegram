"""Credential resolution: explicit -> env -> config file -> OS store -> prompt.

Secrets never travel via argv: macOS `security` receives them on stdin,
Linux uses `secret-tool`, Windows tries `keyring` if installed, and a
0600 file is the documented fallback everywhere.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


def _macos_get(service: str, account: str) -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n")


def _macos_set(service: str, account: str, value: str) -> bool:
    try:
        # Secret on stdin (-w reads password securely path differs by version;
        # use stdin-capable invocation to avoid argv exposure).
        delete = subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", account],
            capture_output=True,
            text=True,
            check=False,
        )
        _ = delete
        result = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", service, "-a", account, "-w"],
            input=value,
            capture_output=True,
            text=True,
            check=False,
        )
        # Older macOS `security` does not accept -w without a value; fall back
        # to stdin-piped form explicitly.
        if result.returncode != 0:
            proc = subprocess.run(
                ["security", "add-generic-password", "-U", "-s", service, "-a", account],
                input=value,
                capture_output=True,
                text=True,
                check=False,
            )
            return proc.returncode == 0
        return True
    except OSError:
        return False


def _linux_get(service: str, account: str) -> str | None:
    try:
        result = subprocess.run(
            ["secret-tool", "lookup", "service", service, "account", account],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n")


def _linux_set(service: str, account: str, value: str) -> bool:
    try:
        result = subprocess.run(
            [
                "secret-tool",
                "store",
                "--label",
                f"{service}/{account}",
                "service",
                service,
                "account",
                account,
            ],
            input=value,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except OSError:
        return False


def os_store_get(service: str, account: str) -> str | None:
    if sys.platform == "darwin":
        return _macos_get(service, account)
    if sys.platform.startswith("linux"):
        return _linux_get(service, account)
    if sys.platform == "win32":
        try:
            import keyring  # type: ignore[import-not-found]

            return keyring.get_password(service, account)  # type: ignore[no-any-return]
        except Exception:
            return None
    return None


def os_store_set(service: str, account: str, value: str) -> bool:
    if sys.platform == "darwin":
        return _macos_set(service, account, value)
    if sys.platform.startswith("linux"):
        return _linux_set(service, account, value)
    if sys.platform == "win32":
        try:
            import keyring  # type: ignore[import-not-found]

            keyring.set_password(service, account, value)
            return True
        except Exception:
            return False
    return False


SERVICE = "tdelegram"


def file_secret_path(base_dir: Path, account: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in account)
    return base_dir / "secrets" / f"{safe}.secret"


def file_get(base_dir: Path, account: str) -> str | None:
    path = file_secret_path(base_dir, account)
    try:
        data = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return data.strip()


def file_set(base_dir: Path, account: str, value: str) -> None:
    path = file_secret_path(base_dir, account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


PromptFn = Callable[[str, bool], str]


def default_prompt(prompt: str, secret: bool) -> str:
    try:
        if secret:
            return getpass.getpass(prompt)
        return input(prompt)
    except EOFError as exc:
        raise RuntimeError(f"Required secret prompt failed (EOF): {prompt}") from exc


def resolve_secret(
    *,
    account: str,
    env_name: str,
    explicit: str | None = None,
    base_dir: Path | None = None,
    prompt_text: str = "",
    secret: bool = True,
    allow_empty: bool = False,
    prompt_fn: PromptFn | None = None,
    save: bool = True,
    ephemeral: bool = False,
) -> str:
    """Resolve one secret through the full precedence chain.

    `ephemeral` marks a single-use secret such as a login code: it is never
    read from or written to persistent storage. Storing one is worse than
    useless -- the saved value is reused on the next login, the user is never
    prompted, and the handshake retries an expired code until it times out.
    """
    if explicit is not None and (explicit != "" or allow_empty):
        return explicit
    env_value = os.environ.get(env_name)
    if env_value is not None and (env_value != "" or allow_empty):
        return env_value
    if not ephemeral:
        stored = os_store_get(SERVICE, account)
        if stored is not None and (stored != "" or allow_empty):
            return stored
        if base_dir is not None:
            file_value = file_get(base_dir, account)
            if file_value is not None and (file_value != "" or allow_empty):
                return file_value
    if allow_empty and not sys.stdin.isatty():
        # Nothing can be asked for without a terminal, and blank is an allowed
        # answer for this secret, so blank is the answer. Prompting here can
        # only end in EOF -- which is what every containerised, cron-driven or
        # piped invocation used to hit on the unencrypted-database key.
        return ""
    fn = prompt_fn or default_prompt
    try:
        value = fn(prompt_text or f"{account}: ", secret).strip()
    except EOFError as exc:
        raise RuntimeError(f"Value for {account} is required.") from exc
    if not value and not allow_empty:
        raise RuntimeError(f"Value for {account} cannot be empty.")
    if save and value and not ephemeral:
        if not os_store_set(SERVICE, account, value) and base_dir is not None:
            try:
                file_set(base_dir, account, value)
            except OSError:
                pass
    return value
