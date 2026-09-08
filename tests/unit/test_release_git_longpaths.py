"""Release validators must read packed objects in deep Windows checkouts."""

import os
import importlib
from pathlib import Path
import subprocess

import pytest

from scripts import scan_release_git_history


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.longpaths=true", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()


@pytest.fixture
def deep_packed_repository(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.name", "Release Path Test")
    _git(source, "config", "user.email", "path-test@example.invalid")
    (source / "fixture.txt").write_text("public fixture\n", encoding="utf-8")
    _git(source, "add", "fixture.txt")
    _git(source, "commit", "-m", "public fixture")
    commit = _git(source, "rev-parse", "HEAD")
    # The checkout itself fits Git's clone limit, but its pack filenames exceed
    # MAX_PATH. Explicitly disable the user's setting so the regression is
    # reproducible even on a developer machine with long paths enabled globally.
    destination = tmp_path / ("deep-" + "x" * max(1, 204 - len(str(tmp_path)) - 6))
    _git(source, "clone", "--no-local", "--no-checkout", str(source), str(destination))
    _git(destination, "config", "core.longpaths", "false")
    assert any(len(str(path)) > 260 for path in (destination / ".git/objects/pack").glob("*.pack"))
    return destination, commit


@pytest.mark.skipif(os.name != "nt", reason="Windows Git MAX_PATH regression")
def test_history_scan_reads_long_packed_object_paths(deep_packed_repository) -> None:
    repository, commit = deep_packed_repository
    assert scan_release_git_history.scan_reachable_history(repository, commit) == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows Git MAX_PATH regression")
def test_archive_inspector_reads_long_packed_object_paths(
    deep_packed_repository, monkeypatch
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts"))
    inspect_source_archive = importlib.import_module("inspect_source_archive")
    repository, commit = deep_packed_repository
    resolved = inspect_source_archive._git(
        repository, "rev-parse", "--verify", f"{commit}^{{commit}}"
    )
    assert resolved.strip() == commit
    assert (
        inspect_source_archive._git_bytes(repository, "cat-file", "blob", f"{commit}:fixture.txt")
        == b"public fixture\n"
    )
