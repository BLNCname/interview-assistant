# Liquid Glass Branding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate the approved prismatic liquid-glass Focus Spark artwork and embed one consistent icon in the application executable, Qt windows, shortcuts, and Windows installer.

**Architecture:** Store one inspected high-resolution PNG and one deterministic multi-resolution ICO under `assets/branding/`. PyInstaller embeds the ICO in the EXE and includes it as runtime data; `main.py` resolves the same resource for Qt, while Inno Setup uses the ICO for the setup executable.

**Tech Stack:** OpenAI built-in image generation, Pillow, Python 3.12, PyQt6, PyInstaller 6, Inno Setup 6.7.2, pytest

## Global Constraints

- Use the approved black field, rounded dark liquid-glass panel, four-petal Focus Spark, and prismatic emerald `#50DE73` edge light.
- Do not include text, a wordmark, third-party branding, or broad neon-green fills.
- The ICO must contain 16, 24, 32, 48, 64, 128, and 256 pixel frames.
- The installer contents policy remains application runtime, CUDA/cuDNN, manifest-pinned STT bundle, and branding only.
- Do not include LM Studio, LLM weights, LM Link, MCP configuration, tokens, or user settings.

---

### Task 1: Generate and validate branding assets

**Files:**
- Create: `assets/branding/interview-assistant-logo.png`
- Create: `assets/branding/interview-assistant.ico`
- Modify: `tests/unit/test_task16_packaging.py`

**Interfaces:**
- Consumes: approved liquid-glass visual specification and exact accent `#50DE73`.
- Produces: a square PNG master and a Pillow-readable multi-frame Windows ICO.

- [ ] **Step 1: Generate the PNG master**

Use the built-in image generation tool with a `logo-brand` prompt specifying a centered square composition, black field, dark rounded liquid-glass panel, original four-petal Focus Spark, controlled prismatic `#50DE73` edge reflections, bold small-size silhouette, and no text, watermark, or third-party branding. Copy the selected generated image into `assets/branding/interview-assistant-logo.png`.

- [ ] **Step 2: Inspect the PNG**

Open the project copy at original detail. Require a centered panel, four distinct petals, one center spark, a black outer field, and restrained emerald highlights. Regenerate once with a targeted correction if these invariants are missing.

- [ ] **Step 3: Convert the PNG to ICO deterministically**

Run a Pillow conversion that center-crops to square, resizes the master to 1024×1024 with `Image.Resampling.LANCZOS`, saves the normalized PNG, and writes:

```python
master.save(
    ico_path,
    format="ICO",
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
```

- [ ] **Step 4: Write the failing asset contract test before wiring packaging**

Add a test that opens both files with Pillow, requires the PNG to be square and at least 1024 pixels wide, iterates every ICO frame, and asserts:

```python
assert {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)} <= frame_sizes
```

- [ ] **Step 5: Run the asset test**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\unit\test_task16_packaging.py -q
```

Expected: PASS after both generated assets validate.

### Task 2: Wire the icon into Qt, PyInstaller, and Inno Setup using TDD

**Files:**
- Modify: `tests/unit/test_main.py`
- Modify: `tests/unit/test_task16_packaging.py`
- Modify: `tests/unit/test_release_packaging.py`
- Modify: `main.py`
- Modify: `packaging/interview_assistant.spec`
- Modify: `packaging/interview_assistant.iss`

**Interfaces:**
- Consumes: `assets/branding/interview-assistant.ico` from Task 1.
- Produces: `_application_icon_path() -> Path`, a Qt application icon, an EXE icon resource, and an Inno Setup icon.

- [ ] **Step 1: Write failing integration tests**

Extend the fake Qt application with `setWindowIcon()` recording the supplied icon. Assert the GUI path sets a non-null icon whose source resolves to `assets/branding/interview-assistant.ico`. Add packaging assertions for:

```python
assert '(str(ROOT / "assets" / "branding" / "interview-assistant.ico"), "assets/branding")' in spec_source
assert 'icon=str(ROOT / "assets" / "branding" / "interview-assistant.ico")' in spec_source
assert 'SetupIconFile={#SourcePath}\\..\\assets\\branding\\interview-assistant.ico' in iss_source
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\unit\test_main.py tests\unit\test_task16_packaging.py tests\unit\test_release_packaging.py -q
```

Expected: FAIL because runtime and packaging icon wiring is absent.

- [ ] **Step 3: Implement Qt resource resolution**

Import `QIcon`, add:

```python
def _application_icon_path() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    root = Path(frozen_root) if isinstance(frozen_root, str) else Path(__file__).resolve().parent
    return root / "assets" / "branding" / "interview-assistant.ico"
