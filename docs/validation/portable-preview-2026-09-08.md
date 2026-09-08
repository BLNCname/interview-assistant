# Portable preview verification — September 8, 2026

The updated portable application is available in
`dist/release-preview-2026-09-08/InterviewAssistant/`. Copy the entire application
folder, including `_internal`. The outer delivery directory includes a blank
`.env.example`, instructions and verification evidence; the application inventory
does not include user configuration or credentials.

This is a preview of the current uncommitted source tree. The EXE still reports
version 0.1.1; it is a separate artifact from the unchanged August installer.
An Inno Setup installer was not rebuilt. The earlier failed September 7 preview
is marked superseded, and failed September 8 artifacts were retained under `build`.

## Artifact identity

- Files in the application directory: **4,311**.
- Total application size: **4,036,518,577 bytes**, approximately **3.76 GiB**.
- EXE SHA-256:
  `67928f7033cac535dae0f8abdb1201184d395b4d362750add6d8cbd0dc9e25d5`.
- Distribution inventory SHA-256:
  `b56b8821337cb8b46dd8a5efebb6edd9d780f85807a431525ba02c2b4ce25e31`.
- Source provenance: 67 implementation/resource hashes plus Git HEAD
  `b08da62155dca8898712395b0442e75cf4905624`, recorded in `source-snapshot.json`.
  The snapshot matches the implementation/resources used for the final build.

The strict distribution validator passed against the generated inventory and the
tracked STT manifest. All pinned model files are included. The historical
`packaging/dist_inventory.json` was not replaced; future installer builds can
select the new reviewed inventory with `-InventoryPath`.

## Checks on the actual EXE

| Check | Result |
|---|---|
| Offline `--self-test` | Exit 0, 1.562 s |
| Headless runtime diagnostics | Exit 0, frozen=true, 1.771 s |
| Actual qasync/Qt dependency imports | Passed |
| Bundled CUDA libraries and visible GPU | No missing runtime DLLs; one CUDA device |
| Invalid config-adjacent `.env` | Rejected, expected exit 1, no GUI |
| Embedded provider/MCP modules and Qt extensions | Present |
| Frozen MCP stdio initialize/list_tools | Passed, 0.905 s |
| Frozen public DuckDuckGo search | 1,854 reference characters, 1.851 s |

The search query was “What is the latest Python release?” and contained no private
context. Search first failed inside the agent sandbox, as did the source comparator,
with denied access to the native certificate store. Repeating the same frozen
request in the ordinary Windows user context succeeded. TLS verification remained
enabled; no certificate store, firewall rule or persistent PATH was changed.

The [separate audio and provider report](provider-and-runtime-2026-09-07.md)
contains actual CUDA speech decoding and local model answer measurements.
The full source suite passed **990 tests with 5 platform/privilege skips** in
79.23 seconds. Ruff and mypy passed; 34 focused packaging tests also passed after
the final build-path correction.

## Reproducibility fixes

The package initially failed in ways not exposed by source-only tests:

1. The optional MCP developer CLI exited during module collection without its
   optional dependency. It is now excluded; the application uses the SDK directly.
2. A diagnostics module imported dynamically by `main.py` was omitted. It is now
   explicitly included, and the compiled diagnostic command was rerun.
3. DLL discovery found incompatible runtimes in Maono, Oculus and Codex tool
   directories. A mismatched root-level `Qt6Core.dll` prevented qasync/Qt imports.
   The spec now limits PATH inside the build process to the selected Python,
   PyQt6 and Windows directories. No user/system environment was changed.

An independent audit of the final `Analysis-00.toc` and `COLLECT-00.toc` found
**422 binary/extension entries and zero unexpected source origins** in each.
All 67 foreign DLLs from the failed attempt were excluded. Remaining unresolved
library warnings concern optional SQL/3D/QML/WebView plugins; the application's
QtCore/QtGui/QtWidgets imports passed in the frozen diagnostic process.

## Scope and evidence

No full interactive interview was run in the final EXE. Audio replay, generation,
startup and package checks were measured separately. A real two-device session
and the teacher's RTX 3060 Ti remain validation steps for that computer. Live
OpenRouter generation remains untested because no OpenRouter key was supplied.

Build logs, the inventory, source hashes and JSON check reports are retained in
`build/release-preview-2026-09-08/`; a copy of delivery evidence is provided beside
the portable application. The package contains no private API keys or recordings.
