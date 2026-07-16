from pathlib import Path

import pytest

from interview_assistant import windows_cuda
from interview_assistant.windows_cuda import configure_cuda_runtime


def test_cuda_runtime_registers_complete_directories(tmp_path: Path) -> None:
    cuda_runtime = tmp_path / "nvidia" / "cuda_runtime" / "bin"
    cublas = tmp_path / "nvidia" / "cublas" / "bin"
    cudnn = tmp_path / "nvidia" / "cudnn" / "bin"
    cuda_runtime.mkdir(parents=True)
    cublas.mkdir(parents=True)
    cudnn.mkdir(parents=True)
    (cuda_runtime / "cudart64_12.dll").touch()
    for name in ("cublas64_12.dll", "cublasLt64_12.dll"):
        (cublas / name).touch()
    (cudnn / "cudnn64_8.dll").touch()
    registered: list[str] = []
    loaded: list[str] = []

    status = configure_cuda_runtime(
        (cuda_runtime, cublas, cudnn),
        add_directory=lambda value: registered.append(value) or object(),
        load_library=lambda value: loaded.append(Path(value).name) or object(),
    )

    assert status.ready
    assert status.missing_dlls == ()
    assert status.directories == (
        cuda_runtime.resolve(),
        cublas.resolve(),
        cudnn.resolve(),
    )
    assert registered == [
        str(cuda_runtime.resolve()),
        str(cublas.resolve()),
        str(cudnn.resolve()),
    ]
    assert loaded == [
        "cudart64_12.dll",
        "cublasLt64_12.dll",
        "cublas64_12.dll",
        "cudnn64_8.dll",
    ]


def test_cuda_runtime_reports_each_missing_dll(tmp_path: Path) -> None:
    status = configure_cuda_runtime(
        (tmp_path,),
        add_directory=lambda _value: object(),
        load_library=lambda _value: object(),
    )

    assert not status.ready
    assert status.missing_dlls == (
        "cudart64_12.dll",
        "cublas64_12.dll",
        "cublasLt64_12.dll",
        "cudnn64_8.dll",
    )


def test_cuda_runtime_discovers_frozen_nvidia_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nvidia_root = tmp_path / "nvidia"
    directories = (
        nvidia_root / "cuda_runtime" / "bin",
        nvidia_root / "cublas" / "bin",
        nvidia_root / "cudnn" / "bin",
    )
    for directory in directories:
        directory.mkdir(parents=True)
    (directories[0] / "cudart64_12.dll").touch()
    (directories[1] / "cublas64_12.dll").touch()
    (directories[1] / "cublasLt64_12.dll").touch()
    (directories[2] / "cudnn64_8.dll").touch()
    monkeypatch.setattr(windows_cuda.sys, "frozen", True, raising=False)
    monkeypatch.setattr(windows_cuda.sys, "_MEIPASS", str(tmp_path), raising=False)

    status = configure_cuda_runtime(
        add_directory=lambda _value: object(),
        load_library=lambda _value: object(),
    )

    assert status.ready
    assert status.directories == tuple(path.resolve() for path in directories)
