# Interview Assistant

![Interview Assistant logo](assets/branding/interview-assistant-logo.png)

> **Educational cybersecurity coursework.** Use this application only with the informed
> consent of every participant and in accordance with the rules of the interview, class,
> meeting platform, and local law. It is not intended for covert recording or assistance.

Interview Assistant is a Windows desktop application for consent-based interview practice and
laboratory demonstrations. It captures a WASAPI system-audio loopback and a microphone as
separate speakers, transcribes Russian and English speech locally with `faster-whisper`, detects
direct and indirect questions, and streams a concise candidate-style answer from LM Studio into
a capture-excluded Liquid Ribbon overlay.

The repository is both the source submission and the audit trail for the coursework. A verified
installer is kept in the repository root so an instructor can run the application without first
installing Python or project dependencies.

## Overview

Core capabilities include:

- separate `Interviewer` and `You` audio streams;
- local CUDA speech-to-text with the pinned `large-v3-turbo` CTranslate2 model;
- bounded Russian and English question/request detection;
- manual context submission and one-shot screen capture;
- streaming text and vision requests through the LM Studio native v1 REST API;
- optional Context7 and DuckDuckGo MCP retrieval through LM Studio;
- safe Markdown rendering in a high-contrast, resizable Liquid Ribbon;
- click-through passive mode and a configurable edit-mode hotkey;
- Windows display-affinity capture exclusion where supported;
- readiness diagnostics for audio, CUDA, LM Studio, LM Link, models, capture, and hotkeys;
- fail-closed release validation for the bundled STT model and packaged runtime.

## What is included

The root installer is:

```text
InterviewAssistant-Setup-0.1.0-win64.exe
```

It contains:

- the Interview Assistant application;
- its frozen Python, PyQt6, and native dependencies;
- the CUDA 12/cuDNN libraries required by the packaged STT stack;
- the pinned `large-v3-turbo` STT model;
- the application prompt, branding, and diagnostic audio fixture.

It does **not** contain:

- an NVIDIA display driver;
- LM Studio or LM Link;
- an interview LLM or vision-model weights;
- MCP servers or MCP credentials;
- an LM Studio API token;
- microphone permissions, audio-device selections, or other machine configuration.

The source tree contains the application, tests, packaging definitions, exact dependency lock,
STT manifest, release inventory, and verification documentation. The large portable runtime and
a duplicate ZIP of the repository are intentionally not included in this handoff.

## Architecture

```text
System audio (WASAPI loopback) ─┐
                                ├─> local CUDA STT ─> question detector ─┐
Microphone ─────────────────────┘                                        │
                                                                         v
Screen capture (only when needed or requested) ───────────────> context builder
                                                                         │
                                                                         v
                                                          LM Studio on 127.0.0.1
                                                                         │
                                              local model or LM Link remote model
                                                                         │
                                                                         v
                                                       capture-excluded Liquid Ribbon
```

Audio capture, STT, screen capture, question detection, and the UI run on the Windows computer
where Interview Assistant is installed. LM Studio also exposes its API on that computer through
loopback. Inference may run locally or on another computer through LM Link; the original Strix
Halo deployment is one supported topology, not a mandatory hardware requirement.

## System requirements

### Ready-to-use installation

- Windows 11 x64 with Desktop Window Manager composition enabled.
- A current NVIDIA GPU driver and a CUDA-capable NVIDIA GPU supported by the bundled
  CTranslate2 runtime. Production STT uses CUDA with `float16`; CPU-only operation does not pass
  the production readiness gate.
- At least 10 GB of free disk space during installation. LM Studio and LLM files require
  additional space.
- A microphone, Windows microphone permission, and a working WASAPI loopback endpoint for the
  system-output device.
- A display configuration supported by Windows Desktop Window Manager. Capture exclusion depends
  on Windows support for `WDA_EXCLUDEFROMCAPTURE`.
- LM Studio 0.4 or newer and at least one compatible instruction model. A multimodal model is
  required when answers must use screenshots.
- Enough system RAM and model VRAM for the selected LM Studio model. The project does not enforce
  a universal memory minimum because requirements vary substantially by model and quantization.

The installer already provides application libraries, the STT model, and CUDA/cuDNN user-space
libraries. It cannot install or replace the NVIDIA display driver.

### LM Studio inference

