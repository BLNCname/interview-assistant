from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).parents[2]
README_PATH = ROOT / "README.md"
START_HERE_PATH = ROOT / "START_HERE.md"
CHECKSUMS_PATH = ROOT / "SHA256SUMS.txt"
GITIGNORE_PATH = ROOT / ".gitignore"
ISS_PATH = ROOT / "packaging" / "interview_assistant.iss"
INSTALLER_SCRIPT_PATH = ROOT / "scripts" / "build_installer.ps1"
ARCHIVE_SCRIPT_PATH = ROOT / "scripts" / "create_source_archive.ps1"
ARCHIVE_INSPECTOR_PATH = ROOT / "scripts" / "inspect_source_archive.py"
GITATTRIBUTES_PATH = ROOT / ".gitattributes"
DIST_VALIDATOR_PATH = ROOT / "scripts" / "validate_release_dist.py"
DIST_INVENTORY_PATH = ROOT / "packaging" / "dist_inventory.json"
HISTORY_SCANNER_PATH = ROOT / "scripts" / "scan_release_git_history.py"
INSTALLER_SMOKE_PATH = ROOT / "scripts" / "smoke_installer.ps1"
PORTABLE_DOC_PATH = ROOT / "docs" / "portable-release.md"
RELEASE_CONFIG_PATH = ROOT / "packaging" / "source_release_config.yaml"
SPEC_PATH = ROOT / "packaging" / "interview_assistant.spec"
APP_ID = "9CE7901A-56E8-49CB-A8ED-8D5CF4F97C7D"


def test_release_version_defaults_are_consistent() -> None:
    assert 'version = "0.1.1"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version_info = (ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    assert "filevers=(0, 1, 1, 0)" in version_info
    assert "prodvers=(0, 1, 1, 0)" in version_info
    assert "StringStruct(u'FileVersion', u'0.1.1')" in version_info
    assert "StringStruct(u'ProductVersion', u'0.1.1')" in version_info
    assert '#define AppVersion "0.1.1"' in ISS_PATH.read_text(encoding="utf-8")
    assert '[string]$Version = "0.1.1"' in INSTALLER_SCRIPT_PATH.read_text(
        encoding="utf-8"
    )
    assert '[string]$Version = "0.1.1"' in ARCHIVE_SCRIPT_PATH.read_text(
        encoding="utf-8"
    )

EXPECTED_RELEASE_HOTKEYS = {
    "force_request": "ctrl+shift+space",
    "screenshot": "ctrl+shift+s",
    "pause": "ctrl+shift+p",
    "overlay_visibility": "ctrl+shift+o",
    "overlay_interaction": "ctrl+shift+i",
    "forced_web_search": "ctrl+shift+w",
    "clear_answer": "ctrl+shift+c",
}


def test_instructor_readme_is_english_and_covers_clean_machine_workflows() -> None:
    text = README_PATH.read_text(encoding="utf-8")
    folded = text.casefold()

    assert re.search(r"[А-Яа-яЁё]", text) is None
    for heading in (
        "## System requirements",
        "## Install the ready-to-use application",
        "## Configure LM Studio",
        "## Build from source",
        "## Run tests and diagnostics",
        "## Troubleshooting",
    ):
        assert heading in text
    for required in (
        "InterviewAssistant-Setup-0.1.0-win64.exe",
        "uv sync --extra dev --extra cuda --frozen",
        "scripts\\build.ps1",
        "scripts\\build_installer.ps1",
        "dropbox-dash/faster-whisper-large-v3-turbo",
        "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        "windows credential manager",
        "participant consent",
    ):
        assert required.casefold() in folded


def test_start_here_is_a_short_english_installer_entry_point() -> None:
    text = START_HERE_PATH.read_text(encoding="utf-8")

    assert re.search(r"[А-Яа-яЁё]", text) is None
    assert "InterviewAssistant-Setup-0.1.0-win64.exe" in text
    assert "README.md" in text
    assert "InterviewAssistant.exe" not in text
    assert "InterviewAssistant-source-0.1.0.zip" not in text


def test_root_checksum_manifest_contains_only_the_installer() -> None:
    lines = CHECKSUMS_PATH.read_text(encoding="ascii").splitlines()

    assert len(lines) == 1
    assert re.fullmatch(
        r"[0-9A-F]{64}  InterviewAssistant-Setup-0\.1\.0-win64\.exe",
        lines[0],
    )


def test_gitignore_exposes_duplicate_handoff_clutter() -> None:
    patterns = GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()

    assert "/InterviewAssistant-Setup-*-win64.exe" in patterns
    for stale_ignore in (
        "/InterviewAssistant.exe",
        "/InterviewAssistant-source-*.zip",
        "/SHA256SUMS.txt",
        "/_internal/",
    ):
        assert stale_ignore not in patterns


def test_frozen_bundle_includes_prompt_but_not_machine_config() -> None:
    source = SPEC_PATH.read_text(encoding="utf-8")

    assert '(str(ROOT / "prompts" / "interview_system.md"), "prompts")' in source
    assert "config.yaml" not in source


def _powershell() -> str:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if executable is None:
        pytest.skip("PowerShell is required for the Windows release-script test")
    return executable


