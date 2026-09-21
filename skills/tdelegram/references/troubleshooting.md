# Troubleshooting

Read the error envelope first — it names the TDLib code, the message, and the
method that failed:

```json
{"ok":false,"error":{"code":400,"message":"Chat not found","method":"getChat"}}
```

Exit 1 means a real failure. Exit 2 means nothing happened: either a preview
without `--yes`, or a malformed request.

## Contents

- [The session is not usable](#the-session-is-not-usable)
- [Nothing happens, or it hangs](#nothing-happens-or-it-hangs)
- [Rate limits](#rate-limits)
- [Chat and message errors](#chat-and-message-errors)
- [Formatting comes out wrong](#formatting-comes-out-wrong)
- [Library and environment](#library-and-environment)

## The session is not usable

Always start here:

```bash
tdelegram auth status
```

| `needs` | What it means |
|---|---|
| `null`, `authorized: true` | good, everything below will work |
| `"api_id and api_hash"` | credentials are not in the environment or keychain |
| `"a phone number"` | no session yet; a fresh login is required |
| `"the login code"` | login started but never finished |
| `"the 2FA password"` | the account has 2-step verification |

**You cannot fix any of these yourself.** A login code arrives on the user's
phone and expires in minutes. Ask them to run `tdelegram auth login` in their own
terminal, then re-check `auth status`. Do not retry in a loop — the handshake
sits waiting on stdin you do not control.

Credentials resolve in order: explicit → environment → OS keychain → a `0600`
file → prompt. So exporting `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` for a single
command is enough; you never need to write them anywhere.

### `Another tdelegram process holds ... .lock`

TDLib allows one client per database directory, so the profile takes an
exclusive lock. Something else is using the session — another `tdelegram`, a
`updates follow` you left streaming, or a crashed process. Find and stop it
rather than deleting the lock file; use `--profile other` if you genuinely need
a second session.

### `Initialization parameters are needed: call setTdlibParameters first`

TDLib parameters live on the client instance, not in the saved session, so every
process replays the handshake. The CLI does this automatically. If you see this,
you are using the library directly and skipped `run_auth` — see
`references/library.md`.

## Nothing happens, or it hangs

- **Exit 2 with a preview on stderr** — working as designed. The command is
  mutating and `--yes` was absent. Show the preview to the user and ask.
- **Exit 2 with no output at all** — the argument parser refused. Usually a
  missing required option, or a value the parser read as a flag.
- **`updates follow` never returns** — it streams until interrupted. Bound it:
  `tdelegram updates follow --types updateNewMessage 2>/dev/null | head -20`.
- **A destructive command sits there** — it wants the method name typed on a
  TTY. Non-interactively it cannot complete, deliberately. Hand the exact
  command to the user.

## Rate limits

`FloodWait` means Telegram is throttling the account.

- **Reads retry automatically** with bounded backoff. `--no-retry` disables it.
- **Writes always raise.** This is deliberate: re-sending a write after a flood
  wait is how people double-post, so the decision is handed back. Do not wrap a
  write in a retry loop — tell the user how long the wait is
  (`retry_after` seconds) and let them decide.

Reading a very large history in one pass is the usual cause. Narrow with
`--since` / `--until` and page in windows.

## Chat and message errors

| Message | Cause |
|---|---|
| `Can't parse as an integer string "..."` | a chat reference reached TDLib unresolved — report it, the CLI should resolve `@handle` everywhere |
| `Chat not found` | not a member, the username is wrong, or the chat is private |
| `Message not found` | wrong `--chat` for that `--id`; message ids are per chat |
| `Have no rights to send a message` | the account lacks permission in that chat |
| `USER_IS_BLOCKED` | the recipient blocked the account |
| `Can't get story archive in the chat` | that chat has no story archive you can read |

Message ids are unique **per chat**, so an id from one chat means nothing in
another. Always carry `chat_id` alongside `message_id`.

Forum groups split into topics. Reading a forum without `--topic` mixes every
topic together; list them first with `tdelegram topic list <chat>`.

## Formatting comes out wrong

`--parse-mode markdown` is **MarkdownV2**:

| Want | Write | Not |
|---|---|---|
| bold | `*bold*` | `**bold**` — yields plain text, silently |
| italic | `_italic_` | |
| underline | `__underline__` | |
| strikethrough | `~struck~` | |
| code | `` `code` `` | |

If formatting vanished with no error, this is why. `--parse-mode html` accepts
`<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<a href>` and is easier to get
right when generating markup programmatically.

Reserved characters must be escaped in MarkdownV2. When the text is user-
supplied and you only want it sent verbatim, send it with no `--parse-mode` at
all.

## Library and environment

### `Could not find libtdjson`

TDLib is a separate native dependency. Point `TDELEGRAM_TDJSON` at the shared
library, or install it (`brew install tdlib` on macOS; build from source on
Linux). `--verbose` prints which library was loaded.

### `Receive must not be called simultaneously from two different threads`

Two reader threads on the global `td_receive`. Current versions key dispatch
loops on the transport's receive domain so this should not happen; if it does,
something is constructing loops directly. See `references/library.md`.

### Unknown method / `no registry verdict`

The method is not in `methods.json`, so the gate refuses rather than running it
ungated. Either the name is a typo, or the installed TDLib is newer than the
registry and it needs regenerating — a maintainer task, documented in
`CONTRIBUTING.md`.

### The wheel is missing `methods.json`

Then every call fails closed. Verify with:

```bash
python -c "from tdelegram import safety; print(safety.verdict('getMe'))"
```

`read` means the registry loaded correctly.
