"""Validate and resolve the optional pinned offline STT model bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterable
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class SttBundleError(RuntimeError):
    """Base class for public, path-redacted STT bundle failures."""


class SttManifestError(SttBundleError):
    """The tracked STT manifest is missing or invalid."""


class SttBundleValidationError(SttBundleError):
    """A candidate bundled model is incomplete or corrupt."""


@dataclass(frozen=True, slots=True)
class SttBundleFile:
    path: str
    size: int
    sha256: str
    runtime_required: bool


@dataclass(frozen=True, slots=True)
class SttModelManifest:
    schema_version: int
    name: str
    repository: str
    revision: str
    license: str
    bundle_subdirectory: str
    files: tuple[SttBundleFile, ...]


_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "name",
        "repository",
        "revision",
        "license",
        "bundle_subdirectory",
        "files",
    }
)
_FILE_KEYS = frozenset({"path", "size", "sha256", "runtime_required"})
_MODEL_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_REPOSITORY_PATTERN = re.compile(r"[^/\s]+/[^/\s]+")
_REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_WINDOWS_FORBIDDEN_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_FILENAMES = frozenset(
    {
        "AUX",
        "CLOCK$",
        "CON",
        "CONIN$",
        "CONOUT$",
        "NUL",
        "PRN",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)


def _invalid_manifest() -> SttManifestError:
    return SttManifestError("STT model manifest is invalid")


def _is_windows_safe_basename(value: object) -> bool:
    if not isinstance(value, str) or not value or value[-1] in {" ", "."}:
        return False
    if any(
        ord(character) < 32
        or character in _WINDOWS_FORBIDDEN_FILENAME_CHARACTERS
        for character in value
    ):
        return False
    reserved_stem = value.split(".", maxsplit=1)[0].rstrip(" ").upper()
    return reserved_stem not in _WINDOWS_RESERVED_FILENAMES


def _parse_manifest(payload: object) -> SttModelManifest:
    if not isinstance(payload, dict) or set(payload) != _MANIFEST_KEYS:
        raise _invalid_manifest()

    schema_version = payload["schema_version"]
    name = payload["name"]
    repository = payload["repository"]
    revision = payload["revision"]
    license_name = payload["license"]
    bundle_subdirectory = payload["bundle_subdirectory"]
    raw_files = payload["files"]
    if schema_version != 1 or isinstance(schema_version, bool):
        raise _invalid_manifest()
    if not isinstance(name, str) or _MODEL_NAME_PATTERN.fullmatch(name) is None:
        raise _invalid_manifest()
    if (
        not isinstance(repository, str)
        or _REPOSITORY_PATTERN.fullmatch(repository) is None
    ):
        raise _invalid_manifest()
    if not isinstance(revision, str) or _REVISION_PATTERN.fullmatch(revision) is None:
        raise _invalid_manifest()
    if not isinstance(license_name, str) or not license_name.strip():
        raise _invalid_manifest()
    expected_subdirectory = PurePosixPath("models", "stt", name).as_posix()
    if bundle_subdirectory != expected_subdirectory:
        raise _invalid_manifest()
    if not isinstance(raw_files, list) or not raw_files:
        raise _invalid_manifest()

    files: list[SttBundleFile] = []
    seen_path_keys: set[str] = set()
    for raw_file in raw_files:
        if not isinstance(raw_file, dict) or set(raw_file) != _FILE_KEYS:
            raise _invalid_manifest()
        file_path = raw_file["path"]
        size = raw_file["size"]
        sha256 = raw_file["sha256"]
        runtime_required = raw_file["runtime_required"]
        if not _is_windows_safe_basename(file_path):
            raise _invalid_manifest()
        path_key = file_path.casefold()
        if path_key in seen_path_keys:
            raise _invalid_manifest()
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise _invalid_manifest()
        if not isinstance(sha256, str) or _SHA256_PATTERN.fullmatch(sha256) is None:
            raise _invalid_manifest()
        if not isinstance(runtime_required, bool):
            raise _invalid_manifest()
        seen_path_keys.add(path_key)
        files.append(SttBundleFile(file_path, size, sha256, runtime_required))
    if not any(item.runtime_required for item in files):
        raise _invalid_manifest()

    return SttModelManifest(
        schema_version=schema_version,
        name=name,
        repository=repository,
        revision=revision,
        license=license_name,
        bundle_subdirectory=bundle_subdirectory,
        files=tuple(files),
    )


def load_stt_manifest(path: Path) -> SttModelManifest:
    """Load a manifest, rejecting malformed schemas without exposing its path."""

    try:
        with path.open("r", encoding="utf-8") as manifest_file:
            payload = json.load(manifest_file)
        return _parse_manifest(payload)
    except SttManifestError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _invalid_manifest() from None


def _source_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_stt_manifest_path() -> Path:
    """Return the tracked manifest location for source and frozen runtimes."""

    return _source_project_root() / "packaging" / "stt_model_manifest.json"


def default_stt_roots() -> tuple[Path, ...]:
    """Return deterministic frozen/source roots without consulting the CWD."""

    roots: list[Path] = []
    frozen_value = getattr(sys, "_MEIPASS", None)
    if isinstance(frozen_value, (str, os.PathLike)):
        frozen_root = Path(frozen_value)
        if frozen_root.is_absolute():
            roots.append(frozen_root)
    source_root = _source_project_root()
    if source_root not in roots:
        roots.append(source_root)
    return tuple(roots)


def _invalid_bundle() -> SttBundleValidationError:
    return SttBundleValidationError("Bundled STT model is incomplete or corrupt")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as model_file:
        while chunk := model_file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_stt_bundle(
    bundle_directory: Path,
    manifest: SttModelManifest,
    *,
    require_all_files: bool = False,
) -> None:
    """Verify required and present manifest files without exposing local paths."""

    try:
        if not bundle_directory.is_dir():
            raise _invalid_bundle()
        for item in manifest.files:
            model_file = bundle_directory / item.path
            required = item.runtime_required or require_all_files
            if not model_file.exists():
                if required:
                    raise _invalid_bundle()
                continue
            if (
                not model_file.is_file()
                or model_file.stat().st_size != item.size
                or _sha256(model_file) != item.sha256
            ):
                raise _invalid_bundle()
    except SttBundleValidationError:
        raise
    except OSError:
        raise _invalid_bundle() from None


def resolve_stt_model(
    model_name: str,
    *,
    roots: Iterable[Path] | None = None,
    manifest: SttModelManifest | None = None,
) -> str:
    """Resolve the configured manifest model to an explicit bundled directory."""

    active_manifest = manifest or load_stt_manifest(default_stt_manifest_path())
    if model_name != active_manifest.name:
        return model_name
    candidate_roots = roots if roots is not None else default_stt_roots()
    parts = PurePosixPath(active_manifest.bundle_subdirectory).parts
    for root in candidate_roots:
        candidate = root.joinpath(*parts)
        try:
            candidate_present = candidate.exists() or candidate.is_symlink()
        except (OSError, ValueError):
            raise _invalid_bundle() from None
        if candidate_present:
            validate_stt_bundle(candidate, active_manifest)
            return str(candidate)
    return model_name


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=default_stt_manifest_path())
    parser.add_argument("--validate-bundle", type=Path, required=True)
    parser.add_argument("--require-all-files", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate a bundle for packaging with path-redacted CLI failures."""

    args = _parser().parse_args(argv)
    try:
        manifest = load_stt_manifest(args.manifest)
        validate_stt_bundle(
            args.validate_bundle,
            manifest,
            require_all_files=args.require_all_files,
        )
    except SttBundleError:
        sys.stderr.write("STT bundle validation failed\n")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by build.ps1
    raise SystemExit(main())
