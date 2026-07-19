from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import yaml

from scan_release_git_history import HistoryScanError, scan_reachable_history
from validate_source_release_config import EXPECTED_CONFIG


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


def _digest_entry(archive: zipfile.ZipFile, name: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with archive.open(name) as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


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
            entries = archive.infolist()
            names = [entry.filename.replace("\\", "/") for entry in entries]
            if not entries or len(names) != len(set(names)):
                raise RuntimeError("source archive inventory is invalid")
            for entry, name in zip(entries, names, strict=True):
                parts = PurePosixPath(name).parts
                file_type = (entry.external_attr >> 16) & 0o170000
                if not parts or name.startswith("/") or ".." in parts or file_type == 0o120000:
                    raise RuntimeError("source archive contains an unsafe entry")
            top_levels = {PurePosixPath(name).parts[0] for name in names if name}
            if len(top_levels) != 1:
                raise RuntimeError("source archive must have one top-level directory")
            actual_top = next(iter(top_levels))
            if expected_top is not None and actual_top != expected_top:
                raise RuntimeError("source archive top-level directory does not match")
            prefix = f"{actual_top}/"
            config_name = f"{prefix}config.yaml"
            if config_name not in names:
                raise RuntimeError("source archive is missing sanitized config")
            if yaml.safe_load(archive.read(config_name).decode("utf-8")) != EXPECTED_CONFIG:
                raise RuntimeError("source archive sanitized config is invalid")

            forbidden_segments = {
                ".cache",
                ".superpowers",
                ".venv",
                "__pycache__",
                "build",
                "dist",
                "logs",
                "screenshots",
            }
            for name in names:
                relative_parts = PurePosixPath(name).parts[1:]
                if any(part.casefold() in forbidden_segments for part in relative_parts):
                    raise RuntimeError("source archive contains forbidden release data")

            model_prefix = f"{prefix}{manifest['bundle_subdirectory']}/"
            expected_models = {
                f"{model_prefix}{entry['path']}": entry for entry in manifest["files"]
            }
            actual_models = {
                name for name in names if name.startswith(model_prefix) and not name.endswith("/")
            }
            if actual_models != set(expected_models):
                raise RuntimeError("source archive STT inventory does not match manifest")
            for name, expected in expected_models.items():
                size, digest = _digest_entry(archive, name)
                if size != expected["size"] or digest.casefold() != expected["sha256"].casefold():
                    raise RuntimeError("source archive STT file does not match manifest")

            extracted_root = temporary / actual_top
            for entry, name in zip(entries, names, strict=True):
                if name == config_name or name.startswith(f"{prefix}.git/"):
                    archive.extract(entry, temporary)

        if not extracted_root.is_dir() or not (extracted_root / ".git").is_dir():
            raise RuntimeError("source archive is missing standalone Git history")
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
        return {
            "status": "ok",
            "archive_bytes": archive_path.stat().st_size,
            "entry_count": len(names),
            "git_head": archive_head,
            "reachable_objects_scanned": scanned_objects,
            "stt_file_count": len(expected_models),
            "top_level": actual_top,
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
