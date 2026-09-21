"""Authorization state machine with injectable credentials.

Covers all eleven TDLib authorization states. Secrets are never read
inline: they come from a CredentialProvider, so the machine is
testable without a console and every prompt is EOFError-guarded at the
provider boundary.
"""

from __future__ import annotations

import base64
import platform
from pathlib import Path
from typing import Any, Protocol

from tdelegram.errors import TelegramError, TelegramTimeoutError, from_td_error


class CredentialProvider(Protocol):
    def get_api_id(self) -> int: ...
    def get_api_hash(self) -> str: ...
    def get_database_key(self) -> str: ...
    def get_phone(self) -> str: ...
    def get_code(self) -> str: ...
    def get_password(self) -> str: ...
    def get_email(self) -> str: ...
    def get_email_code(self) -> str: ...


class ConsoleCredentialProvider:
    """Interactive provider using_env/keyring/file fallbacks via credentials.resolve_secret."""

    def __init__(self, base_dir: Path | None = None) -> None:
        from tdelegram import credentials as creds

        self._creds = creds
        self._base_dir = base_dir

    def _resolve(
        self, account: str, env: str, prompt: str, *, secret: bool, allow_empty: bool = False
    ) -> str:
        return self._creds.resolve_secret(
            account=account,
            env_name=env,
            base_dir=self._base_dir,
            prompt_text=prompt,
            secret=secret,
            allow_empty=allow_empty,
        )

    def get_api_id(self) -> int:
        raw = self._resolve("api_id", "TELEGRAM_API_ID", "Telegram API ID: ", secret=False)
        try:
            return int(raw)
        except ValueError as exc:
            raise RuntimeError("TELEGRAM_API_ID must be an integer.") from exc

    def get_api_hash(self) -> str:
        value = self._resolve("api_hash", "TELEGRAM_API_HASH", "Telegram API hash: ", secret=True)
        if not value:
            raise RuntimeError("Telegram API hash cannot be empty.")
        return value

    def get_database_key(self) -> str:
        return self._resolve(
            "database_key",
            "TELEGRAM_DB_KEY",
            "Local TDLib database key (blank for unencrypted): ",
            secret=True,
            allow_empty=True,
        )

    def get_phone(self) -> str:
        value = self._resolve(
            "phone",
            "TELEGRAM_PHONE",
            "Telegram phone number, including country code: ",
            secret=False,
        )
        if not value:
            raise RuntimeError("Telegram phone number cannot be empty.")
        return value

    def get_code(self) -> str:
        value = self._resolve("code", "TELEGRAM_CODE", "Telegram verification code: ", secret=False)
        if not value:
            raise RuntimeError("Telegram verification code cannot be empty.")
        return value

    def get_password(self) -> str:
        return self._resolve(
            "password", "TELEGRAM_PASSWORD", "Telegram 2FA password: ", secret=True
        )

    def get_email(self) -> str:
        return self._resolve(
            "email", "TELEGRAM_EMAIL", "Telegram login email address: ", secret=False
        )

    def get_email_code(self) -> str:
        return self._resolve(
            "email_code", "TELEGRAM_EMAIL_CODE", "Telegram email verification code: ", secret=False
        )


def tdlib_parameters(
    *,
    api_id: int,
    api_hash: str,
    database_directory: str,
    files_directory: str,
    database_encryption_key: str = "",
    use_test_dc: bool = False,
) -> dict[str, Any]:
    return {
        "@type": "setTdlibParameters",
        "use_test_dc": use_test_dc,
        "database_directory": database_directory,
        "files_directory": files_directory,
        "use_file_database": True,
        "use_chat_info_database": True,
        "use_message_database": True,
        "use_secret_chats": True,
        "api_id": api_id,
        "api_hash": api_hash,
        "system_language_code": "en",
        "device_model": "tdelegram",
        "system_version": platform.release(),
        "application_version": "0.1.0",
        "database_encryption_key": database_encryption_key,
    }


