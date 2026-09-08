"""Verify faster-whisper CUDA inference with a deterministic bundled WAV."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
import wave
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from interview_assistant.composition import default_config_path
from interview_assistant.config import AppConfig
from interview_assistant.stt.bundle import resolve_stt_model
from interview_assistant.windows_cuda import configure_cuda_runtime


class _Segment(Protocol):
    text: str


class _Info(Protocol):
    language: str
    language_probability: float


class _Model(Protocol):
    def transcribe(
        self,
        audio: NDArray[np.float32],
        **options: object,
    ) -> tuple[Iterable[_Segment], _Info]: ...


ModelFactory = Callable[..., _Model]


def _create_model(
    model_name: str,
    *,
    device: str,
    compute_type: str,
    local_files_only: bool = False,
) -> _Model:
    configure_cuda_runtime()
    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    model_options: dict[str, object] = {
        "device": device,
        "compute_type": compute_type,
    }
    if local_files_only:
        model_options["local_files_only"] = True
    return WhisperModel(model_name, **model_options)


def repository_fixture_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "diagnostics" / "stt-smoke.wav"


def _read_fixture(path: Path) -> tuple[NDArray[np.float32], float]:
    with wave.open(str(path), "rb") as fixture:
        channels = fixture.getnchannels()
        sample_width = fixture.getsampwidth()
        sample_rate = fixture.getframerate()
        frame_count = fixture.getnframes()
        if channels != 1 or sample_width != 2 or sample_rate != 16_000:
            raise ValueError("Fixture must be mono 16-bit PCM at 16 kHz")
        frames = fixture.readframes(frame_count)
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32_768.0
    return audio, frame_count / sample_rate


def run_verification(
    config_path: Path,
    fixture_path: Path,
    *,
    model_factory: ModelFactory = _create_model,
    clock: Callable[[], float] = time.perf_counter,
    bundle_roots: Iterable[Path] | None = None,
) -> dict[str, object]:
    """Load the configured CUDA model and force a full transcription iteration."""

    config = AppConfig.load(config_path)
    audio, fixture_seconds = _read_fixture(fixture_path)

    load_started = clock()
    if bundle_roots is None:
        resolved_model = resolve_stt_model(config.audio.stt_model)
    else:
        resolved_model = resolve_stt_model(
            config.audio.stt_model,
            roots=tuple(bundle_roots),
        )
        if resolved_model == config.audio.stt_model:
            raise RuntimeError("Bundled STT model is required for CUDA verification")
    model_options: dict[str, object] = {
        "device": "cuda",
        "compute_type": config.audio.compute_type,
    }
    model_source = "configured"
    if resolved_model != config.audio.stt_model:
        model_options["local_files_only"] = True
        model_source = "bundled"
    model = model_factory(
        resolved_model,
        **model_options,
    )
    model_load_seconds = clock() - load_started

    transcription_started = clock()
    segments, info = model.transcribe(
        audio,
        beam_size=1,
        condition_on_previous_text=False,
        multilingual=True,
        temperature=0.0,
    )
    text = " ".join(
        value
        for segment in segments
        if (value := str(segment.text).strip())
    )
    transcription_seconds = clock() - transcription_started
    real_time_factor = transcription_seconds / fixture_seconds
    return {
        "status": "ok",
        "device": "cuda",
        "compute_type": config.audio.compute_type,
        "model_source": model_source,
        "fixture_seconds": round(fixture_seconds, 6),
        "model_load_seconds": round(model_load_seconds, 6),
        "transcription_seconds": round(transcription_seconds, 6),
        "real_time_factor": round(real_time_factor, 6),
        "language": str(getattr(info, "language", "unknown")),
        "language_probability": round(
            float(getattr(info, "language_probability", 0.0)),
            6,
        ),
        "text_characters": len(text),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--fixture", type=Path, default=repository_fixture_path())
    parser.add_argument("--bundle-root", type=Path, action="append")
    parser.add_argument("--output", type=Path)
    return parser


def _write_report(path: Path, report: dict[str, Any]) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    encoded = (json.dumps(report, ensure_ascii=True, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any]
    try:
        report = run_verification(
            args.config.expanduser().resolve(),
            args.fixture.expanduser().resolve(),
            model_factory=_create_model,
            bundle_roots=(
                tuple(path.expanduser().resolve() for path in args.bundle_root)
                if args.bundle_root
                else None
            ),
        )
    except Exception as error:
        report = {
            "status": "error",
            "device": "cuda",
            "error_type": type(error).__name__,
            "message": "CUDA STT verification failed",
        }
        exit_code = 1
    else:
        exit_code = 0
    if args.output is not None:
        _write_report(args.output, report)
    elif sys.stdout is not None:
        sys.stdout.write(json.dumps(report, ensure_ascii=True, sort_keys=True) + "\n")
        sys.stdout.flush()
    return exit_code


if __name__ == "__main__":  # pragma: no cover - exercised by hardware verification
    raise SystemExit(main())
