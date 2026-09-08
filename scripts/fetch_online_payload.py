"""Fetch pinned inputs for the release builder; end users use native Inno download.

This module is build tooling only and is never included in the application.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def digest_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_file(path: Path, digest: str, size: int) -> bool:
    if not path.exists():
        return False
    info = path.lstat()
    if path.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("Cache entries must not be links or reparse points")
    return path.is_file() and info.st_size == size and digest_file(path) == digest


def fetch_artifact(entry: dict, cache: Path, *, opener=urllib.request.urlopen,
                   attempts: int = 3, seed: Path | None = None) -> str:
    url = urlsplit(entry["url"])
    digest, size, name = entry["sha256"], entry["size"], entry["cache_name"]
    if (url.scheme != "https" or url.hostname not in {"files.pythonhosted.org", "huggingface.co"}
            or url.username or url.password or url.port not in (None, 443)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or type(size) is not int or size < 0
            or not re.fullmatch(re.escape(digest) + r"\.(zip|bin)", name)):
        raise ValueError("Invalid pinned download artifact")
    cache.mkdir(parents=True, exist_ok=True)
    for parent in (cache, *cache.parents):
        info = parent.lstat()
        if parent.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("Cache path must not contain reparse points")
    destination = cache / name
    partial = cache / (name + ".partial")
    if checked_file(destination, digest, size):
        return "cached"
    if partial.exists():
        checked_file(partial, digest, size)  # Reject links before unlink/open.
        partial.unlink()
    if seed is not None and checked_file(seed, digest, size):
        shutil.copyfile(seed, partial)
        if not checked_file(partial, digest, size):
            partial.unlink()
            raise ValueError("Seed copy verification failed")
        partial.replace(destination)
        return "seeded"
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(entry["url"], headers={
                "User-Agent": "InterviewAssistant-ReleaseBuilder/0.1.3",
                "Accept-Encoding": "identity",
            })
            received = 0
            with opener(request, timeout=60) as response, partial.open("xb") as target:
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > size:
                        raise ValueError("Download exceeds pinned size")
                    target.write(chunk)
            if not checked_file(partial, digest, size):
                raise ValueError("Download verification failed")
            partial.replace(destination)
            return "downloaded"
        except (OSError, urllib.error.URLError, ValueError):
            if attempt + 1 == attempts:
                raise
            time.sleep(min(attempt + 1, 3))
        finally:
            if partial.exists():
                partial.unlink()
    raise ValueError("Download attempts must be positive")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--dist", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    started = time.monotonic()
    for index, entry in enumerate(payload["artifacts"], 1):
        seed = None
        if args.dist and entry["kind"] == "file":
            matches = [f for f in payload["files"] if f["artifact"] == entry["id"]]
            if len(matches) == 1:
                candidate = args.dist / matches[0]["path"]
                if candidate.resolve().is_relative_to(args.dist.resolve()):
                    seed = candidate
        print(f"[{index}/{len(payload['artifacts'])}] {entry['display_name']}", flush=True)
        result = fetch_artifact(entry, args.cache, seed=seed)
        print(f"  {result}", flush=True)
    print(f"Verified cache ready in {time.monotonic() - started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
