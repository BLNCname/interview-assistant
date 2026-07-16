# Windows Migration Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the completed Interview Assistant checkout reproducibly runnable on the current Windows/RTX 5070 Ti machine with CUDA 12 STT and a graphite, capture-affinity-compatible Liquid Ribbon.

**Architecture:** Keep the three repair tracks isolated at code level: repository/package hygiene, process-local NVIDIA runtime discovery, and top-level Qt window rendering. Complete and commit each track independently, then run hardware/package acceptance before converting the finished linked worktree into the repository root checkout.

**Tech Stack:** Git worktrees, Python 3.11, uv, PyQt6, CTranslate2 4.8.1, faster-whisper, official NVIDIA CUDA 12/cuDNN 8 Windows wheels, PyInstaller, pytest/pytest-qt, Ruff, mypy, PowerShell.

## Global Constraints

- Preserve `main` and `feature/interview-assistant` history; never rewrite user commits.
- CUDA runtime must remain 12.x; cuDNN must be exactly `8.9.7.29` for the approved CTranslate2 speech configuration.
- Do not replace or downgrade the installed NVIDIA display driver or CUDA 13 toolkits.
- Keep LM Studio credentials only in Windows Credential Manager service `InterviewAssistant`, username `lmstudio_api_token`.
- Top-level `LiquidRibbon` must not set `WA_TranslucentBackground`.
- Effective whole-window opacity is `max(0.65, configured_opacity)` and defaults to `0.88`.
- Ribbon surface colors are graphite `#34363A` → `#25262A`; cyan/teal remains only as a functional accent.
- External corner radius is 18 px; collapsed height remains 48 px.
- Every production-code change follows RED → GREEN → regression verification.

---

### Task 0: Archive staging isolation from process TEMP

**Files:**
- Modify: `scripts/create_source_archive.ps1`
- Test: `tests/unit/test_release_packaging.py`

**Interfaces:**
- Consumes: optional archive-only `INTERVIEW_ASSISTANT_ARCHIVE_TEMP`, including paths longer than 260 characters; otherwise falls back to `TEMP`/`TMP`.
- Produces: normal staging without replacing process-level `TEMP`/`TMP`, plus a fast fail-closed error before creation when the resulting `.git` path would exceed Git for Windows' 260-character limit.

- [x] **Step 1: Confirm the existing failing regression test (RED)**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_release_packaging.py::test_source_archive_rejects_unsupported_long_staging_without_leaking_staging -v
```

Observed baseline: FAIL after 60 seconds. A minimal probe proved that Windows PowerShell 5.1 itself cannot start when the inherited `TEMP` is the deliberately longer-than-260-character test path, so no code inside the script can repair that environment after launch.

- [x] **Step 2: Add a dedicated archive staging override**

Update the test launcher to keep its normal process `TEMP`/`TMP` and pass its requested staging root through `INTERVIEW_ASSISTANT_ARCHIVE_TEMP`. Update `Get-ConfiguredTemporaryRoot` to prefer that archive-only variable and retain `TEMP`/`TMP` as backward-compatible fallbacks. Before creating staging, calculate the anticipated `.git` path and reject values at or above 260 characters with a clear error. Add source-contract assertions so future changes cannot accidentally reintroduce `[IO.Path]::GetTempPath()`, long process `TEMP` inheritance, or a Git hang.

- [x] **Step 3: Verify GREEN and regression behavior**

Run the focused test from Step 1; expect PASS in less than 60 seconds. Then run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_release_packaging.py -v
```

Expected: the full release-packaging module passes, including standalone archive construction and cleanup.

- [x] **Step 4: Commit the root-cause fix**

```powershell
git add scripts/create_source_archive.ps1 tests/unit/test_release_packaging.py docs/superpowers/plans/2026-07-16-windows-migration-repair.md
git commit -m "fix: isolate long archive staging from process temp"
```

---

### Task 1: Reproducible package layout

**Files:**
- Modify: `.gitignore`
- Modify: `pyproject.toml`
- Modify: `tests/unit/test_package.py`
- Delete generated artifacts only: `src/interview_assistant.egg-info/`, `src/__pycache__/`

