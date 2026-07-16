from pathlib import Path

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

    status = configure_cuda_runtime(
        (cuda_runtime, cublas, cudnn),
        add_directory=lambda value: registered.append(value) or object(),
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


def test_cuda_runtime_reports_each_missing_dll(tmp_path: Path) -> None:
    status = configure_cuda_runtime(
        (tmp_path,),
        add_directory=lambda _value: object(),
    )

    assert not status.ready
    assert status.missing_dlls == (
        "cudart64_12.dll",
        "cublas64_12.dll",
        "cublasLt64_12.dll",
        "cudnn64_8.dll",
    )
