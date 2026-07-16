from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path


REQUIRED_CUDA_DLLS = (
    "cudart64_12.dll",
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "cudnn64_8.dll",
)
_DLL_DIRECTORY_HANDLES: list[object] = []


@dataclass(frozen=True)
class CudaRuntimeStatus:
    directories: tuple[Path, ...]
    missing_dlls: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.missing_dlls


def _default_directories() -> tuple[Path, ...]:
    site_packages = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    candidates = (
        site_packages / "cuda_runtime" / "bin",
        site_packages / "cublas" / "bin",
        site_packages / "cudnn" / "bin",
    )
    return tuple(path.resolve() for path in candidates if path.is_dir())


def configure_cuda_runtime(
    search_directories: Iterable[Path] | None = None,
    *,
    add_directory: Callable[[str], object] | None = None,
) -> CudaRuntimeStatus:
    directories = tuple(
        dict.fromkeys(
            Path(path).resolve()
            for path in (
                _default_directories() if search_directories is None else search_directories
            )
            if Path(path).is_dir()
        )
    )
    register = add_directory
    if register is None and os.name == "nt":
        register = os.add_dll_directory
    if register is not None:
        for directory in directories:
            _DLL_DIRECTORY_HANDLES.append(register(str(directory)))
    missing = tuple(
        name
        for name in REQUIRED_CUDA_DLLS
        if not any((directory / name).is_file() for directory in directories)
    )
    return CudaRuntimeStatus(directories, missing)
