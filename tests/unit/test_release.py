"""Releases: the tag, pyproject.toml and CHANGELOG.md must name one version.

A tag publishes to PyPI, which never accepts a version twice, so everything
here is checked before an artifact is built. The last tests hold the
repository itself to the same rules, so a release commit that forgets half of
its bump fails CI before it is tagged rather than after.
"""

from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

import pytest

import tdelegram

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import release  # noqa: E402
from release import ReleaseError  # noqa: E402

IMAGE = "ghcr.io/bulanovdm/tdelegram"


def _pyproject(version: str) -> str:
    return (
        "[tool.other]\n"
        'version = "9.9.9"\n'
        "\n"
        "[project]\n"
        'name = "tdelegram"\n'
        f'version = "{version}"\n'
        'dependencies = ["typer>=0.9"]\n'
        "\n"
        "[project.urls]\n"
        'Homepage = "https://example.invalid"\n'
    )


def _section(version: str, date: str | None = "2026-10-01", body: str = "- A change.") -> str:
    heading = f"## {version}" if date is None else f"## {version} — {date}"
    return f"{heading}\n\n{body}"


def _changelog(*sections: str) -> str:
    return "# Changelog\n\n" + "\n\n".join(sections) + "\n"


CHANGELOG = _changelog(
    "## Unreleased\n\n- Not released yet.",
    _section("0.2.0", body="### Added\n- Releases."),
    _section("0.1.0", date="2026-09-21", body="Initial release."),
)


def test_the_tag_for_the_package_version_is_a_release() -> None:
    result = release.check("v0.2.0", _pyproject("0.2.0"), CHANGELOG, ["v0.1.0", "v0.2.0"])
    assert result.version == "0.2.0"
    assert not result.prerelease
    assert result.latest
    assert result.notes == "### Added\n- Releases."


@pytest.mark.parametrize("tag", ["v0.2.1", "0.2.0", "v0.2", "v0.2.0rc1", "release-0.2.0"])
def test_a_tag_that_disagrees_with_pyproject_is_refused(tag: str) -> None:
    with pytest.raises(ReleaseError, match="tagged v0.2.0"):
        release.check(tag, _pyproject("0.2.0"), CHANGELOG, [])


def test_the_version_is_read_from_the_project_table_only() -> None:
    """A `version` key in another table must not stand in for the package's."""
    assert release.project_field(_pyproject("0.2.0"), "version") == "0.2.0"
    with pytest.raises(ReleaseError, match=r"\[project\] has no version"):
        release.project_field('[tool.other]\nversion = "1.0.0"\n', "version")


@pytest.mark.parametrize(
    ("declared", "normal"),
    [("0.2.0-rc.1", "0.2.0rc1"), ("v0.2.0", "0.2.0"), ("0.2.0RC1", "0.2.0rc1")],
)
def test_a_version_is_written_the_way_pypi_will_spell_it(declared: str, normal: str) -> None:
    """PyPI, the wheel and the Docker tag normalize; a tag spelled otherwise matches none."""
    with pytest.raises(ReleaseError, match=f"as '{normal}'"):
        release.check(f"v{declared}", _pyproject(declared), CHANGELOG, [])


@pytest.mark.parametrize(
    "declared", ["0.2", "0.2.0.1", "0.2.0.post1", "0.2.0.dev1", "1!0.2.0", "0.2.0+local", "soon"]
)
def test_versions_outside_the_policy_are_refused(declared: str) -> None:
    with pytest.raises(ReleaseError, match="pyproject.toml"):
        release.check(f"v{declared}", _pyproject(declared), CHANGELOG, [])


def test_a_prerelease_is_released_but_never_latest() -> None:
    changelog = _changelog(_section("0.3.0rc1"), _section("0.2.0"))
    result = release.check("v0.3.0rc1", _pyproject("0.3.0rc1"), changelog, ["v0.2.0", "v0.3.0rc1"])
    assert result.prerelease
    assert not result.latest
    assert result.docker_tags == ("0.3.0rc1",)


def test_a_fix_to_an_older_line_leaves_latest_where_it_is() -> None:
    """0.1.1 after 0.2.0 is a real release, but `latest` must not move back to it."""
    changelog = _changelog(_section("0.1.1"))
    result = release.check("v0.1.1", _pyproject("0.1.1"), changelog, ["v0.1.0", "v0.2.0", "v0.1.1"])
    assert not result.latest
    assert result.newer == "0.2.0"
    assert result.docker_tags == ("0.1.1", "0.1")


@pytest.mark.parametrize(
    ("version", "released", "expected"),
    [
        # The first release, and one that is newest everywhere.
        ("0.1.0", [], ["0.1.0", "0.1", "latest"]),
        ("1.2.3", ["1.2.2", "1.1.0", "0.9.0"], ["1.2.3", "1.2", "1", "latest"]),
        # Under 0.x a minor release may break, so there is no `0` to follow.
        ("0.3.0", ["0.2.0"], ["0.3.0", "0.3", "latest"]),
        # A fix to 1.1 after 1.2 moves 1.1 only; `1` stays with 1.2.
        ("1.1.5", ["1.1.4", "1.2.3"], ["1.1.5", "1.1"]),
        # The newest 1.x after 2.0 keeps `1`, but `latest` is 2.0's.
        ("1.2.4", ["1.2.3", "2.0.0"], ["1.2.4", "1.2", "1"]),
        # Tagged out of order: 1.2.4 is out, so 1.2.3 takes no floating tag.
        ("1.2.3", ["1.2.4"], ["1.2.3"]),
        # A pre-release takes nothing but its version, and holds nothing back.
        ("2.0.0rc1", ["1.2.3"], ["2.0.0rc1"]),
        ("1.2.4", ["2.0.0rc1"], ["1.2.4", "1.2", "1", "latest"]),
    ],
)
def test_image_tags_float_only_to_the_newest_release_of_their_line(
    version: str, released: list[str], expected: list[str]
) -> None:
    others = [release.Version(v) for v in released]
    assert release.docker_tags(release.Version(version), others) == expected


