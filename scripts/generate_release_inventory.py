from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator


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


def _windows_parent_open_parameters() -> tuple[int, int, int, int]:
    file_list_directory = 0x0001
    file_share_read = 0x0001
    file_share_write = 0x0002
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    return (
        file_list_directory,
        file_share_read | file_share_write,
        open_existing,
        file_flag_backup_semantics | file_flag_open_reparse_point,
    )


def _open_windows_output_parent(directory: Path) -> int:
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    access, sharing, creation, flags = _windows_parent_open_parameters()
    handle = create_file(str(directory), access, sharing, None, creation, flags, None)
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise ValueError("Inventory output parent cannot be locked")

    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    try:
        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(ByHandleFileInformation)]
        get_information.restype = wintypes.BOOL
        information = ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(information)):
            raise ValueError("Inventory output parent cannot be inspected")
        file_attribute_directory = 0x0010
        file_attribute_reparse_point = 0x0400
        if (
            not information.dwFileAttributes & file_attribute_directory
            or information.dwFileAttributes & file_attribute_reparse_point
        ):
            raise ValueError("Inventory output parent is unsafe")
        return handle
    except BaseException:
        close_handle(handle)
        raise


def _close_windows_handle(handle: int) -> None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    if not close_handle(handle):
        raise ValueError("Inventory output parent could not be unlocked")


@contextmanager
def _stable_output_parent(directory: Path) -> Iterator[int | None]:
    if os.name == "nt":
        handle = _open_windows_output_parent(directory)
        try:
            yield None
        finally:
            _close_windows_handle(handle)
        return

    required_flags = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, flag) for flag in required_flags):
        raise ValueError("Inventory output parent cannot be locked safely")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(str(directory), flags)
    except OSError as error:
        raise ValueError("Inventory output parent cannot be locked") from error
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("Inventory output parent is unsafe")
        yield descriptor
    finally:
        os.close(descriptor)


def _replace_file_windows(output: Path, replacement: Path) -> None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    replace_file = kernel32.ReplaceFileW
    replace_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    replace_file.restype = wintypes.BOOL
    if not replace_file(str(output), str(replacement), None, 0, None, None):
        error = ctypes.get_last_error()
        raise OSError(error, "ReplaceFileW failed")


def _write_exclusively(output: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        with _stable_output_parent(output.parent) as parent_descriptor:
            if parent_descriptor is None:
                descriptor = os.open(str(output), flags, 0o666)
            else:
                descriptor = os.open(output.name, flags, 0o666, dir_fd=parent_descriptor)
            with os.fdopen(descriptor, "wb") as destination:
                destination.write(content)
    except FileExistsError as error:
        raise ValueError("Inventory output already exists; pass --replace to overwrite it") from error
    except OSError as error:
        raise ValueError("Inventory output could not be written") from error


def _open_posix_temporary(parent_descriptor: int, output_name: str, mode: int) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for _attempt in range(10):
        temporary_name = f".{output_name}.{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(
                temporary_name,
                flags,
                mode,
                dir_fd=parent_descriptor,
            )
        except FileExistsError:
            continue
        return descriptor, temporary_name
    raise ValueError("Inventory output temporary path could not be created")


def _posix_target_mode(parent_descriptor: int, output_name: str) -> int | None:
    try:
        info = os.stat(output_name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Inventory output must be a regular file")
    return stat.S_IMODE(info.st_mode)


def _write_atomically(
    output: Path,
    resolved_output: Path,
    root: Path,
    content: bytes,
) -> None:
    temporary_path: Path | None = None
    temporary_name: str | None = None
    parent_descriptor: int | None = None
    try:
        with _stable_output_parent(resolved_output.parent) as parent_descriptor:
            if parent_descriptor is None:
                descriptor, temporary_name = tempfile.mkstemp(
                    dir=resolved_output.parent,
                    prefix=f".{resolved_output.name}.",
                    suffix=".tmp",
                )
                temporary_path = Path(temporary_name)
                with os.fdopen(descriptor, "wb") as destination:
                    destination.write(content)
                output_exists = _output_exists_and_is_safe(output)
                if output_exists:
                    _replace_file_windows(resolved_output, temporary_path)
                else:
                    os.replace(temporary_path, resolved_output)
                temporary_path = None
            else:
                mode = _posix_target_mode(parent_descriptor, resolved_output.name)
                descriptor, temporary_name = _open_posix_temporary(
                    parent_descriptor,
                    resolved_output.name,
                    mode if mode is not None else 0o666,
                )
                with os.fdopen(descriptor, "wb") as destination:
                    destination.write(content)
                if mode is not None:
                    os.chmod(temporary_name, mode, dir_fd=parent_descriptor)
                os.replace(
                    temporary_name,
                    resolved_output.name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                )
                temporary_name = None
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
        if temporary_name is not None and os.name != "nt":
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
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
