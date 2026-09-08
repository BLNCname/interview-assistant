# Provider and runtime validation — 2026-09-07

The final source verification on September 8 passed 990 tests with 5 skips in
79.23 seconds. Full-repository
Ruff and mypy (57 source files) also passed. The skips require Windows symlink
privileges or POSIX filesystem behavior unavailable on this test host.

## Real LM Studio answers

The already loaded `qwen3.6-35b-a3b-mtp` model was exercised through the application's
native `/api/v1/chat` client, `ContextBuilder`, tracked system prompt and SSE parser.
Authentication was temporarily disabled by the user. Tests did not change server
settings, reload models, open a listening port or send private interview material.

Five text cases were sent sequentially. They test generation independently of the
[recorded speech replay](recording-replay-2026-09-07.md), so these timings exclude
audio capture, endpoint detection, STT, retrieval and overlay rendering.

| Case | First answer text | Complete answer | Words | Observation |
|---|---:|---:|---:|---|
| Processes versus threads, Russian | 2.765 s | 5.500 s | 103 | Relevant explanation and trade-offs; still somewhat formal |
| Follow-up: why not always threads? | 1.750 s | 4.187 s | 94 | Uses previous answer context; discusses shared memory and isolation |
| Database indexes, English | 1.641 s | 3.860 s | 128 | Correct language and relevant example; exceeds the 120-word style guideline |
| Past conflict, no candidate biography | 1.250 s | 4.140 s | 105 | Explicitly hypothetical approach, no fabricated employer or past incident |
| ELLLO question “What is your dream job?” | 1.610 s | 3.203 s | 76 | English answer; remains generic without a candidate profile |

All five streams ended normally with `chat.end`. No service preamble, heading or
bullet list was present. This is a small manually reviewed sample, not a guarantee
of correctness, style compliance or a statistically meaningful model benchmark.
The dream-job question came from the replay source; the technical and behavioral
questions were original test prompts. Full generated answers are in ignored
`.cache/lmstudio-answers-2026-09-07.json`.

The first live run exposed two problems that mock transport tests cannot catch:
one English question received a Russian response, and the model fabricated a past
workplace conflict. The prompt was refined with explicit language precedence and
a concrete example for missing biography. Both failed cases behaved as intended
in the second run. More varied interview practice is still needed; prompts cannot
enforce factuality on every model. The earlier uncorrected follow-up also included
an inaccurate claim about cross-process isolation, which the second sample did
not repeat.

