# Unified Branding and Graphite UI Design

**Date:** 2026-08-02  
**Status:** Approved in visual brainstorming; awaiting written-spec review  
**Scope:** Application branding, Settings information architecture, Ribbon visual consistency, Windows packaging, and release verification

## Objective

Apply the new transparent liquid-glass logo consistently to the running application and installer, and make the Settings window and assistant Ribbon look like parts of one product. The result must remain readable, keyboard-accessible, capture-affinity compatible, and suitable for an instructor-facing coursework release.

## Approved direction

The approved visual direction is **A1: Graphite Glass with sidebar pages**.

- The UI language remains English.
- Graphite is the dominant surface color.
- `#50DE73` is the restrained brand accent for the selected navigation item, ready states, edit mode, focus/selection emphasis, and the primary `Start` action.
- Windows title bars retain native minimize, maximize, close, drag, resize, scaling, and accessibility behavior. On supported Windows versions, the application requests a dark DWM title bar instead of implementing a custom frameless title bar.
- The existing capture-exclusion and click-through behavior of the Ribbon remains unchanged.

## Visual system

A small shared theme module will own semantic design tokens rather than scattering new literal colors across widgets. It will expose graphite surface colors, borders, text roles, the green accent, warning/error colors, corner radii, and the application-level Qt stylesheet.

The common hierarchy is:

1. native dark Windows title bar with the application icon;
2. graphite application surface with a subtle top-to-bottom tonal change;
3. quiet translucent secondary panels and inputs;
4. high-contrast primary text and muted explanatory text;
5. green accent only where state or action needs emphasis.

The design must preserve at least WCAG AA contrast for normal answer and settings text against the effective surfaces. Warning and error states retain distinct amber/red semantics and never rely on green alone.

## Branding assets

`assets/branding/interview-assistant-logo-cutout.png` is the approved pixel-faithful transparent source. Its RGB content is unchanged from the supplied logo; only the outer background is transparent.

The release branding pipeline will:

1. promote the transparent cutout to the canonical README/application PNG;
2. derive a multi-frame `interview-assistant.ico` with 16, 24, 32, 48, 64, 128, and 256 pixel frames;
3. use high-quality downsampling and retain transparent rounded corners;
4. verify that each ICO frame has nonempty alpha and a centered visible mark;
5. use that single ICO in Qt, the PyInstaller EXE resource, Start menu/desktop shortcuts, Add or Remove Programs, Inno Setup, and the uninstaller.

The small frames may receive scale-appropriate sharpening, but they must not redraw, simplify, recolor, or add a background to the approved mark.

## Settings information architecture

`SettingsWindow` will keep its current public behavior while changing its presentation from one long form into a sidebar plus `QStackedWidget` pages.

### General

- recognition language;
- web-search mode;
- concise privacy/coursework context where useful, without adding a new configuration field.

### Models

- text model;
- vision model;
- shared/unique instance annotation;
- LM Studio token field and its existing Windows Credential Manager behavior.

### Audio

- system-audio loopback device;
- microphone device;
- existing unavailable-device handling.

### Hotkeys

- all seven `HotkeyAction` entries;
- English action label and explanatory help for every entry;
- single-chord editor;
- `Restore defaults` action;
- existing conflict validation, live apply, persistence rollback, and readiness-preservation behavior.

### Appearance

- overlay opacity;
- overlay maximum height;
- clear explanation that position and size are changed through Ribbon edit mode and persisted by the existing overlay geometry logic.

### Diagnostics

- overall readiness status;
- notification/error banner;
- complete five-column readiness table: Check, Status, Message, Remediation, Duration;
- readable ready/warning/error state styling without removing any diagnostic detail.

### Persistent footer

`Save`, `Run checks`, and `Start` remain visible regardless of the selected page. Existing semantics remain authoritative:

- `Save` validates and persists the current controls;
- `Run checks` saves before starting readiness work;
- `Start` remains disabled until the current readiness report explicitly allows start;
- changing a readiness-sensitive field invalidates the report and disables `Start`;
- a disabled `Start` retains an explanatory tooltip.

