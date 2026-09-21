# TDelegram — a full-featured Telegram client (library + CLI)

[![ci](https://github.com/bulanovdm/TDelegram/actions/workflows/ci.yml/badge.svg)](https://github.com/bulanovdm/TDelegram/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/bulanovdm/TDelegram/blob/main/pyproject.toml)
[![license](https://img.shields.io/badge/license-Apache--2.0-green)](https://github.com/bulanovdm/TDelegram/blob/main/LICENSE)

TDLib exposes **1022 functions through a single JSON interface**. TDelegram covers
all of them on day one through one generic transport, with ergonomics,
normalization, safety, errors and docs on top. No MCP layer: an importable Python
library plus a `tdelegram` CLI.

## Install

TDLib is a C++ dependency with no distribution package, so installing it
natively means a ~20 minute compile on Linux. Docker is the short way in — the
image has TDLib already built.

```bash
docker pull ghcr.io/bulanovdm/tdelegram:latest

# The session lives in /session; mount it or every run starts logged out.
docker run --rm -i -v "$HOME/.tdelegram:/session" \
  ghcr.io/bulanovdm/tdelegram auth status
```

One alias makes every command in this README work verbatim:

```bash
alias tdelegram='docker run --rm -i -v "$HOME/.tdelegram:/session" \
  -v "$PWD:/work" -w /work -e TELEGRAM_API_ID -e TELEGRAM_API_HASH \
  ghcr.io/bulanovdm/tdelegram'
```

`auth login` is the exception — it prompts, so run that one with `-it`.

### Native

Preferable on macOS, and the fallback wherever Docker is not available:

```bash
brew install tdlib                                       # macOS
pip install "git+https://github.com/bulanovdm/TDelegram"  # not on PyPI yet
```

On Linux, build TDLib from source and point `TDELEGRAM_TDJSON` at the resulting
`libtdjson.so`. Full instructions, including getting an `api_id`/`api_hash` and
the first login, are in
[skills/tdelegram/references/setup.md](skills/tdelegram/references/setup.md).

## Quickstart

```bash
tdelegram auth login
tdelegram chat list
tdelegram chat history --chat @durov --limit 5
tdelegram msg send --chat me --text "hi"        # previews
tdelegram msg send --chat me --text "hi" --yes  # performs
tdelegram updates follow &
tdelegram msg send --chat me --text "*hi*" --parse-mode markdown --yes  # MarkdownV2
```

Library:

```python
from tdelegram.client import TelegramClient
from tdelegram.transport import TdJsonTransport
from tdelegram.config import discover_library
from tdelegram.api import chats, messages

transport = TdJsonTransport(discover_library())
with TelegramClient(transport) as client:
    for chat in chats.iter_list(client, scope="main", maximum=10):
        print(chat["title"])
```

## Using it from an agent

`skills/tdelegram/` is an agent skill covering the CLI, the gate and the
discipline it implies, reading recipes, the Python API, troubleshooting and
installation from scratch. Point a coding agent at `skills/tdelegram/SKILL.md`,
or install the packaged bundle.

## Safety

Mutating calls preview and exit; `--yes` performs them. Destructive calls
(`deleteChatHistory`, `banChatMember`, `logOut`, `deleteAccount`,
`terminateAllOtherSessions`, …) need `--yes`, plus a typed confirmation on an
interactive TTY. The gate lives in `TelegramClient.call()` — including the raw
`call` escape hatch. See `src/tdelegram/methods.json` for all 1022 verdicts.

## Layout

- `src/tdelegram/tdjson.py` — ctypes, modern C API only
- `transport.py` / `loop.py` / `client.py` — seam, reader thread, facade
- `auth.py` — 11-state machine with `CredentialProvider`
- `safety.py` + `methods.json` — write gate + registry
- `normalize.py` / `entities.py` / `dates.py` / `paging.py` / `files.py`
- `api/` — account chats messages media contacts users admin topics folders drafts reactions polls search updates bots stories secret proxies
- `cli/` — Typer tree, JSONL on stdout, diagnostics on stderr

## Session

Own home at `~/.tdelegram/` (`--session-dir` overrides). Never commit or copy it:
it is full account access. See SECURITY.md.