LM Studio must run an API server on the same Windows computer at `127.0.0.1` (port `1234` by
default). A model can execute on that computer, or LM Link can route it to a connected machine.
LM Link is optional for local inference and required only for remote inference.

Useful official references:

- [LM Studio local server](https://lmstudio.ai/docs/developer/core/server)
- [LM Studio native REST API](https://lmstudio.ai/docs/developer/rest)
- [LM Studio authentication](https://lmstudio.ai/docs/developer/core/authentication)
- [LM Link status](https://lmstudio.ai/docs/cli/link/link-status)

### Building from source

A clean development computer requires:

- Windows 11 x64 and PowerShell 5.1 or newer;
- [Git for Windows](https://git-scm.com/download/win);
- x64 CPython 3.11 or 3.12 from [python.org](https://www.python.org/downloads/windows/);
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/);
- internet access for locked dependencies and the STT model download;
- a current NVIDIA driver for real CUDA/STT verification;
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) only when creating an installer;
- approximately 20 GB of free working space when dependencies, model files, PyInstaller output,
  and installer-compression staging coexist.

Microsoft Visual C++ runtime requirements are supplied by the installed Python/native wheels or
the frozen package. A C/C++ compiler is not normally required because the locked dependencies
have Windows wheels for the supported Python versions.

## Install the ready-to-use application

1. Copy the complete repository folder to a local drive. Do not run the installer directly from
   inside an archive or a cloud-preview window.
2. Open PowerShell in the repository root and verify the installer checksum:

   ```powershell
   $expected = (Get-Content .\SHA256SUMS.txt).Split()[0]
   $actual = (Get-FileHash .\InterviewAssistant-Setup-0.1.0-win64.exe -Algorithm SHA256).Hash
   if ($actual -ne $expected) { throw "Installer checksum mismatch" }
   $actual
   ```

3. Run `InterviewAssistant-Setup-0.1.0-win64.exe` and complete the installation wizard.
4. Windows SmartScreen may display an unknown-publisher warning because this academic build is
   not signed with a commercial code-signing certificate. Verify the checksum before choosing to
   run it.
5. Install and configure LM Studio before starting an interview session.

The installer creates a normal Windows application and uninstaller. Python, `uv`, Git, and Inno
Setup are not required when using the ready-to-use installer.

## Configure LM Studio

1. Install LM Studio 0.4 or newer from [lmstudio.ai](https://lmstudio.ai/).
2. Download a compatible instruction model. Use a vision-capable model for screenshot questions.
3. Open **Developer**, start the Local Server, and keep it bound to `127.0.0.1`. The default URL is
   `http://127.0.0.1:1234`.
4. If authentication is enabled, create a token under **Developer > Server Settings > Manage
   Tokens**. Copy it once and enter it in Interview Assistant Settings.
5. Do not place the token in `config.yaml`. Interview Assistant stores it in Windows Credential
   Manager under the `InterviewAssistant` service.
6. For remote inference, sign in to LM Studio on both computers, enable LM Link, and check:

   ```powershell
   lms link status --json
   ```

7. Select the preferred LM Link device in LM Studio. A remote device such as Strix Halo is
   optional; a local model is valid.
8. If MCP is required, configure it separately in LM Studio. MCP services and keys are not part of
   this repository or installer.

The application intentionally accepts only loopback LM Studio hosts. Do not expose the API on a
LAN merely to connect Interview Assistant; LM Link is responsible for remote inference routing.

## First launch and readiness checks

1. Launch Interview Assistant from the Start menu.
2. In Settings, select a system-audio loopback device and a different microphone device.
3. Select `Auto (Russian / English)` or a fixed recognition language.
4. Select the text model and vision model exposed by LM Studio. The same multimodal model may be
   used for both; the model registry reuses one loaded instance for an identical key.
5. Enter the LM Studio token if server authentication is enabled.
6. Choose the preferred LM Link device only when remote inference is used.
7. Select **Run checks**.

Readiness failures block **Start**. Warnings identify checks that could not be conclusively
verified or performance thresholds that were missed; review their remediation text before
continuing. The bundled STT model must load on CUDA, the selected audio devices must be available,
LM Studio authentication/model discovery must succeed, and the capture/hotkey probes must start.

Useful command-line diagnostics from a source checkout are described below. The installed GUI is
the primary interface for device/model selection.

## Controls and configurable hotkeys

All hotkeys are visible and editable under **Settings > Hotkeys**. Changes are validated as a
complete seven-action map and applied live without restarting the session. The defaults are
fallbacks; rely on the values shown in Settings after customization.

| Action | Default |
|---|---|
| Submit the current conversation context | `Ctrl+Shift+Space` |
| Capture a screenshot for the next request | `Ctrl+Shift+S` |
| Pause or resume recognition | `Ctrl+Shift+P` |
| Hide or show the Ribbon | `Ctrl+Shift+O` |
| Enter or leave Ribbon edit mode | `Ctrl+Shift+I` |
| Force web search for the next request | `Ctrl+Shift+W` |
| Clear the current answer and history | `Ctrl+Shift+C` |

The Ribbon starts in passive click-through mode, so mouse input reaches the application behind
it. Toggle edit mode to move, resize, scroll, or select text, then toggle it again to restore
click-through behavior. Hiding the Ribbon does not stop audio capture, STT, or request processing.

A manual screenshot is one-shot: the UI reports capture progress, readiness for the next request,
and successful attachment. A protected, black, or otherwise unusable frame is rejected instead of
being sent to the model.

## Build from source

The following procedure starts from a clean Windows machine. Run all commands in PowerShell.

### 1. Install development prerequisites

Install Git, x64 CPython 3.11 or 3.12, and `uv`. One official `uv` option is:

```powershell
winget install --id astral-sh.uv -e
```

Alternatively use the reviewed standalone installer command from the
[official uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).
Install Inno Setup 6 only if you intend to compile an installer.

Clone or copy the repository, then enter its root:

```powershell
git clone <repository-url> interview-assistant
Set-Location .\interview-assistant
```

For an offline instructor handoff without a remote URL, copy the supplied repository directory and
open PowerShell there. The included `.git` history is sufficient for source inspection.

### 2. Create the locked environment

```powershell
uv lock --check
uv sync --extra dev --extra cuda --frozen
```

This creates `.venv` and installs the dependency versions resolved in `uv.lock`. The project
supports CPython `>=3.11,<3.13`. `requirements.txt` is only a compatibility shim (`-e .`); it is
not a lockfile and is not the reproducibility path. If an auditor needs only linting and tests
without the optional NVIDIA user-space wheels, the smaller environment is:

```powershell
uv sync --extra dev --frozen
```

See the [official uv project-sync documentation](https://docs.astral.sh/uv/concepts/projects/sync/)
for frozen synchronization semantics. The lockfile makes dependency resolution reproducible for
the supported interpreter range; it does not guarantee a byte-for-byte identical EXE across
different Windows, Python, SDK, or PyInstaller toolchains.

### 3. Obtain the pinned STT model

The cleaned source tree does not duplicate the 1.6 GB STT model already present in the installer.
Download the exact model and revision declared in `packaging/stt_model_manifest.json`:

```powershell
$ModelPath = Join-Path $PWD "models\stt\large-v3-turbo"
.\.venv\Scripts\hf.exe download `
  dropbox-dash/faster-whisper-large-v3-turbo `
  --revision 0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf `
  --local-dir $ModelPath
```

The Hugging Face CLI `--revision` and `--local-dir` options are documented in the
[official CLI guide](https://huggingface.co/docs/huggingface_hub/en/guides/cli). Validate all six
manifest entries before using the bundle:

```powershell
.\.venv\Scripts\python.exe -m interview_assistant.stt.bundle `
  --validate-bundle $ModelPath `
  --require-all-files
```

Validation checks exact filenames, sizes, and SHA-256 values. A mismatched model is rejected.
One multilingual `large-v3-turbo` model serves RU/EN recognition. The six-file release bundle
includes its `README.md` model card and is distributed under the model repository's `MIT` license.

### 4. Run from source

Start LM Studio first, then run:

```powershell
.\.venv\Scripts\python.exe .\main.py
```

The application creates or migrates its user configuration at:

```text
%LOCALAPPDATA%\InterviewAssistant\InterviewAssistant\config.yaml
```

The tracked root `config.yaml` and `packaging/source_release_config.yaml` are secret-free examples,
not locations for API tokens.

### 5. Build the portable application

A build without `-SttModelPath` remains lightweight: it does not download model weights and does
not bundle the Whisper model. This is useful for inspecting whether the source compiles:

```powershell
.\scripts\build.ps1
```

Create a verified PyInstaller onedir build containing the pinned STT model with:

```powershell
.\scripts\build.ps1 -SttModelPath $ModelPath -VerifyCuda `
  -ConfigPath .\packaging\source_release_config.yaml
```

The equivalent execution-policy-explicit invocation is:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1 `
  -SttModelPath $ModelPath `
  -VerifyCuda `
  -ConfigPath .\packaging\source_release_config.yaml
```

The result is:

```text
dist\InterviewAssistant\InterviewAssistant.exe
dist\InterviewAssistant\_internal\
```

The executable must remain beside `_internal`. `build.ps1` checks `uv.lock`, runs the test suite
unless `-SkipTests` is explicitly supplied, performs a clean PyInstaller build, and runs frozen
diagnostics. With `-VerifyCuda`, it also performs real inference using the bundled STT model.

### 6. Build an installer

Install Inno Setup 6, then locate `ISCC.exe` and run:

```powershell
$Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
powershell -ExecutionPolicy Bypass -File .\scripts\build_installer.ps1 `
  -IsccPath $Iscc `
  -DistPath .\dist\InterviewAssistant `
  -OutputPath .\dist\installer
```

The installer builder is intentionally fail-closed. Before invoking Inno Setup, it validates the
entire portable tree against `packaging/dist_inventory.json` and independently verifies the STT
manifest. That inventory authenticates the shipped, reviewed release. A clean build on a different
Windows/Python/PyInstaller toolchain may compile correctly yet have different binary hashes and be
rejected as *not the attested release*. Do not silently regenerate the inventory merely to make an
unknown build pass. Source audit, tests, diagnostics, and the portable build are sufficient to
confirm that the checked-in code is executable; producing a new trusted installer requires a
deliberate inventory review and all release gates documented under `docs/validation/`.

## Run tests and diagnostics

Run the complete source quality gate:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy interview_assistant main.py
.\.venv\Scripts\python.exe -m compileall -q interview_assistant scripts main.py
```

Run side-effect-limited source diagnostics and write a JSON report:

```powershell
.\.venv\Scripts\python.exe .\main.py `
  --diagnostics `
  --no-gui `
  --diagnostics-output .\diagnostics.json
```

Run a real CUDA/STT fixture after preparing `$ModelPath`:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_cuda.py `
  --config .\packaging\source_release_config.yaml `
  --bundle-root .\dist\InterviewAssistant\_internal `
  --fixture .\assets\diagnostics\stt-smoke.wav `
  --output .\cuda-stt.json
```

For a packaged build:

```powershell
.\dist\InterviewAssistant\InterviewAssistant.exe `
  --diagnostics `
  --no-gui `
  --diagnostics-output .\packaged-diagnostics.json
```

Automated diagnostics prove dependency, CUDA, STT, API, capture, and packaging contracts. They do
not replace an observed target-machine session with real microphone/system audio, the selected LM
Studio model, LM Link routing, and participant-consented screen content.

For the observed Teams/target-hardware acceptance procedure, use
[`scripts/teams_acceptance.md`](scripts/teams_acceptance.md) and record results with
[`docs/validation/acceptance-template.md`](docs/validation/acceptance-template.md). A synthetic
self-test deliberately remains unobserved:

```powershell
.\.venv\Scripts\python.exe .\scripts\benchmark_session.py --mode self-test
```

Its report keeps `overall_acceptance: NOT_RUN`. Only observed event evidence may be evaluated:

```powershell
.\.venv\Scripts\python.exe .\scripts\benchmark_session.py --mode event-input `
  --events-input .\session-events.jsonl `
  --report .\acceptance.jsonl `
  --session-id instructor-observed-01
```

## Project structure

```text
interview_assistant/        Application runtime and domain modules
  audio/                    WASAPI and microphone capture
  capture/                  Event-driven screen capture and validation
  context/                  Prompt and multimodal context construction
  diagnostics/              Readiness contracts and production probes
  lmstudio/                 Native REST client, model registry, lifecycle
  retrieval/                Bounded MCP/search policy
  stt/                      Pinned bundle validation and transcription workers
  transcript/               Role-aware transcript and question detection
  ui/                       Settings, safe Markdown, Ribbon, display affinity
assets/                     Branding and diagnostic fixture
packaging/                  PyInstaller/Inno definitions, manifests, inventory
prompts/                    Packaged candidate-style system prompt
scripts/                    Build, diagnostics, release, and validation tools
tests/                      Unit, contract, and integration tests
docs/                       Design, plans, acceptance, and release evidence
main.py                     Windows application entry point
pyproject.toml              Package metadata and dependency ranges
uv.lock                     Canonical locked dependency resolution
```

## Security and privacy

- Obtain participant consent before capturing audio, screens, or meeting content.
- Audio and STT run locally on the primary Windows computer. Conversation context and an optional
  screenshot are sent only to the configured loopback LM Studio API.
- The HTTP client ignores system proxy variables for loopback requests.
- LM Studio API tokens are stored in Windows Credential Manager, never in YAML or the repository.
- Screenshots are event-driven and one-shot. Protected, nearly black, duplicate, and unusable
  frames are rejected.
- The Ribbon requests Windows capture exclusion and does not masquerade as a system process.
- Tool/MCP output is treated as untrusted content. Retrieval is bounded by policy and configured
  separately in LM Studio.
- The shipped installer is unsigned. Verify `SHA256SUMS.txt` before execution.

## Known limitations

- Windows 11 x64 is the supported UI/audio/capture platform.
- Production STT requires a compatible NVIDIA GPU and driver; the bundled CUDA libraries are not a
  CPU fallback and are not a driver installer.
- Display affinity is implemented by Windows and may not protect content in every capture product
  or on unsupported Windows versions.
- LM Studio does not guarantee that every physical placement detail is exposed through its REST
  model response. Verify LM Link routing in LM Studio and on the inference host.
- Automatic question detection is deliberately bounded. Use the manual context hotkey when a
  conversational phrasing is not detected.
- MCP availability and safety depend on the user's independent LM Studio configuration.
- The release is not commercially code-signed, so SmartScreen warnings are expected.

## Troubleshooting

### Start remains disabled

Run checks again and inspect every failed row. Confirm that the loopback and microphone devices
still exist, Windows microphone access is enabled, CUDA exposes a device, the STT bundle loads,
LM Studio authentication works, and the selected model identifiers are available.

### CUDA or STT readiness fails

Install a current NVIDIA driver, reboot if the driver installer requests it, and rerun diagnostics.
Do not copy arbitrary CUDA DLLs into the application directory. From source, validate the exact
model directory against `packaging/stt_model_manifest.json`.

### LM Studio cannot be reached

Start the server from the Developer page or run:

```powershell
lms server start --port 1234
```

Keep the host at `127.0.0.1`. If authentication is required, generate a new token and save it
through Interview Assistant Settings.

### LM Link device is not verified

Run `lms link status --json`, confirm that both devices are signed in and connected, and choose the
preferred device in LM Studio. Local inference does not require LM Link.

### No system audio is available

Select the loopback endpoint corresponding to the active Windows output device. If the output
device changed after readiness ran, save the new selection and run checks again.

### Windows SmartScreen warns about the installer

This academic release is unsigned. Compare the installer's SHA-256 with `SHA256SUMS.txt`. Do not
continue if the value differs.

### Installer compilation rejects a locally built portable tree

The strict inventory protects the reviewed release. A hash difference is expected when toolchain
inputs differ and is not evidence that the source failed to compile. Review the generated portable
application, tests, and diagnostics; update the release inventory only as an intentional new
release with a complete review.

## Release verification

The shipped installer was built and tested on Windows with real CUDA STT, frozen diagnostics, a
15-second GUI smoke test, an isolated install/diagnostics/uninstall cycle, and exact file/model
inventory validation. The detailed evidence is recorded in
[`docs/validation/release-verification-2026-08-02.md`](docs/validation/release-verification-2026-08-02.md).

Verify the handoff installer at any time:

```powershell
Get-FileHash .\InterviewAssistant-Setup-0.1.0-win64.exe -Algorithm SHA256
Get-Content .\SHA256SUMS.txt
```

Expected SHA-256:

```text
1FF2929C390880B911D92E3AC452E908A7DEEF9AE89CC43D9EFF43647F151E64
```
