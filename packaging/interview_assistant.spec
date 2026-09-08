# -*- mode: python ; coding: utf-8 -*-
"""Reproducible onedir bundle for the Windows Interview Assistant."""

import json
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata, get_package_paths


ROOT = Path(SPECPATH).resolve().parent
MANIFEST_PATH = ROOT / "packaging" / "stt_model_manifest.json"
STT_MODEL_ENVIRONMENT = "INTERVIEW_ASSISTANT_STT_MODEL_PATH"
MODEL_WEIGHT_SUFFIXES = frozenset(
    {".bin", ".ckpt", ".gguf", ".pt", ".pth", ".safetensors"}
)
# Dependency analysis must use runtimes belonging to this environment/Windows.
# Other desktop apps and development tools can put incompatible DLLs on PATH;
# collecting those at _internal's root shadows the reviewed package runtimes.
# This changes only the current build process, never the user's system PATH.
QT_BIN = Path(get_package_paths("PyQt6")[1]) / "Qt6" / "bin"
WINDOWS_ROOT = Path(os.environ.get("SystemRoot", r"C:\Windows"))
dependency_paths = (
    QT_BIN,
    Path(sys.executable).parent,
    Path(sys.base_prefix),
    Path(sys.base_prefix) / "DLLs",
    WINDOWS_ROOT / "System32",
    WINDOWS_ROOT,
)
os.environ["PATH"] = os.pathsep.join(dict.fromkeys(
    str(path) for path in dependency_paths if path.is_dir()
))

PACKAGES = (
    "PyQt6",
    "qasync",
    "faster_whisper",
    "ctranslate2",
    "mss",
    "keyring",
    "pyaudiowpatch",
    "pynput",
    "mcp.client",
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
    "mcp",
    "python-dotenv",
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
    (str(ROOT / "assets" / "diagnostics" / "speech-fixtures.json"), "assets/diagnostics"),
    (str(ROOT / "assets" / "diagnostics" / "stt-en.wav"), "assets/diagnostics"),
    (str(ROOT / "assets" / "diagnostics" / "stt-ru.wav"), "assets/diagnostics"),
    (str(ROOT / "assets" / "branding" / "interview-assistant.ico"), "assets/branding"),
    (str(MANIFEST_PATH), "packaging"),
    (str(ROOT / "prompts" / "interview_system.md"), "prompts"),
]
datas.extend(_optional_stt_bundle_datas())
binaries = []
hiddenimports = []
for package in PACKAGES:
    # Collect the MCP client transports, leaving normal import analysis to keep
    # SDK server primitives imported by mcp.__init__. No bundled search server
    # or developer command-line entry point is needed by the application.
    package_datas, package_binaries, package_hiddenimports = collect_all(
        package, filter_submodules=lambda name: name != "mcp.client.__main__"
    )
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
        "interview_assistant.diagnostics.selftest",
        "interview_assistant.diagnostics.cli",
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
