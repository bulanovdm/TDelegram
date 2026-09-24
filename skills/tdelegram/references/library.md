# Python API

Use this when writing code against TDelegram rather than driving the CLI. The
CLI is a thin layer over exactly these calls.

## Contents

- [Opening a client](#opening-a-client)
- [The gate in code](#the-gate-in-code)
- [Domain modules](#domain-modules)
- [Paging](#paging)
- [Normalization](#normalization)
- [Updates](#updates)
- [Errors](#errors)
- [Testing against a fake](#testing-against-a-fake)
- [Two facts that will bite you](#two-facts-that-will-bite-you)

## Opening a client

```python
from tdelegram.auth import ConsoleCredentialProvider, run_auth
from tdelegram.client import TelegramClient
from tdelegram.config import default_base_dir, discover_library
from tdelegram.transport import TdJsonTransport

profile = default_base_dir() / "profiles" / "default"
client = TelegramClient(TdJsonTransport(discover_library()))
run_auth(
    client,
    ConsoleCredentialProvider(base_dir=default_base_dir()),
    database_directory=str(profile / "tdlib"),
    files_directory=str(profile / "files"),
)
```

`run_auth` is not optional. TDLib parameters live on the client instance, not in
the saved session, so a fresh process must replay the handshake before any call
works — skip it and every call returns *"Initialization parameters are needed"*.

To ask whether a session is usable without prompting for anything:

```python
from tdelegram.auth import NonInteractiveCredentialProvider, current_state

state = current_state(
    client,
    NonInteractiveCredentialProvider(base_dir=default_base_dir()),
    database_directory=str(profile / "tdlib"),
    files_directory=str(profile / "files"),
)
# {"@type": "authorizationStateReady", "authorized": True, "needs": None}
```

`TelegramClient` is a context manager, and `close()` shuts the TDLib client down
cleanly.

## The gate in code

`TelegramClient.call()` is the single chokepoint. It looks the method up in
`methods.json` and refuses anything mutating unless you pass the matching flag:

```python
client.call("getMe", {})                                    # read: runs
client.call("sendMessage", {...})                           # raises WriteConfirmationRequired
client.call("sendMessage", {...}, allow_write=True)         # performs
client.call("deleteMessages", {...},
            allow_write=True, allow_destructive=True)       # performs
```

The exception carries a `preview` dict — method, verdict, recorded reason,
params — which is what the CLI prints. Show it to a human rather than
suppressing it and retrying with the flag set.

An unknown method raises `RuntimeError` rather than defaulting to read. That is
the registry failing closed.

`send_request()` skips the gate entirely and exists for the auth handshake.
Do not route ordinary calls through it.

## Domain modules

Each module under `tdelegram.api` owns its own implementations:

```
account  chats  messages  media  contacts  users  admin  topics
folders  drafts reactions polls  search    updates bots   stories
secret   proxies inbox    export
```

Functions take the client first and thread permission through rather than
deciding for themselves:

```python
from tdelegram.api import chats, messages

for record in chats.iter_list(client, scope="main", maximum=20):
    print(record["title"])

messages.send(client, "@durov", "hello", allow_write=True)
chats.leave(client, "@somegroup", allow_write=True, allow_destructive=True)
```

`chats.resolve_id(client, ref)` turns `@handle`, a bare handle, or a numeric
string into an `int` — use it before passing a chat into a raw `call()`.

## Paging

Everything that walks history is a generator, so memory stays flat and you can
stop early:

```python
from tdelegram.api import messages

for record in messages.iter_history(client, "@somegroup", since="7d", maximum=100):
    ...
```

`paginate()` in `tdelegram.paging` enforces per-walk dedup, stops when a page
yields nothing fresh, finishes the current page after an out-of-window item, and
guards against a cursor that does not advance. If you write a new pager, reuse it
rather than looping by hand.

## Normalization

`tdelegram.normalize` flattens TDLib objects into records. Every `*_record()`
takes `include_raw=True` when the flat shape loses something you need:

```python
from tdelegram import normalize

record = normalize.message_record(raw_message, include_raw=True)
record["raw"]   # the untouched TDLib object
```

Entities and links are preserved rather than flattened into the text, so
formatting and URLs survive the trip.

For outbound formatting use `entities.parse_entities(transport, text, "markdown")`,
which goes through synchronous `td_execute`. It is **MarkdownV2**: `*bold*`,
`_italic_`, `__underline__`, `~struck~`. `**bold**` silently yields plain text.

## Updates

```python
sub = client.on_update(lambda event: print(event["@type"]))
event = client.next_update(timeout=1.0)   # or poll
sub.unsubscribe()
client.dropped_updates()                  # queue overflowed by this many
```

Each client gets a bounded queue (1000) that drops oldest on overflow, so a slow
consumer degrades rather than growing without limit.

## Errors

`tdelegram.errors` maps TDLib error objects onto a hierarchy:

| Class | When |
|---|---|
| `TelegramError` | base; carries `code`, `message`, `method` |
| `AuthError` | 401 |
| `TelegramPermissionError` | 403 |
| `NotFoundError` | 404 |
| `InvalidRequest` | 400 |
| `FloodWait` | rate limited; `retry_after` in seconds |
| `TransportError` / `TelegramTimeoutError` | local failures |
| `WriteConfirmationRequired` / `DestructiveConfirmationRequired` | the gate |

`FloodWait` auto-retries **reads only**. Writes always raise, because
re-sending a write after a flood wait is how people double-post — the decision
belongs to the caller.

Note the names: `TelegramPermissionError` and `TelegramTimeoutError` deliberately
do not shadow the builtins.

## Testing against a fake

`FakeTransport` needs no account, network, or TDLib:

```python
from tdelegram.client import TelegramClient
from tdelegram.transport import FakeTransport

transport = FakeTransport()
transport.add_simple_response("getMe", {"@type": "user", "id": 7})
client = TelegramClient(transport)
assert client.call("getMe", {})["id"] == 7
assert transport.sent  # every request, for assertions
```

Every request is checked against the pinned TDLib schema. One with a field
TDLib does not have, or a value of the wrong type, gets the 400 TDLib would
give — or, for a field TDLib would silently ignore, the 400 it should give —
and lands in `transport.schema_violations`. The repository's test suite fails
any test that leaves one there. `FakeTransport(validate=False)` is for testing
transport mechanics with requests that are not TDLib's.

The same check is available directly:

```python
from tdelegram import schema

schema.validate({"@type": "addMessageReaction", "chat_id": 1, "message_id": 5, "emoji": "x"})
# ["addMessageReaction: addMessageReaction has no field 'emoji'; it takes chat_id, ..."]
schema.describe("addMessageReaction")["params"]   # {"chat_id": "int53", ...}
```

Two behaviours will make a test lie if you forget them:

- An unmatched request still gets a generic `{"@type": "ok"}`, so a test that
  forgets to script a response passes unless it asserts on `sent`.
- Rules are first-match-wins, so scripting a second response for a method that
  is already scripted is silently ignored.

Call `reset_loops()` between tests or dispatch loops persist across them.

## Two facts that will bite you

**TDLib is dormant until spoken to.** `td_create_client_id` only reserves an id;
the instance emits no updates at all until it receives its first request. Code
that waits on `next_update()` before sending anything waits forever. `run_auth`
opens with a `getAuthorizationState` probe for exactly this reason.

**`td_receive` is global to the loaded library.** Exactly one thread may call it,
so dispatch loops are keyed on the transport's `receive_domain()` rather than on
the object. Every `TdJsonTransport` over one library shares a reader thread;
starting a second makes libtdjson abort the process. If you implement a custom
transport, return a domain string that reflects where its `receive` actually
reads from.