def _git(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _model_entry(path: str, content: bytes, *, runtime_required: bool) -> dict[str, object]:
    return {
        "path": path,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "runtime_required": runtime_required,
    }


def _create_source_repository(
    tmp_path: Path,
    *,
    include_gitattributes: bool = True,
) -> tuple[Path, Path, dict[str, bytes], str, str, str]:
    repository_store = tmp_path / "repository store"
    repository_store.mkdir()
    repository = tmp_path / "source repository"
    model_files = {
        "config.json": b'{"model": "tiny-test-fixture"}\n',
        "model.bin": b"small-not-a-real-model\x00\x01",
        "README.md": b"Test-only model notice.\n",
    }
    manifest = {
        "schema_version": 1,
        "name": "large-v3-turbo",
        "repository": "dropbox-dash/faster-whisper-large-v3-turbo",
        "revision": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        "license": "MIT",
        "bundle_subdirectory": "models/stt/large-v3-turbo",
        "files": [
            _model_entry("config.json", model_files["config.json"], runtime_required=True),
            _model_entry("model.bin", model_files["model.bin"], runtime_required=True),
            _model_entry("README.md", model_files["README.md"], runtime_required=False),
        ],
    }
    tracked = {
        "packaging/stt_model_manifest.json": json.dumps(manifest, indent=2) + "\n",
        "scripts/build.ps1": "Write-Output 'build fixture'\n",
        "docs/superpowers/plans/implementation.md": "# Implementation plan\n",
        "docs/guide.md": "# Guide\n",
        "interview_assistant/app.py": "VALUE = 'tracked-head'\n",
        "tests/test_fixture.py": "def test_fixture():\n    assert True\n",
        "assets/icon.txt": "asset\n",
        "config.yaml": "machine: checked-in-runtime-value\n",
        "packaging/source_release_config.yaml": RELEASE_CONFIG_PATH.read_text(
            encoding="utf-8"
        ),
        "prompts/interview_system.md": "Candidate release prompt\n",
        ".gitignore": (
            ".venv/\n"
            "build/\n"
            "dist/\n"
            ".cache/\n"
            ".superpowers/\n"
            "logs/\n"
            "models/\n"
            "screenshots/\n"
            "*.reg\n"
            "*token*\n"
        ),
        ".gitattributes": (
            "* text=auto eol=lf\n"
            "*.bin binary\n"
            "*.dll binary\n"
            "*.exe binary\n"
            "*.ico binary\n"
            "*.png binary\n"
            "*.pyd binary\n"
            "*.wav binary\n"
            "*.zip binary\n"
        ),
    }
    if not include_gitattributes:
        tracked.pop(".gitattributes")
    for relative_path, tracked_content in tracked.items():
        path = repository_store / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tracked_content, encoding="utf-8")

    _git("init", "-b", "main", cwd=repository_store)
    _git("config", "user.name", "Release Test", cwd=repository_store)
    _git("config", "user.email", "release-test@example.invalid", cwd=repository_store)
    _git("add", ".", cwd=repository_store)
    _git("commit", "-m", "fixture head", cwd=repository_store)
    head = _git("rev-parse", "HEAD", cwd=repository_store).stdout.strip()

    _git("switch", "-c", "discarded-secret-history", cwd=repository_store)
    _git("config", "user.name", "Secret Fixture Identity", cwd=repository_store)
    _git("config", "user.email", "secret-fixture@example.invalid", cwd=repository_store)
    (repository_store / "credential-secret.txt").write_text(
        "DANGLING_SECRET_BLOB_MUST_NOT_BE_ARCHIVED\n",
        encoding="utf-8",
    )
    _git("add", "credential-secret.txt", cwd=repository_store)
    _git("commit", "-m", "discarded secret fixture", cwd=repository_store)
    secret_commit = _git("rev-parse", "HEAD", cwd=repository_store).stdout.strip()
    secret_blob = _git(
        "rev-parse",
        "HEAD:credential-secret.txt",
        cwd=repository_store,
    ).stdout.strip()
    _git("switch", "main", cwd=repository_store)
    _git("branch", "-D", "discarded-secret-history", cwd=repository_store)
    _git("config", "user.name", "Release Test", cwd=repository_store)
    _git("config", "user.email", "release-test@example.invalid", cwd=repository_store)
    _git(
        "remote",
        "add",
        "origin",
        "https://example.invalid/source.git",
        cwd=repository_store,
    )
    _git("switch", "--detach", cwd=repository_store)
    _git("worktree", "add", str(repository), "main", cwd=repository_store)

    ignored_files = {
        ".venv/secret.txt": "venv secret\n",
        "build/build.txt": "build artifact\n",
        "dist/old.zip": "old release\n",
        ".cache/huggingface/token": "cache token\n",
        ".superpowers/private.md": "internal coordination\n",
        "logs/application.log": "runtime log\n",
        "screenshots/capture.txt": "screen capture\n",
        "credentials.reg": "registry export\n",
        "lmstudio-token.txt": "credential material\n",
    }
    for relative_path, ignored_content in ignored_files.items():
        path = repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ignored_content, encoding="utf-8")
    (repository / "untracked.txt").write_text("untracked source\n", encoding="utf-8")
    (repository / "interview_assistant" / "app.py").write_text(
        "VALUE = 'uncommitted-worktree-change'\n",
        encoding="utf-8",
    )
    (repository / "config.yaml").write_text(
        "machine: uncommitted-runtime-secret\n",
        encoding="utf-8",
    )

    model_path = tmp_path / "downloaded model"
    model_path.mkdir()
    for name, model_content in model_files.items():
        (model_path / name).write_bytes(model_content)
    (model_path / "unlisted.bin").write_bytes(b"must not be archived")
    cache_file = model_path / ".cache" / "huggingface" / "download-token"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("must not be archived\n", encoding="utf-8")
    return repository, model_path, model_files, head, secret_commit, secret_blob


