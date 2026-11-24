"""Create the model-free Windows portable release from verified runtime files."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile


def build_archives(dist: Path, payload_file: Path, output: Path, version: str) -> list[Path]:
    payload = json.loads(payload_file.read_text(encoding="utf-8"))
    if not payload.get("artifacts_verified") or dist.name != "InterviewAssistant":
        raise ValueError("A verified online payload and its portable tree are required")
    external = {item["id"] for item in payload["artifacts"]
                if item.get("cuda") or item.get("kind") == "file"}
    if not all(part.isdecimal() for part in version.split(".")) or len(version.split(".")) != 3:
        raise ValueError("Expected a numeric release version")
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"InterviewAssistant-{version}-win64-portable.zip"
    partial = target.with_suffix(".zip.partial")
    if target.exists() or partial.exists():
        raise FileExistsError("Use a fresh archive output directory")
    try:
        with zipfile.ZipFile(partial, "x", zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
            for item in payload["files"]:
                relative = PurePosixPath(item["path"])
                if item["artifact"] in external:
                    continue
                if (relative.as_posix().casefold().startswith(("_internal/models/", "_internal/nvidia/"))
                        or relative.suffix.lower() in {".bin", ".onnx", ".gguf", ".pt", ".ckpt", ".pth", ".safetensors"}):
                    raise ValueError(f"Unexpected model weights or GPU runtime in portable release: {relative}")
                src = (dist / relative).resolve(strict=True)
                if not src.is_relative_to(dist.resolve()) or relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Invalid portable file path")
                digest = hashlib.sha256()
                with src.open("rb") as source, archive.open(f"{dist.name}/{relative.as_posix()}", "w",
                                                            force_zip64=item["size"] >= 2**31) as dest:
                    while chunk := source.read(1024 * 1024):
                        dest.write(chunk)
                        digest.update(chunk)
                if src.stat().st_size != item["size"] or digest.hexdigest() != item["sha256"]:
                    raise ValueError(f"Portable file differs from inventory: {relative}")
        if partial.stat().st_size >= 2**31:
            raise ValueError("Archive exceeds the per-asset GitHub limit")
        partial.rename(target)
        return [target]
    finally:
        partial.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("dist", "payload", "output"):
        parser.add_argument("--" + argument, type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    for archive in build_archives(args.dist, args.payload, args.output, args.version):
        print(archive)
