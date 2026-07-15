"""Side-effect-bounded diagnostics for packaged, no-GUI startup."""

from __future__ import annotations

import json
import os
import sys
import uuid
from importlib import import_module, metadata
from pathlib import Path
from typing import Any

from interview_assistant.config import AppConfig


_REQUIRED_DEPENDENCIES = (
    ("PyQt6", "PyQt6"),
    ("qasync", "qasync"),
    ("faster_whisper", "faster-whisper"),
    ("ctranslate2", "ctranslate2"),
    ("mss", "mss"),
    ("keyring", "keyring"),
    ("pyaudiowpatch", "PyAudioWPatch"),
)


def _dependency_version(module_name: str, distribution_name: str) -> str:
    import_module(module_name)
    return metadata.version(distribution_name)


def _cpu_compute_types() -> tuple[str, ...]:
    ctranslate2 = import_module("ctranslate2")
    values = ctranslate2.get_supported_compute_types("cpu")
    return tuple(sorted(str(value) for value in values))


def _cuda_device_count() -> int:
    ctranslate2 = import_module("ctranslate2")
    return int(ctranslate2.get_cuda_device_count())


def _config_status(config_path: Path | None) -> str:
    if config_path is None or not config_path.exists():
        return "missing"
    try:
        AppConfig.load(config_path)
    except Exception:
        return "invalid"
    return "ready"


def build_runtime_report(config_path: Path | None) -> dict[str, Any]:
    """Return a redacted package/runtime report without opening GUI or hardware."""

    dependencies: dict[str, dict[str, str]] = {}
    dependencies_ready = True
    for module_name, distribution_name in _REQUIRED_DEPENDENCIES:
        try:
            version = _dependency_version(module_name, distribution_name)
        except Exception:
            dependencies[module_name] = {"status": "failed"}
            dependencies_ready = False
        else:
            dependencies[module_name] = {"status": "ready", "version": version}

    try:
        compute_types = list(_cpu_compute_types())
    except Exception:
        cpu: dict[str, object] = {"status": "failed", "compute_types": []}
        dependencies_ready = False
    else:
        cpu = {
            "status": "ready" if compute_types else "failed",
            "compute_types": compute_types,
        }
        dependencies_ready = dependencies_ready and bool(compute_types)

    try:
        cuda_count = max(0, _cuda_device_count())
    except Exception:
        cuda = {"status": "warning", "device_count": 0}
    else:
        cuda = {
            "status": "ready" if cuda_count > 0 else "warning",
            "device_count": cuda_count,
        }

    config = _config_status(config_path)
    status = "ok" if dependencies_ready and config != "invalid" else "error"
    return {
        "status": status,
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "config": config,
        "dependencies": dependencies,
        "cpu": cpu,
        "cuda": cuda,
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    encoded = (json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n").encode(
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


def run_no_gui_diagnostics(
    *,
    config_path: Path | None,
    output_path: Path | None,
) -> int:
    """Run import/config/backend smoke checks and return a process exit code."""

    report = build_runtime_report(config_path)
    if output_path is not None:
        _atomic_write_json(output_path, report)
    elif sys.stdout is not None:
        sys.stdout.write(json.dumps(report, ensure_ascii=True, sort_keys=True) + "\n")
        sys.stdout.flush()
    return 0 if report["status"] == "ok" else 1
