# Task 5 fix round 2 report

Base commit: `789866d39d2025fe8b29adf319d0d53c3fb18526`

## Inherited RED evidence

The prior fix-round agent supplied these failing regression observations before
the production patch was applied:

- stop/publication race: the output queue mutated after `stop()` had returned;
- queue task accounting: `Queue.join()` returned before the retained hypothesis
  was consumed;
- always-failing partial decode: 29 decode attempts were observed, versus the
  expected cadence-bounded 8 attempts.

The corresponding retained assertions are in
`tests/unit/test_stt_worker.py` and cover reentrant callback shutdown as well.

## Root-cause validation

- The old stop check and queue/callback publication were separate operations, so
  `stop()` could return between them. A reentrant publication gate now linearizes
  the check, queue mutation, callback, and stop transition; `RLock` allows a
  callback to call `stop()` on the worker thread.
- Draining with public `get_nowait()`/`task_done()` and then reinserting exposed a
  transient zero `unfinished_tasks` value. `_CoalescingHypothesisQueue` performs
  selection, replacement, and accounting under `Queue.mutex`, preserving in-flight
  and retained tasks while subtracting only discarded queued tasks.
- Partial cadence and pruning previously ran only after successful decode, and the
  rolling window mutated state before transcription. Cadence/pruning now run in a
  `finally` block, while a planned roll is applied only after successful decode.

## Final GREEN evidence

- Four targeted regressions: `4 passed in 0.23s`.
- Focused Task 5 unit suite: `23 passed in 0.31s`.
- Deterministic dual-source replay (`-m "not cuda"`): `1 passed in 0.08s`.
- Full suite: `42 passed in 0.97s`.
- `python -m compileall -q interview_assistant tests`: exit 0.
- Ruff on both modified Python files: `All checks passed!`.
- Mypy on `interview_assistant/stt/worker.py`: `Success: no issues found in 1 source file`.
- `git diff --check`: exit 0 (Git emitted only the existing LF-to-CRLF checkout warnings).

An exploratory mypy run including the test module reported five pre-existing
`var-annotated` diagnostics for untyped empty lists outside this patch. The scoped
production mypy gate above is clean; Ruff covers both modified files.

All tests use injected fake or deterministic replay engines. No CUDA path, model
load, or model download was invoked.
