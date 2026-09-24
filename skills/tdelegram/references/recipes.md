# Recipes

Worked tasks. Every command here is read-only, so all of it is safe to run
without asking anyone.

## Contents

- [Triage an inbox](#triage-an-inbox)
- [Narrow before you read](#narrow-before-you-read)
- [Everything one person posted](#everything-one-person-posted)
- [Quote a message verbatim](#quote-a-message-verbatim)
- [History or search?](#history-or-search)
- [Page a large history](#page-a-large-history)
- [Watch for new messages](#watch-for-new-messages)
- [Download a file](#download-a-file)
- [Back up a chat](#back-up-a-chat)
- [Reading aloud, and voice messages](#reading-aloud-and-voice-messages)
- [Shaping output for a report](#shaping-output-for-a-report)

## Triage an inbox

```bash
tdelegram chat list --unread --limit 30 2>/dev/null \
  | jq -r '[.unread_count, .unread_mention_count, .title] | @tsv'
tdelegram inbox --chats 10 --per-chat 15 2>/dev/null \
  | jq -r '[.chat_title, .sender_name, (.text | split("\n")[0])] | @tsv'
```

`inbox` marks nothing read, so senders see no read receipt until the user
actually opens the chat. `--per-chat` keeps the newest messages; each record's
`chat_unread_count` says how many there were in all, so a summary can say what
it skipped.

When a message calls for an answer, write it as a draft rather than sending it.
A draft is visible only to the account itself, in that chat's input box on
every device, so the user reads it and presses send:

```bash
tdelegram draft set --chat someone --text "Yes, 8 works."          # previews
tdelegram --yes draft set --chat someone --text "Yes, 8 works."    # after approval
```

## Narrow before you read

Pulling a whole chat and filtering afterwards is slow and usually wrong. Resolve
the chat, find the topic if it is a forum, then read a tight window.

```bash
# 1. What is this chat?
tdelegram chat info cyprusithr
# {"chat_id":-1001246902558,"title":"CY iT HR","type":"chatTypeSupergroup","is_forum":true,...}

# 2. Forums split into topics. Read the wrong one and you get nothing useful.
tdelegram topic list cyprusithr 2>/dev/null | jq -r '[.info.forum_topic_id, .info.name] | @tsv'

# 3. Read inside the topic and a time window
tdelegram chat history --chat cyprusithr --topic 46685 --since 7d --limit 50
```

`--limit` bounds the walk rather than trimming the result afterwards, so a small
limit is genuinely cheap.

## Everything one person posted

Telegram filters by numeric user id, not by `@handle`. Get the id once, reuse it.

```bash
# If they have ever DMed the account, the private chat id IS the user id.
tdelegram chat resolve someuser 2>/dev/null | jq '{id, type: .type["@type"]}'
# chatTypePrivate -> id is the user_id

# Otherwise find one message of theirs and read sender_id off it.
tdelegram chat history --chat cyprusithr --contains "Full Stack" --since 60d --limit 20 2>/dev/null \
  | jq -r '[.date, .sender_id.user_id, (.text | split("\n")[0])] | @tsv'

# Then sweep.
tdelegram chat history --chat cyprusithr --sender 990359878 --since 90d --limit 200 2>/dev/null \
  | jq -r '[.date, .topic_id, .message_id, (.text | split("\n")[0])] | @tsv'
```

## Quote a message verbatim

Paraphrasing someone's words into a summary is how misquotes start. Pull the
text exactly, and get a permalink so the user can check it.

```bash
tdelegram msg get --chat cyprusithr --id 123456 2>/dev/null | jq -r '.text'
tdelegram msg link --chat cyprusithr --id 123456 2>/dev/null | jq -r '.link'
```

`entities` and `links` survive normalization, so formatting and URLs are intact
in the record even though `text` is plain:

```bash
tdelegram chat history --chat cyprusithr --limit 5 2>/dev/null \
  | jq -r 'select(.links | length > 0) | [.message_id, (.links | join(" "))] | @tsv'
```

## History or search?

They answer different questions:

- `chat history` walks backwards in time. Use it for "what happened recently",
  with `--since`, `--topic`, `--sender`, `--contains`.
- `chat search` asks Telegram's server-side search for a term inside one chat.
  Use it for "find the message about X" across a long history.
- `msg search` searches globally, across every chat.

`--contains` filters client-side on text already fetched, so it narrows a window
you are already reading. It is not a substitute for search over years of history.

```bash
tdelegram chat search --chat cyprusithr --query "kubernetes" --limit 20
tdelegram msg search --query "invoice" --limit 20
```

## Page a large history

Global flags go **before** the subcommand. `tdelegram chat history ... --output
f.jsonl` fails with "No such option"; `tdelegram --output f.jsonl chat history
...` works. Shell redirection is equally fine and harder to get wrong.

Paging streams, so memory stays flat and you can stop early. Prefer a bounded
window over `--limit 100000`.

```bash
# A day at a time keeps each call small and the output reviewable.
tdelegram --output /tmp/window.jsonl \
  chat history --chat cyprusithr --since 2d --until 1d --limit 500 2>/dev/null
wc -l /tmp/window.jsonl
```

Message ids are unique per chat, so dedupe on `message_id` if you stitch windows
together.

## Watch for new messages

`watch` streams new messages as records. Always bound it — `--for`, `--count`
or `head` — or it runs until killed.

```bash
tdelegram watch --chat cyprusithr --contains kubernetes --for 1h 2>/dev/null \
  | jq -r '[.date, .sender_name, (.text | split("\n")[0])] | @tsv'
```

`--contains` matches when any of its words appear; `--match` takes a regular
expression. The raw stream is still there as `updates follow --types ...`.

Filter by `@type` rather than reading everything — an idle account still emits a
steady trickle of `updateUserStatus` and friends.

## Download a file

`file_id` comes from the message record's `media`. `media download` writes to
local disk and makes no remote change, which is why it is a read.

```bash
tdelegram chat history --chat somechat --limit 20 2>/dev/null \
  | jq -r 'select(.media != null) | [.message_id, .media.kind, .media.file_id, .media.file_name] | @tsv'

tdelegram media download 12345 2>/dev/null | jq -r '.path'
```

## Back up a chat

```bash
tdelegram chat export --chat somechannel --out ~/backups/somechannel --media
# {"written":5123,"exported":5123,"complete":true,"media_saved":812,...}

# Later: only what is new is fetched.
tdelegram chat export --chat somechannel --out ~/backups/somechannel --media

# In date order, and as CSV for a spreadsheet:
jq -s 'sort_by(.message_id)[]' ~/backups/somechannel/messages.jsonl \
  | jq -r '[.date, .sender_name, .views, .text] | @csv' > somechannel.csv
```

A long history is rate-limited, and reads back off on their own. Stopping it —
Ctrl-C, or `--limit 2000` per run — costs nothing: the next run resumes where
it stopped. Only a hard kill between checkpoints can repeat records, at most a
hundred; dedupe on `message_id` if one did. Media a
chat protects from saving are skipped with a note in the record, not
downloaded. Under Docker, `--out` must be inside a mounted directory (`/work`
in the alias), or the export stays in the container.

## Reading aloud, and voice messages

`--format text` turns records into plain lines — time, sender, chat, then the
text, with media described in words — which a screen reader takes in order
without reading out JSON punctuation:

```bash
tdelegram --format text inbox
# 2026-09-24 14:02, Ada in Friends: dinner?
# 2026-09-24 14:05, Ada in Friends: [voice message, 14 seconds] transcript: "running late"
```

A voice message someone has already transcribed carries its transcript in the
record. For one that has not been, `msg transcribe` asks Telegram — a write,
since it spends the account's quota:

```bash
tdelegram --yes msg transcribe --chat someone --id 8812
```

## Shaping output for a report

Keep the raw JSONL and derive from it, rather than re-running commands to get a
different shape.

```bash
tdelegram --output /tmp/week.jsonl \
  chat history --chat cyprusithr --since 7d --limit 200 2>/dev/null

# Busiest senders
jq -r '.sender_name // "unknown"' /tmp/week.jsonl | sort | uniq -c | sort -rn | head

# A channel's most-viewed and most-forwarded posts
jq -r 'select(.views != null) | [.views, .forwards, .message_id, (.text | .[0:60])] | @tsv' \
  /tmp/week.jsonl | sort -rn | head

# Where forwarded posts came from
jq -r 'select(.forwarded_from != null) | .forwarded_from.chat_id // .forwarded_from.name' \
  /tmp/week.jsonl | sort | uniq -c | sort -rn

# Per-day counts
jq -r '.date[0:10]' /tmp/week.jsonl | sort | uniq -c

# Only messages with links
jq -r 'select(.links | length > 0) | [.date, .links[0]] | @tsv' /tmp/week.jsonl
```

When the report leaves the terminal, redact what is not the user's to share:
phone numbers, personal addresses, and message content from private chats.

And remember that `date` is when a message was sent — not the date of whatever
the message describes.
