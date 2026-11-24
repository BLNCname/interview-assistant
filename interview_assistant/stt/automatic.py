"""Prepare and test local speech recognition for the current Windows machine."""

import argparse
import asyncio
from collections.abc import Callable
import os
from pathlib import Path
import subprocess
import sys
import wave

from filelock import FileLock
import yaml  # type: ignore[import-untyped]

from interview_assistant import __version__
from interview_assistant.config import AppConfig, environment_values, resolve_environment_path
from interview_assistant.windows_cuda import configure_cuda_runtime, use_driver_cache
from .adapters import Adapter, auto_order, detect_adapters, dxgi_adapters
from .cuda_setup import ensure_cuda_libraries
from .download import ModelUnavailable, prepare_model, progress_messages
from .profile import defer_setup, explicit_fields, load_profile, profile_path, save_json, setup_deferred


def executable_args(*args: str) -> list[str]:
    entry = [] if getattr(sys, "frozen", False) else [str(Path(__file__).resolve().parents[2] / "main.py")]
    return [sys.executable, *entry, *args]


def hardware_signature() -> list[list]:
    try:
        return [list(item) for item in dxgi_adapters()]
    except (AttributeError, OSError):
        return []


def needs_first_run(config_path: Path) -> bool:
    if setup_deferred(config_path):
        return False
    profile = load_profile(config_path)
    return not profile or profile.get("adapters") != hardware_signature()


def _verify_worker(kind: str, model: str, device: str, index: int, precision: str) -> None:
    from interview_assistant.diagnostics.speech import FixtureWordMismatch, SpeechFixtureProbe
    from .engine import WhisperEngine

    engine = WhisperEngine(model, device=device, gpu_index=index, compute_type=precision, allow_download=False)
    if kind == "cuda-encoder":
        import ctranslate2  # type: ignore[import-untyped]
        from faster_whisper.audio import pad_or_trim  # type: ignore[import-untyped]
        import numpy as np

        if device != "cuda" or os.environ.get("CUDA_FORCE_PTX_JIT") != "1":
            raise ModelUnavailable("CUDA encoder compilation requires a dedicated PTX JIT process")
        # Compile the shipping Whisper encoder by executing it on a real speech window.
        # cuBLAS's decoder can require native kernels, so full transcription runs separately.
        native = engine._get_model()
        with wave.open(str(SpeechFixtureProbe(engine).root / "stt-en.wav"), "rb") as wav:
            audio = np.frombuffer(wav.readframes(wav.getnframes()), "<i2").astype(np.float32) / 32768
        mel = pad_or_trim(native.feature_extractor(audio))[None, ...]  # type: ignore[attr-defined]
        hidden = native.model.encode(  # type: ignore[attr-defined]
            ctranslate2.StorageView.from_array(np.ascontiguousarray(mel)), to_cpu=True,
        )
        values = np.asarray(hidden)
        if values.ndim != 3 or values.shape[:2] != (1, 1500) or not np.isfinite(values).all():
            raise ModelUnavailable("CUDA encoder returned invalid features")
        return
    async def check() -> None:
        probe = SpeechFixtureProbe(engine)
        for lang in ("en", "ru"):
            try:
                if not await probe(lang):
                    raise ModelUnavailable("Local speech fixture failed")
            except FixtureWordMismatch:
                pass  # The lightweight model recognized the sentence, with word errors.
    asyncio.run(check())


