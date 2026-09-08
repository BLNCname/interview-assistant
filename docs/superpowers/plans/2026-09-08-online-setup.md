# Single-file online installer implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development to execute the bounded tasks below, with independent review and verification before publication.

**Goal:** Publish one network `Setup.exe` that installs the complete assistant without manual dependency downloads.

**Architecture:** Embed the pinned private frozen application core. Download exact public wheels and model files using native Inno Setup; reconstruct the validated portable inventory from verified archive members before installation.

**Tech Stack:** Python 3.12 build tooling, uv.lock, PyInstaller, Inno Setup 6.7.2, GitHub Releases, PyPI, Hugging Face.

**Spec:** ../specs/2026-09-08-online-setup.md

## Global constraints

- No repository visibility change or embedded credentials.
- One installer asset, `Compression=none`, no manual `.bin` payloads.
- Preserve AppId and runtime/UI behavior; version the delivery as 0.1.3.
- All downloads, extraction and checks finish before installed files change.
- No visible test windows on the user's working desktop.

### Task 1: Artifact mapping and build manifest

Files: `scripts/prepare_online_payload.py`, `tests/unit/test_online_payload.py`.

Consumes: frozen directory, inventory JSON, PyInstaller COLLECT TOC, installed
distribution RECORDs, `uv.lock`, STT manifest. Produces: generated Inno file
entries and an artifact manifest containing URL, SHA-256, size, archive-member
mapping and complete per-file installed hashes.

- [x] Test an embedded core plus a wheel member and model file forms an exact,
  duplicate-free partition of the input inventory.
- [x] Test rejecting unpinned/unsafe URLs, path traversal and mismatching content.
- [x] Implement source-to-RECORD mapping using COLLECT provenance and hashes.
- [x] Verify actual pinned wheel entries before accepting the generated manifest.
- [x] Review mapping independently; no broad basename-only trust.

### Task 2: Native online setup

Files: `packaging/interview_assistant_online.iss`,
`packaging/online_downloads.iss`, `scripts/build_online_installer.ps1`,
`tests/unit/test_online_installer.py`.

Consumes: Task 1 artifact/file includes. Produces: one versioned Setup executable
and SHA256SUMS; installation matches the original portable layout.

- [x] Implement native download page, required SHA-256, verified completed-file
  cache and bounded retries, with user cancellation terminating preparation.
- [x] Extract pinned wheels into temporary staging, validate selected members,
  then copy only declared inventory files through standard `[Files]` entries.
- [x] Compile a small fixture using the real Inno compiler and exercise corrupt
  hashes, HTTP failure, retry/cache and silent failure without touching old files.
- [x] Build the real installer with no disk spanning or payload compression.

### Task 3: Release verification and publication

Files: version metadata, README, START_HERE, build/instructor instructions,
`docs/validation/release-0.1.3.md`.

- [x] Bump application and build defaults consistently to 0.1.3; build a fresh
  portable distribution and generate its independent inventory.
- [x] Run appropriate unit checks and the full guarded validation suite.
- [x] Execute a real online installation, compare every installed file against
  the portable inventory, run frozen diagnostics and test repeat/uninstall.
- [x] Review privacy and secret exclusion, update one-file installation docs,
  and correct the obsolete tray instruction.
- [ ] Commit and push reviewed source, create a draft release, upload only Setup,
  include its digest in release notes, compare GitHub digests, then publish.

## Decisions and progress

- Work proceeds autonomously under the user's existing instruction; no extra
  design-approval round is required.
- Work branch: `codex/online-setup`; the working v0.1.2 release stays available.
- Research delegated independently: native Inno behavior, private GitHub/public
  wheel constraints, and complete frozen-file provenance mapping.

- Hardware scope clarified against the application code: STT uses CTranslate2,
  not PyTorch. The installer probes NVIDIA hardware and provides CUDA/CPU
  advice, preserving existing settings. Native tests exercise seven hardware
  profiles, including 3060 Ti and 5070 Ti; only 5070 Ti is physically available.
- Upgrade review found 16 obsolete root DLLs in the 0.1.1 inventory. Exact-file
  cleanup occurs after successful preparation; native tests cover success,
  HTTP failure, cancellation, linked paths, and preservation of unlisted files.
- Final guarded suite: 1223 passed, 5 platform skips; all 15 native Inno tests
  ran, and Ruff, mypy and offline lock verification passed.
- A private, never-activated desktop allowed a real user-context installation
  without visible windows. The first candidate downloaded all 41 artifacts,
  passed full installed-inventory/frozen diagnostics, repeated from cache and
  uninstalled. The final cleanup-enabled binary independently passed cached installation,
  diagnostics, a repeat upgrade seeded with all 16 historical DLLs, exact
  inventory verification, and uninstall (all exit codes 0).
