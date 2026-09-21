# Changelog

## 0.1.0 — unreleased

Initial release-quality cut: library + CLI over the TDLib modern C API.

### Core
- 1022-function registry with read/write/destructive verdicts and a recorded
  reason per method. The write gate lives at `TelegramClient.call()` and fails
  closed on unknown methods, including through the raw `call` escape hatch.
- 11-state auth machine with an injectable `CredentialProvider`.
- Single global reader thread, `@extra` response routing, bounded per-client
  update queues, abandoned-`@extra` set for timed-out requests.
- Structured errors including `FloodWait` (auto-retries reads only; writes
  always raise so the caller decides).
- Normalization with `include_raw`, entity and link preservation, outbound
  formatting via `td_execute`.
- Pagination discipline (dedup, stop-after-page, cursor guards) with generator
  streaming end to end.

### Fixed before first release
- `auth login` hung until its timeout. `run_auth()` waited for updates without
  sending anything, but `td_create_client_id` leaves the TDLib instance dormant
  until it receives a request, so no update could ever arrive. It now opens with
  a `getAuthorizationState` probe.
- No command set TDLib parameters, so every data command failed with
  `400 Initialization parameters are needed`. TDLib parameters live on the
  client instance rather than in the session database, so `make_client()` now
  replays the handshake per process.
- `run_auth()` raised the builtin `TimeoutError`, which
  `tdelegram.errors.TimeoutError` did not catch.

### Security

- Audited the registry's `read` verdicts. Only 3.1% of the 1022 classifications
  had ever been reviewed by a human; the rest came from prefix heuristics, and a
  wrong `read` means the gate silently does not apply. 28 methods moved from
  `read` to `write`: bot interactions the bot acts on (`getCallbackQueryAnswer`,
  `getInlineQueryResults`, `openWebApp`), identity handoffs to third parties
  (`getLoginUrl`, `getExternalLink`, `getPassportAuthorizationForm`, the
  `*WebApp*Url` family), money operations (`getPaymentForm`, the `*WithdrawalUrl`
  family), auth flows that complete rather than validate (`checkPhoneNumberCode`
  and friends), and `cancel*` aborts. No verdict was loosened.
- Fixed a classifier defect: the `can` read-prefix matched every `cancel*`
  method, so `cancelPasswordReset` and `cancelRecoveryEmailAddressVerification`
  were classified as capability probes. Read prefixes now require a word
  boundary.
- **The CLI gate no longer depends on per-command annotations.** `run_call()`
  computed `allow_write = ctx.yes or not (is_write or is_destructive)`, so any
  command whose call site omitted the hint was granted write permission
  regardless of the registry. `tdelegram bot inline` reached a third-party bot
  unconfirmed because of it. The hints are gone; only `--yes` grants permission
  and the registry alone decides.
- Contract tests pin every reviewed verdict and assert that a list of
  side-effecting methods is never classified `read`.

### Changed
- `errors.PermissionError` and `errors.TimeoutError` are now
  `TelegramPermissionError` and `TelegramTimeoutError`; the old names shadowed
  builtins for anyone importing them.
- Domain modules hold their own implementations. `stories`, `secret`, `proxies`,
  `polls`, `drafts`, `folders`, `contacts` and `search` were re-export shims over
  unrelated modules (stories lived in `bots`, drafts in `reactions`).
- The registry generator accepts `td_api.tl` as well as `td_api.h`, and finds
  the schema via `--schema`, `$TDELEGRAM_TD_API`, or unpinned install paths.
  CI fetches the `.tl` at a pinned TDLib commit instead of building TDLib.
- Coverage measures the CLI. Previously `cli/main.py` and `cli/commands/*` were
  excluded, which is where both launch bugs lived; only the ctypes binding is
  exempt now.
- `auth status` answers "am I logged in?" rather than reporting the raw
  pre-handshake state. TDLib parameters are per-process, so a saved session
  always starts at `authorizationStateWaitTdlibParameters`; status now clears
  the steps stored secrets can clear via `NonInteractiveCredentialProvider`,
  then reports `authorized` plus what a human would still need to supply. It
  never prompts.
- `concurrent.futures.TimeoutError` is caught under its own name. It only
  became an alias of the builtin in 3.11, so on 3.10 a request timeout escaped
  as a bare futures error instead of `TelegramTimeoutError`.
- Removed the unreachable `cli/commands/` package.
- Ship `py.typed`.
- Added `CODE_OF_CONDUCT.md`, issue and pull request templates.