**Interfaces:**
- Consumes: setuptools editable installation from the repository root.
- Produces: explicit package discovery for `interview_assistant*`; generated `src` metadata can never become an import source.

- [x] **Step 1: Write the failing package-layout test**

Append to `tests/unit/test_package.py`:

```python
from pathlib import Path


def test_setuptools_discovers_only_the_root_package() -> None:
    root = Path(__file__).parents[2]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

    assert '[tool.setuptools.packages.find]' in pyproject
    assert 'include = ["interview_assistant*"]' in pyproject
    assert 'exclude = ["src*"]' in pyproject
    assert "*.egg-info/" in (root / ".gitignore").read_text(encoding="utf-8")
```

- [x] **Step 2: Run the test and confirm RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_package.py::test_setuptools_discovers_only_the_root_package -v`

Expected: FAIL because `[tool.setuptools.packages.find]` is absent.

- [x] **Step 3: Add explicit discovery and ignore rules**

Append to `pyproject.toml`:

```toml
[tool.setuptools.packages.find]
where = ["."]
include = ["interview_assistant*"]
exclude = ["src*"]
```

Append to `.gitignore`:

```gitignore
*.egg-info/
```

- [x] **Step 4: Verify GREEN and clean only generated artifacts**

Run the same focused pytest command; expect PASS.

Resolve the two generated locations, verify both are inside the current worktree, and remove only those targets:

```powershell
$root = (Resolve-Path -LiteralPath .).Path
$targets = @(
    (Join-Path $root "src\interview_assistant.egg-info"),
    (Join-Path $root "src\__pycache__")
)
foreach ($target in $targets) {
    $full = [System.IO.Path]::GetFullPath($target)
    if (-not $full.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar)) {
        throw "Refusing to remove path outside worktree: $full"
    }
    if (Test-Path -LiteralPath $full) {
        Remove-Item -LiteralPath $full -Recurse -Force
    }
}
```

Then run:

```powershell
uv sync --extra dev --frozen
.\.venv\Scripts\python.exe -c "import interview_assistant, pathlib; print(pathlib.Path(interview_assistant.__file__).resolve())"
```

Expected: import path ends in `\interview_assistant\__init__.py`, not `\src\...`.

- [x] **Step 5: Commit package-layout repair**

```powershell
git add .gitignore pyproject.toml tests/unit/test_package.py
git commit -m "build: make package discovery deterministic"
```

---

### Task 2: Process-local CUDA 12/cuDNN 8 discovery

**Files:**
- Create: `interview_assistant/windows_cuda.py`
- Create: `tests/unit/test_windows_cuda.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces: `CudaRuntimeStatus(directories: tuple[Path, ...], missing_dlls: tuple[str, ...])` with property `ready: bool`.
- Produces: `configure_cuda_runtime(search_directories: Iterable[Path] | None = None, add_directory: Callable[[str], object] | None = None) -> CudaRuntimeStatus`.
- Required DLL names: `cudart64_12.dll`, `cublas64_12.dll`, `cublasLt64_12.dll`, `cudnn64_8.dll`.

- [x] **Step 1: Write failing runtime-discovery tests**

Create `tests/unit/test_windows_cuda.py`:

```python
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
```

- [x] **Step 2: Run tests and confirm RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_windows_cuda.py -v`

Expected: collection ERROR with `ModuleNotFoundError: interview_assistant.windows_cuda`.

- [x] **Step 3: Implement the minimal discovery module**

Create `interview_assistant/windows_cuda.py` with:

```python
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
                _default_directories()
                if search_directories is None
                else search_directories
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
```

- [x] **Step 4: Verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_windows_cuda.py -v`

Expected: 2 passed.

- [x] **Step 5: Declare and lock official NVIDIA runtime wheels**

Add to `pyproject.toml` under `[project.optional-dependencies]`:

```toml
cuda = [
  "nvidia-cuda-runtime-cu12==12.9.79",
  "nvidia-cublas-cu12==12.9.2.10",
  "nvidia-cudnn-cu12==8.9.7.29",
]
```

Run:

