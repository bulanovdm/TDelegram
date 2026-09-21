# Changelog

## 0.1.0 — 2026-09-21

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
- Audited the `write` verdicts too. A `write` needs `--yes`; a `destructive`
  also needs a typed confirmation on a TTY, so an irreversible method sitting
  in `write` had lost a layer. 29 methods moved from `write` to `destructive`:
  ownership and public-identity surrender (`transferChatOwnership`,
  `setUsername`, `setSupergroupUsername`, `disableAllSupergroupUsernames`),
  access revocation (`disconnectWebsite`, `disconnectAllWebsites`), credentials
  (`setPassword`, `recoverPassword`), settings whose effect is bulk deletion
  (`setAccountTtl`, `setChatMessageAutoDeleteTime`), one-way conversions
  (`toggleSupergroupIsBroadcastGroup`, `toggleSupergroupIsAllHistoryAvailable`),
  bulk content loss (`clearAllDraftMessages`, `unpinAll*`), irreversible money
  and asset movement (`sendPaymentForm`, `sendGift`, `transferGift`,
  `placeGiftAuctionBid`), and `leaveChat`. Nothing was demoted.
- Closed a gate bypass: `setChatMemberStatus` can ban, which is exactly what
  `banChatMember` does, but it was only a `write`. `--yes` alone would ban
  someone while the dedicated method still demanded a typed confirmation. The
  gate is per method, not per payload, so the general setter now takes the
  verdict of the worst thing it can express. The cost is a typed confirmation
  for an ordinary promotion, which is the right side to err on.
- Contract tests pin every reviewed verdict, assert that side-effecting methods
  are never `read`, that irreversible ones are never merely `write`, and that a
  general setter never undercuts the specific method it can stand in for.

### Fixed (found by the new tests)

- One-time login codes were written to the OS keystore and then read back on
  every later login, so the second login submitted an expired code, never
  prompted, and the handshake retried it until timing out. `resolve_secret`
  now takes `ephemeral`, and login/email codes are never persisted or read
  from storage. They still honour their env var for automation.
- `render_entities` emitted `**bold**` while `parse_entities` asks TDLib for
  MarkdownV2, where bold is `*bold*` and `**bold**` yields plain text with no
  entity at all. Rendered markup could not be read back, and the README
  documented the syntax that silently drops formatting. Both corrected, and
  the renderer now covers underline and strikethrough.

### Fixed

- A second `TelegramClient` aborted the process. `td_receive` is global to the
  loaded library, but dispatch loops were keyed on `id(transport)`, so a second
  `TdJsonTransport` started a second reader thread and libtdjson killed the
  process with "Receive must not be called simultaneously from two different
  threads". Loops are now keyed on the transport's receive domain, which is what
  the module docstring already claimed.
- Negative chat ids could not be passed as positional arguments. Supergroup and
  channel ids look like `-1001246902558`, which the parser read as a cluster of
  short options, so `tdelegram chat info -100...` exited 2 with no output and the
  ordinary identifier for a group was unusable without the `--` escape.
- `--session-dir` no longer sniffs the path. It climbed two directory levels when
  a component was named `profiles` and when the string merely contained
  "profile", so the same flag meant different things for `/opt/tg/profiles/work`
  and `/opt/tg/session`, and where a secret was read from depended on how the
  user had named their directories. The flag now names the profile directory,
  always.

- Thirteen CLI commands passed `--chat` straight into `chat_id`, so a username
  produced `Can't parse as an integer string` and only numeric ids worked.
  `msg link`, `msg edit`, `msg delete`, `msg forward`, `msg react`, `msg poll`,
  `chat join`, `chat leave`, `admin ban`, `admin promote`, `draft set` and
  `story list` now resolve the reference first, like `msg send` always did.

- `me`, `self` and `saved` resolve to Saved Messages. The README's quickstart
  has used `--chat me` since the first commit; it was looked up as a username
  and came back `USERNAME_INVALID`.

- `messageRichMessage` normalized to an empty `text`, so instant-view style
  posts vanished from any sweep that filters on text — two of thirty-eight
  messages in a single real window, with nothing to indicate they were skipped.
  Rich page blocks are now flattened, and contents that carry their own `text`
  (gifts, premium codes, poll option changes) are read instead of falling
  straight through to the caption.

- `iter_topics` deduped on `info.topic_id`, but the field is
  `forum_topic_id`, so the set never filled and every page was yielded twice —
  a caller counting a four-topic forum got eight. The fallback key was
  `id(topic)`, a fresh address per page, so a missing id meant no dedup at all
  rather than a weaker one; it is now stable.
- `msg get` returned the raw TDLib message while `chat history` returned
  normalized records, so the same message had two shapes and `.text` was null
  on one of them. It now returns a record, with `include_raw=True` for the
  original.

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
