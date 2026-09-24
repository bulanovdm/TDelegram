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


# Methods whose TDLib semantics reach a third party, move money, hand over the
# account's identity, or complete an auth flow. A prefix heuristic classified
# every one of these as a harmless read at some point, which meant no gate at
# all. If a future change flips one back, that is a safety regression and this
# test is the tripwire.
MUST_BE_GATED = [
    "getCallbackQueryAnswer",
    "getInlineQueryResults",
    "openWebApp",
    "getWebAppUrl",
    "getWebAppLinkUrl",
    "getMainWebApp",
    "getLoginUrl",
    "getExternalLink",
    "getPassportAuthorizationForm",
    "getPaymentForm",
    "getChatRevenueWithdrawalUrl",
    "getStarWithdrawalUrl",
    "getGramWithdrawalUrl",
    "getUpgradedGiftWithdrawalUrl",
    "checkAuthenticationBotToken",
    "checkPhoneNumberCode",
    "checkEmailAddressVerificationCode",
    "checkRecoveryEmailAddressCode",
    "cancelPasswordReset",
    "cancelRecoveryEmailAddressVerification",
    # star_count spends Telegram Stars on the query.
    "searchPublicPosts",
]


def test_side_effecting_methods_are_never_read() -> None:
    registry = _registry()
    ungated = [m for m in MUST_BE_GATED if registry.get(m, {}).get("verdict") == "read"]
    assert not ungated, f"these have remote side effects but would run without --yes: {ungated}"


def test_reviewed_verdicts_are_pinned() -> None:
    """A recorded human judgment must survive any change to the heuristics."""
    from generate_method_registry import OVERRIDES

    registry = _registry()
    drifted = [
        f"{m}: reviewed as {verdict}, registry says {registry[m]['verdict']}"
        for m, (verdict, _reason) in OVERRIDES.items()
        if m in registry and registry[m]["verdict"] != verdict
    ]
    assert not drifted, drifted


def test_read_prefix_requires_a_word_boundary() -> None:
    """Regression: the `can` prefix used to swallow every `cancel*` method."""
    from generate_method_registry import classify

    assert classify("cancelPasswordReset")[0] != "read"
    assert classify("canPostStory")[0] == "read", "genuine can* probes stay reads"


# Irreversible operations. A `write` only needs --yes; a `destructive` also
# needs a typed confirmation on a TTY. Demoting any of these to `write` drops
# a layer of protection from the most final things the API can do.
MUST_BE_DESTRUCTIVE = [
    "transferChatOwnership",
    "transferGift",
    "transferBusinessAccountStars",
    "setUsername",
    "setSupergroupUsername",
    "disableAllSupergroupUsernames",
    "setPassword",
    "recoverPassword",
    "setAccountTtl",
    "setChatMessageAutoDeleteTime",
    "disconnectAllWebsites",
    "toggleSupergroupIsBroadcastGroup",
    "toggleSupergroupIsAllHistoryAvailable",
    "clearAllDraftMessages",
    "unpinAllChatMessages",
    "sendPaymentForm",
    "searchPublicPosts",
    "leaveChat",
    # Already destructive before the audit; pinned so they stay that way.
    "deleteAccount",
    "deleteChatHistory",
    "banChatMember",
    "logOut",
    "terminateAllOtherSessions",
]


def test_irreversible_methods_are_destructive() -> None:
    registry = _registry()
    weak = [
        f"{m}={registry[m]['verdict']}"
        for m in MUST_BE_DESTRUCTIVE
        if m in registry and registry[m]["verdict"] != "destructive"
    ]
    assert not weak, f"these lost the typed-confirmation layer: {weak}"


def test_a_general_setter_cannot_undercut_a_specific_one() -> None:
    """setChatMemberStatus can ban, so it must be gated like banChatMember.

    The gate is per method, not per payload. If the general form were merely
    a write, `--yes` alone would ban someone while the dedicated method still
    demanded a typed confirmation -- a way around the stricter gate.
    """
    registry = _registry()
    assert registry["setChatMemberStatus"]["verdict"] == registry["banChatMember"]["verdict"]
