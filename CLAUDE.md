# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e ".[dev]"                 # Python 3.10+

pytest                                  # full suite (no account, no network, no TDLib)
pytest tests/unit/test_safety.py -q     # one file
pytest -k write_requires_confirmation   # one test
pytest --no-cov                         # skip the coverage gate while iterating

ruff check src tests scripts
mypy --strict src/                      # strict, must stay clean

TDELEGRAM_INTEGRATION=1 pytest tests/integration -q   # opt-in, needs real libtdjson
```

`pytest` enforces `--cov-fail-under=87`; the suite sits at ~90%. Only `tdjson.py` is
omitted, because it is a direct ctypes FFI surface that cannot run without a real
libtdjson — everything else is measured, including the CLI. Do not add exclusions to
make a number go up: `cli/main.py` and `cli/commands/` were once excluded, and that is
exactly where both launch-blocking bugs lived.

### Method registry

`src/tdelegram/methods.json` is generated, not hand-edited:

```bash
python scripts/generate_method_registry.py --output src/tdelegram/methods.json
```

CI regenerates it and runs `git diff --exit-code`, so the committed file must match what
the generator emits. Classification judgement calls belong in the `OVERRIDES` dict in the
generator, never edited into the JSON, and every override must name a function the schema
has — a renamed method would otherwise lose its reviewed verdict without a word.

The same run writes `src/tdelegram/schema.json`: every function's parameters and every
object's fields. It is generated from `td_api.tl` only (a `td_api.h` leaves it untouched)
and CI diffs it too.

The generator accepts either schema form and they produce identical output:

- `td_api.h` — the generated C++ header, present in a TDLib install.
- `td_api.tl` — the scheme the header is generated from. It lives in the tdlib/td repo,
  so CI fetches it at the commit pinned in `TDLIB_COMMIT` rather than building TDLib.

Resolution order is `--schema`, then `$TDELEGRAM_TD_API`, then unpinned install paths.
`tests/contract/test_registry.py` imports that same discovery so the two can never
disagree, and skips when no schema is present. A TDLib bump is a deliberate event:
move `TDLIB_COMMIT`, regenerate both JSON files, and update `EXPECTED_FUNCTION_COUNT` in one
commit. Expect the bump to break tests: a renamed parameter now fails the schema check
instead of shipping.

## Architecture

One generic transport covers all 1022 TDLib functions; ergonomics, normalization,
safety, and errors are layered on top. Strictly one-directional:

```
tdjson.py      ctypes, modern C API only — the only module that touches libtdjson
transport.py   Transport protocol; TdJsonTransport (real) + FakeTransport (tests)
loop.py        one global reader thread per transport, @extra/@client_id routing
client.py      TelegramClient facade — the safety chokepoint
api/*.py       domain modules over TelegramClient
cli/           Typer tree over api/ (main.py is the whole tree)
schema.py      request shapes from schema.json; validate() and describe()
```

Each `api/` module owns its own implementations. Several were once re-export shims over
unrelated modules — stories living in `bots`, drafts and folders in `reactions` — so if a
function seems missing, it was moved to the module its name implies, not deleted.

**The write gate lives in `TelegramClient.call()`, nowhere else.** It looks the method
up in `methods.json` and raises `WriteConfirmationRequired` / `DestructiveConfirmationRequired`
unless `allow_write` / `allow_destructive` are passed. `safety.verdict()` fails closed —
an unknown method raises `RuntimeError` rather than defaulting to read. Any new call path
(including the raw `tdelegram call --request` escape hatch) must go through `client.call()`;
`send_request()` bypasses the gate and exists only for the auth handshake. `api/` functions
thread an `allow_write: bool = False` keyword down to `call()` rather than deciding for
themselves.

**Request shapes.** TDLib ignores a field it does not recognise and defaults a missing one,
so a request built with an outdated or misspelled parameter name runs with that argument
silently unset — only a JSON type it cannot convert is refused. `schema.validate()` rejects
both. `FakeTransport` runs it on every request a test sends and answers a violation with a
400, and the autouse fixture in `tests/conftest.py` fails any test that leaves one behind;
`tdelegram call` runs it before sending and `describe` exposes the shapes. Before this, a
dozen commands sent parameters TDLib had renamed or never had, and every test passed.

**Dispatch loop.** `td_receive` is global, not per-client, so exactly one thread may call
it. `loop_for(transport)` returns a process-wide `DispatchLoop` keyed on the transport's
`receive_domain()` — every `TdJsonTransport` over one library shares a loop, because two
reader threads make libtdjson abort the process. Keying on `id(transport)` looks equivalent
and is not;
responses route by `@extra` to a pending `Future`, updates route by `@client_id` to a
bounded (1000-item) per-client subscriber queue that drops oldest on overflow. Timed-out
requests move their `@extra` into an `_abandoned` set so a late reply is discarded instead
of leaking. Tests must call `reset_loops()` (the `client` fixture does) or loops persist
across tests.

`Future.result()` raises `concurrent.futures.TimeoutError`, which only became an alias of
the builtin `TimeoutError` in 3.11. Catch it under its own name — catching the builtin
lets the raw error escape on 3.10 instead of becoming a `TelegramTimeoutError`. The CI
matrix runs 3.10 precisely because this class of drift is invisible on a newer local
interpreter.

**Errors and flood control.** `from_td_error()` maps TDLib error objects onto the
hierarchy in `errors.py`. `FloodWait` auto-retries **reads only** — writes always raise so
the caller decides, because blind write retries are how people double-post.

**Auth** (`auth.py`) is a state machine over TDLib's authorization states, driven by update
events. (TDLib dropped `WaitEncryptionKey` and `checkDatabaseEncryptionKey`; the database
key rides in `setTdlibParameters`.) `response_for_state()`
is a pure state→request function (easy to test); `run_auth()` pumps updates and, on a TDLib
error, stays in the current state so a mistyped code re-prompts instead of aborting the
login. Secrets come from a `CredentialProvider` protocol; `credentials.py` resolves
explicit → env → config file → OS store → prompt, and passes secrets on stdin, never argv.

Two non-obvious TDLib facts govern this code, and violating either produces a hang or a
`400 Initialization parameters are needed` on every call:

1. `td_create_client_id` only reserves an id. The instance stays dormant and emits **no
   updates at all** until it receives its first request, so `run_auth()` opens with a
   `getAuthorizationState` probe to wake it and seed the machine. Never write an auth
   path that waits on `next_update()` before sending something.
2. TDLib parameters live on the **client instance, not in the database**. A saved session
   does not carry them, so every new process must replay `setTdlibParameters` (plus the
   encryption key) before any call works. `make_client()` therefore runs `ensure_login()`
   by default. `login=False` skips it, which is why `auth status` can report the state
   without prompting: it uses `auth.current_state()` with a
   `NonInteractiveCredentialProvider`, clearing only the steps stored secrets can clear
   (`UNATTENDED_STATES`) and reporting what a human would still have to supply.

Test doubles must honor fact 1 — a fake whose `next_update()` hands out updates unprompted
models a TDLib that does not exist and will hide bootstrap bugs. `DormantClient` in
`tests/unit/test_auth.py` is the correct shape.

**Normalization** (`normalize.py`) flattens TDLib objects into records; every `*_record()`
takes `include_raw` as the pressure valve when the flat shape loses something. Entities and
links are preserved rather than flattened into plain text. Outbound markdown/HTML goes
through `entities.parse_entities()`, which uses synchronous `td_execute(parseTextEntities)`.

**Pagination** (`paging.py`) is generator-based end to end so `--all` stays constant-memory.
`paginate()` enforces per-walk dedup, stop-on-no-fresh-items, `stop_after_page` (an
out-of-window item finishes its page, then ends the walk), and a cursor-didn't-advance guard.

## CLI conventions

- Data goes to stdout as JSONL; diagnostics, previews, and warnings go to stderr (`cli/output.py`).
- Mutating commands preview and exit 2; `--yes` performs. Destructive ones additionally
  require typing the method name on an interactive TTY (`cli/context.confirm_destructive`):
  `--yes` *and* the typed name, never either alone. With no terminal they exit 2, so a
  script or an agent cannot complete one. Tests fake the terminal by patching
  `cli_context.interactive` and passing `input=` to the runner.
- Command bodies are thin: parse options, call an `api/` function, `emit`/`emit_many`.
  Wrap each with `@handle_errors`, which turns `TelegramError` into an error envelope and
  a non-zero exit. Open the client with `with session(ctx) as client:`, and run anything
  mutating as `perform(ctx, lambda w, d: api.fn(client, ..., allow_write=w, allow_destructive=d))`
  so the preview, `--yes` and the typed confirmation apply. Do not build TDLib requests in
  a command body: that is where most of the wrong parameter names lived.
- Global options live on the Typer callback and are stashed in a module-level `Ctx`.
- Commands taking a positional chat or user reference need
  `context_settings={"ignore_unknown_options": True}`, or a negative id like
  `-1001246902558` is parsed as a cluster of short options and the command exits 2.
- `--session-dir` names the profile directory itself. Do not infer anything from the
  path's spelling.

## Testing

`tests/conftest.py` gives a `client` fixture backed by `FakeTransport` — no account, no
network, no TDLib. Script responses with `add_response`/`add_simple_response`, inject
updates with `add_update`, and assert on `transport.sent`.

`FakeTransport` checks every request against `schema.json` (see *Request shapes*). Pass
`validate=False` only when testing transport mechanics with requests that are not TDLib's.
Script responses shaped like TDLib's real objects, too: a chat without its `type`, say, is
not something TDLib ever sends, and code that works on it may not work on the real thing.

Two `FakeTransport` behaviours make tests lie if you forget them:

- An unmatched request gets a generic `{"@type": "ok"}` echoing `@extra`, so a test that
  forgets to script a response still passes unless it asserts on `sent`.
- Rules are first-match-wins, so scripting a second response for a method already scripted
  is silently ignored. Clear `_rules` when you need to override one.

`tests/unit/test_cli.py` drives the Typer app through `CliRunner` against a scripted
transport. Its fixture sets `TELEGRAM_*` env vars, without which the credential chain
falls through to an interactive prompt and the suite blocks on stdin. It also asserts,
across every mutating command, that nothing reaches TDLib without `--yes`.

Per CONTRIBUTING.md: mutating behavior needs a test proving the gate (preview without `--yes`).

## Session data

`~/.tdelegram/profiles/<profile>/` (overridable via `TDELEGRAM_HOME` or `--session-dir`)
is full account access. Never commit, copy, or paste it. `SessionLock` takes an exclusive
flock per profile dir because TDLib allows one client per database directory.