def _write_dirty_worktree_manifest(repository: Path, model_path: Path) -> None:
    manifest_path = repository / "packaging" / "stt_model_manifest.json"
    dirty_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dirty_manifest["dirty_marker"] = "WORKTREE_MANIFEST_MUST_NOT_BE_ARCHIVED"
    dirty_manifest["files"] = [
        _model_entry(
            "unlisted.bin",
            (model_path / "unlisted.bin").read_bytes(),
            runtime_required=True,
        )
    ]
    manifest_path.write_text(json.dumps(dirty_manifest, indent=2) + "\n", encoding="utf-8")


def _archive_temp_directory(repository: Path) -> Path:
    temporary_id = hashlib.sha256(str(repository.parent).encode()).hexdigest()[:12]
    temporary_root = Path(os.environ.get("TEMP", os.environ.get("TMP", str(ROOT))))
    return temporary_root / f"ia-t19-{temporary_id}"


def _extended_windows_path(path: Path) -> str:
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def _staging_directory_names(parent: Path) -> set[str]:
    extended_parent = _extended_windows_path(parent)
    if not os.path.isdir(extended_parent):
        return set()
    return {
        entry.name
        for entry in os.scandir(extended_parent)
        if entry.name.startswith("InterviewAssistant-source-staging-")
    }


