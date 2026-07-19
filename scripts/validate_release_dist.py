from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any


MODEL_SUFFIXES = frozenset({".bin", ".ckpt", ".gguf", ".onnx", ".pt", ".pth", ".safetensors"})
ALLOWLISTED_RUNTIME_MODELS = {
    "_internal/faster_whisper/assets/silero_vad_v6.onnx": (
        1_245_151,
        "4cbf549b8326f60f80f2536d9eefeb450a9abe83365a098031c89719f1be17d2",
    )
}
SUSPICIOUS_FILE_NAMES = frozenset(
    {
        ".env",
        ".netrc",
        ".npmrc",
        "config.yaml",
        "config.yml",
        "credential.json",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
        "interview_assistant.log",
        "mcp.json",
        "secret.json",
        "secrets.json",
        "token.json",
        "tokens.json",
    }
)
SUSPICIOUS_DIRECTORY_NAMES = frozenset(
    {".git", ".venv", "credentials", "logs", "screenshots", "secrets", "tokens"}
)
SUSPICIOUS_SUFFIXES = frozenset({".bak", ".log", ".p12", ".pfx", ".reg"})
class DistValidationError(RuntimeError):
    pass


def _is_reparse(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _check_node(path: Path, *, directory: bool | None = None) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as error:
        raise DistValidationError from error
    if path.is_symlink() or _is_reparse(info):
        raise DistValidationError
    if directory is True and not stat.S_ISDIR(info.st_mode):
        raise DistValidationError
    if directory is False and not stat.S_ISREG(info.st_mode):
        raise DistValidationError
    return info


def _safe_bundle_path(raw: Any) -> PurePosixPath:
    if not isinstance(raw, str):
        raise DistValidationError
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise DistValidationError
    return path


def _load_manifest(path: Path) -> tuple[PurePosixPath, dict[str, dict[str, Any]]]:
    _check_node(path, directory=False)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DistValidationError from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise DistValidationError
    bundle = _safe_bundle_path(manifest.get("bundle_subdirectory"))
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise DistValidationError
    expected: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise DistValidationError
        name = entry.get("path")
        size = entry.get("size")
        digest = entry.get("sha256")
        if (
            not isinstance(name, str)
            or name != Path(name).name
            or name.casefold() in expected
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None
        ):
            raise DistValidationError
        expected[name.casefold()] = {"name": name, "size": size, "sha256": digest.casefold()}
    return bundle, expected


def _load_inventory(path: Path) -> dict[str, dict[str, Any]]:
    _check_node(path, directory=False)
    try:
        inventory = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DistValidationError from error
    if (
        not isinstance(inventory, dict)
        or inventory.get("schema_version") != 1
        or inventory.get("application") != "InterviewAssistant"
        or not isinstance(inventory.get("files"), list)
        or not inventory["files"]
    ):
        raise DistValidationError
    expected: dict[str, dict[str, Any]] = {}
    for entry in inventory["files"]:
        if not isinstance(entry, dict):
            raise DistValidationError
        raw_path = entry.get("path")
        size = entry.get("size")
        digest = entry.get("sha256")
        path_value = _safe_bundle_path(raw_path)
        normalized = path_value.as_posix()
        key = normalized.casefold()
        if (
            key in expected
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None
        ):
            raise DistValidationError
        expected[key] = {
            "path": normalized,
            "size": size,
            "sha256": digest.casefold(),
        }
    return expected


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise DistValidationError from error
    return digest.hexdigest()


def _iter_tree(root: Path) -> list[tuple[Path, bool]]:
    nodes: list[tuple[Path, bool]] = []
    pending = [root]
    while pending:
        current = pending.pop()
        _check_node(current, directory=True)
        try:
            entries = list(os.scandir(current))
        except OSError as error:
            raise DistValidationError from error
        for entry in entries:
            path = Path(entry.path)
            info = _check_node(path)
            is_directory = stat.S_ISDIR(info.st_mode)
            if not is_directory and not stat.S_ISREG(info.st_mode):
                raise DistValidationError
            nodes.append((path, is_directory))
            if is_directory:
                pending.append(path)
    return nodes


def validate_distribution(
    dist: Path,
    manifest_path: Path,
    inventory_path: Path,
    *,
    installed: bool = False,
) -> None:
    dist = dist.absolute()
    _check_node(dist, directory=True)
    bundle, expected_model = _load_manifest(manifest_path.absolute())
    expected_inventory = _load_inventory(inventory_path.absolute())
    nodes = _iter_tree(dist)
    top_names = {path.name.casefold() for path, _ in nodes if path.parent == dist}
    expected_top = {"interviewassistant.exe", "_internal"}
    if installed:
        expected_top |= {"unins000.dat", "unins000.exe"}
    if top_names != expected_top:
        raise DistValidationError
    _check_node(dist / "InterviewAssistant.exe", directory=False)
    internal = dist / "_internal"
    _check_node(internal, directory=True)

    model_dir = internal.joinpath(*bundle.parts)
    _check_node(model_dir, directory=True)
    actual_model: dict[str, Path] = {}
    actual_inventory: dict[str, Path] = {}
    actual_directories: set[str] = set()
    installer_metadata = {"unins000.dat", "unins000.exe"} if installed else set()
    for path, is_directory in nodes:
        relative = path.relative_to(dist)
        relative_key = relative.as_posix().casefold()
        folded_parts = tuple(part.casefold() for part in relative.parts)
        name = path.name.casefold()
        if is_directory:
            if name in SUSPICIOUS_DIRECTORY_NAMES:
                raise DistValidationError
            actual_directories.add(relative_key)
            continue
        if (
            name in SUSPICIOUS_FILE_NAMES
            or name.startswith(".env.")
            or path.suffix.casefold() in SUSPICIOUS_SUFFIXES
        ):
            raise DistValidationError
        if any(part in SUSPICIOUS_DIRECTORY_NAMES for part in folded_parts[:-1]):
            raise DistValidationError
        if relative_key not in installer_metadata:
            actual_inventory[relative_key] = path
        if path.parent == model_dir:
            actual_model[name] = path
        elif path.suffix.casefold() in MODEL_SUFFIXES:
            normalized_relative = relative.as_posix()
            allowlisted = ALLOWLISTED_RUNTIME_MODELS.get(normalized_relative)
            info = _check_node(path, directory=False)
            if (
                allowlisted is None
                or info.st_size != allowlisted[0]
                or _digest(path) != allowlisted[1]
            ):
                raise DistValidationError

    expected_directories = {
        PurePosixPath(entry["path"]).parent.as_posix().casefold()
        for entry in expected_inventory.values()
        if PurePosixPath(entry["path"]).parent.as_posix() != "."
    }
    expected_directories |= {
        parent.as_posix().casefold()
        for entry in expected_inventory.values()
        for parent in PurePosixPath(entry["path"]).parents
        if parent.as_posix() != "."
    }
    if set(actual_inventory) != set(expected_inventory) or actual_directories != expected_directories:
        raise DistValidationError

    digest_cache: dict[Path, str] = {}
    for key, expected in expected_inventory.items():
        path = actual_inventory[key]
        info = _check_node(path, directory=False)
        digest_cache[path] = _digest(path)
        if info.st_size != expected["size"] or digest_cache[path] != expected["sha256"]:
            raise DistValidationError

    if set(actual_model) != set(expected_model):
        raise DistValidationError
    for key, expected in expected_model.items():
        path = actual_model[key]
        info = _check_node(path, directory=False)
        if info.st_size != expected["size"] or digest_cache[path] != expected["sha256"]:
            raise DistValidationError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--installed", action="store_true")
    arguments = parser.parse_args()
    try:
        validate_distribution(
            arguments.dist,
            arguments.manifest,
            arguments.inventory,
            installed=arguments.installed,
        )
    except DistValidationError:
        print("distribution failed release validation", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