```powershell
uv lock
uv sync --extra dev --extra cuda --frozen
```

Expected: official Windows wheels are installed under `.venv\Lib\site-packages\nvidia`; CUDA 13 and the display driver are unchanged.

- [x] **Step 6: Commit runtime discovery**

```powershell
git add interview_assistant/windows_cuda.py tests/unit/test_windows_cuda.py pyproject.toml uv.lock
git commit -m "feat: configure CUDA 12 runtime libraries"
```

---

### Task 3: Wire CUDA diagnostics and STT entry points

**Files:**
- Modify: `interview_assistant/diagnostics/cli.py`
- Modify: `interview_assistant/stt/engine.py`
- Modify: `scripts/verify_cuda.py`
- Modify: `tests/unit/test_task16_packaging.py`
- Modify: `tests/unit/test_stt_engine.py`

**Interfaces:**
- Consumes: `configure_cuda_runtime() -> CudaRuntimeStatus` from Task 2.
- Produces: diagnostics `cuda.runtime` with `status`, `missing_dlls`, and `search_directories`.
- CUDA model creation configures DLL search before importing faster-whisper.

- [x] **Step 1: Add failing diagnostics assertions**

Add this import to `tests/unit/test_task16_packaging.py`:

```python
from interview_assistant.windows_cuda import CudaRuntimeStatus
```

Then add:

```python
def test_runtime_report_lists_cuda_search_directories_and_missing_dlls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_diagnostics_module()
    monkeypatch.setattr(
        module,
        "configure_cuda_runtime",
        lambda: CudaRuntimeStatus(
            (Path(r"C:\CUDA12\bin"), Path(r"C:\CUDNN8\bin")),
            ("cudnn64_8.dll",),
        ),
    )
    monkeypatch.setattr(module, "_dependency_version", lambda *_args: "1.0")
    monkeypatch.setattr(module, "_cpu_compute_types", lambda: ("float32",))
    monkeypatch.setattr(module, "_cuda_device_count", lambda: 1)

    report = module.build_runtime_report(config_path=None)

    assert report["cuda"]["runtime"] == {
        "status": "failed",
        "missing_dlls": ["cudnn64_8.dll"],
        "search_directories": [r"C:\CUDA12\bin", r"C:\CUDNN8\bin"],
    }
```

- [x] **Step 2: Run the focused test and confirm RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_task16_packaging.py -k runtime_report_lists_cuda -v`

Expected: FAIL because `cuda.runtime` is absent.

- [x] **Step 3: Add the diagnostics report**

Import `configure_cuda_runtime` in `interview_assistant/diagnostics/cli.py`. At the start of `build_runtime_report`, call it once. Add this object inside the existing `cuda` mapping on both success and warning paths:

```python
"runtime": {
    "status": "ready" if runtime.ready else "failed",
    "missing_dlls": list(runtime.missing_dlls),
    "search_directories": [str(path) for path in runtime.directories],
},
```

The overall report remains `status="ok"` when CPU dependencies/config are valid; GPU runtime remains a readiness warning until hardware verification.

- [x] **Step 4: Verify diagnostics GREEN**

Run the focused command from Step 2; expect PASS.

- [x] **Step 5: Add failing model-factory ordering tests**

Add `import sys`, `from types import ModuleType`, and `from interview_assistant.stt import engine as engine_module` to `tests/unit/test_stt_engine.py`, then add:

```python
def test_cuda_model_factory_configures_runtime_before_whisper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    fake_module = ModuleType("faster_whisper")

    def whisper_model(*_args: object, **_kwargs: object) -> FakeWhisperModel:
        calls.append("WhisperModel")
        return FakeWhisperModel()

    fake_module.WhisperModel = whisper_model  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(
        engine_module,
        "configure_cuda_runtime",
        lambda: calls.append("configure_cuda_runtime"),
    )

    engine_module._create_whisper_model(
        "test-model",
        device="cuda",
        compute_type="float16",
    )

    assert calls == ["configure_cuda_runtime", "WhisperModel"]
