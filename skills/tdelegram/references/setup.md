# Setting up TDelegram

For a machine where `tdelegram version` fails.

TDelegram needs TDLib, a C++ library with no distribution package. On Linux that
means a ~20 minute compile, which is why **Docker is the recommended route** —
the image has TDLib already built in. Install natively if Docker is unavailable,
or on macOS, where `brew install tdlib` makes the native path a one-liner.

Either way, two steps are not an agent's to do:

| | What | Who |
|---|---|---|
| 1 | TDLib + the `tdelegram` package | you or the user |
| 2 | an `api_id` / `api_hash` from my.telegram.org | **the user only** |
| 3 | a logged-in session (phone, login code, maybe 2FA) | **the user only** |

The credentials sit behind a web form requiring a Telegram login, and the login
code arrives on the user's phone and expires in minutes. If you are an agent,
do step 1, then hand over with the exact commands. Do not loop retrying a login
— the handshake blocks on input you do not have.

## Contents

- [Docker (recommended)](#docker-recommended)
- [Native install](#native-install)
- [Get API credentials](#get-api-credentials-human-only)
- [Log in](#log-in-human-only)
- [Where Telegram is blocked](#where-telegram-is-blocked)
- [Verify](#verify)
- [Where secrets live](#where-secrets-live)

## Docker (recommended)

```bash
docker pull ghcr.io/bulanovdm/tdelegram:latest
```

Published for `linux/amd64` and `linux/arm64`, so Apple Silicon and x86 both
get a native image rather than an emulated one. Building it yourself gives the
same result and takes about 20 minutes:

```bash
git clone https://github.com/bulanovdm/TDelegram && cd TDelegram
docker build -t ghcr.io/bulanovdm/tdelegram:latest .
```

The container keeps its session in `/session`, so mount a host directory there
or every run starts logged out:

```bash
docker run --rm -i -v "$HOME/.tdelegram:/session" \
  ghcr.io/bulanovdm/tdelegram auth status
```

### Make the documented commands work verbatim

Every command elsewhere in this skill is written as `tdelegram ...`. One alias
makes those work unchanged, which is worth more than it looks:

```bash
alias tdelegram='docker run --rm -i \
  -v "$HOME/.tdelegram:/session" \
  -v "$PWD:/work" -w /work \
  -e TELEGRAM_API_ID -e TELEGRAM_API_HASH \
  ghcr.io/bulanovdm/tdelegram'
```

Mounting `$PWD` matters for anything touching files: `media upload --path
./cv.pdf` resolves inside the container, so a host path the container cannot
see fails with a confusing "file not found".

Two container-specific wrinkles:

- **`auth login` needs `-it`**, not `-i`. It prompts. The alias above uses `-i`
  because that is right for every other command; run the login by hand once.
  Destructive commands prompt too — for the method name, after `--yes` — so a
  human runs those with `-it` as well. Under `-i` alone they refuse, by design.
- **stdout stays clean** with `-i`; `-t` allocates a TTY and can interleave
  stderr into it, which breaks JSON parsing. Keep `-t` for the login only.

```bash
docker run --rm -it -v "$HOME/.tdelegram:/session" \
  -e TELEGRAM_API_ID -e TELEGRAM_API_HASH \
  ghcr.io/bulanovdm/tdelegram auth login
```

The image is ~200MB and pins TDLib to the commit the method registry was
generated from, so the gate's verdicts match the TDLib actually running.

Only `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` need passing. The database key is
blank for an unencrypted database and resolves to blank automatically when there
is no terminal to ask at; pass `-e TELEGRAM_DB_KEY` only if the session is
encrypted.

## Native install

Preferable on macOS, and the fallback anywhere Docker is not available.

**1. Install libtdjson.**

```bash
# macOS — the easy case
brew install tdlib

# Debian / Ubuntu — no package exists, so build it (~20 minutes)
sudo apt-get install -y build-essential cmake g++ git zlib1g-dev libssl-dev gperf php-cli
git clone --depth 1 https://github.com/tdlib/td.git /tmp/td
cmake -S /tmp/td -B /tmp/td/build -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/td/build --target install -j"$(nproc)"
sudo ldconfig
```

TDelegram finds the library via `TDELEGRAM_TDJSON`, then `find_library`, then
the usual prefixes. Point at it directly if it lives somewhere unusual:

```bash
export TDELEGRAM_TDJSON=/path/to/libtdjson.so   # .dylib on macOS, .dll on Windows
```

`tdelegram --verbose <any command>` prints which library it loaded.

**2. Install the package.** Not on PyPI yet, so from the repository:

```bash
python3 -m venv ~/.venvs/tdelegram
~/.venvs/tdelegram/bin/pip install "git+https://github.com/bulanovdm/TDelegram"
~/.venvs/tdelegram/bin/tdelegram version     # 0.1.0
```

`version` answers before libtdjson is present — it does not load the library —
so it cleanly separates "package installed" from "TDLib installed".

## Get API credentials (human only)

Telegram issues these per person, not per application:

1. Open <https://my.telegram.org> and sign in with the phone number on the account.
2. Choose **API development tools**.
3. Create an application; the name and description do not matter.
4. Copy the **api_id** (a number) and **api_hash** (32 hex characters).

They are as sensitive as a password. Keep them in the environment or an OS
keychain — never a repository, a shell history, or a message.

```bash
export TELEGRAM_API_ID=1234567
export TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
```

## Log in (human only)

```bash
tdelegram auth login
```

It asks for the phone number, then a login code Telegram sends to the phone or
to an existing Telegram session, then the 2FA password if the account has one.
The session is stored under `~/.tdelegram/profiles/default/` and reused after.

An agent cannot complete this. Ask the person to run it and say when it is done.

## Where Telegram is blocked

A login cannot reach Telegram through a blocked network, so store a proxy first.
Every `proxy` command works before login; it needs only the API credentials.

```bash
tdelegram --yes proxy add 'https://t.me/proxy?server=...&port=443&secret=...'  # MTProto
tdelegram --yes proxy add 'socks5://127.0.0.1:9050'                            # e.g. Tor
tdelegram proxy ping 1        # seconds to Telegram through proxy 1
tdelegram auth login          # now goes through the proxy
```

`proxy add` takes the forms proxies are shared in — `tg://proxy`, `t.me/proxy`,
`tg://socks`, `t.me/socks`, `socks5://` and `http://` URLs — and switches to the
proxy at once (`--no-enable` to only store it). It is kept in the profile, so
every later command uses it. `proxy list` shows what is stored, without secrets;
`proxy check <id>` fails fast on a dead proxy; `proxy enable <id>` switches;
`proxy disable` connects directly again.

## Verify

```bash
tdelegram auth status
# {"@type":"authorizationStateReady","authorized":true,"needs":null,"profile":"default"}
```

`authorized: true` means everything is in place. Otherwise `needs` names the
missing piece and points at the step that supplies it:

| `needs` | Go back to |
|---|---|
| `"api_id and api_hash"` | [credentials](#get-api-credentials-human-only) |
| `"a phone number"` | [log in](#log-in-human-only) |
| `"the login code"` | [log in](#log-in-human-only) — started but not finished |
| `"the 2FA password"` | [log in](#log-in-human-only) |

This command never prompts and never writes, so it is always safe to run.
Then confirm a real read works:

```bash
tdelegram chat list --limit 3
```

## Where secrets live

Resolution order is explicit argument → environment → OS keychain → a `0600`
file under the profile → an interactive prompt. Whatever is typed at a prompt is
saved to the keychain, so the prompt happens once.

Login codes are the exception: single-use, never stored. A saved code would be
replayed on the next login and fail as expired.

Under Docker there is no host keychain inside the container, so pass
`TELEGRAM_API_ID` / `TELEGRAM_API_HASH` through with `-e`, or let them fall back
to the `0600` file in the mounted `/session`.

The session directory is **full account access** — equivalent to being logged
in. Never copy it between machines, commit it, or paste its contents anywhere.
See SECURITY.md in the repository.
