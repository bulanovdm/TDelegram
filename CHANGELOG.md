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
- The 2FA password is no longer saved. It is needed once per login and never
  after, yet every login stored it — in the keychain, or wherever there is none,
  such as in a container, in a plain file beside the session it protects. It is
  still read from `TELEGRAM_PASSWORD` or an earlier copy; a saved copy is left
  for the user to delete, not removed behind their back.
- **Destructive calls need `--yes` and the typed method name, not either one.**
  Every document described the typed confirmation as a second layer, but
  `run_call()` treated it as an alternative: `--yes` alone performed any
  destructive call from a script, a pipe or an agent's shell, and at a terminal
  typing the name performed one without `--yes`. Now `--yes` gets a destructive
  call as far as the prompt, the prompt goes to stderr instead of the JSONL
  stream on stdout, and with no terminal to type at the command exits 2.
- `searchPublicPosts` moved from `read` to `destructive`. The `search*` prefix
  made it a read, but its `star_count` parameter pays Telegram Stars for the
  query, and money that leaves is destructive everywhere else in the registry.
- Closed a gate bypass: `media upload` sent the file without `--yes`.
  `files.send_file()` passed `allow_write=True` itself instead of threading the
  caller's permission down, so the one mutating command missing from the
  every-command gate test was the one the gate did not cover.
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

- **Requests are checked against TDLib's schema.** TDLib ignores a field it does
  not recognise and defaults a missing one, so a request built with the wrong
  parameter names runs with its arguments silently unset, and `FakeTransport`
  answered anything. The generator now also emits `schema.json` (every
  function's parameters, every object's fields) from the pinned `td_api.tl`;
  the fake refuses a request that does not match, the test suite fails on one,
  and `tdelegram call` refuses one before sending. It found these, each broken
  against the TDLib the Docker image runs:
  - Every media send: TDLib now wraps the file in `inputPhoto`,
    `inputDocument` and friends, so `media upload`, `send_photo`,
    `send_video`, `send_voice` and `send_sticker` were all refused.
  - `msg edit`, `msg react`, `draft set`, `admin ban` and `admin promote` built
    their own requests with field names TDLib does not have (`text`, `emoji`,
    `user_id`), so each ran with its main argument unset. They now go through
    the `api/` functions, which were right — except `promote`, which granted
    no rights at all and now takes `--right` and `--title`.
  - `msg search` sent `offset: 0` where TDLib wants a string cursor and was
    refused outright; it pages by `next_offset` now and takes `--since` and
    `--until`.
  - `chat members` passed the chat reference where TDLib wants the supergroup
    id; it now reads the id from the chat's type, and lists basic groups too.
  - `folder list` asked for `getChatFolders`, which TDLib does not have, and
    failed closed on every run. The folder list only arrives as an update, so
    the client now remembers the latest `updateChatFolders`.
  - Scheduling sat outside `messageSendOptions`, so a message meant for later
    went out at once. `msg send` takes `--schedule`, `--silent` and `--topic`.
  - Forum topics sent `topic_id` for `forum_topic_id`, so editing, closing or
    deleting a topic addressed topic 0; drafts used a field TDLib removed; the
    proxy calls, `addContact`, `importContacts`, `deleteStory`,
    `sendInlineQueryResultMessage` and `unpinChatMessage` all sent shapes
    TDLib does not take.
- `chat info` and `chat list` reported `username`, `member_count` and
  `is_forum` as null for every chat: TDLib keeps them on the user, supergroup
  or basic group, and the record read them from the chat. Records now come
  with the detail object they belong to.
- Message records carried no file id, so the documented way to download a
  message's file had nothing to pass to `media download`, and a voice note's
  file was not found at all. Records now carry `media` (kind, `file_id`, name,
  MIME type, size, and the transcript of a voice or video note already
  transcribed), `sender_name`, `edit_date`, `views`, `forwards`, `replies`,
  `reactions`, `forwarded_from`, `album_id`, `author_signature` and inline
  `buttons` — what research on a public channel needs, without `--raw`.
- `media download` emitted TDLib's raw file object, which keeps the path at
  `.local.path`, so the documented `jq -r '.path'` printed null. It emits a
  file record with `path` and `completed`.
- The README's and the skill's examples of a performing send put `--yes` after
  the command, where the parser does not look for it, so the one line meant to
  show a send going through exited 2 with "No such option". The examples put
  it first now, and a global flag given too late gets an error saying where it
  goes. The parser stays strict on purpose: reading `--yes` anywhere would let
  a message text of "--yes" grant permission. The quickstart also ran
  `updates follow &` and then a send, which the profile lock refuses.
- `msg delete` deleted only for the account itself: TDLib revokes nothing
  unless told to, and the command never said, so in a private chat the other
  side kept every "deleted" message. It deletes for everyone now, takes
  several `--id`s, and `--only-for-me` keeps the old behaviour.
