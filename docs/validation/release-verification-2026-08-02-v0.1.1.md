# Release verification — 2026-08-02 — v0.1.1

> The approved report path retains the plan date. Execution completed on
> 2026-08-03 in the Europe/Moscow time zone.

## Scope and source

- Reviewed Tasks 1–7 baseline: `ecc36624a66067f04341dd1e5d358a195c2d9945`.
- Release version: `0.1.1` in Python package metadata, the application PE file
  and product versions, the installer product version, and the installer build
  argument.
- Task 8 added two controller-approved visual gate fixes before freezing the
  final candidate: English Ribbon question copy and a shrinkable visible Ribbon
  application mark. The release commit is the commit containing this report.
- Build host: Windows 10, Python 3.12.11, PyInstaller 6.21.0, Inno Setup 6.7.2,
  NVIDIA GeForce RTX 5070 Ti with driver 610.74.

## Source and TDD gates

- Initial complete source gate: `uv lock --check` and frozen sync exited 0;
  pytest reported 844 passed and 5 skipped in 39.50 seconds; Ruff reported zero
  findings; `git diff --check` exited 0.
- English Ribbon RED: the focused overlay test failed because the live product
  still exposed `ВОПРОС`; GREEN: 1 passed after changing only the two visible
  literals to `QUESTION` and `Waiting for a question…`.
- Ribbon logo RED: the 20 px icon had zero rendered width under the old ignored
  size policy. The final focused RED observed a 381 px minimum instead of the
  required pre-logo 361 px baseline; GREEN: 1 passed after a private label kept
  its real 20×20 size hint but advertised a zero-width minimum hint under a
  preferred horizontal policy. The complete overlay file then reported 74 passed.
- Post-fix complete source gate: 844 passed and 5 skipped in 39.18 seconds;
  Ruff reported zero findings; `git diff --check` exited 0.
- Handoff-contract RED: 3 focused failures proved README, START_HERE, and the
  checksum still named 0.1.0; GREEN: 3 passed after the verified binary existed
  and those contracts were updated to 0.1.1.
- The final clean PyInstaller build reran the full suite: 844 passed and 5
  skipped in 39.54 seconds.
- Final tracked-tree gate: 844 passed and 5 skipped in 41.67 seconds; Ruff
  reported zero findings, `git diff --check` exited 0, and a fresh root
  installer digest matched `SHA256SUMS.txt`.

## Branding evidence

- Canonical cutout: `assets/branding/interview-assistant-logo-cutout.png`, RGBA,
  935×935, with alpha 0 at all four corners.
- ICO: `assets/branding/interview-assistant.ico`, exactly seven frames at 16,
  24, 32, 48, 64, 128, and 256 px. Corner alpha was 0 for every frame except
  the antialiased 16 px frame, whose four corner alpha values were 2.
- The extracted application and installer icons were both 32×32 PNGs with the
  same SHA-256,
  `25E18FB6B8EBF1FECB0155AE6F75582B37A75755BB9E94BF3143A95F81CDF8C1`.
  Both were visually inspected as the recognizable rounded transparent mark.

## Pinned STT bundle

- Repository: `dropbox-dash/faster-whisper-large-v3-turbo`.
- Immutable revision: `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`.
- The required-all-files validator exited 0 before packaging. The final frozen
  model directory contains exactly these six manifest entries:

| File | Bytes | SHA-256 |
|---|---:|---|
| `config.json` | 2,263 | `b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e` |
| `model.bin` | 1,617,884,929 | `e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da` |
| `preprocessor_config.json` | 340 | `7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711` |
| `tokenizer.json` | 2,710,337 | `297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd` |
| `vocabulary.json` | 1,068,114 | `c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1` |
| `README.md` | 1,445 | `b3068692728faed23580cce5cd569fc47ff76c690c032b2641ffd5554ea64d8f` |

## Frozen application and CUDA

- Final build command exited 0 in 176.03 seconds and produced
  `dist/InterviewAssistant/InterviewAssistant.exe`.
- Packaged diagnostics: `status=ok`, `frozen=true`, `config=missing`, Python
  3.12.11; all seven runtime dependencies ready.
- CUDA diagnostics: one device, runtime ready, zero missing DLLs, with packaged
  CUDA runtime, cuBLAS, and cuDNN user-space search directories.
