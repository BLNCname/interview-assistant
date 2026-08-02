from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath


APPLICATION = "InterviewAssistant"
EXPECTED_TOP_LEVEL = frozenset({"InterviewAssistant.exe", "_internal"})


def _is_reparse(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _check_node(path: Path, *, directory: bool | None = None) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as error:
        raise ValueError("Distribution contains an unreadable path") from error
    if path.is_symlink() or _is_reparse(info):
        raise ValueError("Distribution inventory refuses symbolic links or reparse points")
    if directory is True and not stat.S_ISDIR(info.st_mode):
        raise ValueError("Distribution contains an unexpected non-directory")
    if directory is False and not stat.S_ISREG(info.st_mode):
        raise ValueError("Distribution contains an unexpected non-file")
    return info


def _resolve_distribution_root(dist: Path) -> Path:
    if dist.name != APPLICATION:
        raise ValueError("Distribution root must be named InterviewAssistant")
    _check_node(dist, directory=True)
    try:
        resolved = dist.resolve(strict=True)
    except OSError as error:
        raise ValueError("Distribution root is not accessible") from error
    _check_node(resolved, directory=True)
    return resolved


def _safe_relative(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError("Distribution path escapes its root") from error
    normalized = PurePosixPath(relative.as_posix())
    if (
        normalized.is_absolute()
        or not normalized.parts
        or any(part in {"", ".", ".."} for part in normalized.parts)
    ):
        raise ValueError("Distribution contains an unsafe relative path")
    return normalized.as_posix()


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise ValueError("Distribution contains an unreadable file") from error
    return digest.hexdigest()


def _walk_distribution(root: Path) -> list[Path]:
    paths: list[Path] = []
    pending = [root]
    while pending:
        current = pending.pop()
        _check_node(current, directory=True)
        try:
            entries = list(os.scandir(current))
        except OSError as error:
            raise ValueError("Distribution contains an unreadable directory") from error
        if not entries:
            raise ValueError("Distribution contains an empty directory")
        for entry in entries:
            path = Path(entry.path)
            info = _check_node(path)
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                paths.append(path)
            else:
                raise ValueError("Distribution contains an unexpected non-file")
    return paths


def build_inventory(dist: Path) -> dict[str, object]:
    """Build a reviewed inventory from an already-complete PyInstaller onedir tree."""
    root = _resolve_distribution_root(dist)
    paths = _walk_distribution(root)
    top_level = {path.name for path in root.iterdir()}
    if top_level != EXPECTED_TOP_LEVEL:
        raise ValueError("Distribution does not match the reviewed top-level shape")
    _check_node(root / "InterviewAssistant.exe", directory=False)
    _check_node(root / "_internal", directory=True)

    entries: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    for path in sorted(paths, key=lambda item: _safe_relative(item, root).casefold()):
        relative = _safe_relative(path, root)
        key = relative.casefold()
        if key in seen_paths:
            raise ValueError("Distribution contains case-insensitive duplicate paths")
        seen_paths.add(key)
        info = _check_node(path, directory=False)
        entries.append(
            {"path": relative, "size": info.st_size, "sha256": _digest(path)}
        )

    if not entries:
        raise ValueError("Distribution is incomplete")
    return {"schema_version": 1, "application": APPLICATION, "files": entries}


def _resolve_output(output: Path, root: Path) -> Path:
    _check_existing_output_ancestors(output)
    try:
        resolved = output.resolve(strict=False)
    except OSError as error:
        raise ValueError("Inventory output is not accessible") from error
    if resolved.is_relative_to(root):
        raise ValueError("Inventory output must not be inside the distribution")
    return resolved


def _check_existing_output_ancestors(output: Path) -> None:
    current = output.absolute().parent
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise ValueError("Inventory output ancestors are not accessible") from error
        else:
            if current.is_symlink() or _is_reparse(info):
                raise ValueError(
                    "Inventory output refuses symbolic links or reparse points in its ancestors"
                )
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("Inventory output ancestors must be directories")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _output_exists_and_is_safe(output: Path) -> bool:
    try:
        info = output.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ValueError("Inventory output is not accessible") from error
    if output.is_symlink() or _is_reparse(info):
        raise ValueError("Inventory output refuses symbolic links or reparse points")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Inventory output must be a regular file")
    return True


def _inventory_bytes(inventory: dict[str, object]) -> bytes:
    return (json.dumps(inventory, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _write_exclusively(output: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(str(output), flags, 0o666)
    except FileExistsError as error:
        raise ValueError("Inventory output already exists; pass --replace to overwrite it") from error
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
    except OSError as error:
        raise ValueError("Inventory output could not be written") from error


def _write_atomically(
    output: Path,
    resolved_output: Path,
    root: Path,
    content: bytes,
) -> None:
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=resolved_output.parent,
            prefix=f".{resolved_output.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)

        revalidated_output = _resolve_output(output, root)
        if revalidated_output != resolved_output:
            raise ValueError("Inventory output changed during generation")
        _output_exists_and_is_safe(output)
        os.replace(temporary_path, resolved_output)
        temporary_path = None
    except OSError as error:
        raise ValueError("Inventory output could not be written") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def write_inventory(dist: Path, output: Path, *, replace: bool) -> None:
    root = _resolve_distribution_root(dist)
    resolved_output = _resolve_output(output, root)
    output_exists = _output_exists_and_is_safe(output)
    if output_exists:
        if not replace:
            raise ValueError("Inventory output already exists; pass --replace to overwrite it")
    elif not resolved_output.parent.is_dir():
        raise ValueError("Inventory output parent directory does not exist")
    inventory = build_inventory(root)
    content = _inventory_bytes(inventory)
    if replace:
        _write_atomically(output, resolved_output, root, content)
        return

    revalidated_output = _resolve_output(output, root)
    if revalidated_output != resolved_output or _output_exists_and_is_safe(output):
        raise ValueError("Inventory output already exists; pass --replace to overwrite it")
    _write_exclusively(resolved_output, content)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--application", choices=(APPLICATION,), required=True)
    parser.add_argument("--replace", action="store_true")
    arguments = parser.parse_args()
    try:
        write_inventory(arguments.dist, arguments.output, replace=arguments.replace)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
