# Instructor Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a verified Windows installer containing the application and offline STT model, plus a sanitized editable source archive for instructor review.

**Architecture:** Validate the user-level MCP servers independently, then build the committed application through the existing frozen PyInstaller, Inno Setup, and standalone-source-archive scripts. Release artifacts contain no LM Studio installation, LLM, LM Link, MCP configuration, user settings, or credentials.

**Tech Stack:** PowerShell, Python 3.12, MCP 1.28, PyInstaller 6, Inno Setup 6.7.2, Windows tar, SHA-256

## Global Constraints

- Installer contents are limited to the application runtime and manifest-pinned `large-v3-turbo` STT bundle.
- Source ZIP is built from committed HEAD and includes standalone Git history and the same STT bundle.
- LM Studio, LM Link, LLM weights, MCP installation/configuration, tokens, and machine-specific settings are excluded.
- No API key or credential value may be printed, archived, or committed.
- Both artifacts use version `0.1.0`.

---

### Task 1: Verify permanent MCP services

**Files:**
- Read: `C:\Users\BLNCname\.lmstudio\mcp.json`
- Read: `C:\Users\BLNCname\.local\bin\duckduckgo-mcp-server.exe`

**Interfaces:**
- Consumes: sanitized `mcpServers.context7` URL/headers and `mcpServers.duckduckgo.command`.
- Produces: evidence that DuckDuckGo search and Context7 documentation lookup return successful MCP tool results.

- [ ] **Step 1: Parse configuration without printing secret values**

Print server names, URL, executable existence, and header names only. Fail if either server is missing or the DuckDuckGo command is not an existing user-level file.

- [ ] **Step 2: Exercise DuckDuckGo**

Initialize the configured stdio server, require the `search` tool, call it with `{"query": "Python official documentation", "max_results": 1}`, and print only `isError` plus result count.

- [ ] **Step 3: Exercise Context7**

Initialize the configured streamable-HTTP server with headers loaded in memory, require `resolve-library-id` and `query-docs`, call `resolve-library-id` for PyQt6, and print only `isError` plus result length.

Expected: all three steps exit 0 without emitting header values.

### Task 2: Prepare and validate the release STT bundle

**Files:**
- Read: `packaging/stt_model_manifest.json`
- Create (ignored): `build/release-stt-model/*`

**Interfaces:**
- Consumes: cached snapshot revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`.
- Produces: a flat directory containing exactly the six manifest-listed files.

- [ ] **Step 1: Copy the five cached runtime files**

Copy `config.json`, `model.bin`, `preprocessor_config.json`, `tokenizer.json`, and `vocabulary.json` from the cached snapshot to `build/release-stt-model`.

- [ ] **Step 2: Download only the pinned model card**

Use `huggingface_hub.hf_hub_download()` with repository `dropbox-dash/faster-whisper-large-v3-turbo`, the pinned revision, and filename `README.md`; copy the result into the release model directory.

- [ ] **Step 3: Validate all files**

Run:

```powershell
.venv\Scripts\python.exe -m interview_assistant.stt.bundle `
  --validate-bundle build\release-stt-model `
  --require-all-files
```

Expected: exit 0 after size and SHA-256 validation.

### Task 3: Verify and build the committed onedir application

**Files:**
- Read: all tracked source and tests
- Produce (ignored): `dist/InterviewAssistant/*`

**Interfaces:**
- Consumes: committed readiness UX and validated release STT bundle.
- Produces: frozen onedir distribution with bundled CUDA runtime and STT files.

- [ ] **Step 1: Run the full suite**

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Expected: zero failures.

- [ ] **Step 2: Build with bundled STT and real CUDA verification**

```powershell
.\scripts\build.ps1 `
  -SkipTests `
  -VerifyCuda `
  -ConfigPath "$env:LOCALAPPDATA\InterviewAssistant\InterviewAssistant\config.yaml" `
  -SttModelPath "$PWD\build\release-stt-model"
```

Expected: packaged diagnostics pass, CUDA exposes at least one device, and `dist\InterviewAssistant\InterviewAssistant.exe` exists.

- [ ] **Step 3: Verify packaged warning/start transition**

Use Windows UI Automation to wait for `Start.IsEnabled=True`, invoke Start, and require the visible top-level window name to change from `Interview Assistant Settings` to `Interview Assistant`.

### Task 4: Build and smoke-test the installer

**Files:**
- Read: `packaging/interview_assistant.iss`
- Produce: `dist/release/InterviewAssistant-Setup-0.1.0-win64.exe`

**Interfaces:**
- Consumes: verified onedir distribution and Inno Setup compiler.
- Produces: per-user installer containing the onedir tree, including STT files.

- [ ] **Step 1: Build the installer**

```powershell
.\scripts\build_installer.ps1 `
  -IsccPath "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" `
  -Version 0.1.0 `
  -DistPath "$PWD\dist\InterviewAssistant" `
  -OutputPath "$PWD\dist\release"
```

Expected: the versioned installer exists and is non-empty.

- [ ] **Step 2: Perform a temporary silent install**

Resolve and verify `$PWD\build\installer-smoke` as a workspace-contained directory. Install with `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` and `/DIR=$PWD\build\installer-smoke`. Run the installed EXE with `--diagnostics --no-gui` and require a redacted JSON report whose status is `ok` and `frozen` is true.

- [ ] **Step 3: Uninstall the smoke copy**

Run the generated uninstaller silently, require its exit code to be 0, and verify installer-owned executable/runtime files are removed.

### Task 5: Build and inspect the editable source archive

**Files:**
- Produce: `dist/release/InterviewAssistant-source-0.1.0.zip`
- Produce: `dist/release/SHA256SUMS.txt`

**Interfaces:**
- Consumes: committed HEAD and validated release STT bundle.
- Produces: standalone editable Git checkout without remotes or local data.

- [ ] **Step 1: Create the source archive**

```powershell
.\scripts\create_source_archive.ps1 `
  -SttModelPath "$PWD\build\release-stt-model" `
  -OutputPath "$PWD\dist\release\InterviewAssistant-source-0.1.0.zip" `
  -Version 0.1.0 `
  -RepositoryPath "$PWD"
```

Expected: the ZIP exists and contains one top-level directory.

- [ ] **Step 2: Inspect exclusions and Git state**

Extract to a verified temporary directory. Require no `origin`, no remote refs, no reflogs, no `config.yaml`, no `.venv`, no Hugging Face cache metadata, and all six manifest-listed model files.

- [ ] **Step 3: Scan committed blobs and extracted files for credentials**

Check every reachable Git blob and regular extracted file for LM Studio/Context7 key prefixes and private-key markers. Print filenames only on failure; never print matching bytes.

- [ ] **Step 4: Generate checksums**

Write uppercase SHA-256 plus filename for the installer and source ZIP to `dist/release/SHA256SUMS.txt`, then recompute both hashes and require an exact match.

- [ ] **Step 5: Record release inventory**

Report absolute artifact paths, byte sizes, SHA-256 values, unsigned Authenticode status, bundled STT manifest revision, test count, and installer smoke-test result.