```

Add to `tests/unit/test_task16_packaging.py`:

```python
def test_cuda_verifier_configures_runtime_before_whisper_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_cuda_module()
    calls: list[str] = []
    fake_module = ModuleType("faster_whisper")

    def whisper_model(*_args: object, **_kwargs: object) -> _Model:
        calls.append("WhisperModel")
        return _Model()

    fake_module.WhisperModel = whisper_model  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(
        module,
        "configure_cuda_runtime",
        lambda: calls.append("configure_cuda_runtime"),
    )

    module._create_model("test-model", device="cuda", compute_type="float16")

    assert calls == ["configure_cuda_runtime", "WhisperModel"]
```

Also add `import sys` to this test module; `ModuleType` is already imported.

- [x] **Step 6: Run ordering tests and confirm RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_stt_engine.py -k configures_runtime_before_whisper -v
.\.venv\Scripts\python.exe -m pytest tests\unit\test_task16_packaging.py -k configures_runtime_before_whisper -v
```

Expected: FAIL because neither entry point configures DLL search.

- [x] **Step 7: Wire both entry points**

In `interview_assistant/stt/engine.py`, import `configure_cuda_runtime` and make the factory body:

```python
def _create_whisper_model(
    model_name: str,
    *,
    device: str,
    compute_type: str,
    local_files_only: bool = False,
) -> _WhisperModel:
    if device == "cuda":
        configure_cuda_runtime()
    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    model_options: dict[str, object] = {
        "device": device,
        "compute_type": compute_type,
    }
    if local_files_only:
        model_options["local_files_only"] = True
    return cast(
        _WhisperModel,
        WhisperModel(model_name, **model_options),
    )
```

In `scripts/verify_cuda.py`, import `configure_cuda_runtime` and make `_create_model` begin with:

```python
configure_cuda_runtime()
from faster_whisper import WhisperModel  # type: ignore[import-untyped]
```

- [x] **Step 8: Verify GREEN and commit**

Run both focused commands, then:

```powershell
git add interview_assistant/diagnostics/cli.py interview_assistant/stt/engine.py scripts/verify_cuda.py tests/unit/test_stt_engine.py tests/unit/test_task16_packaging.py
git commit -m "feat: expose CUDA runtime readiness"
```

---

### Task 4: Capture-compatible graphite Ribbon

**Files:**
- Modify: `interview_assistant/ui/overlay.py`
- Modify: `tests/unit/test_overlay.py`

**Interfaces:**
- Produces: `LiquidRibbon.effective_window_opacity: float`.
- Produces: `LiquidRibbon._update_window_mask() -> None`, called after construction and from `resizeEvent`.
- Preserves: affinity HWND lifecycle, state machine, drag/resize, collapse height 48 px.

- [x] **Step 1: Replace the old translucency test with failing capture-compatible assertions**

Update `test_window_uses_overlay_flags_and_translucent_surface` in `tests/unit/test_overlay.py` to:

```python
def test_window_uses_capture_compatible_opacity_and_rounded_mask(qtbot) -> None:
    ribbon = LiquidRibbon(
        EventBus(),
        config=OverlayConfig(opacity=0.2, max_height=280),
        settings=None,
    )
    qtbot.addWidget(ribbon)

    assert not ribbon.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert ribbon.effective_window_opacity == 0.65
    assert math.isclose(ribbon.windowOpacity(), 0.65)
    assert not ribbon.mask().isEmpty()
    assert ribbon.surface.corner_radius == 18
```

Add:

```python
def test_default_ribbon_uses_approved_graphite_palette(qtbot) -> None:
    ribbon = LiquidRibbon(EventBus(), settings=None)
    qtbot.addWidget(ribbon)

    assert ribbon.effective_window_opacity == 0.88
    assert ribbon.surface.gradient_top_rgb == (52, 54, 58)
    assert ribbon.surface.gradient_bottom_rgb == (37, 38, 42)


def test_window_mask_tracks_resize(qtbot) -> None:
    ribbon = LiquidRibbon(EventBus(), settings=None)
    qtbot.addWidget(ribbon)
    ribbon.resize(780, 180)
    QApplication.processEvents()

    assert ribbon.mask().boundingRect() == ribbon.rect()
```

