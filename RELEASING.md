# Releasing

A release is a tag. Pushing `v0.2.0` runs `.github/workflows/release.yml`,
which publishes that one version everywhere it goes: PyPI, the Docker image,
and a [GitHub release][releases] carrying the sdist and the wheel.

## Versions

The version is written once, as `version` in `pyproject.toml`.
`tdelegram version` and `tdelegram.__version__` read it back from the installed
package, so there is no second copy to forget. A development checkout reports
the version it was installed at; reinstall after a bump.

- **Shape.** `MAJOR.MINOR.PATCH`, with [SemVer][semver]'s meaning. A
  pre-release adds `aN`, `bN` or `rcN`, spelled the way PEP 440 normalizes it:
  `0.3.0rc1`, not `0.3.0-rc.1`, since that is how PyPI, the wheel's filename
  and the image tag will all spell it. There are no `.postN`, `.devN` or local
  versions; a fix is a patch release.
- **Under 0.x** a minor release (0.2 → 0.3) may break things; a patch release
  never does.
- **The tag** is `v` plus exactly that version. `scripts/release.py` refuses
  anything else before a single artifact is built.

## What a tag does

Each step starts only once the one before has passed, so a failure stops
everything after it:

1. **verify** — the full suite on 3.10–3.13 (`ci.yml`).
2. **check** — `scripts/release.py`: the tag is `v` + the version in
   `pyproject.toml`, and `CHANGELOG.md` has a dated section for it, which
   becomes the release notes. Also chooses the image's tags and whether GitHub
   marks the release "Latest". Runs alongside verify.
3. **build** — sdist and wheel, `twine check`, then the wheel is installed
   somewhere clean and must report the version and load the method registry.
4. **docker** — both architectures are built and run by digest (right
   version, registry present) before any tag is created. Only then is the
   image tagged.
5. **pypi** — [Trusted Publishing][tp], behind the `pypi` environment. There
   is no API token in this repository — no secret to leak, rotate, or
   mis-scope. GitHub proves the workflow's identity to PyPI with a short-lived
   OIDC token.
6. **github-release** — `TDelegram 0.2.0`, with both files attached, the
   changelog section as notes, and pre-release / "Latest" set to match the
   image tags.

## Docker image tags

`ghcr.io/bulanovdm/tdelegram`, for `linux/amd64` and `linux/arm64`:

| Tag | Is | Moves |
|---|---|---|
| `0.2.0` | that release | never |
| `0.2` | the newest `0.2.x` | with each `0.2.x` release |
| `1` | the newest `1.x.y` | with each `1.x` release, from 1.0.0 on |
| `latest` | the newest release | with each release newer than all the others |
| `0.3.0rc1` | a pre-release | never; a pre-release takes no other tag |
| `main` | the tip of `main`, unreleased | with every push to `main` |
| `sha-2d6ff4f` | one commit on `main` | never |

A floating tag only moves forward. A fix to an older line — `1.1.5` after
`1.2.0` — takes `1.1` and leaves `1` and `latest` with `1.2.0`. There is no `0`
tag, because under 0.x a minor release may break. `docker.yml` does not choose
these itself: `release.yml` passes the list `scripts/release.py` works out from
every release tag in the repository, and its tests pin the rules.

Every image says which build it is:

```bash
docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' \
  ghcr.io/bulanovdm/tdelegram:latest
```

A release's image is never rebuilt in place, not even for a base image fix:
release a patch. `docker.yml` refuses to run by hand on a tag for this reason.

## One-time setup

Do this once, before the first release. It cannot be done from this repo.

1. **PyPI.** Sign in to <https://pypi.org> and open **Your projects →
   Publishing** (or, for a name that does not exist yet,
   <https://pypi.org/manage/account/publishing/>). Add a **pending publisher**:

   | Field | Value |
   |---|---|
   | PyPI project name | `tdelegram` |
   | Owner | `bulanovdm` |
   | Repository name | `TDelegram` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

2. **The `pypi` environment.** In GitHub: **Settings → Environments → New
   environment → `pypi`**. Adding a required reviewer there means every
   publish waits for an explicit approval, which is worth the click. The
   approval comes after the image is tagged and before PyPI.

3. **Protect release tags** (recommended). A pushed tag publishes, and a moved
   one would make the GitHub release and PyPI disagree about what `v0.2.0`
   is. **Settings → Rules → Rulesets → New tag ruleset**, targeting `v*`, with
   *Restrict updates* and *Restrict deletions* on. Moving the tag of a failed
   release (below) then means switching the ruleset off for that moment,
   which is the point.

## Cutting a release

```bash
# 1. Name the version and date it, together:
#      pyproject.toml:  version = "0.2.0"
#      CHANGELOG.md:    ## Unreleased  ->  ## 0.2.0 — 2026-10-01
#    Changes collect under "## Unreleased" at the top of CHANGELOG.md between
#    releases; that heading becomes the release's. The suite fails when the
#    newest dated section and pyproject.toml disagree, so a half-done bump is
#    caught here rather than by the tag.

# 2. On the FIRST release only:
#    - 0.1.0 already has its section; set its date to the day of the release.
#    - correct the README, which currently says "Not on PyPI yet".
#      Claiming an install that does not work is worse than saying it
#      is unavailable.
#    - set the repository website, left empty for the same reason:
#      gh repo edit bulanovdm/TDelegram \
#        --homepage https://pypi.org/project/tdelegram/

# 3. Land it on main and let CI go green.
git commit -am "Release 0.2.0" && git push

# 4. Tag. The tag is what publishes.
git tag v0.2.0
git push origin v0.2.0
```

A pre-release is the same, as `0.3.0rc1`, with its own dated section.

## When a step fails

- **Before `docker` tags the image** (verify, check, build, or a build or smoke
  test inside `docker`): nothing is published. Fix it on `main`, then, with
  the fixed commit checked out, move the tag to it:

  ```bash
  git push origin :refs/tags/v0.2.0 && git tag -f v0.2.0 && git push origin v0.2.0
  ```

- **At `pypi` or `github-release`**: the image is out; the rest is not. When
  the cause is outside the code — the publisher not set up yet, an approval
  declined, a network error — fix that and use **Re-run failed jobs** on the
  run. When it takes a code change and PyPI does not have the version yet, the
  tag can still move as above; the image is rebuilt and retagged with it.
- **After PyPI has the version**, it is spent. PyPI never accepts the same
  version twice, even deleted, so a mistake found now is fixed by a patch
  release, never by moving the tag.

## Checking a build by hand

The release does this itself (step 3), but it is quick to run before tagging:

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

[releases]: https://github.com/bulanovdm/TDelegram/releases
[semver]: https://semver.org/
[tp]: https://docs.pypi.org/trusted-publishers/
