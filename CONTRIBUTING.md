# Contributing

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

- Python 3.10+. `pip install -e ".[dev]"`.
- Tests run with **no account, no network, no TDLib**: `pytest` (FakeTransport).
- Opt-in live test: `TDELEGRAM_INTEGRATION=1 pytest tests/integration -q`.
- Lint: `ruff check src tests scripts`. Types: `mypy --strict src/`.
- Regenerate the registry after a TDLib upgrade:
  `python scripts/generate_method_registry.py --output src/tdelegram/methods.json`.
  It reads `td_api.h` or `td_api.tl`, found via `--schema`, `$TDELEGRAM_TD_API`, or a
  TDLib install. CI fetches the `.tl` at a pinned commit, so no TDLib build is needed.
  A bump moves `TDLIB_COMMIT` in the workflow and `EXPECTED_FUNCTION_COUNT` in
  `tests/contract/test_registry.py` alongside the regenerated JSON.
  The contract test fails CI on unclassified functions — classify, don't default.
- Mutating behavior needs a test proving the gate (preview without `--yes`).
- Never put session data, an `api_hash`, or real message content in a test or a diff.