def _run_archive_script(
    repository: Path,
    model_path: Path,
    output_path: Path,
    *,
    source_commit: str | None = None,
    temporary_directory: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    if temporary_directory is None:
        temporary_directory = _archive_temp_directory(repository)
    os.makedirs(_extended_windows_path(temporary_directory), exist_ok=True)
    environment = os.environ.copy()
    environment["INTERVIEW_ASSISTANT_ARCHIVE_TEMP"] = str(temporary_directory)
    if source_commit is None:
        source_commit = _git("rev-parse", "HEAD", cwd=repository).stdout.strip()
    return subprocess.run(
        [
            _powershell(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ARCHIVE_SCRIPT_PATH),
            "-SttModelPath",
            str(model_path),
            "-OutputPath",
            str(output_path),
            "-Version",
            "9.8.7-test",
            "-RepositoryPath",
            str(repository),
            "-SourceCommit",
            source_commit,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
    )


def _run_archive_inspector(
    archive: Path,
    manifest: Path,
    source_commit: str,
    *,
    top: str = "InterviewAssistant-source-9.8.7-test",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(ARCHIVE_INSPECTOR_PATH),
            "--archive",
            str(archive),
            "--manifest",
            str(manifest),
            "--expected-commit",
            source_commit,
            "--expected-top",
            top,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture(scope="module")
def inspectable_source_archive(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    tmp_path = tmp_path_factory.mktemp("inspectable-source-archive")
    repository, model_path, _files, source_commit, _secret_commit, _secret_blob = (
        _create_source_repository(tmp_path)
    )
    output_path = tmp_path / "inspectable.zip"
    result = _run_archive_script(
        repository,
        model_path,
        output_path,
        source_commit=source_commit,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {
        "archive": output_path,
        "manifest": repository / "packaging" / "stt_model_manifest.json",
        "source_commit": source_commit,
        "top": "InterviewAssistant-source-9.8.7-test",
    }


def _rewrite_archive(
    source: Path,
    destination: Path,
    *,
    omit: frozenset[str] = frozenset(),
    replacements: dict[str, bytes] | None = None,
    additions: tuple[tuple[zipfile.ZipInfo | str, bytes], ...] = (),
) -> None:
    replacements = replacements or {}
    with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as outgoing:
        for entry in incoming.infolist():
            normalized = entry.filename.replace("\\", "/")
            if normalized in omit:
                continue
            payload = replacements.get(normalized, incoming.read(entry))
            outgoing.writestr(entry, payload)
        for name, payload in additions:
            outgoing.writestr(name, payload)


def test_inno_setup_is_per_user_versioned_and_deletes_only_owned_upgrade_files() -> None:
    assert ISS_PATH.is_file(), "The Inno Setup definition has not been created"
    source = ISS_PATH.read_text(encoding="utf-8")

    assert f"AppId={{{{{APP_ID}}}" in source
    assert "PrivilegesRequired=lowest" in source
    assert "DefaultDirName={localappdata}\\Programs\\InterviewAssistant" in source
    assert "ArchitecturesAllowed=x64compatible" in source
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in source
    assert "OutputDir={#OutputPath}" in source
    assert "OutputBaseFilename=InterviewAssistant-Setup-{#AppVersion}-win64" in source
    assert "Compression=lzma2/ultra64" in source
    assert "SolidCompression=yes" in source
    assert ("SetupIconFile={#SourcePath}\\..\\assets\\branding\\interview-assistant.ico") in source
    assert 'Source: "{#DistPath}\\*"; DestDir: "{app}"' in source
    assert "recursesubdirs" in source
    assert "createallsubdirs" in source
    assert 'Name: "{group}\\Interview Assistant"' in source
    assert 'Name: "{autodesktop}\\Interview Assistant"' in source
    assert "Tasks: desktopicon" in source

    install_delete = source.split("[InstallDelete]", maxsplit=1)[1].split("[", maxsplit=1)[0]
    entries = [line.strip() for line in install_delete.splitlines() if line.strip()]
    assert entries == [
        'Type: filesandordirs; Name: "{app}\\_internal"',
        'Type: files; Name: "{app}\\InterviewAssistant.exe"',
    ]
    assert "[UninstallDelete]" not in source
    assert "{userappdata}" not in source.casefold()
    assert "{localappdata}\\InterviewAssistant" not in install_delete
    forbidden = ("{userstartup}", "[run]", "service", "runatstartup", "signTool=")
    assert not any(item.casefold() in source.casefold() for item in forbidden)


def test_installer_builder_validates_dist_bundle_and_invokes_supplied_iscc() -> None:
    assert INSTALLER_SCRIPT_PATH.is_file(), "The installer build script has not been created"
    source = INSTALLER_SCRIPT_PATH.read_text(encoding="utf-8")

    assert '$ErrorActionPreference = "Stop"' in source
    assert "Set-StrictMode -Version Latest" in source
    assert "[Parameter(Mandatory" in source
    for parameter in ("$IsccPath", "$Version", "$DistPath", "$OutputPath"):
        assert parameter in source
    assert "Resolve-Path -LiteralPath $IsccPath" in source
    assert "InterviewAssistant.exe" in source
    assert "stt_model_manifest.json" in source
    assert "bundle_subdirectory" in source
    assert "Start-Process" in source
    assert "-WindowStyle Hidden" in source
    assert "-Wait" in source
    assert "-PassThru" in source
    assert '"/DAppVersion=' in source
    assert '"/DDistPath=' in source
    assert '"/DOutputPath=' in source
    assert ".ExitCode" in source
    assert "InterviewAssistant-Setup-$Version-win64.exe" in source
    assert "validate_release_dist.py" in source
    assert "dist_inventory.json" in source
    for downloader in ("Invoke-WebRequest", "Start-BitsTransfer", "winget", "choco"):
        assert downloader not in source


def test_installer_smoke_rechecks_installed_model_hashes() -> None:
    source = INSTALLER_SMOKE_PATH.read_text(encoding="utf-8")

    assert "validate_release_dist.py" in source
    assert '"--installed"' in source
    assert "stt_model_manifest.json" in source
    assert "dist_inventory.json" in source


def test_source_archive_script_declares_fail_closed_git_and_zip_contract() -> None:
    assert ARCHIVE_SCRIPT_PATH.is_file(), "The source archive script has not been created"
    source = ARCHIVE_SCRIPT_PATH.read_text(encoding="utf-8")

    assert '$ErrorActionPreference = "Stop"' in source
    assert "Set-StrictMode -Version Latest" in source
    assert "[Parameter(Mandatory" in source
    for parameter in (
        "$SttModelPath",
        "$OutputPath",
        "$Version",
        "$RepositoryPath",
        "$SourceCommit",
    ):
        assert parameter in source
    assert "stt_model_manifest.json" in source
    assert "ConvertFrom-Json" in source
    assert "Get-FileHash" in source
    assert "SHA256" in source
    assert "runtime_required" in source
    assert "bundle_subdirectory" in source
    assert "rev-parse" in source
    assert "--verify" in source
    assert "symbolic-ref" in source
    assert '"show"' in source
    assert "clone" in source
    assert "--no-local" in source
    assert "--single-branch" in source
    assert "--no-tags" in source
    assert "--no-checkout" in source
    assert "checkout" in source
    assert '"-B"' in source
    assert "--detach" not in source
    assert "--no-hardlinks" not in source
    assert "remote" in source
    assert "remove" in source
    assert "origin" in source
    assert "refs/remotes/origin/HEAD" in source
    assert '"--delete"' in source
    assert "reflog" in source
    assert "expire" in source
    assert '"gc"' in source
    assert "--prune=now" in source
    assert "fsck" in source
    assert "--no-reflogs" in source
    assert "--unreachable" in source
    assert "scan_release_git_history.py" in source
    assert '".git\\logs"' in source
    assert "FETCH_HEAD" in source
    assert "ORIG_HEAD" in source
    assert "update-index" in source
    assert "--skip-worktree" in source
    assert "tar.exe" in source
    assert '"-a"' in source
    assert "finally" in source
    assert "[IO.Directory]::Delete" in source
    assert "[IO.Directory]::Exists($extendedPath)" in source
    assert "[IO.FileAttributes]::ReadOnly" in source
    assert "\\\\?\\UNC\\" in source
    assert "\\\\?\\" in source
    assert "InterviewAssistant-source-$Version" in source
    assert "INTERVIEW_ASSISTANT_ARCHIVE_TEMP" in source
    assert "too long for Git on Windows" in source
    assert "GetTempPath" not in source
    assert "7z" not in source.casefold()
    assert source.index('"clone"') < source.index("Get-FileHash")


def test_source_archive_is_standalone_sanitized_and_model_overlay_is_exact(
    tmp_path: Path,
) -> None:
    repository, model_path, model_files, head, secret_commit, secret_blob = (
        _create_source_repository(tmp_path)
    )
    assert (repository / ".git").is_file(), "fixture must exercise a linked worktree source"
    source_fsck = _git(
        "fsck",
        "--no-reflogs",
        "--unreachable",
        "--no-progress",
        cwd=repository,
    ).stdout
    assert f"unreachable commit {secret_commit}" in source_fsck
    assert f"unreachable blob {secret_blob}" in source_fsck
    _write_dirty_worktree_manifest(repository, model_path)
    output_path = tmp_path / "release output" / "portable source.zip"
    status_before = _git("status", "--porcelain=v1", "--untracked-files=all", cwd=repository).stdout
    remote_before = _git("remote", "get-url", "origin", cwd=repository).stdout
    staging_parent = _archive_temp_directory(repository)
    staging_before = set(staging_parent.glob("InterviewAssistant-source-staging-*"))

    result = _run_archive_script(repository, model_path, output_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert output_path.is_file()
    assert str(output_path.resolve()) in result.stdout
    status_after = _git("status", "--porcelain=v1", "--untracked-files=all", cwd=repository).stdout
    assert status_after == status_before
    assert _git("remote", "get-url", "origin", cwd=repository).stdout == remote_before
    assert set(staging_parent.glob("InterviewAssistant-source-staging-*")) == staging_before

    with zipfile.ZipFile(output_path) as archive:
        names = archive.namelist()
        normalized = [name.replace("\\", "/") for name in names]
        top_levels = {name.split("/", maxsplit=1)[0] for name in normalized if name}
        assert top_levels == {"InterviewAssistant-source-9.8.7-test"}
        prefix = "InterviewAssistant-source-9.8.7-test/"
        assert prefix + ".git/HEAD" in normalized
        assert prefix + ".git/config" in normalized
        assert prefix + "interview_assistant/app.py" in normalized
        app_name = names[normalized.index(prefix + "interview_assistant/app.py")]
        assert archive.read(app_name).decode("utf-8").strip() == "VALUE = 'tracked-head'"
        assert prefix + "docs/superpowers/plans/implementation.md" in normalized
        assert prefix + "packaging/stt_model_manifest.json" in normalized
        manifest_name = names[normalized.index(prefix + "packaging/stt_model_manifest.json")]
        archived_manifest = json.loads(archive.read(manifest_name))
        assert "dirty_marker" not in archived_manifest
        assert [entry["path"] for entry in archived_manifest["files"]] == list(model_files)
        assert prefix + "scripts/build.ps1" in normalized
        assert prefix + "prompts/interview_system.md" in normalized
        assert prefix + "config.yaml" in normalized
        config_name = names[normalized.index(prefix + "config.yaml")]
        archived_config_text = archive.read(config_name).decode("utf-8")
        archived_config = yaml.safe_load(archived_config_text)
        assert archived_config["audio"]["system_device_id"] is None
        assert archived_config["audio"]["microphone_device_id"] is None
        assert archived_config["lmstudio"]["host"] == "127.0.0.1"
        assert archived_config["lmstudio"]["text_model"] == ""
        assert archived_config["lmstudio"]["vision_model"] == ""
        assert archived_config["hotkeys"] == EXPECTED_RELEASE_HOTKEYS
        assert "checked-in-runtime-value" not in archived_config_text
        assert "uncommitted-runtime-secret" not in archived_config_text
        for forbidden in (
            ".venv/",
            "build/",
            "dist/",
            ".cache/",
            ".superpowers/",
            "logs/",
            "screenshots/",
            "credentials.reg",
            "lmstudio-token.txt",
            "untracked.txt",
            "credential-secret.txt",
        ):
            assert not any(name.startswith(prefix + forbidden) for name in normalized)
        model_prefix = prefix + "models/stt/large-v3-turbo/"
        for name, content in model_files.items():
            archive_name = model_prefix + name
            assert archive_name in normalized
            assert archive.read(names[normalized.index(archive_name)]) == content
        assert model_prefix + "unlisted.bin" not in normalized
        assert not any(".cache/huggingface" in name.casefold() for name in normalized)
        git_config_name = names[normalized.index(prefix + ".git/config")]
        git_config = archive.read(git_config_name).decode("utf-8")
        assert '[remote "origin"]' not in git_config
        assert str(repository) not in git_config
        assert "Secret Fixture Identity" not in git_config
        assert prefix + ".git/objects/info/alternates" not in normalized
        assert not any(name.startswith(prefix + ".git/logs/") for name in normalized)
        assert not any(name.startswith(prefix + ".git/refs/remotes/") for name in normalized)
        assert prefix + ".git/FETCH_HEAD" not in normalized
        assert prefix + ".git/ORIG_HEAD" not in normalized

    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(output_path) as archive:
        archive.extractall(extracted)
    extracted_repository = extracted / "InterviewAssistant-source-9.8.7-test"
    assert _git("rev-parse", "HEAD", cwd=extracted_repository).stdout.strip() == head
    assert _git("symbolic-ref", "--short", "HEAD", cwd=extracted_repository).stdout.strip() == (
        "main"
    )
    assert _git("remote", cwd=extracted_repository).stdout.strip() == ""
    assert _git("status", "--porcelain=v1", cwd=extracted_repository).stdout.strip() == ""
    _git("cat-file", "-e", "HEAD^{commit}", cwd=extracted_repository)
    fsck_output = _git(
        "fsck",
        "--no-reflogs",
        "--unreachable",
        "--no-progress",
        cwd=extracted_repository,
    )
    assert fsck_output.stdout == ""
    assert fsck_output.stderr == ""
    reachable_objects = _git("rev-list", "--objects", "--all", cwd=extracted_repository).stdout
    assert "credential-secret.txt" not in reachable_objects
    identities = _git(
        "log",
        "--all",
        "--format=%an <%ae>",
        cwd=extracted_repository,
    ).stdout
    assert "Secret Fixture Identity" not in identities
    assert "secret-fixture@example.invalid" not in identities
    secret_lookup = subprocess.run(
        ["git", "cat-file", "-e", f"{secret_commit}^{{commit}}"],
        cwd=extracted_repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert secret_lookup.returncode != 0
    secret_blob_lookup = subprocess.run(
        ["git", "cat-file", "-e", secret_blob],
        cwd=extracted_repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert secret_blob_lookup.returncode != 0
    assert not (extracted_repository / ".git" / "logs").exists()
    assert not (extracted_repository / ".git" / "FETCH_HEAD").exists()
    assert not (extracted_repository / ".git" / "ORIG_HEAD").exists()


def test_reachable_history_scanner_rejects_deleted_credential_without_echoing_it(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "history"
    repository.mkdir()
    _git("init", "-b", "main", cwd=repository)
    _git("config", "user.name", "Release Test", cwd=repository)
    _git("config", "user.email", "release-test@example.invalid", cwd=repository)
    (repository / "README.md").write_text("clean\n", encoding="utf-8")
    _git("add", ".", cwd=repository)
    _git("commit", "-m", "clean", cwd=repository)
    fake_credential = "sk-" + ("A" * 32)
    (repository / "deleted.txt").write_text(fake_credential, encoding="utf-8")
    _git("add", "deleted.txt", cwd=repository)
    _git("commit", "-m", "temporary file", cwd=repository)
    _git("rm", "deleted.txt", cwd=repository)
    _git("commit", "-m", "delete temporary file", cwd=repository)

    result = subprocess.run(
        [sys.executable, str(HISTORY_SCANNER_PATH), "--repository", str(repository)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert fake_credential not in result.stdout + result.stderr
    assert "reachable Git history failed the release privacy scan" in result.stderr


def test_source_archive_rejects_credential_in_reachable_history(tmp_path: Path) -> None:
    repository, model_path, _files, _head, _secret_commit, _secret_blob = (
        _create_source_repository(tmp_path)
    )
    _git("restore", "config.yaml", "interview_assistant/app.py", cwd=repository)
    fake_credential = "sk-" + ("B" * 32)
    (repository / "historic-data.txt").write_text(fake_credential, encoding="utf-8")
    _git("add", "historic-data.txt", cwd=repository)
    _git("commit", "-m", "temporary credential fixture", cwd=repository)
    _git("rm", "historic-data.txt", cwd=repository)
    _git("commit", "-m", "remove credential fixture", cwd=repository)
    source_commit = _git("rev-parse", "HEAD", cwd=repository).stdout.strip()
    output_path = tmp_path / "must-not-exist.zip"

    result = _run_archive_script(
        repository,
        model_path,
        output_path,
        source_commit=source_commit,
    )

    assert result.returncode != 0
    assert not output_path.exists()
    assert fake_credential not in result.stdout + result.stderr


def test_source_archive_uses_explicit_pinned_ancestor_commit(tmp_path: Path) -> None:
    repository, model_path, _files, source_commit, _secret_commit, _secret_blob = (
        _create_source_repository(tmp_path)
    )
    _git("restore", "config.yaml", "interview_assistant/app.py", cwd=repository)
    (repository / "docs" / "after-release.md").write_text(
        "metadata after pinned release\n",
        encoding="utf-8",
    )
    _git("add", "docs/after-release.md", cwd=repository)
    _git("commit", "-m", "metadata after release", cwd=repository)
    output_path = tmp_path / "pinned.zip"

    result = _run_archive_script(
        repository,
        model_path,
        output_path,
        source_commit=source_commit,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    extracted = tmp_path / "pinned-extracted"
    with zipfile.ZipFile(output_path) as archive:
        archive.extractall(extracted)
    archived_repository = extracted / "InterviewAssistant-source-9.8.7-test"
    assert _git("rev-parse", "HEAD", cwd=archived_repository).stdout.strip() == source_commit
    assert not (archived_repository / "docs" / "after-release.md").exists()
    inspection = subprocess.run(
        [
            sys.executable,
            str(ARCHIVE_INSPECTOR_PATH),
            "--archive",
            str(output_path),
            "--manifest",
            str(repository / "packaging" / "stt_model_manifest.json"),
            "--expected-commit",
            source_commit,
            "--expected-top",
            "InterviewAssistant-source-9.8.7-test",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert inspection.returncode == 0, inspection.stdout + inspection.stderr
    assert json.loads(inspection.stdout)["git_head"] == source_commit


def _write_dist_fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, bytes]]:
    dist = tmp_path / "InterviewAssistant"
    model_dir = dist / "_internal" / "models" / "stt" / "large-v3-turbo"
    model_dir.mkdir(parents=True)
    (dist / "InterviewAssistant.exe").write_bytes(b"fixture executable")
    model_files = {
        "config.json": b"{}\n",
        "model.bin": b"fixture-model",
        "README.md": b"fixture readme\n",
    }
    for name, content in model_files.items():
        (model_dir / name).write_bytes(content)
    manifest = {
        "schema_version": 1,
        "bundle_subdirectory": "models/stt/large-v3-turbo",
        "files": [
            _model_entry(name, content, runtime_required=name != "README.md")
            for name, content in model_files.items()
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    inventory = {
        "schema_version": 1,
        "application": "InterviewAssistant",
        "files": [
            {
                "path": path.relative_to(dist).as_posix(),
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(item for item in dist.rglob("*") if item.is_file())
        ],
    }
    inventory_path = tmp_path / "dist-inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    return dist, manifest_path, inventory_path, model_files


def _run_dist_validator(
    dist: Path,
    manifest: Path,
    inventory: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(DIST_VALIDATOR_PATH),
            "--dist",
            str(dist),
            "--manifest",
            str(manifest),
            "--inventory",
            str(inventory),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_installer_dist_validator_rejects_modified_model(tmp_path: Path) -> None:
    dist, manifest, inventory, _files = _write_dist_fixture(tmp_path)
    (dist / "_internal" / "models" / "stt" / "large-v3-turbo" / "model.bin").write_bytes(
        b"modified-model"
    )

    result = _run_dist_validator(dist, manifest, inventory)

    assert result.returncode != 0
    assert "distribution failed release validation" in result.stderr


def test_installer_dist_validator_rejects_extra_suspicious_file(tmp_path: Path) -> None:
    dist, manifest, inventory, _files = _write_dist_fixture(tmp_path)
    (dist / "_internal" / "config.yaml").write_text("machine: private\n", encoding="utf-8")

    result = _run_dist_validator(dist, manifest, inventory)

    assert result.returncode != 0
    assert "distribution failed release validation" in result.stderr


def test_installer_dist_validator_rejects_extra_stt_file(tmp_path: Path) -> None:
    dist, manifest, inventory, _files = _write_dist_fixture(tmp_path)
    model_dir = dist / "_internal" / "models" / "stt" / "large-v3-turbo"
    (model_dir / "extra.bin").write_bytes(b"extra model")

    result = _run_dist_validator(dist, manifest, inventory)

    assert result.returncode != 0
    assert "distribution failed release validation" in result.stderr


def test_release_archive_inspector_requires_pinned_expected_commit() -> None:
    source = ARCHIVE_INSPECTOR_PATH.read_text(encoding="utf-8")

    assert "--expected-commit" in source
    assert "required=True" in source
    assert "git rev-parse HEAD" not in source


def test_release_secret_stream_scanner_detects_binary_private_key_across_chunks() -> None:
    import scripts.scan_release_git_history as release_security

    private_key_marker = b"-----BEGIN " + b"PRIVATE KEY-----"
    payload = b"\x00\xffprefix" + private_key_marker + b"suffix\x00"

    assert release_security.stream_contains_release_secret(
        payload,
        chunk_size=7,
    )


def test_archive_inspector_scans_every_regular_file(
    inspectable_source_archive: dict[str, object],
) -> None:
    result = _run_archive_inspector(
        inspectable_source_archive["archive"],  # type: ignore[arg-type]
        inspectable_source_archive["manifest"],  # type: ignore[arg-type]
        inspectable_source_archive["source_commit"],  # type: ignore[arg-type]
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    with zipfile.ZipFile(inspectable_source_archive["archive"]) as archive:  # type: ignore[arg-type]
        expected_regular_files = sum(not entry.is_dir() for entry in archive.infolist())
    assert report["regular_files_scanned"] == expected_regular_files
    assert report["tracked_files_verified"] > 0


def test_archive_inspector_rejects_extra_credential_without_echoing_it(
    tmp_path: Path,
    inspectable_source_archive: dict[str, object],
) -> None:
    fake_credential = "sk-" + ("C" * 32)
    top = inspectable_source_archive["top"]
    tampered = tmp_path / "extra-credential.zip"
    _rewrite_archive(
        inspectable_source_archive["archive"],  # type: ignore[arg-type]
        tampered,
        additions=((f"{top}/extra-data.txt", fake_credential.encode("ascii")),),
    )

    result = _run_archive_inspector(
        tampered,
        inspectable_source_archive["manifest"],  # type: ignore[arg-type]
        inspectable_source_archive["source_commit"],  # type: ignore[arg-type]
    )

    assert result.returncode != 0
    assert fake_credential not in result.stdout + result.stderr


def test_archive_inspector_rejects_missing_tracked_file(
    tmp_path: Path,
    inspectable_source_archive: dict[str, object],
) -> None:
    top = inspectable_source_archive["top"]
    tampered = tmp_path / "missing-tracked.zip"
    _rewrite_archive(
        inspectable_source_archive["archive"],  # type: ignore[arg-type]
        tampered,
        omit=frozenset({f"{top}/docs/guide.md"}),
    )

    result = _run_archive_inspector(
        tampered,
        inspectable_source_archive["manifest"],  # type: ignore[arg-type]
        inspectable_source_archive["source_commit"],  # type: ignore[arg-type]
    )

    assert result.returncode != 0


def test_archive_inspector_rejects_replaced_tracked_file(
    tmp_path: Path,
    inspectable_source_archive: dict[str, object],
) -> None:
    top = inspectable_source_archive["top"]
    tampered = tmp_path / "replaced-tracked.zip"
    tracked_name = f"{top}/interview_assistant/app.py"
    _rewrite_archive(
        inspectable_source_archive["archive"],  # type: ignore[arg-type]
        tampered,
        replacements={tracked_name: b"VALUE = 'replaced'\n"},
    )

    result = _run_archive_inspector(
        tampered,
        inspectable_source_archive["manifest"],  # type: ignore[arg-type]
        inspectable_source_archive["source_commit"],  # type: ignore[arg-type]
    )

    assert result.returncode != 0


def test_archive_inspector_accepts_legacy_checkout_eol_conversion(
    tmp_path: Path,
) -> None:
    repository, model_path, _files, source_commit, _secret_commit, _secret_blob = (
        _create_source_repository(tmp_path, include_gitattributes=False)
    )
    original_archive = tmp_path / "legacy-original.zip"
    build_result = _run_archive_script(
        repository,
        model_path,
        original_archive,
        source_commit=source_commit,
    )
    assert build_result.returncode == 0, build_result.stdout + build_result.stderr
    top = "InterviewAssistant-source-9.8.7-test"
    tracked_name = f"{top}/interview_assistant/app.py"
    with zipfile.ZipFile(original_archive) as archive:
        original = archive.read(tracked_name)
    normalized = original.replace(b"\r\n", b"\n")
    legacy_archive = tmp_path / "legacy-checkout-eol.zip"
    _rewrite_archive(
        original_archive,
        legacy_archive,
        replacements={tracked_name: normalized.replace(b"\n", b"\r\n")},
    )

    result = _run_archive_inspector(
        legacy_archive,
        repository / "packaging" / "stt_model_manifest.json",
        source_commit,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("unsafe_kind", ["symlink", "traversal"])
def test_archive_inspector_rejects_unsafe_entries(
    tmp_path: Path,
    inspectable_source_archive: dict[str, object],
    unsafe_kind: str,
) -> None:
    top = inspectable_source_archive["top"]
    if unsafe_kind == "symlink":
        name: zipfile.ZipInfo | str = zipfile.ZipInfo(f"{top}/unsafe-link")
        name.create_system = 3
        name.external_attr = (stat.S_IFLNK | 0o777) << 16
        payload = b"README.md"
    else:
        name = f"{top}/../escaped.txt"
        payload = b"escape"
    tampered = tmp_path / f"unsafe-{unsafe_kind}.zip"
    _rewrite_archive(
        inspectable_source_archive["archive"],  # type: ignore[arg-type]
        tampered,
        additions=((name, payload),),
    )

    result = _run_archive_inspector(
        tampered,
        inspectable_source_archive["manifest"],  # type: ignore[arg-type]
        inspectable_source_archive["source_commit"],  # type: ignore[arg-type]
    )

    assert result.returncode != 0


def test_extracted_archive_checkout_is_clean_for_all_autocrlf_modes(
    tmp_path: Path,
    inspectable_source_archive: dict[str, object],
) -> None:
    for mode in ("false", "true", "input"):
        extracted = tmp_path / mode
        with zipfile.ZipFile(inspectable_source_archive["archive"]) as archive:  # type: ignore[arg-type]
            archive.extractall(extracted)
        repository = extracted / str(inspectable_source_archive["top"])
        _git("config", "core.autocrlf", mode, cwd=repository)
        assert _git("status", "--porcelain=v1", cwd=repository).stdout == ""


def test_release_gitattributes_make_text_checkout_deterministic() -> None:
    source = GITATTRIBUTES_PATH.read_text(encoding="utf-8")

    assert "* text=auto eol=lf" in source
    for pattern in ("*.bin", "*.dll", "*.exe", "*.ico", "*.png", "*.pyd", "*.wav", "*.zip"):
        assert f"{pattern} binary" in source


def test_source_archive_rejects_unsupported_long_staging_without_leaking_staging(
    tmp_path: Path,
) -> None:
    repository, model_path, _model_files, _head, _secret_commit, _secret_blob = (
        _create_source_repository(tmp_path)
    )
    original_model = (model_path / "model.bin").read_bytes()
    corrupted_model = bytes((original_model[0] ^ 1,)) + original_model[1:]
    (model_path / "model.bin").write_bytes(corrupted_model)
    output_path = tmp_path / "must-not-exist.zip"
    staging_parent = tmp_path.parent
    for index in range(3):
        staging_parent /= f"long-temp-{index}-" + ("x" * 70)
    assert len(str(staging_parent)) > 260
    long_root = tmp_path.parent / ("long-temp-0-" + ("x" * 70))
    before = _staging_directory_names(staging_parent)

    try:
        result = _run_archive_script(
            repository,
            model_path,
            output_path,
            temporary_directory=staging_parent,
        )

        assert result.returncode != 0
        assert not output_path.exists()
        assert _staging_directory_names(staging_parent) == before
        assert "too long for Git on Windows" in result.stderr or (
            "too long for Git on Windows" in result.stdout
        )
        assert "cleanup failed" not in (result.stdout + result.stderr).casefold()
        assert "RemoveFileSystemItemIOError" not in result.stderr
    finally:
        shutil.rmtree(_extended_windows_path(long_root), ignore_errors=True)


def test_portable_release_guide_documents_migration_and_unsigned_boundaries() -> None:
    assert PORTABLE_DOC_PATH.is_file(), "The portable release guide has not been created"
    source = PORTABLE_DOC_PATH.read_text(encoding="utf-8")
    folded = source.casefold()

    assert "%LOCALAPPDATA%\\InterviewAssistant\\InterviewAssistant\\config.yaml" in source
    assert "while the app is closed" in folded
    assert "credential manager" in folded
    assert "neither exported nor archived" in folded
    assert "enter" in folded and "again" in folded
    assert "audio device" in folded and "machine-specific" in folded
    assert "lm studio" in folded and "model availability" in folded
    assert "large-v3-turbo" in source
    assert "ru/en" in folded
    assert "captured source branch" in folded
    assert "unreachable git objects" in folded
    assert "git history" in folded
    assert ".venv" in source
    assert "uv sync --extra dev --frozen" in source
    assert "unsigned" in folded
    assert "sha256sums.txt" in folded
    assert "windows" in folded and "warn" in folded
    assert "rtx 5070 ti" in folded
    assert "amd" in folded
    assert "cuda" in folded and "not" in folded and "tested" in folded
    assert "upgrade" in folded and "uninstall" in folded and "preserve" in folded