def _run_worker(kind: str, model: str, adapter: Adapter, precision: str) -> None:
    env = os.environ.copy()
    if kind == "cuda-encoder":
        env["CUDA_FORCE_PTX_JIT"] = "1"
        env.pop("CUDA_DISABLE_PTX_JIT", None)
        env["CUDA_CACHE_DISABLE"] = "0"
    else:
        env.pop("CUDA_FORCE_PTX_JIT", None)
    args = executable_args("--stt-worker", kind, "--model", model, "--device", adapter.device,
                           "--adapter", str(adapter.index), "--precision", precision)
    try:
        result = subprocess.run(args, env=env, capture_output=True, timeout=300,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ModelUnavailable(f"{adapter.device} worker failed to start or timed out") from error
    if result.returncode:
        raise ModelUnavailable(f"{adapter.device} {kind} failed on adapter {adapter.index}")


def prepare_backend(model: str, adapter: Adapter, precision: str, report: Callable[[str], None]) -> str:
    report(f"Preparing Whisper {model} for {adapter.name}")
    prepare_model(model, adapter.device, download=True, progress=progress_messages("Model files", report))
    if adapter.device == "cuda":
        use_driver_cache()
        report("Checking and preparing NVIDIA runtime libraries")
        ensure_cuda_libraries(report)
        if adapter.capability >= 86:
            report("Compiling Whisper encoder kernels on the NVIDIA GPU")
            _run_worker("cuda-encoder", model, adapter, precision)
            compilation = "CUDA driver PTX JIT"
        else:
            compilation = "CUDA native compatibility mode"
    else:
        compilation = {"openvino": "OpenVINO GPU model compilation",
                       "directml": "DirectML GPU graph compilation", "cpu": "CTranslate2 CPU"}[adapter.device]
    report(f"{compilation}. Checking local English and Russian speech")
    _run_worker("inference", model, adapter, precision)
    return compilation


def prepare_hardware(config_path: Path, report: Callable[[str], None], *, force_auto: bool = False,
                     model_name: str | None = None, if_needed: bool = False) -> dict:
    receipt = profile_path(config_path)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(receipt) + ".lock"):
        current = load_profile(config_path)
        if if_needed and current and current.get("adapters") == hardware_signature():
            return current
        raw = (yaml.safe_load(config_path.read_text("utf-8")) or {}) if config_path.is_file() else {}
        config = AppConfig.load(config_path)
        fields = explicit_fields(raw)
        env = environment_values(resolve_environment_path(config_path))
        model = model_name or (config.audio.stt_model if "stt_model" in fields or "STT_MODEL" in env else "base")
        manual = not force_auto and ("device" in fields or "STT_DEVICE" in env)
        candidates = detect_adapters()
        if manual:
            if config.audio.device == "cpu":
                candidates = [Adapter("cpu", 0, "CPU")]
            else:
                candidates = [d for d in candidates if (d.device, d.index) == (config.audio.device, config.audio.gpu_index)]
            if not candidates:
                raise ModelUnavailable("The manually selected GPU is unavailable. Choose automatic detection in Audio.")
        else:
            candidates = auto_order(candidates)
        report(f"Checking model files for Whisper {model}")
        # Failure to fetch the shared weights cannot be fixed by changing adapters.
        prepare_model(model, "cpu", download=True, progress=progress_messages("Whisper weights", report))
        failures = []
        for adapter in candidates:
            precision = (config.audio.compute_type if not force_auto and (
                manual or "compute_type" in fields or "STT_COMPUTE_TYPE" in env)
                         else ("int8_float16" if adapter.device == "cuda" else "int8"))
            if adapter.device != "cuda" and precision not in {"float32", "int8"}:
                failures.append({"device": adapter.device, "index": adapter.index, "reason": "Incompatible decoder precision"})
                continue
            try:
                compilation = prepare_backend(model, adapter, precision, report)
            except (ModelUnavailable, RuntimeError, OSError, ValueError) as error:
                reason = str(error) if isinstance(error, ModelUnavailable) else type(error).__name__
                failures.append({"device": adapter.device, "index": adapter.index, "reason": reason})
                report(f"{adapter.name}: {reason}")
                continue
            result = {"status": "ready", "version": __version__, "device": adapter.device,
                      "gpu_index": adapter.index, "adapter_name": adapter.name, "compute_type": precision,
                      "stt_model": model, "compilation": compilation, "speech_verified": True,
                      "fallbacks": failures, "adapters": hardware_signature()}
            save_json(receipt, result)
            config_path.with_suffix(".stt-deferred.json").unlink(missing_ok=True)
            report(f"Speech recognition ready on {adapter.name} ({adapter.device})")
            return result
        raise ModelUnavailable("Speech recognition preparation failed. " + "; ".join(str(f["reason"]) for f in failures))


def cli(args: list[str], default_config: Callable[[], Path]) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare-stt", action="store_true")
    group.add_argument("--defer-stt", action="store_true")
    group.add_argument("--stt-worker", choices=("cuda-encoder", "inference"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--device", choices=("cuda", "openvino", "directml", "cpu"))
    parser.add_argument("--adapter", type=int, default=0)
    parser.add_argument("--precision", default="int8")
    parser.add_argument("--force-auto", action="store_true")
    parser.add_argument("--if-needed", action="store_true")
    opt = parser.parse_args(args)
    def message(text: str) -> None:
        if sys.stdout is not None:
            print(text, flush=True)
    try:
        if opt.defer_stt:
            defer_setup(opt.config or default_config())
            return 0
        if opt.stt_worker:
            if not opt.model or not opt.device or opt.adapter < 0:
                return 2
            if opt.device == "cuda":
                if not configure_cuda_runtime().ready:
                    raise ModelUnavailable("NVIDIA runtime is missing")
            _verify_worker(opt.stt_worker, opt.model, opt.device, opt.adapter, opt.precision)
            return 0
        result = prepare_hardware(opt.config or default_config(), message, force_auto=opt.force_auto,
                                  model_name=opt.model, if_needed=opt.if_needed)
    except Exception as error:
        reason = str(error) if isinstance(error, ModelUnavailable) else type(error).__name__
        result = {"status": "failed", "reason": reason}
        message(reason)
    if opt.result:
        save_json(opt.result, result)
    return 0 if result["status"] == "ready" else 1
