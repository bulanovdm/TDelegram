#!/usr/bin/env python3
"""Emit methods.json: one {verdict: read|write|destructive, reason: str} per
TDLib function. Judgement calls are recorded, not buried.

Two schema sources are accepted, and both yield the same function set:

- `td_api.h`  - the generated C++ header, present in a TDLib install.
- `td_api.tl` - the scheme file that header is generated from. It lives in
  the tdlib/td repository, so CI can fetch it at a pinned commit instead of
  building TDLib. See .github/workflows/ci.yml.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

CLASS_RE = re.compile(r"class (\w+) final : public Function")
# In td_api.tl every function is one declaration below the ---functions---
# marker: `name arg:Type ... = ReturnType;`, with // comment lines between.
TL_FUNCTION_RE = re.compile(r"^([a-z][A-Za-z0-9]*)[ =]")
TL_FUNCTIONS_MARKER = "---functions---"

SCHEMA_ENV = "TDELEGRAM_TD_API"

# Explicit overrides: method -> (verdict, reason). Judgement calls live here.
OVERRIDES: dict[str, tuple[str, str]] = {
    # Reads that look like writes but are safe lookups.
    "getMessageLink": ("read", "pure lookup returning a link; no state change"),
    "searchPublicChat": ("read", "pure lookup by username; no state change"),
    "checkChatInviteLink": ("read", "validates an invite link without joining"),
    "checkAuthenticationCode": ("write", "advances the login state machine"),
    "checkAuthenticationPassword": ("write", "advances the login state machine"),
    "checkAuthenticationEmailCode": ("write", "advances the login state machine"),
    "checkDatabaseEncryptionKey": ("write", "unlocks the local database"),
    "setAuthenticationPhoneNumber": ("write", "advances the login state machine"),
    "setAuthenticationEmailAddress": ("write", "advances the login state machine"),
    "setTdlibParameters": ("write", "configures the TDLib instance"),
    # Local/read-receipt side effects, deliberately classified read.
    "viewMessages": ("read", "marks messages read; local/read-receipt state only"),
    "openChat": ("read", "marks chat open; local/read-receipt state only"),
    "closeChat": ("read", "marks chat closed; local state only"),
    "openMessageContent": ("read", "marks content viewed; local state only"),
    "downloadFile": ("read", "fetches bytes to local disk; no remote mutation"),
    "loadChats": ("read", "loads chat list into memory; local state only"),
    "getChats": ("read", "read-only chat list fetch"),
    "readAllChatReactions": ("read", "clears local reaction badges only"),
    "markReactionsAsSeen": ("read", "clears local reaction badges only"),
    # Destructive: called out explicitly in the plan.
    "logOut": ("destructive", "ends the session on all devices state; requires re-login"),
    "deleteAccount": ("destructive", "irreversibly deletes the Telegram account"),
    "terminateAllOtherSessions": ("destructive", "kills all other active sessions"),
    "terminateSession": ("destructive", "kills an active session"),
    "deleteChatHistory": ("destructive", "irreversibly deletes chat history"),
    "deleteChat": ("destructive", "irreversibly deletes a chat"),
    "banChatMember": ("destructive", "kicks and bans; disrupts another user"),
    "deleteMessages": ("destructive", "irreversibly deletes messages"),
    "removeMessageReaction": ("write", "removes own reaction; reversible"),
    "removeContacts": ("write", "removes contacts; re-addable"),
    "pingProxy": ("read", "connectivity probe; no state change"),
    "testProxy": ("read", "connectivity probe; no state change"),
    "getProxies": ("read", "read-only proxy list fetch"),
    "testNetwork": ("read", "connectivity probe; no state change"),
    # --- Audited reads: a prefix said "read", the semantics say otherwise. ---
    # A wrong write costs an extra --yes. A wrong read means no gate at all,
    # so these are the classifications that actually matter.
    #
    # Third parties act on these.
    "getCallbackQueryAnswer": ("write", "presses a bot's inline button; the bot sees it and acts"),
    "getInlineQueryResults": ("write", "sends an inline query to a bot, which is notified"),
    "openWebApp": ("write", "starts a bot web-app session; the bot is notified"),
    # These hand the account's identity to something outside Telegram.
    "getLoginUrl": ("write", "authorizes the user on a third-party website"),
    "getExternalLink": ("write", "hands the account identity to an external site"),
    "getPassportAuthorizationForm": ("write", "begins sharing identity documents with a service"),
    "getWebAppUrl": ("write", "opens a bot web app as the user; the bot is notified"),
    "getWebAppLinkUrl": ("write", "opens a bot web app as the user; the bot is notified"),
    "getMainWebApp": ("write", "opens a bot's main web app as the user"),
    "getGuardBotWebAppUrl": ("write", "opens a bot web app as the user"),
    # Money leaves, or an authenticated financial session opens.
    "getPaymentForm": ("write", "opens a payment session with the provider"),
    "getChatRevenueWithdrawalUrl": ("write", "initiates a revenue withdrawal"),
    "getStarWithdrawalUrl": ("write", "initiates a Telegram Stars withdrawal"),
    "getGramWithdrawalUrl": ("write", "initiates a currency withdrawal"),
    "getUpgradedGiftWithdrawalUrl": ("write", "initiates withdrawal of an upgraded gift"),
    "getStarAdAccountUrl": ("write", "opens an authenticated ad-platform session"),
    # check* that completes a flow rather than merely validating.
    "checkAuthenticationBotToken": ("write", "advances the login state machine"),
    "checkAuthenticationPasskey": ("write", "advances the login state machine"),
    "checkAuthenticationWebToken": ("write", "advances the login state machine"),
    "checkAuthenticationPremiumPurchase": ("write", "advances the login state machine"),
    "checkEmailAddressVerificationCode": ("write", "completes email verification"),
    "checkLoginEmailAddressCode": ("write", "completes login email verification"),
    "checkOauthRequestMatchCode": ("write", "advances an OAuth authorization"),
    "checkPhoneNumberCode": ("write", "completes the request the code was sent for"),
    "checkRecoveryEmailAddressCode": ("write", "completes recovery email verification"),
    # cancel* aborts a remote operation. Reviewed as reads where purely local.
    "cancelDownloadFile": ("read", "stops a local download; no remote effect"),
    "cancelPasswordReset": ("write", "aborts a pending password reset on the server"),
    "cancelRecoveryEmailAddressVerification": ("write", "aborts a pending verification"),
    "cancelPreliminaryUploadFile": ("write", "aborts an upload already in flight"),
    # Reviewed and deliberately left as reads, so a later prefix change cannot
    # silently flip them without this file changing too.
    "getLoginUrlInfo": ("read", "reports whether a login URL needs confirmation"),
    "getExternalLinkInfo": ("read", "reports whether a link needs confirmation"),
    "getPaymentReceipt": ("read", "reads a receipt for a completed payment"),
    "getStarTransactions": ("read", "reads transaction history"),
    "getTonTransactions": ("read", "reads transaction history"),
    "getChatRevenueTransactions": ("read", "reads revenue history"),
    "getAllPassportElements": ("read", "reads the user's own stored documents"),
    "getPassportElement": ("read", "reads one of the user's own stored documents"),
    "checkPasswordRecoveryCode": ("read", "validates a recovery code without using it"),
    "checkAuthenticationPasswordRecoveryCode": ("read", "validates without recovering"),
    "checkPremiumGiftCode": ("read", "reads gift code info; applying it is separate"),
}

READ_PREFIXES = (
    "get",
    "search",
    "fetch",
    "load",
    "view",
    "open",
    "download",
    "parse",
    "checkChatUsername",
    "checkSticker",
    "can",
    "test",
)

DESTRUCTIVE_PREFIXES = ("delete", "remove", "ban", "terminate", "destroy", "revoke", "reset")
DESTRUCTIVE_EXACT = {
    "logOut",
    "close",
    "closeSecretChat",
    "destroy",
}


def classify(name: str) -> tuple[str, str]:
    if name in OVERRIDES:
        return OVERRIDES[name]
    if name in DESTRUCTIVE_EXACT or name.startswith(DESTRUCTIVE_PREFIXES):
        # close (global) kills the client; closeChat is already overridden to read.
        if name == "close":
            return ("destructive", "shuts down the TDLib client instance")
        return ("destructive", f"heuristic: {name} irreversibly removes or revokes state")
    for prefix in READ_PREFIXES:
        # Require a word boundary: without this "can" matches "cancelPasswordReset"
        # and a remote abort is classified as a capability probe.
        if name.startswith(prefix) and (
            len(name) == len(prefix) or name[len(prefix)].isupper()
        ):
            if prefix in {"load", "view", "open", "download"}:
                return ("read", "local or read-receipt state only; no remote mutation")
            return ("read", f"heuristic: {prefix}* lookup without remote mutation")
    if name.startswith("check"):
        return ("read", "heuristic: check* validation without mutation")
    # Default closed-safe: anything not recognised as a read mutates something.
    return ("write", f"heuristic: {name} mutates remote or account state")


def parse_header(text: str) -> list[str]:
    return _dedup(CLASS_RE.findall(text))


def parse_tl(text: str) -> list[str]:
    _, _, body = text.partition(TL_FUNCTIONS_MARKER)
    if not body:
        raise SystemExit(f"No {TL_FUNCTIONS_MARKER} section: is this really a td_api.tl?")
    names = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        match = TL_FUNCTION_RE.match(stripped)
        if match:
            names.append(match.group(1))
    return _dedup(names)


def parse_schema(path: Path) -> list[str]:
    """Extract function names from either schema form."""
    text = path.read_text(encoding="utf-8", errors="replace")
    names = parse_tl(text) if path.suffix == ".tl" else parse_header(text)
    if not names:
        raise SystemExit(f"Parsed no functions from {path}; wrong file?")
    return names


def _dedup(names: list[str]) -> list[str]:
    """Preserve source order, drop repeats."""
    return list(dict.fromkeys(names))


def candidate_paths() -> list[Path]:
    """Where a TDLib install may have left the generated header.

    Deliberately free of version pins: a Homebrew Cellar path carries a
    build hash that is specific to one machine.
    """
    candidates = [
        Path("/usr/include/td/telegram/td_api.h"),
        Path("/usr/local/include/td/telegram/td_api.h"),
        Path("/opt/homebrew/opt/tdlib/include/td/telegram/td_api.h"),
        Path("/opt/homebrew/include/td/telegram/td_api.h"),
    ]
    candidates.extend(sorted(Path("/opt/homebrew/Cellar/tdlib").glob("*/include/td/telegram/*.h")))
    return candidates


def discover_schema(explicit: str = "") -> Path:
    """Resolve the schema: explicit -> $TDELEGRAM_TD_API -> install candidates."""
    import os

    for value in (explicit, os.environ.get(SCHEMA_ENV, "")):
        if value:
            path = Path(value).expanduser()
            if not path.exists():
                raise SystemExit(f"Schema not found: {path}")
            return path
    for candidate in candidate_paths():
        if candidate.exists() and candidate.name in ("td_api.h", "td_api.tl"):
            return candidate
    raise SystemExit(
        "Could not locate td_api.h or td_api.tl. Pass --schema, set "
        f"{SCHEMA_ENV}, or install TDLib. CI fetches the .tl at a pinned "
        "commit; see .github/workflows/ci.yml."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate TDLib method registry")
    parser.add_argument("--schema", default="", help="Path to td_api.h or td_api.tl")
    parser.add_argument("--header", default="", help="Deprecated alias for --schema")
    parser.add_argument("--output", default="src/tdelegram/methods.json")
    args = parser.parse_args()

    schema = discover_schema(args.schema or args.header)
    names = parse_schema(schema)
    registry = {name: {"verdict": v, "reason": r} for name in names for (v, r) in [classify(name)]}
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for entry in registry.values():
        counts[entry["verdict"]] = counts.get(entry["verdict"], 0) + 1
    print(f"Parsed {schema}: {len(registry)} functions {counts}")


if __name__ == "__main__":
    main()
