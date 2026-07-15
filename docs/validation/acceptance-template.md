# Interview Assistant acceptance evidence

Copy this file for each hardware/Teams run. Do not mark an item `PASS` from unit tests,
synthetic timings, memory, or expectation. Use `NOT_RUN` when evidence is absent or
incomplete and `FAIL` when a completed observation misses a criterion.

Do not paste transcript text, prompts, answers, audio, screenshots, recordings, image
bytes, API tokens, or credentials into this document or benchmark JSONL. Evidence files
remain in consented restricted storage; this template uses opaque references only.

## Session identity and consent

| Field | Observed value |
|---|---|
| Session ID (opaque) | `NOT_RUN` |
| Start date/time (UTC, ISO-8601) | `NOT_RUN` |
| End date/time (UTC, ISO-8601) | `NOT_RUN` |
| Tester | `NOT_RUN` |
| Interviewer/teacher consent confirmed | `NOT_RUN` |
| Second Teams participant consent confirmed | `NOT_RUN` |
| Recording consent confirmed | `NOT_RUN` |
| Evidence directory/reference (opaque) | `NOT_RUN` |
| Git commit | `NOT_RUN` |
| Packaged application version/hash | `NOT_RUN` |

Consent notes (no spoken content): `NOT_RUN`

## Dated hardware and software inventory

| Component | Observed version/configuration | Evidence ref |
|---|---|---|
| Main PC / CPU / RAM | `NOT_RUN` | `NOT_RUN` |
| Main GPU | `RTX 5070 Ti — NOT_RUN` | `NOT_RUN` |
| NVIDIA driver | `NOT_RUN` | `NOT_RUN` |
| CUDA runtime visible to CTranslate2 | `NOT_RUN` | `NOT_RUN` |
| STT model / compute type / beam / VAD | `NOT_RUN` | `NOT_RUN` |
| Windows edition/build | `NOT_RUN` | `NOT_RUN` |
| InterviewAssistant.exe version | `NOT_RUN` | `NOT_RUN` |
| Strix Halo host / RAM | `NOT_RUN` | `NOT_RUN` |
| LM Studio version | `NOT_RUN` | `NOT_RUN` |
| LM Link version/topology | `NOT_RUN` | `NOT_RUN` |
| Selected text model | `NOT_RUN` | `NOT_RUN` |
| Selected vision model | `NOT_RUN` | `NOT_RUN` |
| Loaded instance ID/count | `NOT_RUN` | `NOT_RUN` |
| Confirmed inference device | `NOT_RUN` | `NOT_RUN` |
| Microsoft Teams Desktop version | `NOT_RUN` | `NOT_RUN` |
| Observer endpoint / Teams version | `NOT_RUN` | `NOT_RUN` |
| Displays, resolution, HDR, scaling | `NOT_RUN` | `NOT_RUN` |
| Microphone and WASAPI loopback devices | `NOT_RUN` | `NOT_RUN` |

## RU/EN STT evidence

Keep the reference/recognized utterances in a separate consented WER worksheet. Record
only counts and timings here and in JSONL.

| Criterion | Russian | English |
|---|---:|---:|
| Complete consented utterances (minimum 20) | `NOT_RUN` | `NOT_RUN` |
| Reference word count | `NOT_RUN` | `NOT_RUN` |
| Word error count | `NOT_RUN` | `NOT_RUN` |
| Aggregate WER | `NOT_RUN` | `NOT_RUN` |
| First partial p50 (ms) | `NOT_RUN` | `NOT_RUN` |
| First partial p95 (ms, must be ≤1000) | `NOT_RUN` | `NOT_RUN` |
| Finalization after pause p50 (ms) | `NOT_RUN` | `NOT_RUN` |
| Finalization after pause p95 (ms, must be ≤800) | `NOT_RUN` | `NOT_RUN` |
| Source separation checked | `NOT_RUN` | `NOT_RUN` |
| Evidence ref | `NOT_RUN` | `NOT_RUN` |
| Result (`PASS`/`FAIL`/`NOT_RUN`) | `NOT_RUN` | `NOT_RUN` |

If rerun after tuning, link the failed session and list only changed configuration:
`NOT_RUN`.

## LM Studio / LM Link metrics and recovery

| Observation | Value | Evidence ref |
|---|---:|---|
| Normal request sample count | `NOT_RUN` | `NOT_RUN` |
| Prompt processing p50/p95 (ms) | `NOT_RUN` | `NOT_RUN` |
| Request-to-first-token p50/p95 (ms) | `NOT_RUN` | `NOT_RUN` |
| Tokens/s p50/p95 (`(tokens-1)/(end-first token)`, responses ≥2 tokens) | `NOT_RUN` | `NOT_RUN` |
| Initial model load duration (ms) | `NOT_RUN` | `NOT_RUN` |
| Initial load attempts (recorded, no three-attempt acceptance cap) | `NOT_RUN` | `NOT_RUN` |
| Same model key selected for text and vision | `NOT_RUN` | `NOT_RUN` |
| Text/vision same key uses exactly one instance | `NOT_RUN` | `NOT_RUN` |
| Distinct text/vision keys use two non-shared instances (if applicable) | `NOT_RUN` | `NOT_RUN` |
| Preferred Strix Halo device confirmed | `NOT_RUN` | `NOT_RUN` |
| Controlled unload occurred during request | `NOT_RUN` | `NOT_RUN` |
| Reload operation count (must equal 1) | `NOT_RUN` | `NOT_RUN` |
| Reload attempts (must be ≤3) | `NOT_RUN` | `NOT_RUN` |
| Recovery duration (ms; recorded without fixed limit) | `NOT_RUN` | `NOT_RUN` |
| Only latest queued request replayed | `NOT_RUN` | `NOT_RUN` |
| Older queued requests produced no answer | `NOT_RUN` | `NOT_RUN` |
| No RTX LLM fallback | `NOT_RUN` | `NOT_RUN` |
| LM Link reconnect run/session | `NOT_RUN` | `NOT_RUN` |
| Result (`PASS`/`FAIL`/`NOT_RUN`) | `NOT_RUN` | `NOT_RUN` |