def response_for_state(
    state: str,
    state_data: dict[str, Any],
    provider: CredentialProvider,
    *,
    database_directory: str,
    files_directory: str,
    use_test_dc: bool = False,
) -> dict[str, Any] | None:
    """Build the TDLib request for an authorization state, or None if terminal."""
    if state == "authorizationStateWaitTdlibParameters":
        key = provider.get_database_key()
        return tdlib_parameters(
            api_id=provider.get_api_id(),
            api_hash=provider.get_api_hash(),
            database_directory=database_directory,
            files_directory=files_directory,
            database_encryption_key=base64.b64encode(key.encode("utf-8")).decode("ascii")
            if key
            else "",
            use_test_dc=use_test_dc,
        )
    if state == "authorizationStateWaitEncryptionKey":
        key = provider.get_database_key()
        encoded = base64.b64encode(key.encode("utf-8")).decode("ascii")
        return {"@type": "checkDatabaseEncryptionKey", "encryption_key": encoded}
    if state == "authorizationStateWaitPhoneNumber":
        return {
            "@type": "setAuthenticationPhoneNumber",
            "phone_number": provider.get_phone(),
            "settings": {
                "@type": "phoneNumberAuthenticationSettings",
                "allow_flash_call": False,
                "allow_missed_call": False,
                "is_current_phone_number": False,
                "allow_sms_retriever_api": False,
                "authentication_tokens": [],
            },
        }
    if state == "authorizationStateWaitCode":
        return {"@type": "checkAuthenticationCode", "code": provider.get_code()}
    if state == "authorizationStateWaitPassword":
        return {"@type": "checkAuthenticationPassword", "password": provider.get_password()}
    if state == "authorizationStateWaitEmailAddress":
        return {"@type": "setAuthenticationEmailAddress", "email_address": provider.get_email()}
    if state == "authorizationStateWaitEmailCode":
        return {
            "@type": "checkAuthenticationEmailCode",
            "code": {"@type": "emailAddressAuthenticationCode", "code": provider.get_email_code()},
        }
    if state == "authorizationStateWaitOtherDeviceConfirmation":
        link = state_data.get("link", "")
        raise RuntimeError(
            "TDLib requested QR/device confirmation. Confirm it in Telegram."
            + (f" Link: {link}" if link else "")
        )
    if state == "authorizationStateWaitRegistration":
        raise RuntimeError("This Telegram account is not registered yet.")
    if state == "authorizationStateWaitPremiumPurchase":
        raise RuntimeError("Telegram requires a Premium purchase before this login can continue.")
    if state == "authorizationStateReady":
        return None
    if state in {
        "authorizationStateLoggingOut",
        "authorizationStateClosing",
        "authorizationStateClosed",
    }:
        raise RuntimeError(f"Telegram authorization ended in {state}.")
    raise RuntimeError(f"Unknown authorization state: {state}")


def run_auth(
    client: Any,
    provider: CredentialProvider,
    *,
    timeout: float = 300.0,
    database_directory: str = "",
    files_directory: str = "",
    use_test_dc: bool = False,
) -> dict[str, Any]:
    """Drive the auth state machine to `authorizationStateReady`.

    A TDLib `error` event retries the current state rather than aborting
    the login, so one mistyped code does not end the attempt. Bounded by
    an overall timeout.
    """
    import time

    deadline = time.monotonic() + timeout
    last_state: str | None = None

    def advance(state_data: dict[str, Any]) -> bool:
        """Answer one authorization state. True when the machine is done."""
        nonlocal last_state
        state = str(state_data.get("@type", ""))
        if state == "authorizationStateReady":
            return True
        if state == last_state:
            return False
        request = response_for_state(
            state,
            state_data,
            provider,
            database_directory=database_directory,
            files_directory=files_directory,
            use_test_dc=use_test_dc,
        )
        if request is None:
            return True
        result = client.send_request(request)
        if isinstance(result, dict) and result.get("@type") == "error":
            err = from_td_error(result, str(request.get("@type", "")))
            if isinstance(err, TelegramError):
                # Stay in this state; TDLib will re-emit an update.
                last_state = None
                return False
        last_state = state
        return False

    # td_create_client_id only reserves an id: the TDLib instance stays
    # dormant and emits no updates until it receives its first request.
    # Asking for the current state both wakes it and seeds the machine,
    # so this must happen before the update loop or it waits forever.
    bootstrap = client.send_request({"@type": "getAuthorizationState"})
    if isinstance(bootstrap, dict) and str(bootstrap.get("@type", "")).startswith(
        "authorizationState"
    ):
        if advance(bootstrap):
            return {"@type": "ok"}

    while True:
        if time.monotonic() > deadline:
            raise TelegramTimeoutError("Timed out during Telegram authentication.")
        event = client.next_update(timeout=1.0)
        if event is None:
            continue
        etype = event.get("@type")
        if etype == "error":
            # Do not abort the whole login on one mistyped code; stay in
            # the current state so the next update re-prompts.
            last_state = None
            continue
        if etype != "updateAuthorizationState":
            continue
        if advance(event.get("authorization_state", {})):
            return {"@type": "ok"}
