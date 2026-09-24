#!/usr/bin/env python3
"""Check a release tag against the repository, and describe the release.

    python scripts/release.py v0.2.0 --tags "$(git tag --list 'v*')" \\
        --image ghcr.io/bulanovdm/tdelegram --notes notes.md

The version is written once, in pyproject.toml, and a release is the tag "v"
plus exactly that version. CHANGELOG.md must have a dated section for it,
which becomes the release notes. Anything else is refused before an artifact
is built: PyPI never accepts the same version twice, so a mistake found after
publishing costs a version number.

Prints `version=`, `prerelease=`, `latest=` and `docker_tags=` lines for
$GITHUB_OUTPUT. A floating tag -- `latest`, and the image's `1` and `1.2` --
moves only to a final release at least as new as every other in its line, so
neither a pre-release nor a fix to an older line takes one from a newer
release. GitHub's "Latest" badge follows the same rule.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parents[1]

POLICY = "MAJOR.MINOR.PATCH, optionally followed by aN, bN or rcN"


class ReleaseError(Exception):
    """The tag, pyproject.toml and CHANGELOG.md do not agree on a releasable version."""


@dataclass(frozen=True)
class Section:
    version: Version
    date: str | None
    body: str


@dataclass(frozen=True)
class Release:
    version: str
    prerelease: bool
    latest: bool
    docker_tags: tuple[str, ...]
    notes: str
    # The final release that keeps `latest` when this one does not take it.
    newer: str | None = None


def project_field(pyproject: str, field: str) -> str:
    """A string field of pyproject.toml's [project] table.

    Read without a TOML parser because Python 3.10, which the suite runs on,
    has none in the standard library.
    """
    table = re.search(r"^\[project\][ \t]*$(.*?)(?=^\[|\Z)", pyproject, re.M | re.S)
    found = table and re.search(rf'^{re.escape(field)}\s*=\s*"([^"]*)"', table.group(1), re.M)
    if not found:
        raise ReleaseError(f'pyproject.toml: [project] has no {field} = "..."')
    return found.group(1)


def parse_version(text: str, where: str) -> Version:
    """A version this project releases: normalized PEP 440 in SemVer's shape.

    The normalized spelling is the one PyPI, the wheel's filename and the
    Docker tag all use, so the tag has to use it too or the four disagree.
    """
    try:
        version = Version(text)
    except InvalidVersion:
        raise ReleaseError(f"{where}: {text!r} is not a version") from None
    if str(version) != text:
        raise ReleaseError(
            f"{where}: write {text!r} as {str(version)!r}, the spelling PyPI and the "
            "Docker tag will use"
        )
    if (
        version.epoch
        or len(version.release) != 3
        or version.is_postrelease
        or version.is_devrelease
        or version.local is not None
    ):
        raise ReleaseError(f"{where}: {text!r} is not {POLICY}")
    return version


def released_versions(tags: list[str]) -> list[Version]:
    """The versions among `tags`; tags that are not a release are ignored."""
    versions = []
    for tag in tags:
        if not tag.startswith("v"):
            continue
        try:
            versions.append(parse_version(tag[1:], tag))
        except ReleaseError:
            continue
    return versions


def changelog_sections(changelog: str) -> list[Section]:
    """Every `## <version>` section of CHANGELOG.md, newest (topmost) first.

    A heading that does not start with a version, such as `## Unreleased`,
    is not a release and is skipped.
    """
    lines = changelog.splitlines()
    starts = []
    fenced = False
    for number, line in enumerate(lines):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("## "):
            starts.append(number)

    sections = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        head, _, rest = lines[start][3:].strip().partition(" ")
        try:
            version = Version(head.strip("[]"))
        except InvalidVersion:
            continue
        body = "\n".join(lines[start + 1 : end]).strip()
        sections.append(Section(version, _date(rest), body))
    return sections


def _date(text: str) -> str | None:
    found = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if not found:
        return None
    try:
        datetime.date.fromisoformat(found.group(1))
    except ValueError:
        return None
    return found.group(1)


def notes_for(changelog: str, version: Version) -> str:
    """The body of the version's CHANGELOG.md section, which must be dated."""
    heading = f"## {version} — YYYY-MM-DD"
    for section in changelog_sections(changelog):
        if section.version != version:
            continue
        if section.date is None:
            raise ReleaseError(f"CHANGELOG.md: date the {version} section: {heading}")
        if not section.body:
            raise ReleaseError(f"CHANGELOG.md: the {version} section is empty")
        return section.body
    raise ReleaseError(f"CHANGELOG.md has no section for {version}; add {heading}")