A separate vision smoke test sent a generated PNG containing two lines of Python
code, with no desktop capture or private content. The loaded model correctly
returned `30`; the existing native `type="text"` input was accepted with HTTP 200
and a 1.828 s time to first token. The [current LM Studio documentation](https://lmstudio.ai/docs/developer/rest/chat)
specifies `type="message"` for text inside an image request, but that shape returned
HTTP 400 `invalid_union` on this installed server, which explicitly expected
`text` or `image`. The native builder therefore retains the locally verified
`text` shape. This observed documentation/server discrepancy should be checked
when upgrading LM Studio; an unconditional switch would break this installation.

## OpenRouter and application MCP

OpenRouter is implemented as a separate transport using its chat-completions API.
Contract tests cover role separation, images, streamed deltas, completion markers,
malformed/truncated streams, cancellation, missing/invalid keys, service errors,
model metadata and credential validation. The public model catalog is not treated
as proof that an API key is valid. No OpenRouter generation was billed or tested
live because no OpenRouter key was supplied.

Native MCP was checked against both a local stdio fixture and live services:

- Built-in DuckDuckGo: returned 1,854 reference characters in 2.69 s.
- Context7, FastAPI documentation: returned 5,943 reference characters in 5.91 s.

The live retrieval checks used public technical queries and no API keys. Their
results are availability samples; network latency and service quotas can change.
The `.env.example` search timeout is 10 seconds. Optional search failures do not
prevent generation without references.

The application executes fixed, read-only search/documentation tools on queries
sanitized by `SearchPolicy`. It does not hand the whole transcript or screenshot
to search, execute arbitrary model-selected MCP tools, or require LM Studio to
host MCP. Standard I/O servers are child processes with no TCP listener; remote
servers use outgoing HTTPS. A trusted custom MCP server configuration may change
its own transport behavior and must be configured deliberately.

## Runtime changes verified

- Fair scheduling prevents one audio source from starving the other. An idle
  source can finalize a phrase even if no additional packets arrive.
- Cold STT and model readiness budgets accommodate real model initialization.
  Russian and English checks decode bundled speech instead of accepting silence.
- Previous completed answers are available to follow-ups and are cleared with
  interview history. Failed/cancelled partial answers do not replace them.
- Text-only provider configurations do not submit unsupported screenshots.
- Disabling MCP cannot be overridden by the forced-search shortcut.
- An empty `.env` key field allows use of a key saved in Windows Credential Manager.
- Startup checks and the GUI resolve the same `.env`: beside the selected YAML
  first, then beside the executable (or at the source repository root).
- Fast local checks run before GUI startup; full pytest remains an explicit
  developer option. No pytest installation is required on the instructor's PC.
- The CUDA verification script respects the configured precision and reports it.
  Installer builds can use a separate validated distribution inventory via
  `-InventoryPath`; the historical inventory remains the default.

## GPU interpretation

The teacher's [RTX 3060 Ti has 8 GB VRAM](https://www.nvidia.com/en-us/geforce/graphics-cards/30-series/rtx-3060-3060ti/),
while the tested [RTX 5070 Ti has 16 GB](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5070-family/).
The bundled CTranslate2 `large-v3-turbo` weights can be used with CUDA
`int8_float16`; the [CTranslate2 quantization documentation](https://github.com/OpenNMT/CTranslate2/blob/master/docs/quantization.md)
describes this execution mode. It changes runtime precision, without requiring a
different bundled model download.

The local recording measured approximately 1,744 MiB incremental peak usage in
`int8_float16`, versus 2,487 MiB in `float16`. This supports choosing OpenRouter
plus CUDA `int8_float16` for the 8 GB computer: the language model then consumes no
local GPU memory. It does not establish the 3060 Ti's decoding speed or guarantee
headroom alongside other GPU applications. CPU/int8 is an explicit fallback.

The locked CUDA runtime/cuBLAS/cuDNN libraries are bundled for the portable build;
an NVIDIA driver is still required. The local driver was 610.74. Check the
[CTranslate2 installation requirements](https://opennmt.net/CTranslate2/installation.html)
when updating the runtime dependencies. Do not infer hardware compatibility from
the newer GPU's timings alone.

## Delivery verification

The September 8 portable preview passed frozen startup, dependency, configuration,
MCP and inventory checks. Its public search returned 1,854 reference characters
in 1.851 seconds in the ordinary Windows user context, with TLS verification
enabled. The sandbox-only search failure was caused by denied access to the
Windows certificate store and also reproduced from source. See
[portable validation](portable-preview-2026-09-08.md) for file hashes and scope.
The original root 0.1.1 installer is an earlier artifact and does not contain
these source changes.

Build testing exposed issues specific to frozen packaging: the unused MCP
developer CLI exited during collection, a dynamically imported diagnostics module
was omitted, and unrelated programs' DLLs contaminated dependency resolution.
The spec excludes the optional CLI, explicitly includes lazy diagnostics imports,
and limits the build process's DLL search PATH to the active Python/PyQt6 runtimes
and Windows. It does not modify the user's persistent PATH. A provenance audit
found 67 foreign DLLs in the failed build, originating from Maono, Oculus and
Codex tool dependencies. The final build's 422 binary/extension entries contain
none of those sources.

The portable bundle's CUDA DLLs, pinned model and both speech files were tested
using the source Python interpreter and locked packages. Both phrases were
recognized exactly with `int8_float16`; initial English decoding took 17.099 s,
including cold model load, followed by Russian decoding in 0.177 s. This verifies
the bundled data/native libraries, independently of the frozen EXE startup tests.
