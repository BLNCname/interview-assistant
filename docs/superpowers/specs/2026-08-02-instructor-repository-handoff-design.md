# Instructor Repository Handoff Design

Date: 2026-08-02  
Status: Approved design, awaiting written-spec review

## Objective

Prepare the repository for direct delivery to an instructor. The handoff must
make the ready-to-install application immediately visible, while preserving a
complete, reviewable, and reproducibly buildable source tree. It must not
contain local development environments, duplicate release layouts, caches,
temporary diagnostics, machine-specific data, or credentials.

## Handoff Layout

The repository root will retain:

- `InterviewAssistant-Setup-0.1.0-win64.exe` as the single binary delivery;
- `SHA256SUMS.txt` containing only the installer checksum;
- `README.md` as the primary English-language instructor guide;
- `START_HERE.md` as a short entry point;
- the tracked source, tests, prompts, packaging definitions, build scripts,
  documentation, lockfile, and safe example configuration;
- normal Git metadata and history.

The handoff will remove:

- the portable root `InterviewAssistant.exe` and its `_internal/` runtime;
- the duplicate `InterviewAssistant-source-0.1.0.zip`;
- `dist/`, `build/`, `.venv/`, test/lint caches, bytecode, egg metadata, and
  temporary verification reports not intentionally tracked;
- local logs, screenshots, credentials, tokens, device identifiers, and other
  machine-specific artifacts.

The installer remains intentionally untracked by Git but physically visible in
the delivered folder. The tracked checksum file provides its integrity value.

## README Information Architecture

`README.md` will be rewritten entirely in English using a conventional GitHub
project structure:

1. Project title, concise purpose, screenshot/logo, and academic-use notice.
2. Feature summary and deployment architecture.
3. Clear distinction between components bundled in the installer and external
   components the user must install or configure.
4. System requirements for installation, runtime inference, CUDA STT, audio,
   Windows capture affinity, LM Studio, and LM Link.
5. Installation from the bundled installer on a clean Windows computer,
   including SmartScreen expectations and first-launch configuration.
6. LM Studio, LM Link, model, API-token, and optional MCP setup.
7. First-run readiness checks and explanation of warnings versus blockers.
8. User controls and all seven configurable hotkeys.
9. Source audit and clean-machine development setup using Python 3.11/3.12,
   `uv`, the committed `uv.lock`, and documented native prerequisites.
10. Test, diagnostics, portable build, installer build, and source-archive
    commands, with explicit STT model preparation requirements.
11. Project layout, privacy/security behavior, known limitations,
    troubleshooting, and release verification references.

Instructions will not assume Python, package managers, CUDA libraries, LM
Studio, models, Inno Setup, or developer tools are already present. Commands
will use PowerShell and repository-relative paths.

## Installation and Build Contracts

The installer path is the recommended instructor workflow. It bundles the
application, Python/native dependencies, CUDA/cuDNN runtime used by the frozen
application, branding, prompt resources, and the pinned STT model. It does not
bundle LM Studio, LM Link, an interview LLM, MCP services, API tokens, or
machine-specific configuration.

The source-build path will explain two outputs:

- a development/source run after `uv sync --extra dev --extra cuda --frozen`;
- a verified release build using `scripts/build.ps1` and
  `scripts/build_installer.ps1`.

A release build requires a manifest-matching offline STT bundle. The README
will identify the tracked model repository/revision and explain how to provide
the bundle path without pretending that the model is stored in the cleaned
source tree. Installer generation additionally requires Inno Setup 6 and must
pass the tracked exact distribution inventory.

## Safety and Privacy

The README will clearly state that this is an educational cybersecurity
coursework project and must only be used with participant consent. It will
describe local audio/screen processing boundaries, Windows Credential Manager
token storage, loopback-only LM Studio access, screenshot capture exclusion,
and the absence of credentials from the repository and installer.

No secret value will be added to documentation. Example values will use empty
strings, `null`, loopback addresses, or explicit placeholders.

## Verification

Before handoff completion:

- validate that tracked source changes pass repository tests and Ruff;
- validate README links, referenced paths, commands, and English-only prose;
- scan tracked documentation/config/source for credential and private-key
  signatures without printing matched values;
- independently recompute the installer SHA-256 and compare it with the single
  `SHA256SUMS.txt` entry;
- confirm `git status` has no unintended tracked or untracked files;
- confirm ignored handoff contents consist only of the visible installer;
- confirm removed duplicate/runtime/cache paths are absent, except for any
  Windows-locked file that is explicitly reported and removed after its owner
  is closed.

The previously verified installer binary will not be rebuilt solely for this
repository-layout/documentation change. A rebuild is required only if runtime,
packaging inputs, or installer contents change.