def docker_tags(version: Version, released: list[Version]) -> list[str]:
    """The image tags a release takes, its own version first.

    A pre-release gets its version and nothing else. A final release also
    takes each floating tag it is the newest release for: `MAJOR.MINOR`,
    `MAJOR` from 1.0.0 on (under 0.x a minor release may break, so `0` would
    promise nothing), and `latest`. A fix to an older line moves that line's
    tags and leaves a newer release's alone.
    """
    tags = [str(version)]
    if version.is_prerelease:
        return tags
    finals = [v for v in released if not v.is_prerelease]

    def newest(in_line: Callable[[Version], bool]) -> bool:
        return not any(v > version for v in finals if in_line(v))

    if newest(lambda v: v.release[:2] == version.release[:2]):
        tags.append(f"{version.major}.{version.minor}")
    if version.major >= 1 and newest(lambda v: v.major == version.major):
        tags.append(str(version.major))
    if newest(lambda v: True):
        tags.append("latest")
    return tags


def check(tag: str, pyproject: str, changelog: str, tags: list[str]) -> Release:
    """Refuse the release unless the tag, the package and the changelog agree."""
    declared = parse_version(project_field(pyproject, "version"), "pyproject.toml")
    if tag != f"v{declared}":
        raise ReleaseError(
            f"tag {tag} does not match pyproject.toml, whose version {declared} is "
            f"tagged v{declared}"
        )
    notes = notes_for(changelog, declared)

    released = released_versions(tags)
    image_tags = docker_tags(declared, released)
    newer = max((v for v in released if not v.is_prerelease and v > declared), default=None)
    return Release(
        version=str(declared),
        prerelease=declared.is_prerelease,
        latest="latest" in image_tags,
        docker_tags=tuple(image_tags),
        notes=notes,
        newer=None if newer is None else str(newer),
    )


def render_notes(release: Release, package: str, image: str) -> str:
    """The GitHub release body: the changelog section, then how to install it."""
    return (
        f"{release.notes}\n"
        "\n"
        "---\n"
        "\n"
        f"- Docker: `docker pull {image}:{release.version}`\n"
        f"- PyPI: `pip install {package}=={release.version}` (TDLib is separate; "
        "see the README)\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="the tag being released, e.g. v0.2.0")
    parser.add_argument(
        "--tags", default="", help="every tag in the repository, separated by whitespace"
    )
    parser.add_argument(
        "--image", required=True, help="the Docker image the release is published as"
    )
    parser.add_argument("--notes", type=Path, help="write the release notes to this file")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    pyproject = (args.root / "pyproject.toml").read_text(encoding="utf-8")
    changelog = (args.root / "CHANGELOG.md").read_text(encoding="utf-8")
    try:
        release = check(args.tag, pyproject, changelog, args.tags.split())
    except ReleaseError as exc:
        # On a runner the prefix turns the message into an annotation on the
        # run's summary page, where it is seen without opening the log.
        prefix = "::error::" if os.environ.get("GITHUB_ACTIONS") else "error: "
        print(f"{prefix}{exc}", file=sys.stderr)
        return 1

    if args.notes is not None:
        package = project_field(pyproject, "name")
        args.notes.write_text(render_notes(release, package, args.image), encoding="utf-8")
    print(f"image tags: {' '.join(release.docker_tags)}", file=sys.stderr)
    if release.newer is not None:
        print(f"v{release.newer} is newer, so `latest` stays with it", file=sys.stderr)

    print(f"version={release.version}")
    print(f"prerelease={str(release.prerelease).lower()}")
    print(f"latest={str(release.latest).lower()}")
    print(f"docker_tags={' '.join(release.docker_tags)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
