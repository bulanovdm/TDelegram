# Setting up TDelegram

For a machine where `tdelegram version` fails. Three separate things are needed,
and two of the steps can only be done by the person whose account it is.

## Contents

- [What is required](#what-is-required)
- [1. Install libtdjson](#1-install-libtdjson)
- [2. Install the package](#2-install-the-package)
- [3. Get API credentials](#3-get-api-credentials-human-only)
- [4. Log in](#4-log-in-human-only)
- [5. Verify](#5-verify)
- [Docker, which skips steps 1 and 2](#docker-which-skips-steps-1-and-2)
- [Where secrets live](#where-secrets-live)

## What is required

| | What | Who can do it |
|---|---|---|
| 1 | `libtdjson`, TDLib's shared library — a native dependency, not a Python one | you or the user |
| 2 | the `tdelegram` package | you or the user |
| 3 | an `api_id` / `api_hash` from my.telegram.org | **the user only** |
| 4 | a logged-in session (phone number, SMS code, maybe 2FA) | **the user only** |

Steps 3 and 4 need a human: the credentials come from a web form behind a
Telegram login, and the login code arrives on their phone and expires in
minutes. If you are an agent, install what you can, then stop and hand over
with the exact commands. Do not loop retrying a login — the handshake blocks on
input you do not have.

## 1. Install libtdjson

TDLib is C++ and ships separately from the Python package.

```bash
# macOS
brew install tdlib

# Debian / Ubuntu — no distribution package exists, so build it (~20 minutes)
sudo apt-get install -y build-essential cmake g++ git zlib1g-dev libssl-dev gperf php-cli
git clone --depth 1 https://github.com/tdlib/td.git /tmp/td
cmake -S /tmp/td -B /tmp/td/build -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/td/build --target install -j"$(nproc)"
sudo ldconfig
```

If the build is unattractive, use the Docker image below instead — it bakes
libtdjson in.

TDelegram finds the library via `TDELEGRAM_TDJSON`, then `find_library`, then
the usual install prefixes. If it is somewhere unusual, point at it directly:

```bash
export TDELEGRAM_TDJSON=/path/to/libtdjson.so   # .dylib on macOS, .dll on Windows
```

`tdelegram --verbose <any command>` prints which library it loaded.

## 2. Install the package

Not on PyPI yet, so install from the repository:

```bash
pip install "git+https://github.com/bulanovdm/TDelegram"
tdelegram version     # 0.1.0
```

A virtualenv is the usual courtesy:

```bash
python3 -m venv ~/.venvs/tdelegram
~/.venvs/tdelegram/bin/pip install "git+https://github.com/bulanovdm/TDelegram"
~/.venvs/tdelegram/bin/tdelegram version
```

`tdelegram version` works before libtdjson is installed — it does not load the
library. That makes it a clean check that step 2 succeeded on its own.

## 3. Get API credentials (human only)

Telegram issues these per person, not per application:

1. Open <https://my.telegram.org> and sign in with the phone number on the account.
2. Choose **API development tools**.
3. Create an application — the name and description are not important.
4. Copy the **api_id** (a number) and **api_hash** (32 hex characters).

These identify the application to Telegram and are as sensitive as a password.
They belong in the environment or an OS keychain, never in a repository, a
shell history, or a message.

```bash
export TELEGRAM_API_ID=1234567
export TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
```

## 4. Log in (human only)

```bash
tdelegram auth login
```

It asks for the phone number, then a login code Telegram sends to the phone or
to an existing Telegram session, then the 2FA password if the account has one.
The session is then stored under `~/.tdelegram/profiles/default/` and later
commands reuse it.

An agent cannot complete this. The code is short-lived and arrives out of band,
so an agent that tries will sit until the handshake times out. Ask the person to
run it and say when it is done.

## 5. Verify

```bash
tdelegram auth status
# {"@type":"authorizationStateReady","authorized":true,"needs":null,"profile":"default"}
```

`authorized: true` means everything is in place. Otherwise `needs` names the
missing piece, and it maps directly onto the steps above:

| `needs` | Go back to |
|---|---|
| `"api_id and api_hash"` | step 3 |
| `"a phone number"` | step 4 |
| `"the login code"` | step 4, the login was started but not finished |
| `"the 2FA password"` | step 4 |

This command never prompts and never writes, so it is always safe to run.

Then confirm a real read works:

```bash
tdelegram chat list --limit 3
```

## Docker, which skips steps 1 and 2

The image builds libtdjson from source, so nothing native is needed on the host:

```bash
git clone https://github.com/bulanovdm/TDelegram && cd TDelegram
docker build -t tdelegram .

# Mount the session directory so the login survives between runs.
docker run -it -v ~/.tdelegram:/root/.tdelegram \
  -e TELEGRAM_API_ID -e TELEGRAM_API_HASH \
  tdelegram auth login

docker run -i -v ~/.tdelegram:/root/.tdelegram tdelegram chat list --limit 5
```

The first build takes a while — it compiles TDLib. Use `-it` for `auth login`,
which is interactive; `-i` is enough afterwards.

In those commands the first `tdelegram` is the *image* name from `-t tdelegram`,
and what follows it are the CLI's own arguments: the image's entrypoint is
already `tdelegram`, so `docker run ... tdelegram chat list` runs
`tdelegram chat list`, not the binary twice.

## Where secrets live

Resolution order is explicit argument → environment → OS keychain → a `0600`
file under the profile → an interactive prompt. Whatever is answered at a prompt
is saved to the keychain, so the prompt happens once.

Login codes are the exception: they are single-use and are never stored, because
a saved code would be replayed on the next login and silently fail.

The session directory is **full account access** — equivalent to being logged
in. Never copy it between machines, commit it, or paste its contents anywhere.
See SECURITY.md in the repository.
