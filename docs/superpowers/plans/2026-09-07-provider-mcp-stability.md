# Provider, MCP and interview replay implementation plan

**Goal:** Make the consent-based coursework application usable with LM Studio or
OpenRouter, run MCP in the application, and verify speech/question/answer behavior.

**Architecture:** Preserve the native LM Studio client and its model lifecycle. Add
an OpenRouter transport behind the existing stream protocol. Runtime uses the
selected provider's model keys. Native MCP consumes only the existing SearchPolicy
sanitized question and fixed read-only tool allowlists. Configuration comes from
YAML plus an explicit adjacent `.env`, with process environment taking precedence.
Secrets remain outside serialized configuration. Qt UI stays in English.

- [x] Establish the current pytest/Ruff baseline in a local development environment.
- [x] Add provider/environment/STT settings with round-trip and precedence tests.
- [x] Add OpenRouter streaming, multimodal conversion, errors, model discovery and
  cancellation contract tests; integrate provider-specific readiness and settings.
- [x] Add native MCP stdio/Streamable HTTP retrieval and cleanup tests; wire the
  runtime and optional readiness probes without an LM Studio dependency.
- [x] Reproduce/fix STT source starvation and language settings; add recording replay
  with timestamps, detected questions and timing; validate generated answers separately.
- [x] Correct CLI configuration selection and startup readiness time budgets; keep
  full developer tests explicitly runnable instead of launching pytest in frozen GUI.
- [x] Improve spoken-answer prompt and send it as actual system instructions.
- [x] Validate with full tests, actual recorded speech and available local inference;
  record untested external services separately from passing mock contracts.
- [x] Document instructor `.env` setup and RTX 3060 Ti versus RTX 5070 Ti constraints,
  including measured local STT memory/latency and limits of cross-GPU estimates.
- [x] Build and validate an isolated portable preview with bundled STT and native MCP.

RTX 3060 Ti is the instructor target. Retain bundled large-v3-turbo, expose
`int8_float16` for CUDA memory savings, and keep CPU/int8 as explicit fallback.
Do not claim performance on hardware that was not physically tested. Existing
0.1.1 installer remains an old release until rebuilt and verified separately.
