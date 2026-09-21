# Releasing

Publishing uses [PyPI Trusted Publishing][tp]. There is no API token in this
repository — no secret to leak, rotate, or mis-scope. GitHub proves the
workflow's identity to PyPI with a short-lived OIDC token.

## One-time setup on PyPI

Do this once, before the first release. It cannot be done from this repo.

1. Sign in to <https://pypi.org> and open
   **Your projects → Publishing** (or, for a name that does not exist yet,
   <https://pypi.org/manage/account/publishing/>).
2. Add a **pending publisher**:

   | Field | Value |
   |---|---|
   | PyPI project name | `tdelegram` |
   | Owner | `bulanovdm` |
   | Repository name | `TDelegram` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

3. In GitHub, create the environment: **Settings → Environments → New
   environment → `pypi`**. Adding a required reviewer there means every
   publish waits for an explicit approval, which is worth the click.

## Cutting a release

```bash
# 1. Bump the version and date them together.
#    pyproject.toml: version = "0.2.0"
#    CHANGELOG.md:   ## 0.2.0 — YYYY-MM-DD

# 2. On the FIRST release only, correct the README: it currently says
#    "Not on PyPI yet". Claiming an install that does not work is worse
#    than saying it is unavailable.

# 3. Land it on main and let CI go green.
git commit -am "Release 0.2.0" && git push

# 4. Tag. The tag is what publishes.
git tag v0.2.0
git push origin v0.2.0
```

The workflow re-runs the full suite on 3.10–3.13, refuses to continue if the
tag disagrees with the version in `pyproject.toml`, builds an sdist and a
wheel, runs `twine check`, and publishes.

## Checking a build by hand

```bash
python -m build
python -m twine check dist/*

# Install the wheel somewhere clean and make sure it actually runs.
python -m venv /tmp/relcheck
/tmp/relcheck/bin/pip install dist/tdelegram-*.whl
/tmp/relcheck/bin/tdelegram version
```

`methods.json` must be present in the installed package: the write gate fails
closed without it, so a wheel missing it refuses every call rather than
silently running ungated.

[tp]: https://docs.pypi.org/trusted-publishers/
