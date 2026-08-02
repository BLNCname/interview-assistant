# Instructor Repository Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a clean instructor-facing repository with one visible installer, an English GitHub-style README, complete clean-machine installation/build instructions, and no duplicate runtime or local development debris.

**Architecture:** Treat the repository itself as the source/audit artifact and the root installer as the only binary handoff. Encode the intended documentation and root-layout contracts in tests, rewrite the two entry documents and checksum metadata, then remove ignored duplicate artifacts and caches only after source verification succeeds.

**Tech Stack:** Markdown, PowerShell 5.1+, Git, Python 3.11/3.12, pytest, Ruff, uv, PyInstaller, Inno Setup 6, LM Studio/LM Link, CTranslate2/faster-whisper, NVIDIA CUDA 12 runtime.

## Global Constraints

- Keep `InterviewAssistant-Setup-0.1.0-win64.exe` visible at repository root and do not rebuild it because runtime and packaging inputs are unchanged.
- Remove the portable root `InterviewAssistant.exe`, `_internal/`, the duplicate source ZIP, `dist/`, `build/`, `.venv/`, caches, bytecode, egg metadata, and temporary verification files.
- Do not terminate LM Studio or any other user process; report a Windows-locked cleanup target instead of forcing process termination.
- `README.md` and `START_HERE.md` must contain English prose only and must not contain credentials, device IDs, user-profile paths, or copied local tokens.
- The installer path is the recommended workflow; it bundles the application, Python/native dependencies, CUDA/cuDNN runtime, branding, prompt, and pinned STT model, but not the NVIDIA driver, LM Studio, LM Link, LLM weights, MCP services, API tokens, or user configuration.
- Source development supports CPython `>=3.11,<3.13`; the committed `uv.lock` is the canonical dependency resolution.
- Preserve the existing fail-closed release inventory and explain that it attests the shipped installer; an independently compiled portable application may be audited without silently replacing the pinned release inventory.
- `SHA256SUMS.txt` must contain exactly one uppercase SHA-256 entry for the visible installer.
- Final ignored contents may contain only the visible installer; Git status must otherwise be clean.

---

### Task 1: Encode the instructor handoff contract

**Files:**
- Modify: `tests/unit/test_release_packaging.py`

**Interfaces:**
- Consumes: approved handoff design and current root filenames.
- Produces: regression contracts for English documentation, clean-machine workflows, one-artifact checksums, and a clutter-detecting `.gitignore`.

- [ ] **Step 1: Add failing documentation and layout tests**

Add path constants and tests equivalent to:

```python
README_PATH = ROOT / "README.md"
START_HERE_PATH = ROOT / "START_HERE.md"
CHECKSUMS_PATH = ROOT / "SHA256SUMS.txt"
GITIGNORE_PATH = ROOT / ".gitignore"


def test_instructor_readme_is_english_and_covers_clean_machine_workflows() -> None:
    text = README_PATH.read_text(encoding="utf-8")
    folded = text.casefold()
    assert re.search(r"[А-Яа-яЁё]", text) is None
    for heading in (
        "## System requirements",
        "## Install the ready-to-use application",
        "## Configure LM Studio",
        "## Build from source",
        "## Run tests and diagnostics",
        "## Troubleshooting",
    ):
        assert heading in text
    for required in (
        "InterviewAssistant-Setup-0.1.0-win64.exe",
        "uv sync --extra dev --extra cuda --frozen",
        "scripts\\build.ps1",
        "scripts\\build_installer.ps1",
        "dropbox-dash/faster-whisper-large-v3-turbo",
        "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        "windows credential manager",
        "participant consent",
    ):
        assert required.casefold() in folded


def test_start_here_is_a_short_english_installer_entry_point() -> None:
    text = START_HERE_PATH.read_text(encoding="utf-8")
    assert re.search(r"[А-Яа-яЁё]", text) is None
    assert "InterviewAssistant-Setup-0.1.0-win64.exe" in text
    assert "README.md" in text
    assert "InterviewAssistant.exe" not in text
    assert "InterviewAssistant-source-0.1.0.zip" not in text


def test_root_checksum_manifest_contains_only_the_installer() -> None:
    lines = CHECKSUMS_PATH.read_text(encoding="ascii").splitlines()
    assert len(lines) == 1
    assert re.fullmatch(
        r"[0-9A-F]{64}  InterviewAssistant-Setup-0\.1\.0-win64\.exe",
        lines[0],
    )


def test_gitignore_exposes_duplicate_handoff_clutter() -> None:
    patterns = GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()
    assert "/InterviewAssistant-Setup-*-win64.exe" in patterns
    for stale_ignore in (
        "/InterviewAssistant.exe",
        "/InterviewAssistant-source-*.zip",
        "/SHA256SUMS.txt",
        "/_internal/",
    ):
        assert stale_ignore not in patterns
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
uv sync --extra dev --extra cuda --frozen
.\.venv\Scripts\python.exe -m pytest tests\unit\test_release_packaging.py -q
```