- [x] **Step 2: Run the three tests and confirm RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_overlay.py -k "capture_compatible_opacity or approved_graphite or window_mask_tracks_resize" -v
```

Expected: FAIL on top-level translucent attribute, missing property/mask, and old blue palette.

- [x] **Step 3: Implement whole-window opacity and mask**

In `interview_assistant/ui/overlay.py`:

- Change `_SURFACE_TOP_RGB` to `(52, 54, 58)` and `_SURFACE_BOTTOM_RGB` to `(37, 38, 42)`.
- Change `_ANSWER_BACKGROUND_RGB` to `(18, 18, 20)`.
- Remove `self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)` from `LiquidRibbon.__init__`; retain child `_RibbonSurface` painting.
- Add `effective_window_opacity` and apply it before building content:

```python
self.effective_window_opacity = max(0.65, self._config.opacity)
self.setWindowOpacity(self.effective_window_opacity)
```

- Add mask methods:

```python
def _update_window_mask(self) -> None:
    if self.width() <= 0 or self.height() <= 0:
        return
    path = QPainterPath()
    path.addRoundedRect(QRectF(self.rect()), 18.0, 18.0)
    self.setMask(QRegion(path.toFillPolygon().toPolygon()))

def resizeEvent(self, event: QResizeEvent | None) -> None:
    super().resizeEvent(event)
    self._update_window_mask()
```

Import `QRegion` from `PyQt6.QtGui`. Call `_update_window_mask()` once after `_restore_geometry()`.

In `apply_config`, update both values before geometry changes:

```python
self.effective_window_opacity = max(0.65, config.opacity)
self.setWindowOpacity(self.effective_window_opacity)
```

- [x] **Step 4: Remove decorative blue/violet edge tint**

Replace the edge gradient in `_RibbonSurface.paintEvent` with a neutral highlight:

```python
edge.setColorAt(0.0, QColor(255, 255, 255, 72))
edge.setColorAt(0.45, QColor(255, 255, 255, 0))
edge.setColorAt(1.0, QColor(255, 255, 255, 42))
```

Keep `_MODEL_CHIP_RGB` as the small cyan functional accent; change neutral secondary text inputs from blue-gray `(148, 163, 184)` to `(174, 174, 178)` and answer text preference to `(232, 232, 234)`.

- [x] **Step 5: Verify GREEN and the full Ribbon regression set**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_overlay.py -v
.\.venv\Scripts\python.exe -m pytest tests\unit\test_bootstrap.py tests\unit\test_app.py tests\unit\test_production_probes.py -v
```

Expected: all tests pass; update existing assertions that intentionally encode the old RGB values, never affinity/state behavior.

- [ ] **Step 6: Commit Ribbon repair**

```powershell
git add interview_assistant/ui/overlay.py tests/unit/test_overlay.py
git commit -m "fix: make graphite ribbon capture compatible"
```

---

### Task 5: Bundle CUDA runtime in packaged builds

**Files:**
- Modify: `packaging/interview_assistant.spec`
- Modify: `scripts/build.ps1`
- Modify: `tests/unit/test_task16_packaging.py`

**Interfaces:**
- Consumes: optional dependency group `cuda` from Task 2.
- Produces: PyInstaller onedir `_internal/nvidia/{cuda_runtime,cublas,cudnn}/bin` and frozen-process discovery using the same relative layout.

- [ ] **Step 1: Write failing packaging assertions**

Extend `test_pyinstaller_spec_declares_reproducible_onedir_windowed_bundle`:

```python
for package in ("nvidia.cuda_runtime", "nvidia.cublas", "nvidia.cudnn"):
    assert package in source
```

Extend `test_build_script_runs_packaged_headless_diagnostics_and_optional_cuda`:

```python
assert "uv sync --extra dev --extra cuda --frozen" in source
```

- [ ] **Step 2: Run and confirm RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_task16_packaging.py -k "pyinstaller_spec or build_script" -v`

Expected: FAIL because NVIDIA packages and the CUDA extra are absent.

- [ ] **Step 3: Include the two NVIDIA runtime packages**

Add these exact entries to `PACKAGES` in `packaging/interview_assistant.spec`:

```python
    "nvidia.cuda_runtime",
    "nvidia.cublas",
    "nvidia.cudnn",
