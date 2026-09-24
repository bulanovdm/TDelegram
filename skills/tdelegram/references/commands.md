# Command reference

Every command, its options, and what the gate requires. Verdicts come from
`methods.json`; `tdelegram call --request` uses the identical gate.

- **read** — runs immediately
- **write** — previews and exits 2 unless `--yes`
- **destructive** — needs `--yes` *and* a typed confirmation on an interactive
  terminal, so you cannot complete one non-interactively

## Contents

- [Global flags](#global-flags)
- [auth, account](#auth-account)
- [chat](#chat)
- [msg](#msg)
- [media, contact, user](#media-contact-user)
- [admin, topic, folder, draft](#admin-topic-folder-draft)
- [bot, story, secret, proxy](#bot-story-secret-proxy)
- [updates, call, version](#updates-call-version)

## Global flags

Placed before the subcommand: `tdelegram --format json chat list`.

| Flag | Effect |
|---|---|
| `--profile NAME` | profile under `~/.tdelegram/` (default `default`) |
| `--session-dir PATH` | use this directory as the profile directory |
| `--format jsonl\|json\|table` | `jsonl` default; `table` is for humans, never parse it |
| `--output FILE` | append to a file instead of stdout |
| `--yes` | perform mutating operations rather than previewing |
| `--verbose` | extra diagnostics on stderr (library path, profile dir) |
| `--no-retry` | disable automatic `FloodWait` retry on reads |

## auth, account

| Command | Gate | Notes |
|---|---|---|
| `auth status` | read | Never prompts. Returns `authorized` and `needs`. Run this first. |
| `auth login` | interactive | Drives the handshake. Needs a code from the user's phone; you cannot complete it unattended. |
| `auth logout` | destructive | Ends the session everywhere; a full re-login is required afterwards. |
| `account info` | read | `getMe`. The user's own id, useful for `--sender` and Saved Messages. |
| `account sessions` | read | Active login sessions. |

## chat

| Command | Gate | Options |
|---|---|---|
| `chat list` | read | `--scope main\|archive\|all`, `--limit` |
| `chat info <chat>` | read | normalized record (title, type, `is_forum`, counts) |
| `chat resolve <chat>` | read | the raw TDLib chat object |
| `chat history` | read | `--chat`, `--limit`, `--since`, `--until`, `--sender`, `--topic`, `--contains` |
| `chat search` | read | `--chat`, `--query`, `--limit`, `--sender` — records shaped like `chat history` |
| `chat members <chat>` | read | `--limit` — one bounded page; supergroups, channels and basic groups |
| `chat create <title>` | write | creates a supergroup |
| `chat join <chat>` | write | |
| `chat leave <chat>` | **destructive** | a private chat cannot be rejoined without a new invite |

Chat references accept `@handle`, a bare handle, a numeric id (negative for
groups and channels), or `me` / `self` / `saved` for Saved Messages.

`--since` / `--until` accept `7d`, `24h`, `2w`, or ISO-8601 (`2026-09-01`,
`2026-09-01T12:00:00Z`).

## msg

| Command | Gate | Options |
|---|---|---|
| `msg get` | read | `--chat`, `--id` |
| `msg link` | read | `--chat`, `--id` — a t.me permalink |
| `msg search` | read | `--query`, `--limit`, `--since`, `--until` — global, across chats |
| `msg send` | write | `--chat`, `--text`, `--parse-mode markdown\|html`, `--reply-to`, `--topic`, `--silent`, `--schedule 2h\|ISO-8601` |
| `msg edit` | write | `--chat`, `--id`, `--text`, `--parse-mode` |
| `msg forward` | write | `--from`, `--to`, `--id` (repeatable) |
| `msg react` | write | `--chat`, `--id`, `--emoji` |
| `msg poll` | write | `--chat`, `--id`, `--option` (repeatable) — votes in a poll |
| `msg delete` | **destructive** | `--chat`, `--id` (repeatable), `--only-for-me` — deletes for everyone by default |

`--parse-mode markdown` is **MarkdownV2**: bold is `*bold*`, italic `_italic_`,
underline `__underline__`, strikethrough `~struck~`, code `` `code` ``.
`**bold**` is *not* bold in V2 — it produces plain text with the asterisks
stripped and no formatting, silently. Use `html` if you want `<b>`/`<i>`.

## media, contact, user

| Command | Gate | Notes |
|---|---|---|
| `media download <file_id>` | read | writes to local disk only, which is why it is a read |
| `media upload` | write | `--chat`, `--path`, `--caption` — sends a file as a document |
| `contact list` | read | |
| `user info <user_id>` | read | numeric id, not a username |

## admin, topic, folder, draft

| Command | Gate | Notes |
|---|---|---|
| `topic list <chat>` | read | forum topics; get `topic_id` here before reading a topic |
| `folder list` | read | one record per folder, from the list TDLib pushes after login |
| `draft set` | write | `--chat`, `--text` |
| `admin promote` | **destructive** | `--chat`, `--user`, `--right` (repeatable; default `manage_chat`), `--title` |
| `admin ban` | **destructive** | `--chat`, `--user` |

`admin promote` is destructive because it goes through `setChatMemberStatus`,
the same method that can ban. The gate is per method, not per payload, so the
method takes the verdict of the worst thing it can express.

## bot, story, secret, proxy

| Command | Gate | Notes |
|---|---|---|
| `story list <chat>` | read | archived stories |
| `proxy list` | read | stored proxies, without secrets or passwords |
| `proxy add <link>` | write | `tg://proxy`, `t.me/proxy`, `tg://socks`, `socks5://`, `http://`; `--comment`, `--no-enable` |
| `proxy enable <id>` / `proxy disable` | write | switch proxy, or connect directly |
| `proxy remove <id>` | **destructive** | |
| `proxy ping [<id>]` | read | seconds to Telegram through the proxy, or directly |
| `proxy check <id>` | read | fails if the proxy cannot reach Telegram |
| `bot callback <query_id>` | write | answers a callback query |
| `bot inline` | write | `--bot`, `--query` — the bot is notified of the query |
| `secret create <user>` | write | new secret chat |

Every `proxy` command works before login — a blocked network needs the proxy
before a login can get through. They need only `TELEGRAM_API_ID` and
`TELEGRAM_API_HASH`.

`bot inline` looks like a read and is not: sending an inline query notifies a
third-party bot, which can act on it.

## updates, call, version

| Command | Gate | Notes |
|---|---|---|
| `updates follow` | read | `--types` comma-separated `@type` filter; streams until interrupted |
| `call --request '<json>'` | per method | raw TDLib JSON through the same gate, checked against the schema first |
| `describe <name>` | — | a function's parameters and verdict, an object's fields, or a type's objects |
| `version` | — | prints the package version |

`updates follow` runs until killed. Give it a bounded window or a filter rather
than leaving it streaming:

```bash
tdelegram updates follow --types updateNewMessage 2>/dev/null | head -20
```

### The raw escape hatch

```bash
tdelegram call --request '{"@type":"getChatMember","chat_id":-100123,"member_id":{"@type":"messageSenderUser","user_id":42}}'
```

Reaches any of the 1022 TDLib methods. It exists because no CLI surface covers
all of them — not because it bypasses anything. Without `--yes` it previews and
exits 2 exactly like a named command, and an unrecognized `@type` fails closed.

Every request is checked against the TDLib schema the build was made from, and
refused with exit 2 if a field is unknown or has the wrong JSON type. That is
not pedantry: TDLib *ignores* a field it does not recognise and runs the call
without it, so `"revoke_all": true` on `deleteMessages` would delete for you
alone, silently. Look parameters up first:

```bash
tdelegram describe deleteMessages
# {"kind":"function","name":"deleteMessages","params":{"chat_id":"int53","message_ids":"vector<int53>","revoke":"Bool"},"returns":"Ok","verdict":"destructive",...}
tdelegram describe ReactionType     # the objects an abstract type accepts
```

`--no-validate` skips the check, for a TDLib newer than the pinned schema.