Expected: failures show Cyrillic documentation, three checksum entries, portable/source-ZIP references, and stale ignore rules.

- [ ] **Step 3: Commit only the RED contract**

```powershell
git add tests\unit\test_release_packaging.py
git commit -m "test: define clean instructor handoff"
```

---

### Task 2: Rewrite the instructor documentation and release metadata

**Files:**
- Modify: `README.md`
- Modify: `START_HERE.md`
- Modify: `.gitignore`
- Modify: `SHA256SUMS.txt`
- Modify: `tests/unit/test_release_packaging.py` only if an assertion needs a factual path correction, never to weaken the approved contract.

**Interfaces:**
- Consumes: Task 1 documentation/layout tests and the existing installer hash.
- Produces: the primary English setup/build guide, a concise English entry point, and a single-installer checksum manifest.

- [ ] **Step 1: Rewrite `README.md` in conventional GitHub order**

Use this heading skeleton and fill every section with concrete project-specific instructions:

```markdown
# Interview Assistant

> Educational cybersecurity coursework. Use only with informed participant consent.

## Overview
## What is included
## Architecture
## System requirements
### Ready-to-use installation
### LM Studio inference
### Building from source
## Install the ready-to-use application
## Configure LM Studio
## First launch and readiness checks
## Controls and configurable hotkeys
## Build from source
### 1. Install development prerequisites
### 2. Create the locked environment
### 3. Obtain the pinned STT model
### 4. Run from source
### 5. Build the portable application
### 6. Build an installer
## Run tests and diagnostics
## Project structure
## Security and privacy
## Known limitations
## Troubleshooting
## Release verification
```

The finished text must explicitly state:

- Windows 11 x64/DWM, microphone permission, a microphone, and WASAPI loopback are runtime requirements.
- The bundled CUDA 12/cuDNN libraries do not replace the NVIDIA display driver; production STT requires a compatible NVIDIA GPU/driver. Recommend at least 10 GB free disk for installation and additional disk/RAM/VRAM for LM Studio models without claiming an unenforced hard memory minimum.
- LM Studio 0.4+ runs on loopback; LM Link is required only when inference is hosted on another machine such as Strix Halo. A compatible local model is also valid.
- The installer contains the application and pinned `large-v3-turbo` STT bundle, while the user supplies LM Studio, LLM weights, token, and optional MCP setup.
- SmartScreen may warn because the installer is not commercially signed.
- The token is entered through Settings and stored in Windows Credential Manager; it must never be added to YAML.
- All seven default hotkeys and passive/edit Ribbon behavior are documented in a table.
- Clean source setup installs Git for Windows, CPython 3.11/3.12 x64, uv, a current NVIDIA driver for CUDA execution, and Inno Setup 6 only for installer creation.
- Use `uv lock --check`, `uv sync --extra dev --extra cuda --frozen`, and `.venv\Scripts\python.exe`; do not present `requirements.txt` as a lockfile.
- Obtain `dropbox-dash/faster-whisper-large-v3-turbo` at revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`, validate the six files against `packaging/stt_model_manifest.json`, and pass the local directory through `-SttModelPath`.
- Show the exact `scripts/build.ps1` and `scripts/build_installer.ps1 -IsccPath ...` invocations. Explain that `packaging/dist_inventory.json` authenticates the shipped release and must not be silently regenerated merely to make a different binary pass.
- Explain source tests, source/frozen diagnostics, CUDA verification, project layout, capture/privacy behavior, and the limits of automated hardware acceptance.

- [ ] **Step 2: Rewrite `START_HERE.md` as a concise English handoff**

Keep it below roughly 100 lines. Lead with the installer filename, checksum command, SmartScreen note, external LM Studio requirement, and a link to `README.md`. Do not mention the removed root portable runtime or source ZIP.

- [ ] **Step 3: Make ignored clutter visible**

Replace the root delivery section of `.gitignore` with:

```gitignore
# Instructor delivery installer: visible in the folder, intentionally untracked.
/InterviewAssistant-Setup-*-win64.exe
```

Retain normal environment/cache/build ignores. Remove root ignore rules for the portable EXE, source ZIP, checksum file, and `_internal/` so an accidental duplicate is visible in `git status`.

- [ ] **Step 4: Regenerate the one-line installer checksum**

Run:

```powershell
$name = "InterviewAssistant-Setup-0.1.0-win64.exe"
$hash = (Get-FileHash -LiteralPath $name -Algorithm SHA256).Hash
Set-Content -LiteralPath SHA256SUMS.txt -Encoding ASCII -Value "$hash  $name"
```

Expected hash for the already verified binary:

```text
1FF2929C390880B911D92E3AC452E908A7DEEF9AE89CC43D9EFF43647F151E64
```

Stop if the recomputed value differs; do not rewrite the expected value to hide a mismatch.

- [ ] **Step 5: Run the focused documentation contracts and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_release_packaging.py -q
.\.venv\Scripts\ruff.exe check tests\unit\test_release_packaging.py
git diff --check
```