def test_a_newer_prerelease_does_not_hold_latest_back() -> None:
    changelog = _changelog(_section("0.2.1"))
    result = release.check("v0.2.1", _pyproject("0.2.1"), changelog, ["v0.2.0", "v0.3.0rc1"])
    assert result.latest


def test_tags_that_are_not_releases_are_ignored() -> None:
    tags = ["v9", "vendor-drop", "nightly", "v10.0.0.post1", "v0.1.0", "v0.2.0"]
    assert release.check("v0.2.0", _pyproject("0.2.0"), CHANGELOG, tags).latest


def test_the_notes_are_the_section_and_nothing_after_it() -> None:
    changelog = _changelog(
        _section(
            "0.2.0", body="Intro.\n\n### Fixed\n- A bug.\n\n```md\n## 9.9.9 — 2026-01-01\n```"
        ),
        _section("0.1.0", body="Older."),
    )
    notes = release.check("v0.2.0", _pyproject("0.2.0"), changelog, []).notes
    assert notes.startswith("Intro.")
    assert "### Fixed\n- A bug." in notes
    assert "## 9.9.9" in notes, "a heading inside a code block is not a section"
    assert "Older." not in notes


@pytest.mark.parametrize(
    "heading",
    ["## [0.2.0rc1] - 2026-10-01", "## 0.2.0-rc.1 – 2026-10-01", "## v0.2.0rc1 — 2026-10-01"],
)
def test_a_heading_matches_by_version_not_spelling(heading: str) -> None:
    changelog = _changelog(f"{heading}\n\n- A change.")
    assert release.check("v0.2.0rc1", _pyproject("0.2.0rc1"), changelog, []).notes == "- A change."


@pytest.mark.parametrize("date", [None, "Unreleased", "2026-13-01"])
def test_an_undated_section_is_refused(date: str | None) -> None:
    changelog = _changelog(_section("0.2.0", date=date))
    with pytest.raises(ReleaseError, match="date the 0.2.0 section"):
        release.check("v0.2.0", _pyproject("0.2.0"), changelog, [])


def test_a_release_with_no_section_is_refused() -> None:
    with pytest.raises(ReleaseError, match="no section for 0.3.0"):
        release.check("v0.3.0", _pyproject("0.3.0"), CHANGELOG, [])


def test_a_release_with_an_empty_section_is_refused() -> None:
    changelog = _changelog(_section("0.2.0", body=""), _section("0.1.0"))
    with pytest.raises(ReleaseError, match="section is empty"):
        release.check("v0.2.0", _pyproject("0.2.0"), changelog, [])


def test_the_notes_say_how_to_install_that_version() -> None:
    result = release.check("v0.2.0", _pyproject("0.2.0"), CHANGELOG, [])
    notes = release.render_notes(result, "tdelegram", IMAGE)
    assert notes.startswith("### Added\n- Releases.\n")
    assert f"docker pull {IMAGE}:0.2.0" in notes
    assert "pip install tdelegram==0.2.0" in notes


def _repo(tmp_path: Path, version: str, changelog: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(_pyproject(version), encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    return tmp_path


def test_main_prints_the_outputs_and_writes_the_notes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(tmp_path, "0.2.0", CHANGELOG)
    notes = tmp_path / "notes.md"
    argv = ["v0.2.0", "--tags", "v0.1.0\nv0.2.0", "--image", IMAGE, "--notes", str(notes)]
    assert release.main([*argv, "--root", str(root)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "version=0.2.0",
        "prerelease=false",
        "latest=true",
        "docker_tags=0.2.0 0.2 latest",
    ]
    assert f"docker pull {IMAGE}:0.2.0" in notes.read_text(encoding="utf-8")


def test_main_refuses_without_writing_an_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """stdout goes to $GITHUB_OUTPUT, so a refusal must leave it empty."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    root = _repo(tmp_path, "0.2.0", CHANGELOG)
    assert release.main(["v0.3.0", "--image", IMAGE, "--root", str(root)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("::error::tag v0.3.0 does not match")


# The repository itself.


def _declared() -> str:
    return release.project_field(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"), "version"
    )


def test_the_package_reports_the_version_in_pyproject() -> None:
    """`tdelegram version` and `__version__` come from pyproject.toml, not a second copy.

    Fails in a checkout installed before a bump: reinstall it.
    """
    assert tdelegram.__version__ == _declared()


def test_an_uninstalled_tree_reports_an_unknown_version(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", missing)
    assert tdelegram._installed_version() == "0+unknown"


def test_the_repository_can_be_released_at_its_version() -> None:
    """pyproject.toml's version is releasable and CHANGELOG.md has dated notes for it."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release.check(f"v{_declared()}", pyproject, changelog, []).notes


def test_the_newest_changelog_release_is_the_version_in_pyproject() -> None:
    """A release bumps both; bumping one fails here, before the tag is pushed."""
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    newest = next(s for s in release.changelog_sections(changelog) if s.date is not None)
    assert str(newest.version) == _declared(), (
        f"CHANGELOG.md's newest release is {newest.version}, but pyproject.toml says "
        f"{_declared()}: a release sets both"
    )
