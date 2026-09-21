"""Credential resolution, including the property SECURITY.md promises.

This module decides where secrets come from and how they reach the OS
keystore. It was the least-tested file in the package despite being the
one that handles `api_hash`, the database key, and the 2FA password.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from tdelegram import credentials as creds


class FakeRun:
    """Records subprocess.run calls and replays scripted results."""

    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.calls: list[dict[str, Any]] = []
        self.returncode = returncode
        self.stdout = stdout

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")

    def every_arg(self) -> list[str]:
        return [a for call in self.calls for a in call["argv"]]


# --- the security property -------------------------------------------------


def test_secrets_never_appear_in_argv_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    """SECURITY.md: `security` receives secrets on stdin, never argv.

    argv is world-readable via `ps`, so a secret passed there leaks to every
    local process for the lifetime of the call.
    """
    fake = FakeRun()
    monkeypatch.setattr(subprocess, "run", fake)
    creds._macos_set("tdelegram", "api_hash", "s3cret-value")
    assert fake.calls, "no subprocess was invoked"
    assert "s3cret-value" not in fake.every_arg(), "secret leaked into argv"
    assert any(call.get("input") == "s3cret-value" for call in fake.calls), "secret not on stdin"


def test_secrets_never_appear_in_argv_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun()
    monkeypatch.setattr(subprocess, "run", fake)
    creds._linux_set("tdelegram", "api_hash", "s3cret-value")
    assert "s3cret-value" not in fake.every_arg(), "secret leaked into argv"
    assert fake.calls[0].get("input") == "s3cret-value"


def test_secret_file_is_owner_only(tmp_path: Path) -> None:
    creds.file_set(tmp_path, "api_hash", "s3cret")
    path = creds.file_secret_path(tmp_path, "api_hash")
    assert path.read_text().strip() == "s3cret"
    assert path.stat().st_mode & 0o777 == 0o600, "secret file must not be group/world readable"


def test_secret_file_name_cannot_escape_its_directory(tmp_path: Path) -> None:
    path = creds.file_secret_path(tmp_path, "../../etc/passwd")
    assert tmp_path in path.parents, f"{path} escaped {tmp_path}"
    assert "/" not in path.name


# --- OS store behaviour ----------------------------------------------------


def test_macos_get_returns_none_when_lookup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", FakeRun(returncode=1))
    assert creds._macos_get("tdelegram", "missing") is None


def test_macos_get_strips_the_trailing_newline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", FakeRun(stdout="value\n"))
    assert creds._macos_get("tdelegram", "api_hash") == "value"


def test_linux_get_returns_none_when_lookup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", FakeRun(returncode=1))
    assert creds._linux_get("tdelegram", "missing") is None


def test_store_helpers_survive_a_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """No keychain tool installed must degrade, not crash."""

    def boom(*a: Any, **k: Any) -> Any:
        raise OSError("no such binary")

    monkeypatch.setattr(subprocess, "run", boom)
    assert creds._macos_get("s", "a") is None
    assert creds._linux_get("s", "a") is None
    assert creds._macos_set("s", "a", "v") is False
    assert creds._linux_set("s", "a", "v") is False


def test_os_store_dispatches_by_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(creds, "_macos_get", lambda s, a: "mac")
    monkeypatch.setattr(creds, "_linux_get", lambda s, a: "linux")
    monkeypatch.setattr(creds.sys, "platform", "darwin")
    assert creds.os_store_get("s", "a") == "mac"
    monkeypatch.setattr(creds.sys, "platform", "linux")
    assert creds.os_store_get("s", "a") == "linux"
    monkeypatch.setattr(creds.sys, "platform", "sunos")
    assert creds.os_store_get("s", "a") is None
    assert creds.os_store_set("s", "a", "v") is False


# --- the precedence chain --------------------------------------------------


@pytest.fixture
def no_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate resolution from this machine's real keychain."""
    monkeypatch.setattr(creds, "os_store_get", lambda service, account: None)
    monkeypatch.setattr(creds, "os_store_set", lambda service, account, value: False)


def _resolve(**kwargs: Any) -> str:
    kwargs.setdefault("account", "api_hash")
    kwargs.setdefault("env_name", "TELEGRAM_API_HASH")
    return creds.resolve_secret(**kwargs)


def test_explicit_beats_everything(
    monkeypatch: pytest.MonkeyPatch, no_store: None, tmp_path: Path
) -> None:
    monkeypatch.setenv("TELEGRAM_API_HASH", "from-env")
    creds.file_set(tmp_path, "api_hash", "from-file")
    assert _resolve(explicit="from-arg", base_dir=tmp_path) == "from-arg"


def test_env_beats_store_and_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TELEGRAM_API_HASH", "from-env")
    monkeypatch.setattr(creds, "os_store_get", lambda service, account: "from-store")
    creds.file_set(tmp_path, "api_hash", "from-file")
    assert _resolve(base_dir=tmp_path) == "from-env"


def test_store_beats_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    monkeypatch.setattr(creds, "os_store_get", lambda service, account: "from-store")
    creds.file_set(tmp_path, "api_hash", "from-file")
    assert _resolve(base_dir=tmp_path) == "from-store"


def test_file_is_the_last_resort_before_prompting(
    monkeypatch: pytest.MonkeyPatch, no_store: None, tmp_path: Path
) -> None:
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    creds.file_set(tmp_path, "api_hash", "from-file")
    assert _resolve(base_dir=tmp_path) == "from-file"