Expected: all focused tests and Ruff pass.

- [ ] **Step 6: Commit the documentation and metadata**

```powershell
git add README.md START_HERE.md .gitignore SHA256SUMS.txt
git commit -m "docs: prepare instructor repository handoff"
```

---

### Task 3: Remove duplicate artifacts and verify the delivered repository

**Files:**
- Delete ignored root artifact: `InterviewAssistant.exe`
- Delete ignored root directory: `_internal/`
- Delete ignored root artifact: `InterviewAssistant-source-0.1.0.zip`
- Delete ignored/generated directories when present: `dist/`, `build/`, `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.superpowers/`, `__pycache__/`, nested `__pycache__/`, `*.egg-info/`
- Verify tracked files; no additional production source changes are expected.

**Interfaces:**
- Consumes: green source/documentation state and the retained installer.
- Produces: final instructor folder with one ignored binary artifact and a clean tracked repository.

- [ ] **Step 1: Run the full source gate before deleting the environment**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

Expected: zero failures and zero lint/diff errors.

- [ ] **Step 2: Verify every cleanup target resolves inside the repository**

Resolve each existing target with `Resolve-Path`, require its absolute path to start with the repository root plus a directory separator, and reject the repository root itself. Do not use wildcard deletion for the installer or tracked source.

- [ ] **Step 3: Delete explicit duplicate/runtime artifacts**

Delete only the verified paths `InterviewAssistant.exe`, `_internal/`, `InterviewAssistant-source-0.1.0.zip`, `dist/`, and `build/`. Use native PowerShell/.NET filesystem operations end-to-end. If Windows reports a loaded DLL, identify the owning process without terminating it and ask the user to close the application before retrying.

- [ ] **Step 4: Delete development caches**

Remove the verified `.venv/`, pytest/Ruff/mypy caches, `.superpowers/`, egg metadata, root bytecode, and nested `__pycache__/` directories. Do not remove `.git/`, tracked source, tests, docs, assets, prompts, packaging files, or the visible installer.

- [ ] **Step 5: Run documentation, secret, and artifact audits without recreating caches**

Use Git and PowerShell to require:

```powershell
rg -n "[А-Яа-яЁё]" README.md START_HERE.md
rg -n "sk-lm-|-----BEGIN .*PRIVATE KEY-----" README.md START_HERE.md config.yaml packaging prompts scripts interview_assistant
git ls-files | rg "(^|/)(\.venv|build|dist|__pycache__|\.pytest_cache|\.ruff_cache|\.superpowers)(/|$)|\.pyc$|\.pyo$"
Get-FileHash InterviewAssistant-Setup-0.1.0-win64.exe -Algorithm SHA256
Get-Content SHA256SUMS.txt
```

Expected: the language and secret scans return no matches; no generated path is tracked; the recomputed installer hash exactly equals the sole checksum line. Never print a credential value if a scanner fails.

- [ ] **Step 6: Verify the final visible and ignored layout**

Require these root absences:

```text
InterviewAssistant.exe
_internal/
InterviewAssistant-source-0.1.0.zip
build/
dist/
.venv/
.pytest_cache/
.ruff_cache/
.superpowers/
```

Require the installer, `README.md`, `START_HERE.md`, `SHA256SUMS.txt`, source,
tests, scripts, packaging, prompts, assets, and docs to exist. `git status
--short --ignored` may show exactly one ignored root artifact: the installer.

- [ ] **Step 7: Commit any final tracked cleanup correction and record evidence**

If Steps 5–6 reveal a tracked documentation or ignore-rule correction, apply it, rerun the relevant checks, and commit only that correction. Otherwise create no empty commit. Record the final test count, installer byte size/hash, current commit, and the known limitation that the ignored installer is intentionally outside Git history in the handoff response.

---

## Plan self-review

- Spec coverage: the root keep/remove policy, English README, clean-machine installer and source-build paths, privacy, checksums, and cleanup verification are each assigned to a task.
- Placeholder scan: the plan contains no deferred implementation markers; all commands, filenames, expected headings, model identity/revision, and hash contracts are explicit.
- Type/interface consistency: Task 1 tests the exact files Task 2 changes; Task 3 consumes the green Task 2 layout and does not require production-code changes or an installer rebuild.

