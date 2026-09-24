#!/usr/bin/env python3
"""Emit methods.json: one {verdict: read|write|destructive, reason: str} per
TDLib function. Judgement calls are recorded, not buried.

Two schema sources are accepted, and both yield the same function set:

- `td_api.h`  - the generated C++ header, present in a TDLib install.
- `td_api.tl` - the scheme file that header is generated from. It lives in
  the tdlib/td repository, so CI can fetch it at a pinned commit instead of
  building TDLib. See .github/workflows/ci.yml.

From a `td_api.tl` it also emits schema.json: the parameters of every
function and the fields of every object. TDLib ignores a field it does not
know and defaults one that is missing, so a request built with the wrong
names still runs -- with its arguments silently unset. schema.json is what
`tdelegram.schema.validate()` checks requests against.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

CLASS_RE = re.compile(r"class (\w+) final : public Function")
# In td_api.tl every function is one declaration below the ---functions---
# marker: `name arg:Type ... = ReturnType;`, with // comment lines between.
TL_FUNCTION_RE = re.compile(r"^([a-z][A-Za-z0-9]*)[ =]")
TL_FUNCTIONS_MARKER = "---functions---"
# The same declaration, with its fields and result captured.
TL_DECLARATION_RE = re.compile(r"^([a-z][A-Za-z0-9]*)((?:\s+\w+:\S+)*)\s*=\s*([A-Za-z0-9<>]+);$")
# The preamble declares TL's own primitives; they are not TDLib objects.
TL_BUILTINS = frozenset(
    {"double", "string", "int32", "int53", "int64", "bytes", "boolFalse", "boolTrue", "vector"}
)

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
    # --- Audited writes: a `write` needs --yes, a `destructive` also needs a
    # typed confirmation on a TTY. These earn the second layer. The test is
    # irreversible loss, surrendered access, or disruption to someone else.
    #
    # Ownership and public identity, gone or claimable by a squatter.
    "transferChatOwnership": ("destructive", "hands the chat to another account; irreversible"),
    "setUsername": ("destructive", "frees the old username for anyone to claim"),
    "setSupergroupUsername": ("destructive", "frees the old public link for anyone to claim"),
    "disableAllSupergroupUsernames": ("destructive", "releases every public link at once"),
    # The general member setter can ban, which is what banChatMember does. If
    # this stayed a write it would be a way around that confirmation, so the
    # method takes the verdict of the worst thing it can express -- at the cost
    # of a typed confirmation for an ordinary promotion.
    "setChatMemberStatus": ("destructive", "can ban or demote; bans disrupt another user"),
    # Revoking access others are relying on.
    "disconnectWebsite": ("destructive", "revokes a website login"),
    "disconnectAllWebsites": ("destructive", "revokes every website login at once"),
    # Credentials.
    "setPassword": ("destructive", "changes or removes 2-step verification"),
    "recoverPassword": ("destructive", "replaces the 2FA password via recovery"),
    "recoverAuthenticationPassword": ("destructive", "replaces the 2FA password via recovery"),
    # Settings whose effect is bulk deletion, now or later.
    "setAccountTtl": ("destructive", "schedules deletion of the whole account"),
    "setChatMessageAutoDeleteTime": ("destructive", "schedules irreversible message deletion"),
    "setDefaultMessageAutoDeleteTime": ("destructive", "schedules irreversible message deletion"),
    # One-way conversions and exposures.
    "toggleSupergroupIsBroadcastGroup": ("destructive", "upgrade to broadcast group is one-way"),
    "toggleSupergroupIsAllHistoryAvailable": (
        "destructive",
        "exposes past history to new members; cannot be unseen",
    ),
    # Bulk loss of user content.
    "clearAllDraftMessages": ("destructive", "wipes every draft with no undo"),
    "clearImportedContacts": ("destructive", "drops all imported contacts server-side"),
    "unpinAllChatMessages": ("destructive", "unpins everything; the previous set is not recorded"),
    "unpinAllForumTopicMessages": ("destructive", "unpins an entire topic at once"),
    "unpinAllDirectMessagesChatTopicMessages": ("destructive", "unpins an entire topic at once"),
    "dropGiftOriginalDetails": ("destructive", "removes provenance from a gift permanently"),
    # Money and assets that do not come back.
    "sendPaymentForm": ("destructive", "completes a payment; funds leave"),
    "sendGift": ("destructive", "spends currency on a gift"),
    "sendResoldGift": ("destructive", "spends currency on a resold gift"),
    "placeGiftAuctionBid": ("destructive", "commits currency to a bid"),
    "increaseGiftAuctionBid": ("destructive", "commits more currency to a bid"),
    "transferBusinessAccountStars": ("destructive", "moves currency out of the account"),
    "transferGift": ("destructive", "hands a gift to someone else; irreversible"),
    # A search* prefix made this a read, but star_count pays for the query.
    "searchPublicPosts": ("destructive", "can spend Telegram Stars on the search"),
    # Access you may not be able to regain.
    "leaveChat": ("destructive", "a private chat cannot be rejoined without a new invite"),
    # Reviewed and deliberately left as writes, so a heuristic change cannot
    # quietly promote or demote them without this file changing too.
    "setChatPermissions": ("write", "restricts members, but is reversible"),
    "setMessageSenderBlockList": ("write", "blocking is reversible"),
    "clearRecentStickers": ("write", "clears a local convenience list"),
    "clearRecentlyFoundChats": ("write", "clears a local convenience list"),
    "unpinChatMessage": ("write", "unpins one known message; re-pinnable"),
    "reportChat": ("write", "reporting abuse should not be gated behind friction"),
    "reportSupergroupSpam": ("write", "reporting abuse should not be gated behind friction"),
    "discardCall": ("write", "ends a call; nothing is destroyed"),
    "optimizeStorage": ("write", "deletes local cache only"),
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


def parse_shapes(text: str) -> dict[str, dict[str, dict[str, Any]]]:
    """Every function's parameters and every object's fields, from td_api.tl.

    Field order is the schema's, which is also the order TDLib documents them in.
    """
    types_part, marker, functions_part = text.partition(TL_FUNCTIONS_MARKER)
    if not marker:
        raise SystemExit(f"No {TL_FUNCTIONS_MARKER} section: is this really a td_api.tl?")

    def _declarations(part: str) -> list[tuple[str, dict[str, str], str]]:
        found = []
        for line in part.splitlines():
            match = TL_DECLARATION_RE.match(line.strip())
            if match and match.group(1) not in TL_BUILTINS:
                fields = dict(pair.split(":", 1) for pair in match.group(2).split())
                found.append((match.group(1), fields, match.group(3)))
        return found

    return {
        "constructors": {
            name: {"type": result, "fields": fields}
            for name, fields, result in _declarations(types_part)
        },
        "functions": {
            name: {"returns": result, "params": fields}
            for name, fields, result in _declarations(functions_part)
        },
    }


def render_shapes(shapes: dict[str, dict[str, dict[str, Any]]]) -> str:
    """JSON with one declaration per line, so a TDLib bump reviews as a diff."""
    sections = []
    for section in ("constructors", "functions"):
        entries = shapes[section]
        body = ",\n".join(
            f"{json.dumps(name)}: {json.dumps(entries[name], separators=(',', ':'))}"
            for name in sorted(entries)
        )
        sections.append(f'"{section}": {{\n{body}\n}}')
    return "{\n" + ",\n".join(sections) + "\n}\n"


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
    parser.add_argument(
        "--shapes-output",
        default="src/tdelegram/schema.json",
        help="Where to write request shapes (needs td_api.tl)",
    )
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

    if schema.suffix != ".tl":
        # The header carries the same shapes as C++ members; parsing C++ to get
        # them back is fragile, and the .tl is one curl away. See CONTRIBUTING.
        print(f"Request shapes need td_api.tl; {args.shapes_output} left as it was.")
        return
    shapes = parse_shapes(schema.read_text(encoding="utf-8", errors="replace"))
    shapes_out = Path(args.shapes_output)
    shapes_out.parent.mkdir(parents=True, exist_ok=True)
    shapes_out.write_text(render_shapes(shapes), encoding="utf-8")
    print(
        f"Wrote {shapes_out}: {len(shapes['functions'])} functions, "
        f"{len(shapes['constructors'])} objects"
    )


if __name__ == "__main__":
    main()
