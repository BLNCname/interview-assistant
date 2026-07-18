# -*- mode: python ; coding: utf-8 -*-
"""Reproducible onedir bundle for the Windows Interview Assistant."""

import json
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata


ROOT = Path(SPECPATH).resolve().parent
MANIFEST_PATH = ROOT / "packaging" / "stt_model_manifest.json"
STT_MODEL_ENVIRONMENT = "INTERVIEW_ASSISTANT_STT_MODEL_PATH"
MODEL_WEIGHT_SUFFIXES = frozenset(
    {".bin", ".ckpt", ".gguf", ".pt", ".pth", ".safetensors"}
)
PACKAGES = (
    "PyQt6",
    "qasync",
    "faster_whisper",
    "ctranslate2",
    "mss",
    "keyring",
    "pyaudiowpatch",
    "pynput",
    "nvidia.cuda_runtime",
    "nvidia.cublas",
    "nvidia.cudnn",
)
DISTRIBUTIONS = (
    "PyQt6",
    "qasync",
    "faster-whisper",
    "ctranslate2",
    "mss",
    "keyring",
    "PyAudioWPatch",
    "pynput",
    "nvidia-cuda-runtime-cu12",
    "nvidia-cublas-cu12",
    "nvidia-cudnn-cu12",
)


def _without_model_weights(items):
    return [
        item
        for item in items
        if Path(item[0]).suffix.casefold() not in MODEL_WEIGHT_SUFFIXES
    ]


def _optional_stt_bundle_datas():
    source_value = os.environ.get(STT_MODEL_ENVIRONMENT, "").strip()
    if not source_value:
        return []
    source_root = Path(source_value)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    destination = manifest["bundle_subdirectory"]
    bundle_datas = []
    for entry in manifest["files"]:
        source_file = source_root / entry["path"]
        bundle_datas.append((str(source_file), destination))
    return bundle_datas


datas = [
    (str(ROOT / "assets" / "diagnostics" / "stt-smoke.wav"), "assets/diagnostics"),
    (str(ROOT / "assets" / "branding" / "interview-assistant.ico"), "assets/branding"),
    (str(ROOT / "config" / "mcp.template.json"), "config"),
    (str(MANIFEST_PATH), "packaging"),
]
datas.extend(_optional_stt_bundle_datas())
binaries = []
hiddenimports = []
for package in PACKAGES:
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas.extend(_without_model_weights(package_datas))
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hiddenimports)
for distribution in DISTRIBUTIONS:
    datas.extend(copy_metadata(distribution))

# Explicit backend imports make keyring usable in a frozen process even when the
# active Windows backend is selected through entry-point discovery.
hiddenimports.extend(
    (
        "keyring.backends.Windows",
        "keyring.backends.fail",
        "pynput.keyboard._win32",
    )
)

analysis = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=("pytest", "mypy", "ruff"),
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="InterviewAssistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(ROOT / "packaging" / "version_info.txt"),
    icon=str(ROOT / "assets" / "branding" / "interview-assistant.ico"),
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="InterviewAssistant",
)