```

Add these exact entries to `DISTRIBUTIONS`:

```python
    "nvidia-cuda-runtime-cu12",
    "nvidia-cublas-cu12",
    "nvidia-cudnn-cu12",
```

Replace `_default_directories` in `windows_cuda.py` with:

```python
def _default_directories() -> tuple[Path, ...]:
    if bool(getattr(sys, "frozen", False)):
        package_root = Path(getattr(sys, "_MEIPASS"))
    else:
        package_root = Path(sys.prefix) / "Lib" / "site-packages"
    nvidia_root = package_root / "nvidia"
    candidates = (
        nvidia_root / "cuda_runtime" / "bin",
        nvidia_root / "cublas" / "bin",
        nvidia_root / "cudnn" / "bin",
    )
    return tuple(path.resolve() for path in candidates if path.is_dir())
```

Change the existing build sync command in `scripts/build.ps1` to:

```powershell
& $uv.Source sync --extra dev --extra cuda --frozen
```

- [ ] **Step 4: Verify GREEN and commit**

Run the focused pytest command and `uv lock --check`; expect PASS, then:

```powershell
git add packaging/interview_assistant.spec scripts/build.ps1 interview_assistant/windows_cuda.py tests/unit/test_task16_packaging.py tests/unit/test_windows_cuda.py
git commit -m "build: bundle CUDA runtime for Windows"
```

---

### Task 6: Automated regression and real hardware acceptance

**Files:**
- Modify only if results require factual documentation: `PROJECT_ANALYSIS_REPORT.md`

**Interfaces:**
- Consumes: all commits from Tasks 1–5.
- Produces: objective evidence for Python quality, CUDA STT, capture affinity, packaged diagnostics, and GUI liveness.

- [ ] **Step 1: Run static and unit/integration checks**

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy interview_assistant main.py
.\.venv\Scripts\python.exe -m compileall -q interview_assistant main.py scripts\verify_cuda.py
.\.venv\Scripts\python.exe -m pytest -v
```

Expected: Ruff/mypy/compileall succeed and all tests pass. If the known PowerShell 5.1 long-TEMP packaging test still times out, rerun that exact test with a short explicit TEMP and record both outputs; do not label the suite green until the short-TEMP invocation passes.

- [ ] **Step 2: Verify installed runtime in a fresh process**

```powershell
.\.venv\Scripts\python.exe -c "from interview_assistant.windows_cuda import configure_cuda_runtime; s=configure_cuda_runtime(); print({'ready':s.ready,'missing':s.missing_dlls,'dirs':[str(p) for p in s.directories]})"
.\.venv\Scripts\python.exe -c "from interview_assistant.windows_cuda import configure_cuda_runtime; configure_cuda_runtime(); import ctranslate2; print(ctranslate2.get_cuda_device_count())"
```

Expected: `ready=True`, no missing DLLs, and CUDA device count at least 1.

- [ ] **Step 3: Run real faster-whisper inference**

Use the existing sanitized verifier with the current non-secret config and bundled/test model path:

```powershell
.\.venv\Scripts\python.exe scripts\verify_cuda.py --config config.yaml --fixture assets\diagnostics\stt-smoke.wav
```

Expected JSON: `status="ok"`, `device="cuda"`, positive `text_characters`, and finite timings. The report must not contain transcript text, model identity, LM token, or private cache paths.

- [ ] **Step 4: Run real Windows capture-affinity probe**

Run:

```powershell
@'
from PyQt6.QtWidgets import QApplication
from interview_assistant.events import EventBus
from interview_assistant.ui.overlay import LiquidRibbon

app = QApplication([])
ribbon = LiquidRibbon(EventBus(), settings=None)
ribbon.show()
app.processEvents()
result = ribbon.affinity_result
print({
    "ok": None if result is None else result.ok,
    "value": None if result is None else result.value,
    "error_code": None if result is None else result.error_code,
})
ribbon.close()
app.processEvents()
raise SystemExit(0 if result is not None and result.ok and result.value == 17 else 1)
'@ | .\.venv\Scripts\python.exe -
```

