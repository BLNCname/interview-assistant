"""Create an offline Windows portable ZIP and its optional NVIDIA libraries."""

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile


def build_archives(dist: Path, payload_file: Path, output: Path, version: str) -> list[Path]:
    payload = json.loads(payload_file.read_text(encoding="utf-8"))
    if not payload.get("artifacts_verified") or dist.name != "InterviewAssistant":
        raise ValueError("A verified online payload and its portable tree are required")
    cuda = {item["id"] for item in payload["artifacts"] if item.get("cuda")}
    if not cuda:
        raise ValueError("The CUDA component is missing from the verified payload")
    if not all(part.isdecimal() for part in version.split(".")) or len(version.split(".")) != 3:
        raise ValueError("Expected a numeric release version")
    output.mkdir(parents=True, exist_ok=True)
    names = [output / f"InterviewAssistant-{version}-win64-{kind}.zip" for kind in ("portable", "cuda")]
    partials = [p.with_suffix(".zip.partial") for p in names]
    if any(p.exists() for p in names + partials):
        raise FileExistsError("Use a fresh archive output directory")
    try:
        with ExitStack() as stack:
            archives = [stack.enter_context(zipfile.ZipFile(p, "x", zipfile.ZIP_DEFLATED, compresslevel=5))
                        for p in partials]
            for item in payload["files"]:
                relative = PurePosixPath(item["path"])
                src = (dist / relative).resolve(strict=True)
                if not src.is_relative_to(dist.resolve()) or relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Invalid portable file path")
                archive = archives[int(item["artifact"] in cuda)]
                digest = hashlib.sha256()
                with src.open("rb") as source, archive.open(f"{dist.name}/{relative.as_posix()}", "w",
                                                            force_zip64=item["size"] >= 2**31) as dest:
                    while chunk := source.read(1024 * 1024):
                        dest.write(chunk)
                        digest.update(chunk)
                if src.stat().st_size != item["size"] or digest.hexdigest() != item["sha256"]:
                    raise ValueError(f"Portable file differs from inventory: {relative}")
        if any(p.stat().st_size >= 2**31 for p in partials):
            raise ValueError("Archive exceeds the per-asset GitHub limit")
        for partial, target in zip(partials, names, strict=True):
            partial.rename(target)
        return names
    finally:
        for path in partials:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("dist", "payload", "output"):
        parser.add_argument("--" + argument, type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    for archive in build_archives(args.dist, args.payload, args.output, args.version):
        print(archive)