Navigation changes must never discard unsaved widget values. Runtime model/audio refreshes continue to update the same combo-box instances even when their page is not visible.

## Assistant Ribbon

The Ribbon will adopt the same semantic theme tokens while retaining its established compact layout and behavior.

- Replace the blue model-chip tint with neutral graphite/green brand treatment.
- Keep the status, model, edit-mode, question, answer, source, notification, and collapse elements.
- Add the new application mark to the header only if it remains legible without materially reducing space for state labels; otherwise the Windows/taskbar icon is sufficient and the Ribbon header remains text-first.
- Maintain the existing answer contrast calculation, safe Markdown rendering, scroll preservation, resize/move edit mode, temporary hide hotkey, forced screenshot, forced transcript submission, click-through mode, and display-affinity behavior.
- Do not introduce animated blur, custom window chrome, or decorative effects that could compromise capture exclusion or response readability.

## Component boundaries

The implementation should keep responsibilities separated:

- `interview_assistant.ui.theme`: semantic palette, QSS generation, and best-effort native dark-title-bar helper;
- `SettingsWindow`: widget ownership, page composition, navigation, existing save/readiness signals, and geometry persistence;
- `RibbonWindow` and `_RibbonSurface`: compact overlay composition and custom painting using shared tokens;
- branding build helper/script: deterministic PNG-to-ICO generation and validation;
- PyInstaller/Inno files: consume the canonical ICO without embedding alternate branding logic.

Theme application must fail safely. If the Windows DWM call is unavailable, the UI continues with native title-bar behavior; it must not prevent startup.

## Release and versioning

Because the executable UI and installer resources change, the rebuilt instructor release should use patch version **0.1.1**. This prevents the new installer from being confused with the already verified `0.1.0` binary.

The full release flow will:

1. recreate the locked `uv` development/CUDA environment;
2. obtain and validate the pinned `large-v3-turbo` STT bundle;
3. build the PyInstaller onedir application with the new icon;
4. run frozen diagnostics and GUI smoke checks;
5. review and update the strict distribution inventory for the intentional UI/branding delta;
6. build `InterviewAssistant-Setup-0.1.1-win64.exe` with the STT bundle;
7. perform isolated install, launch/diagnostic, icon, uninstall, and cleanup checks;
8. place only the new installer and its authoritative checksum in the repository root;
9. remove superseded installers, portable trees, virtual environments, caches, temporary models, and build output after verification.

The repository must not contain the LM Studio token or any other credential. LM Studio remains an external prerequisite and the application token remains in Windows Credential Manager.

## Testing and acceptance

Implementation follows test-driven development for behavior and packaging contracts.

Automated coverage must verify:

- canonical PNG transparency and ICO frame inventory;
- PyInstaller and Inno references to the canonical ICO;
- Settings sidebar item order and page mapping;
- visibility of all seven English hotkey rows;
- preservation of current control values while switching pages;
- dynamic model/audio updates on hidden pages;
- persistent footer actions and unchanged start gating;
- readiness table contents and state styling;
- theme application and safe fallback when native dark-title-bar support is unavailable;
- Ribbon capture/click-through/edit-mode regressions;
- complete pytest and Ruff gates.

Release acceptance additionally requires:

- inspection of the EXE and installer icon resources at representative sizes;
- a visible GUI smoke test for Settings and Ribbon on Windows;
- successful packaged diagnostics;
- successful isolated installer install and uninstall;
- confirmation that the installed application includes the pinned STT model;
- a fresh SHA-256 manifest containing only the final instructor installer.

## Non-goals

- Russian UI localization or a language switcher;
- changing question detection, STT, LM Studio, MCP, prompt, or answer-generation behavior;
- custom frameless title bars;
- redesigning the approved logo;
- introducing an additional GUI framework or third-party theme package;
- weakening readiness gates, capture affinity, credential storage, distribution inventory, or installer validation.

## Completion criteria

The work is complete when the source UI consistently implements A1 Graphite Glass, all prior behavior and automated gates pass, the application and installer display the new multi-resolution icon, the `0.1.1` installer with bundled STT passes isolated smoke verification, and the repository is again reduced to source plus the single visible instructor installer and checksum.