- `chat search` emitted raw TDLib messages while `chat history` emitted
  records, the same two-shapes bug `msg get` had. It emits records, and takes
  `--sender`.
- Removed the handling for `authorizationStateWaitEncryptionKey`, a state
  TDLib no longer has, and two registry overrides for functions it no longer
  has. A contract test now fails on an override naming a missing function.
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

- An optional secret with no terminal to ask at now resolves to blank instead
  of prompting. The TDLib database key is blank for an unencrypted database, but
  every containerised, cron-driven or piped invocation prompted for it and died
  on EOF, so `docker run ... chat list` failed on each data command.
- `auth status` names the credential that actually failed to resolve. It
  inferred `needs` from the authorization state, so a session missing only the
  database key was reported as missing `api_id and api_hash`, which had resolved
  perfectly well.
- The Docker image ships `libtdjson` once rather than twice: the `libtdjson.so*`
  glob also matched the unversioned development symlink, and COPY dereferences
  symlinks, duplicating 34MB. The image no longer carries TDLib's C++ headers
  either, and pins TDLib to the registry's commit so the gate's verdicts match
  the library actually loaded. 235MB to 201MB.

- A multi-architecture image is published to `ghcr.io/bulanovdm/tdelegram` for
  `linux/amd64` and `linux/arm64`, built on native runners because TDLib under
  QEMU takes hours.

### Changed
- `--until` with a bare date (`--until 2026-09-01`) now means through the end
  of that day rather than its first second, in `chat history`, `msg search`
  and `msg delete-mine`. `parse_date` always had an `end_of_day` switch for
  this; nothing used it.
- `tdelegram describe <name>` shows what the schema says about a function (its
  parameters and the gate's verdict), an object or an abstract type, for
  building `call` requests from the schema instead of from memory.
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

### Added
- Proxy commands that work before login: `proxy add <link>` takes a proxy in
  any form one is shared in (`tg://proxy`, `t.me/proxy`, `tg://socks`,
  `socks5://`, `http://`) and switches to it, then `proxy ping`, `proxy check`,
  `proxy enable`, `proxy disable`, `proxy remove`. Where Telegram is blocked a
  login cannot get through without one, and until now only `proxy list`
  existed — behind a full login. `proxy list` leaves secrets and passwords out.
- `inbox`: unread incoming messages across chats, oldest first within each,
  capped per chat with the newest kept. It only reads history — no `openChat`,
  no `viewMessages` — so triaging an inbox sends no read receipts. Muted chats
  are left out unless they mention the account. `chat list --unread` lists the
  chats themselves.
- `chat export`: a resumable export of one chat to a directory of JSON Lines,
  with a checkpoint, so a re-run fetches only what is missing — new messages,
  then whatever an interrupted run had not reached — in constant memory.
  `--media` saves files alongside, except where the chat or message forbids
  saving, which is recorded instead.
- `msg delete-mine`: delete the account's own messages in a chat, for
  everyone, within an optional window. It counts them first and, without
  `--yes`, stops at the count; with `--yes` it takes one typed confirmation
  for the whole batch, deletes a hundred at a time, and steps around a message
  Telegram will not delete (a 400 or a 403) rather than stalling on it — or,
  when the chat refuses every deletion, stops after one batch and says why. The official apps have
  no way to do this, and the scripts that do have neither a preview nor a
  confirmation.
- `watch`: new messages as records, filtered by chat, any of several words,
  a regular expression or a sender, bounded by `--for` or `--count`. The chats
  it watches are opened for the duration, because TDLib receives every update
  of a supergroup or channel only while it is open; that marks nothing read. `updates
  follow` only ever offered the raw stream filtered by update type.
- `--format text`: one plain line per message, in reading order — time,
  sender, chat, text, and media described in words, transcripts included — for
  a person or a screen reader, which would otherwise read JSON punctuation
  aloud. Chats render as a title and an unread count.
- `msg transcribe`: the words of a voice or video message. A transcript anyone
  already asked for is kept on the message and returned free, without `--yes`;
  otherwise it is a write, because Telegram counts it against the account's
  quota, and the record says how many free ones are left.
- `bot press --chat --id --button LABEL` presses an inline button under a bot's
  message, by the label its record lists in `buttons`, and returns the bot's
  answer; a link button comes back unpressed. It replaces `bot callback`, which
  called `answerCallbackQuery` — a method only a bot account can use, so the
  command could never work from the user account TDelegram drives.
- `user info` and `secret create` take `@username` and `me` as well as an id,
  and `contact list` emits a user record per contact instead of TDLib's bare
  list of ids.
- `FakeTransport` answers `close` with `authorizationStateClosed` as TDLib does,
  so `close()` no longer waits out a second per client and the suite runs in
  a quarter of the time.