Expected: `{'ok': True, 'value': 17, 'error_code': None}` and exit code 0.

- [ ] **Step 5: Build and test the packaged application**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build.ps1
dist\InterviewAssistant\InterviewAssistant.exe --diagnostics --no-gui --config config.yaml
```

Expected: build exit 0, diagnostics exit 0, packaged CUDA runtime ready. Start the GUI EXE, keep it alive for 15 seconds, confirm the process remains running, then close it normally.

- [ ] **Step 6: Inspect for secret leakage and final diff**

```powershell
git grep -n -I -E "sk-lm-|lmstudio_api_token[[:space:]]*[:=][[:space:]]*[^\"']" -- . ":(exclude)docs/superpowers/plans/*"
git status --short
git log --oneline --decorate -8
```

Expected: no credential value, only known pre-existing generated/stat-only files outside committed scope, and the planned commits are present.

---

### Task 7: Final Git/worktree normalization

**Files:**
- Git metadata only: `.git/refs/codex/...`, `.git/worktrees/feature-interview-assistant/*`, linked worktree `.git` pointer.

**Interfaces:**
- Consumes: clean, verified `feature/interview-assistant` HEAD.
- Produces: repository root checked out on `feature/interview-assistant`, no stale linked worktree, valid refs.

- [ ] **Step 1: Verify repaired worktree pointers and the exact broken generated ref**

Before touching refs, run from the repository root:

```powershell
Get-Content -LiteralPath .worktrees\feature-interview-assistant\.git
Get-Content -LiteralPath .git\worktrees\feature-interview-assistant\gitdir
```

Expected values are respectively:

```text
gitdir: C:/Users/BLNCname/Desktop/assistant/interview-assistant/.git/worktrees/feature-interview-assistant
C:/Users/BLNCname/Desktop/assistant/interview-assistant/.worktrees/feature-interview-assistant/.git
```

Run `git for-each-ref`, `git cat-file -e` for its recorded object, and confirm the only warning is the generated `refs/codex/turn-diffs/checkpoints/.../f0353d56-08d4-48f7-b2f6-a4cc58849d0f` ref. Confirm its resolved file is under the root repository `.git\refs\codex\turn-diffs\checkpoints` before deleting that single ref file. Do not delete the valid `captures/*/base` refs.

- [ ] **Step 2: Validate repository objects and both worktrees**

```powershell
git fsck --full
git worktree list --porcelain
git -C .worktrees\feature-interview-assistant status --short --branch
git status --short --branch
```

Expected: no invalid objects/refs; hidden worktree is clean on `feature/interview-assistant`; root is clean on `main`.

- [ ] **Step 3: Record immutable recovery points**

Record `git rev-parse main` and `git rev-parse feature/interview-assistant`. Create a safety tag only if no equivalent local recovery ref already exists:

```powershell
git tag migration-pre-root-switch feature/interview-assistant
```

- [ ] **Step 4: Remove the verified linked worktree and switch the root**

From the root, after confirming the hidden worktree is clean:

```powershell
git worktree remove .worktrees\feature-interview-assistant
git switch feature/interview-assistant
git worktree prune
```

If `git worktree remove` reports any modified/untracked file, stop and preserve it; never use `--force`.

- [ ] **Step 5: Recreate the environment in the final root and run smoke checks**

```powershell
uv sync --extra dev --extra cuda --frozen
.\.venv\Scripts\python.exe -m pytest tests\unit\test_package.py tests\unit\test_windows_cuda.py tests\unit\test_overlay.py -q
git worktree list --porcelain
git status --short --branch
```

Expected: only the root worktree exists, it is on `feature/interview-assistant`, focused tests pass, and status is clean.

- [ ] **Step 6: Final acceptance summary**

Report exact commit SHA, CUDA/cuDNN package versions, real STT timing/result, affinity value `0x11`, full test counts, packaged EXE result, remaining limitations, and the Credential Manager location. Do not print or copy the LM Studio token.
