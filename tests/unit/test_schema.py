"""The schema check: what it refuses, what it lets through, and where it runs.

TDLib drops a field it does not know and defaults one that is missing, so a
request with the wrong parameter names runs with its arguments silently unset.
Every test double used to answer such requests happily, which is how a dozen
commands shipped sending fields TDLib does not have.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tdelegram import schema
from tdelegram.transport import FakeTransport


def test_a_conforming_request_has_no_problems() -> None:
    request = {
        "@type": "addMessageReaction",
        "@extra": "abc",
        "chat_id": -1001246902558,
        "message_id": 5,
        "reaction_type": {"@type": "reactionTypeEmoji", "emoji": "👍"},
    }
    assert schema.validate(request) == []


def test_an_unknown_field_is_named_with_the_fields_there_are() -> None:
    """`msg react` sent `emoji` where TDLib reads `reaction_type`."""
    problems = schema.validate(
        {"@type": "addMessageReaction", "chat_id": 1, "message_id": 5, "emoji": "👍"}
    )
    assert len(problems) == 1
    assert "'emoji'" in problems[0] and "reaction_type" in problems[0]


def test_a_near_miss_suggests_the_real_name() -> None:
    problems = schema.validate({"@type": "getForumTopics", "chat_id": 1, "offset_topic_id": 0})
    assert "did you mean 'offset_forum_topic_id'?" in problems[0]


def test_a_number_where_tdlib_wants_a_string_is_refused() -> None:
    """Global search sent `offset: 0`; TDLib refuses a number for a string."""
    problems = schema.validate({"@type": "searchMessages", "query": "x", "offset": 0})
    assert problems and "expected a string" in problems[0]


def test_integers_may_travel_as_strings() -> None:
    # That is how int64 values cross JSON without losing precision.
    assert schema.validate({"@type": "getChat", "chat_id": "-1001246902558"}) == []
    assert schema.validate({"@type": "getChat", "chat_id": "twelve"})
    assert schema.validate({"@type": "getChat", "chat_id": True}), "a boolean is not an id"


def test_nested_objects_are_checked_too() -> None:
    """Media sends passed a bare InputFile where TDLib now wants inputPhoto."""
    request = {
        "@type": "sendMessage",
        "chat_id": 1,
        "input_message_content": {
            "@type": "inputMessagePhoto",
            "photo": {"@type": "inputFileLocal", "path": "/tmp/a.jpg"},
        },
    }
    problems = schema.validate(request)
    assert problems and "inputPhoto" in problems[0]
    request["input_message_content"]["photo"] = {
        "@type": "inputPhoto",
        "photo": {"@type": "inputFileLocal", "path": "/tmp/a.jpg"},
    }
    assert schema.validate(request) == []


def test_an_abstract_type_needs_one_of_its_objects() -> None:
    missing = schema.validate({"@type": "getChats", "chat_list": {"limit": 1}})
    assert missing and "needs an @type" in missing[0]
    wrong = schema.validate({"@type": "getChats", "chat_list": {"@type": "formattedText"}})
    assert wrong and "is not a ChatList" in wrong[0]


def test_arrays_are_checked_element_by_element() -> None:
    problems = schema.validate(
        {"@type": "deleteMessages", "chat_id": 1, "message_ids": [1, "two"], "revoke": True}
    )
    assert problems == ["deleteMessages.message_ids[1]: expected int53, got string 'two'"]
    assert schema.validate({"@type": "deleteMessages", "chat_id": 1, "message_ids": 5})


def test_null_and_missing_fields_are_left_to_tdlib() -> None:
    # Both mean "the default", which plenty of requests rely on.
    assert schema.validate({"@type": "getChatHistory", "chat_id": 1, "only_local": None}) == []
    assert schema.validate({"@type": "getChatHistory"}) == []


def test_an_unknown_function_is_refused_with_a_suggestion() -> None:
    problems = schema.validate({"@type": "getChatFolders"})
    assert problems and "not a TDLib function" in problems[0]
    assert schema.validate({"chat_id": 1}) == ["the request has no @type"]


def test_every_function_in_the_registry_has_a_shape() -> None:
    """schema.json and methods.json come from the same td_api.tl."""
    registry = json.loads(
        (schema.Path(schema.__file__).with_name("methods.json")).read_text(encoding="utf-8")
    )
    assert all(schema.function(name) is not None for name in registry)


def test_describe_covers_functions_objects_and_types() -> None:
    fn = schema.describe("addMessageReaction")
    assert fn is not None and fn["kind"] == "function"
    assert list(fn["params"])[:3] == ["chat_id", "message_id", "reaction_type"]
    assert fn["docs"].endswith("classtd_1_1td__api_1_1add_message_reaction.html")
    obj = schema.describe("reactionTypeEmoji")
    assert obj is not None and obj["kind"] == "object" and obj["type"] == "ReactionType"
    kind = schema.describe("ReactionType")
    assert kind is not None and "reactionTypeEmoji" in kind["constructors"]
    assert schema.describe("noSuchThing") is None
    assert "getChatFolder" in schema.suggest("getChatFolders")


def test_the_fake_answers_a_malformed_request_as_tdlib_would() -> None:
    transport = FakeTransport()
    transport.send(1, json.dumps({"@type": "getChat", "@extra": "x", "chat_id": "somechat"}))
    reply = json.loads(transport.receive(1.0) or "{}")
    assert reply["@type"] == "error" and reply["code"] == 400
    assert reply["@extra"] == "x", "the refusal must route back to the caller"
    assert transport.schema_violations
    # Recorded, so the autouse fixture would fail the test; clear it for this one.
    transport.schema_violations.clear()


def test_the_fake_checks_td_execute_too() -> None:
    transport = FakeTransport()
    reply = json.loads(transport.execute(json.dumps({"@type": "parseTextEntities", "txt": ""})))
    assert reply["@type"] == "error"
    transport.schema_violations.clear()


def test_the_schema_file_must_be_there(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Like the registry, a missing schema is a loud failure, not a pass."""
    monkeypatch.setattr(schema, "__file__", str(tmp_path / "schema.py"))
    schema._shapes.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="schema unavailable"):
            schema.validate({"@type": "getMe"})
    finally:
        monkeypatch.undo()
        schema._shapes.cache_clear()
