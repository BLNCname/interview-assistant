from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO


PRIVATE_KEY_PATTERN = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
)
TOKEN_PATTERNS = (
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(
        rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
    ),
)


class HistoryScanError(RuntimeError):
    pass


def _git(repository: Path, *arguments: str, text: bool = False) -> bytes | str:
    try:
        process = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=False,
            capture_output=True,
            text=text,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HistoryScanError from error
    if process.returncode != 0:
        raise HistoryScanError
    return process.stdout


def contains_release_secret(payload: bytes) -> bool:
    return PRIVATE_KEY_PATTERN.search(payload) is not None or any(
        pattern.search(payload) is not None for pattern in TOKEN_PATTERNS
    )


def stream_contains_release_secret(
    source: bytes | BinaryIO,
    *,
    chunk_size: int = 1024 * 1024,
) -> bool:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    stream = io.BytesIO(source) if isinstance(source, bytes) else source
    overlap = b""
    while chunk := stream.read(chunk_size):
        payload = overlap + chunk
        if contains_release_secret(payload):
            return True
        overlap = payload[-256:]
    return False


def _run_batch(repository: Path, arguments: list[str], payload: bytes) -> bytes:
    try:
        process = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            input=payload,
            check=False,
            capture_output=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HistoryScanError from error
    if process.returncode != 0:
        raise HistoryScanError
    return process.stdout


def _object_inventory(repository: Path, object_ids: tuple[str, ...]) -> list[tuple[str, str, int]]:
    raw = _run_batch(
        repository,
        ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        ("\n".join(object_ids) + "\n").encode("ascii"),
    )
    lines = raw.decode("ascii").splitlines()
    if len(lines) != len(object_ids):
        raise HistoryScanError
    inventory: list[tuple[str, str, int]] = []
    for expected_id, line in zip(object_ids, lines, strict=True):
        fields = line.split()
        if len(fields) != 3 or fields[0].casefold() != expected_id.casefold():
            raise HistoryScanError
        object_type = fields[1]
        if object_type not in {"blob", "commit", "tag", "tree"}:
            raise HistoryScanError
        try:
            size = int(fields[2])
        except ValueError as error:
            raise HistoryScanError from error
        if size < 0:
            raise HistoryScanError
        inventory.append((expected_id, object_type, size))
    return inventory


def _scan_batch_contents(
    repository: Path,
    inventory: list[tuple[str, str, int]],
) -> int:
    selected = [entry for entry in inventory if entry[1] != "tree"]
    raw = _run_batch(
        repository,
        ["cat-file", "--batch"],
        ("\n".join(entry[0] for entry in selected) + "\n").encode("ascii"),
    )
    offset = 0
    for expected_id, expected_type, expected_size in selected:
        newline = raw.find(b"\n", offset)
        if newline < 0:
            raise HistoryScanError
        try:
            header = raw[offset:newline].decode("ascii").split()
        except UnicodeDecodeError as error:
            raise HistoryScanError from error
        if (
            len(header) != 3
            or header[0].casefold() != expected_id.casefold()
            or header[1] != expected_type
            or int(header[2]) != expected_size
        ):
            raise HistoryScanError
        start = newline + 1
        end = start + expected_size
        if end >= len(raw) or raw[end : end + 1] != b"\n":
            raise HistoryScanError
        if contains_release_secret(raw[start:end]):
            raise HistoryScanError
        offset = end + 1
    if offset != len(raw):
        raise HistoryScanError
    return len(selected)


def scan_reachable_history(repository: Path, expected_commit: str | None = None) -> int:
    repository = repository.absolute()
    if not repository.is_dir():
        raise HistoryScanError
    _git(repository, "rev-parse", "--git-dir")
    if expected_commit is not None:
        if re.fullmatch(r"[0-9a-fA-F]{40,64}", expected_commit) is None:
            raise HistoryScanError
        resolved = str(
            _git(repository, "rev-parse", "--verify", f"{expected_commit}^{{commit}}", text=True)
        ).strip()
        if resolved.casefold() != expected_commit.casefold():
            raise HistoryScanError
        reachable = subprocess.run(
            ["git", "-C", str(repository), "merge-base", "--is-ancestor", expected_commit, "HEAD"],
            check=False,
            capture_output=True,
            timeout=60,
        )
        if reachable.returncode != 0:
            raise HistoryScanError

    object_lines = str(_git(repository, "rev-list", "--objects", "--all", text=True)).splitlines()
    object_ids = tuple(dict.fromkeys(line.split(maxsplit=1)[0] for line in object_lines if line))
    if not object_ids:
        raise HistoryScanError

    for object_id in object_ids:
        if re.fullmatch(r"[0-9a-fA-F]{40,64}", object_id) is None:
            raise HistoryScanError
    scanned = _scan_batch_contents(repository, _object_inventory(repository, object_ids))
    if scanned == 0:
        raise HistoryScanError
    return scanned


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--expected-commit")
    arguments = parser.parse_args()
    try:
        scan_reachable_history(arguments.repository, arguments.expected_commit)
    except HistoryScanError:
        print(
            "reachable Git history failed the release privacy scan",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
