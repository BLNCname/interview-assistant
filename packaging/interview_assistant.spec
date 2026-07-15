# -*- mode: python ; coding: utf-8 -*-
"""Reproducible onedir bundle for the Windows Interview Assistant."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata


ROOT = Path(SPECPATH).resolve().parent
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
)


def _without_model_weights(items):
    return [
        item
        for item in items
        if Path(item[0]).suffix.casefold() not in MODEL_WEIGHT_SUFFIXES
    ]


datas = [
    (str(ROOT / "assets" / "diagnostics" / "stt-smoke.wav"), "assets/diagnostics"),
    (str(ROOT / "config" / "mcp.template.json"), "config"),
]
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
