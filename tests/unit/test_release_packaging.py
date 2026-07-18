from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
ISS_PATH = ROOT / "packaging" / "interview_assistant.iss"
INSTALLER_SCRIPT_PATH = ROOT / "scripts" / "build_installer.ps1"
ARCHIVE_SCRIPT_PATH = ROOT / "scripts" / "create_source_archive.ps1"
PORTABLE_DOC_PATH = ROOT / "docs" / "portable-release.md"
APP_ID = "9CE7901A-56E8-49CB-A8ED-8D5CF4F97C7D"


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
    }
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
    temporary_directory: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    if temporary_directory is None:
        temporary_directory = _archive_temp_directory(repository)
    os.makedirs(_extended_windows_path(temporary_directory), exist_ok=True)
    environment = os.environ.copy()
    environment["INTERVIEW_ASSISTANT_ARCHIVE_TEMP"] = str(temporary_directory)
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
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
    )


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
    assert (
        "SetupIconFile={#SourcePath}\\..\\assets\\branding\\"
        "interview-assistant.ico"
    ) in source
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
    for downloader in ("Invoke-WebRequest", "Start-BitsTransfer", "winget", "choco"):
        assert downloader not in source


def test_source_archive_script_declares_fail_closed_git_and_zip_contract() -> None:
    assert ARCHIVE_SCRIPT_PATH.is_file(), "The source archive script has not been created"
    source = ARCHIVE_SCRIPT_PATH.read_text(encoding="utf-8")

    assert '$ErrorActionPreference = "Stop"' in source
    assert "Set-StrictMode -Version Latest" in source
    assert "[Parameter(Mandatory" in source
    for parameter in ("$SttModelPath", "$OutputPath", "$Version", "$RepositoryPath"):
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
        assert prefix + "config.yaml" not in normalized
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