def test_prompt_is_used_only_when_nothing_is_stored(
    monkeypatch: pytest.MonkeyPatch, no_store: None, tmp_path: Path
) -> None:
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    asked: list[str] = []

    def prompt(text: str, secret: bool) -> str:
        asked.append(text)
        return "typed"

    assert _resolve(base_dir=tmp_path, prompt_text="hash: ", prompt_fn=prompt) == "typed"
    assert asked == ["hash: "]


def test_a_prompted_secret_is_persisted(
    monkeypatch: pytest.MonkeyPatch, no_store: None, tmp_path: Path
) -> None:
    """Falling back to a file is fine; silently forgetting the answer is not."""
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    _resolve(base_dir=tmp_path, prompt_fn=lambda t, s: "typed")
    assert creds.file_get(tmp_path, "api_hash") == "typed"


def test_save_can_be_declined(
    monkeypatch: pytest.MonkeyPatch, no_store: None, tmp_path: Path
) -> None:
    monkeypatch.delenv("TELEGRAM_CODE", raising=False)
    creds.resolve_secret(
        account="code",
        env_name="TELEGRAM_CODE",
        base_dir=tmp_path,
        prompt_fn=lambda t, s: "11111",
        save=False,
    )
    assert creds.file_get(tmp_path, "code") is None, "a one-time code must not be persisted"


def test_empty_is_rejected_unless_allowed(
    monkeypatch: pytest.MonkeyPatch, no_store: None, tmp_path: Path
) -> None:
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    with pytest.raises(RuntimeError, match="cannot be empty"):
        _resolve(base_dir=tmp_path, prompt_fn=lambda t, s: "")


def test_empty_is_accepted_when_allowed(
    monkeypatch: pytest.MonkeyPatch, no_store: None, tmp_path: Path
) -> None:
    """An unencrypted TDLib database is a blank key, not a missing one."""
    monkeypatch.setenv("TELEGRAM_DB_KEY", "")
    value = creds.resolve_secret(
        account="database_key",
        env_name="TELEGRAM_DB_KEY",
        base_dir=tmp_path,
        allow_empty=True,
    )
    assert value == ""


def test_eof_at_the_prompt_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, no_store: None, tmp_path: Path
) -> None:
    """Piped stdin must fail with a message, not an opaque EOFError."""
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)

    def eof(text: str, secret: bool) -> str:
        raise EOFError

    with pytest.raises(RuntimeError):
        _resolve(base_dir=tmp_path, prompt_fn=eof)


def test_one_time_codes_are_not_persisted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: a saved login code is replayed forever.

    The code was written to the OS store on first login, then returned from
    it on every later login instead of prompting. The handshake then retried
    an expired code until it timed out.
    """
    monkeypatch.delenv("TELEGRAM_CODE", raising=False)
    store: dict[str, str] = {}
    monkeypatch.setattr(creds, "os_store_get", lambda service, account: store.get(account))
    def remember(service: str, account: str, value: str) -> bool:
        store[account] = value
        return True

    monkeypatch.setattr(creds, "os_store_set", remember)

    def ask(answer: str) -> str:
        return creds.resolve_secret(
            account="code",
            env_name="TELEGRAM_CODE",
            base_dir=tmp_path,
            prompt_fn=lambda t, s: answer,
            ephemeral=True,
        )

    assert ask("11111") == "11111"
    assert store == {}, "a single-use code must never be stored"
    assert creds.file_get(tmp_path, "code") is None
    assert ask("22222") == "22222", "the second login must ask again, not replay the first code"


def test_ephemeral_still_honours_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Automation passes a code by env; only persistence is refused."""
    monkeypatch.setenv("TELEGRAM_CODE", "33333")
    value = creds.resolve_secret(
        account="code",
        env_name="TELEGRAM_CODE",
        base_dir=tmp_path,
        prompt_fn=lambda t, s: pytest.fail("should not prompt"),
        ephemeral=True,
    )
    assert value == "33333"


def test_login_code_provider_is_ephemeral(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The wiring matters as much as the flag: check the provider passes it."""
    from tdelegram.auth import ConsoleCredentialProvider

    monkeypatch.delenv("TELEGRAM_CODE", raising=False)
    monkeypatch.setattr(creds, "os_store_get", lambda service, account: "stale-code")
    provider = ConsoleCredentialProvider(tmp_path, prompt_fn=lambda t, s: "fresh-code")
    assert provider.get_code() == "fresh-code", "a stored code must not shadow the prompt"


def test_optional_secret_is_blank_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch, no_store: None, tmp_path: Path
) -> None:
    """Regression: containers and cron jobs died prompting for an optional key.

    The TDLib database key is blank for an unencrypted database. With no TTY
    there is nobody to ask, so the prompt could only fail with EOF -- which is
    exactly what `docker run ... chat list` hit on every data command.
    """
    monkeypatch.delenv("TELEGRAM_DB_KEY", raising=False)
    monkeypatch.setattr(creds.sys.stdin, "isatty", lambda: False)
    value = creds.resolve_secret(
        account="database_key",
        env_name="TELEGRAM_DB_KEY",
        base_dir=tmp_path,
        allow_empty=True,
        prompt_fn=lambda t, s: pytest.fail("must not prompt without a terminal"),
    )
    assert value == ""


def test_required_secret_still_fails_loudly_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch, no_store: None, tmp_path: Path
) -> None:
    """Only optional secrets default to blank; a required one must not."""
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    monkeypatch.setattr(creds.sys.stdin, "isatty", lambda: False)

    def eof(text: str, secret: bool) -> str:
        raise EOFError

    with pytest.raises(RuntimeError):
        creds.resolve_secret(
            account="api_hash", env_name="TELEGRAM_API_HASH",
            base_dir=tmp_path, prompt_fn=eof,
        )
