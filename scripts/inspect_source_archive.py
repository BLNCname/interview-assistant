from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml

from scan_release_git_history import (
    HistoryScanError,
    scan_reachable_history,
    stream_contains_release_secret,
)
from validate_source_release_config import EXPECTED_CONFIG


WINDOWS_RESERVED_NAMES = frozenset(
    {"aux", "con", "nul", "prn"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
FORBIDDEN_SEGMENTS = frozenset(
    {
        ".cache",
        ".superpowers",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "logs",
        "screenshots",
    }
)


def _git(repository: Path, *arguments: str, allowed: tuple[int, ...] = (0,)) -> str:
    process = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if process.returncode not in allowed:
        raise RuntimeError("source archive Git inspection failed")
    return (process.stdout + process.stderr).strip()


def _git_bytes(repository: Path, *arguments: str) -> bytes:
    process = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        timeout=60,
    )
    if process.returncode != 0:
        raise RuntimeError("source archive Git inspection failed")
    return process.stdout


def _digest_path(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _validated_member_path(entry: zipfile.ZipInfo) -> PurePosixPath:
    raw_name = entry.filename
    if (
        not raw_name
        or "\x00" in raw_name
        or "\\" in raw_name
        or raw_name.startswith("/")
        or PureWindowsPath(raw_name).drive
        or entry.flag_bits & 0x1
    ):
        raise RuntimeError("source archive contains an unsafe entry")
    path = PurePosixPath(raw_name)
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError("source archive contains an unsafe entry")
    for part in parts:
        stem = part.split(".", maxsplit=1)[0].casefold()
        if (
            part.endswith((" ", "."))
            or ":" in part
            or stem in WINDOWS_RESERVED_NAMES
        ):
            raise RuntimeError("source archive contains an unsafe entry")

    unix_type = (entry.external_attr >> 16) & 0o170000
    windows_attributes = entry.external_attr & 0xFFFF
    if windows_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise RuntimeError("source archive contains an unsafe entry")
    if entry.is_dir():
        if not raw_name.endswith("/") or unix_type not in {0, stat.S_IFDIR}:
            raise RuntimeError("source archive contains an unsafe entry")
    elif raw_name.endswith("/") or unix_type not in {0, stat.S_IFREG}:
        raise RuntimeError("source archive contains an unsafe entry")
    return path


def _extract_all_members(
    archive: zipfile.ZipFile,
    entries: list[tuple[zipfile.ZipInfo, PurePosixPath]],
    destination: Path,
) -> None:
    destination_resolved = destination.resolve()
    for entry, member_path in entries:
        target = destination.joinpath(*member_path.parts)
        target_resolved = target.resolve(strict=False)
        try:
            target_resolved.relative_to(destination_resolved)
        except ValueError as error:
            raise RuntimeError("source archive entry escaped inspection root") from error
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise RuntimeError("source archive extraction would overwrite an entry")
        with archive.open(entry) as source, target.open("xb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _walk_extracted_tree(root: Path) -> tuple[list[Path], list[Path]]:
    files: list[Path] = []
    directories: list[Path] = []
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as error:
            raise RuntimeError("source archive extraction could not be inspected") from error
        for entry in entries:
            path = Path(entry.path)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise RuntimeError("source archive extraction could not be inspected") from error
            if entry.is_symlink() or _is_reparse(info):
                raise RuntimeError("source archive extraction contains a link or reparse point")
            if stat.S_ISDIR(info.st_mode):
                directories.append(path)
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                files.append(path)
            else:
                raise RuntimeError("source archive extraction contains a special file")
    return files, directories


def _tracked_blobs(repository: Path, commit: str) -> dict[str, tuple[str, str]]:
    raw = _git_bytes(repository, "ls-tree", "-r", "-z", commit)
    tracked: dict[str, tuple[str, str]] = {}
    for record in raw.split(b"\x00"):
        if not record:
            continue
        try:
            metadata, path_bytes = record.split(b"\t", maxsplit=1)
            mode, object_type, object_id = metadata.decode("ascii").split()
            path = path_bytes.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise RuntimeError("pinned source tree inventory is invalid") from error
        normalized = PurePosixPath(path)
        key = normalized.as_posix().casefold()
        if (
            object_type != "blob"
            or mode not in {"100644", "100755"}
            or normalized.is_absolute()
            or any(part in {"", ".", ".."} for part in normalized.parts)
            or key in tracked
        ):
            raise RuntimeError("pinned source tree contains an unsupported entry")
        tracked[key] = (normalized.as_posix(), object_id)
    if not tracked:
        raise RuntimeError("pinned source tree is empty")
    return tracked


def _git_blob_id(path: Path, algorithm: str) -> str:
    size = path.stat().st_size
    digest = hashlib.new(algorithm)
    digest.update(f"blob {size}\0".encode("ascii"))
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _tracked_content_matches(
    repository: Path,
    path: Path,
    object_id: str,
    object_format: str,
    *,
    allow_legacy_eol: bool,
) -> bool:
    if _git_blob_id(path, object_format) == object_id:
        return True
    if not allow_legacy_eol:
        return False
    expected = _git_bytes(repository, "cat-file", "blob", object_id)
    if b"\x00" in expected or b"\r" in expected:
        return False
    actual = path.read_bytes()
    without_crlf = actual.replace(b"\r\n", b"")
    if b"\r" in without_crlf or b"\r\n" not in actual:
        return False
    return actual.replace(b"\r\n", b"\n") == expected


def _expected_directories(paths: set[str]) -> set[str]:
    return {
        parent.as_posix().casefold()
        for path in paths
        for parent in PurePosixPath(path).parents
        if parent.as_posix() != "."
    }


def inspect_archive(
    archive_path: Path,
    manifest_path: Path,
    expected_commit: str,
    expected_top: str | None,
) -> dict[str, object]:
    if re.fullmatch(r"[0-9a-fA-F]{40,64}", expected_commit) is None:
        raise RuntimeError("expected source commit is invalid")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    temporary = Path(tempfile.mkdtemp(prefix="interview-assistant-archive-inspect-"))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            raw_entries = archive.infolist()
            entries = [(entry, _validated_member_path(entry)) for entry in raw_entries]
            normalized_names = [path.as_posix() for _, path in entries]
            folded_names = [name.casefold() for name in normalized_names]
            if not entries or len(folded_names) != len(set(folded_names)):
                raise RuntimeError("source archive inventory is invalid")
            top_levels = {path.parts[0] for _, path in entries}
            if len(top_levels) != 1:
                raise RuntimeError("source archive must have one top-level directory")
            actual_top = next(iter(top_levels))
            if expected_top is not None and actual_top != expected_top:
                raise RuntimeError("source archive top-level directory does not match")
            for name in normalized_names:
                relative_parts = PurePosixPath(name).parts[1:]
                if any(part.casefold() in FORBIDDEN_SEGMENTS for part in relative_parts):
                    raise RuntimeError("source archive contains forbidden release data")
            _extract_all_members(archive, entries, temporary)

        extracted_root = temporary / actual_top
        if not extracted_root.is_dir() or not (extracted_root / ".git").is_dir():
            raise RuntimeError("source archive is missing standalone Git history")
        extracted_files, extracted_directories = _walk_extracted_tree(extracted_root)
        if len(extracted_files) != sum(not entry.is_dir() for entry in raw_entries):
            raise RuntimeError("source archive extraction inventory does not match ZIP inventory")

        regular_files_scanned = 0
        for path in extracted_files:
            with path.open("rb") as source:
                if stream_contains_release_secret(source):
                    raise RuntimeError("source archive failed the release privacy scan")
            regular_files_scanned += 1

        archive_head = _git(extracted_root, "rev-parse", "--verify", "HEAD")
        if archive_head.casefold() != expected_commit.casefold():
            raise RuntimeError("source archive commit does not match pinned commit")
        if _git(extracted_root, "remote"):
            raise RuntimeError("source archive retains a Git remote")
        if _git(extracted_root, "for-each-ref", "--format=%(refname)", "refs/remotes/"):
            raise RuntimeError("source archive retains remote refs")
        if _git(extracted_root, "reflog", "show", "--all"):
            raise RuntimeError("source archive retains reflogs")
        if _git(extracted_root, "fsck", "--no-reflogs", "--unreachable", "--no-progress"):
            raise RuntimeError("source archive retains unreachable Git objects")
        try:
            scanned_objects = scan_reachable_history(extracted_root, expected_commit)
        except HistoryScanError as error:
            raise RuntimeError("source archive reachable history failed privacy scan") from error

        tracked = _tracked_blobs(extracted_root, expected_commit)
        working_files = {
            path.relative_to(extracted_root).as_posix().casefold(): path
            for path in extracted_files
            if path.relative_to(extracted_root).parts[0].casefold() != ".git"
        }
        working_directories = {
            path.relative_to(extracted_root).as_posix().casefold()
            for path in extracted_directories
            if path.relative_to(extracted_root).parts[0].casefold() != ".git"
        }
        config_key = "config.yaml"
        config_path = working_files.get(config_key)
        if config_path is None:
            raise RuntimeError("source archive is missing sanitized config")
        try:
            config_payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise RuntimeError("source archive sanitized config is invalid") from error
        if config_payload != EXPECTED_CONFIG:
            raise RuntimeError("source archive sanitized config is invalid")

        bundle_path = PurePosixPath(manifest["bundle_subdirectory"])
        expected_models = {
            (bundle_path / entry["path"]).as_posix().casefold(): entry
            for entry in manifest["files"]
        }
        expected_working_files = set(tracked) | set(expected_models)
        if set(working_files) != expected_working_files:
            raise RuntimeError("source archive working-tree inventory does not match pinned source")
        if working_directories != _expected_directories(expected_working_files):
            raise RuntimeError("source archive directory inventory does not match pinned source")

        object_format = _git(extracted_root, "rev-parse", "--show-object-format")
        if object_format not in {"sha1", "sha256"}:
            raise RuntimeError("source archive Git object format is unsupported")
        tracked_files_verified = 0
        allow_legacy_eol = ".gitattributes" not in tracked
        for key, (_relative, object_id) in tracked.items():
            if key != config_key and not _tracked_content_matches(
                extracted_root,
                working_files[key],
                object_id,
                object_format,
                allow_legacy_eol=allow_legacy_eol,
            ):
                raise RuntimeError("source archive tracked content does not match pinned source")
            tracked_files_verified += 1

        for key, expected in expected_models.items():
            size, digest = _digest_path(working_files[key])
            if size != expected["size"] or digest.casefold() != expected["sha256"].casefold():
                raise RuntimeError("source archive STT file does not match manifest")

        return {
            "status": "ok",
            "archive_bytes": archive_path.stat().st_size,
            "entry_count": len(normalized_names),
            "git_head": archive_head,
            "reachable_objects_scanned": scanned_objects,
            "regular_files_scanned": regular_files_scanned,
            "stt_file_count": len(expected_models),
            "top_level": actual_top,
            "tracked_files_verified": tracked_files_verified,
        }
    finally:
        shutil.rmtree(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-top")
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    report = inspect_archive(
        arguments.archive,
        arguments.manifest,
        arguments.expected_commit,
        arguments.expected_top,
    )
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.report is not None:
        arguments.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
