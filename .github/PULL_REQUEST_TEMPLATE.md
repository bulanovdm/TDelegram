## What this changes

<!-- And why. Link an issue if there is one. -->

## Checklist

- [ ] `pytest` passes (no account, no network, no TDLib required)
- [ ] `ruff check src tests scripts` and `mypy --strict src/` are clean
- [ ] Mutating behaviour has a test proving the gate previews without `--yes`
- [ ] `methods.json` was regenerated, not hand-edited, if the schema changed
- [ ] No session data, `api_hash`, or real message content in the diff or tests