- Real bundled-model inference: `status=ok`, `device=cuda`,
  `model_source=bundled`, model load 5.560681 seconds, transcription 0.517549
  seconds, and real-time factor 0.258774.
- PyInstaller logged unresolved optional Qt plugin dependencies for unused Qt
  modules. The application build, frozen diagnostics, CUDA probe, model
  inference, GUI launch, and installed smoke all completed successfully.

## GUI and icon smoke

- The final frozen EXE was launched on the target Windows desktop. All six
  Settings pages switched in the approved order: General, Models, Audio,
  Hotkeys, Appearance, Diagnostics.
- The Settings title bar was dark and showed the application icon. User-facing
  copy was English; all seven hotkeys were visible; Save, Run checks, and Start
  remained in the footer. The readiness table was readable with distinct green
  ready and amber warning states.
- Frozen Ribbon UI Automation confirmed the 20×20 application image, English
  question copy, `WDA_EXCLUDEFROMCAPTURE=17`, and default click-through. The
  configured interaction hotkey exposed Edit mode and removed click-through
  while retaining affinity 17; the visibility hotkey hid and restored the
  Ribbon while retaining affinity 17.
- A native external resize to 1800×200 was accepted and restored. A synthetic
  pointer drag was attempted but did not change geometry, so manual pointer-drag
  resizing was not claimed; regression tests cover the move/resize handlers.
- Because normal desktop capture correctly omits the affinity-protected Ribbon,
  production `QWidget.grab()` owner-process captures were used to inspect the
  actual Ribbon widget. They show the logo, graphite palette, English copy,
  Markdown heading/list/inline-code rendering, readable contrast, and the green
  edit-mode label and outline. This was production-widget rendering rather than
  a screenshot of the frozen window itself.
- Controller-retained evidence is under
  `.superpowers/sdd/2026-08-02-unified-branding-and-graphite-ui/task-8-evidence/`:
  six `gui-settings-*.png` captures, `gui-ribbon-widget-grab.png`,
  `gui-ribbon-widget-grab-edit.png`, `gui-ribbon-capture-exclusion.png`,
  `exe-icon-smoke.png`, `installer-icon-smoke.png`,
  `packaged-diagnostics.json`, and `installer-smoke.json`.

## Inventory and security review

- Inventory generation occurred only after the final model, frozen diagnostics,
  CUDA/STT, GUI, and icon checks.
- Final strict inventory: 4,093 files, 275 directories, and 4,193,298,972 bytes.
- Semantic comparison with the prior inventory found zero added and zero removed
  paths. Eight paths changed: the reviewed branding ICO, the application EXE,
  and six same-size package `RECORD` metadata files regenerated from the isolated
  worktree environment.
- Strict validation rehashed the complete tree and exited 0. It confirmed the
  exact six-file STT bundle and the single allowlisted Silero VAD ONNX model.
- No token, credential, secret, user config, transcript, screenshot, log, cache,
  unrelated model, symlink, or reparse point was accepted. No LM Studio or LM
  Link executable, LLM/MCP server or model, host driver, or credential was
  copied into the installer. The Credential Manager value was never copied or
  logged; GUI evidence only records the redacted stored-token state.

## Installer and handoff

- Inno Setup build: exit 0 in 990.35 seconds. The builder validated the complete
  distribution/model inventory before compilation.
- Isolated smoke: exit 0 in 160.91 seconds; install 0, frozen diagnostics 0,
  inventory/model hashes validated, uninstall 0, and install tree removed.
- Root handoff artifact:
  `InterviewAssistant-Setup-0.1.1-win64.exe`.
- Size: 2,439,441,604 bytes.
- SHA-256:
  `1FCBEC8B098E42490DEF5ACC4DAB2404E923BC4CB6CB5CFD7C9BBE1FAD3AFAFA`.
- `SHA256SUMS.txt` contains exactly that one authoritative uppercase digest line.

## Remaining acceptance boundary

The automated source, packaging, CUDA/STT, GUI, inventory, installer,
install/diagnostics/uninstall, icon, and checksum gates above are observed. A
live interview with observed Teams behavior, the two RU/EN readiness fixtures,
and pointer-drag resizing remain instructor/target-machine exercises; this
report does not promote those unseen behaviors to PASS.