```

Immediately after `QApplication(runtime_argv)`, construct `QIcon(str(_application_icon_path()))`, require it to be non-null, and call `qt_app.setWindowIcon(icon)`.

- [ ] **Step 4: Implement packaging wiring**

Add the ICO tuple to the PyInstaller `datas` list, pass the same path through the EXE `icon=` keyword, and add this Setup directive:

```ini
SetupIconFile={#SourcePath}\..\assets\branding\interview-assistant.ico
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the three focused test files again. Expected: all pass.

- [ ] **Step 6: Run static and full regression checks**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check main.py tests\unit\test_main.py tests\unit\test_task16_packaging.py tests\unit\test_release_packaging.py
.venv\Scripts\python.exe -m pytest -q
```

Expected: Ruff exits 0 and all tests pass.

- [ ] **Step 7: Commit implementation**

Stage only the two branding assets, three production files, and three focused test files; commit with `feat: add liquid glass application branding`.

### Task 3: Rebuild and verify final release artifacts

**Files:**
- Produce: `dist/InterviewAssistant/*`
- Replace: `dist/release/InterviewAssistant-Setup-0.1.0-win64.exe`
- Produce: `dist/release/InterviewAssistant-source-0.1.0.zip`
- Produce: `dist/release/SHA256SUMS.txt`

**Interfaces:**
- Consumes: committed branding implementation and validated `build/release-stt-model`.
- Produces: final branded installer, sanitized editable source archive, checksums, and smoke evidence.

- [ ] **Step 1: Build the branded onedir distribution**

Run `scripts/build.ps1` with `-SkipTests -VerifyCuda`, the active local config path, and `build/release-stt-model`. Require packaged diagnostics, bundled CUDA STT inference, EXE existence, and no bundled `config.yaml`.

- [ ] **Step 2: Build the branded installer**

Run `scripts/build_installer.ps1` with Inno Setup 6.7.2 and version `0.1.0`. Require the versioned setup executable to be non-empty.

- [ ] **Step 3: Repeat isolated installer smoke testing**

Install silently into the verified workspace-contained `build/installer-smoke` path. Revalidate exact installed inventory and every STT size/SHA-256 against the manifest, run installed frozen diagnostics with a missing config, require `status=ok` and `frozen=true`, then silently uninstall and require installer-owned EXE/runtime files to be removed.

- [ ] **Step 4: Create and inspect the source archive**

Run `scripts/create_source_archive.ps1` with an explicit pinned source commit and the validated STT model. Extract to a verified temporary directory and require one top-level folder, all six STT files, a generated sanitized `config.yaml` with all seven hotkeys, standalone clean Git history, no remotes/reflogs, and no live machine config, cache, venv, or user data.

- [ ] **Step 5: Scan for credential material**

Scan all reachable Git blobs and commit/tag objects plus extracted regular files for token prefixes and private-key markers. Never print matching values. Require the inspector's explicit expected commit to equal the archived `HEAD`.

- [ ] **Step 6: Generate and verify checksums**

Write uppercase SHA-256 plus filenames for the installer and source ZIP to `SHA256SUMS.txt`, recompute both digests, and require exact equality.

- [ ] **Step 7: Record final inventory**

Report absolute paths, byte sizes, SHA-256 values, unsigned Authenticode status, STT revision, test count, and final installer smoke-test result.