## Teams entire-primary-display observation

| Observation | Value | Evidence ref |
|---|---|---|
| Second participant actively observed live share | `NOT_RUN` | `NOT_RUN` |
| Entire primary display (not app window) shared | `NOT_RUN` | `NOT_RUN` |
| Consented Teams recording completed | `NOT_RUN` | `NOT_RUN` |
| Ribbon move/resize/collapse/expand/opacity observed | `NOT_RUN` | `NOT_RUN` |
| Streaming answer observed | `NOT_RUN` | `NOT_RUN` |
| Automatic and hotkey capture triggered | `NOT_RUN` | `NOT_RUN` |
| Ribbon absent from live share | `NOT_RUN` | `NOT_RUN` |
| Desktop remained visible and live | `NOT_RUN` | `NOT_RUN` |
| Recording reviewed | `NOT_RUN` | `NOT_RUN` |
| Ribbon absent from recording | `NOT_RUN` | `NOT_RUN` |
| Display-affinity readiness diagnostics saved | `NOT_RUN` | `NOT_RUN` |
| InterviewAssistant.exe remained visible in Task Manager | `NOT_RUN` | `NOT_RUN` |
| Result (`PASS`/`FAIL`/`NOT_RUN`) | `NOT_RUN` | `NOT_RUN` |

If Ribbon is visible in either path, record `FAIL`; do not claim invisibility. Compatibility
notes: `NOT_RUN`.

## Capture, protected content, and notification observation

| Observation | Value | Evidence ref |
|---|---|---|
| Ordinary event capture duration p50/p95 (ms) | `NOT_RUN` | `NOT_RUN` |
| Ribbon excluded from ordinary event capture | `NOT_RUN` | `NOT_RUN` |
| Legitimately protected source used without bypass | `NOT_RUN` | `NOT_RUN` |
| Assistant reported `Protected content` | `NOT_RUN` | `NOT_RUN` |
| Protected image stopped before VLM | `NOT_RUN` | `NOT_RUN` |
| Audio path continued | `NOT_RUN` | `NOT_RUN` |
| Explicit manual-text path continued | `NOT_RUN` | `NOT_RUN` |
| Third-party notification observation completed | `NOT_RUN` | `NOT_RUN` |
| Notification appeared (`true` or `false`, both are observations) | `NOT_RUN` | `NOT_RUN` |
| Notification suppression attempted (must be `false`) | `NOT_RUN` | `NOT_RUN` |
| DRM/DLP/capture protection bypass attempted (must be `false`) | `NOT_RUN` | `NOT_RUN` |
| Event-capture exclusion result (`PASS`/`FAIL`/`NOT_RUN`) | `NOT_RUN` | `NOT_RUN` |
| Result (`PASS`/`FAIL`/`NOT_RUN`) | `NOT_RUN` | `NOT_RUN` |

## Shutdown evidence

| Resource | Released/closed | Evidence ref |
|---|---|---|
| Microphone and loopback streams | `NOT_RUN` | `NOT_RUN` |
| STT/CUDA worker and model references | `NOT_RUN` | `NOT_RUN` |
| LM Studio HTTP/SSE client | `NOT_RUN` | `NOT_RUN` |
| Capture worker and temporary images | `NOT_RUN` | `NOT_RUN` |
| MCP subprocesses | `NOT_RUN` | `NOT_RUN` |
| Global hotkeys | `NOT_RUN` | `NOT_RUN` |
| Shutdown duration (ms) | `NOT_RUN` | `NOT_RUN` |
| Result (`PASS`/`FAIL`/`NOT_RUN`) | `NOT_RUN` | `NOT_RUN` |

## Machine-readable report review

| Field | Value |
|---|---|
| Source observed-event JSONL ref | `NOT_RUN` |
| Generated report path/hash | `NOT_RUN` |
| `stt_ru` | `NOT_RUN` |
| `stt_en` | `NOT_RUN` |
| `lm_link_recovery` | `NOT_RUN` |
| `capture_pipeline` | `NOT_RUN` |
| `teams_full_screen` | `NOT_RUN` |
| `protected_content` | `NOT_RUN` |
| `shutdown_resources` | `NOT_RUN` |
| `overall_acceptance` | `NOT_RUN` |
| Reviewer and review time (UTC) | `NOT_RUN` |

Failures and incompatibilities (do not erase): `NOT_RUN`.

Final decision: **`NOT_RUN`**.
