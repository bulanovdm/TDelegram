# Security

The session directory (`~/.tdelegram/`, or `--session-dir`) is **full account
access, equivalent to a logged-in Telegram**. Never commit it, never copy it to
another machine, never paste its contents into issues.

- Secrets resolve explicit → env → config → OS store → prompt. macOS `security`
  receives secrets on stdin (never argv); Linux uses `secret-tool`; Windows tries
  `keyring`; a 0600 file is the documented fallback.
- Flood waits auto-retry for reads only. Writes raise so the caller decides —
  re-sending a write after a flood wait is how people double-post.
- To report a vulnerability, open a private security advisory on GitHub.
