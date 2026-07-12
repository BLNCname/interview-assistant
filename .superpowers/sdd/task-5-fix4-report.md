# Task 5 Fix Round 4 Report

Base commit: `2b3e7b4464d526dcec6a18d2acd5dcafaafe0b55`

## Status

DONE. This round changes only Task 5 regression tests and this report. The two
reviewer-specified production mutations were applied only temporarily to prove
RED, then the production file was restored byte-for-byte to the base commit.

No reviewer subagent was spawned.

## Tests Added

### One deadline across publication-gate acquisition and worker-thread join

`test_stop_shares_deadline_between_publication_gate_and_thread_join` patches
the worker module's `monotonic` with the controlled sequence `100.00`, `100.00`,
and `100.04`. A real holder thread acquires `_publication_gate`; the second
clock call releases it and waits until the holder has exited, so cleanup and
gate coordination do not rely on elapsed-time thresholds. A recording worker
thread captures `join(timeout=...)` and stays alive.

The assertions prove that:

- `stop(timeout=0.10)` returns `False` for the still-alive worker thread;
- the gate and join consume one shared deadline;
- `join` receives the remaining `0.06` seconds, not the original `0.10`;
- exactly the expected three monotonic reads occur; and
- the real gate-holder thread is always released and joined.

### Supplied plain bounded queue retains both finals

`test_worker_retains_two_source_finals_in_supplied_plain_queue` injects a
standard `Queue(maxsize=2)` through the public `StreamingSTTWorker`
constructor. The fake recording engine and real worker loop publish one system
final and one microphone final. The test proves the supplied queue identity,
priority order (system before microphone), `qsize() == 2`, and
`unfinished_tasks == 2`, then consumes both items, calls `task_done()` for both,
and completes `join()` with zero unfinished work.

### Supplied queue wakes an already-blocked consumer

`test_worker_publication_notifies_consumer_on_supplied_plain_queue` wraps the
real queue condition's `wait` method only to signal that `Queue.get()` has
entered the empty-queue wait. Publication then goes through the worker into the
same supplied standard queue. The consumer must wake, receive the exact
hypothesis, account for the task, and terminate. A `finally` fallback manually
notifies and joins the consumer so even a notification regression cannot leak a
blocked test thread.

## Mutation RED Evidence

### Original timeout incorrectly reused for join

Temporary mutation:

```python
thread.join(timeout=timeout)
```

Targeted result: `1 failed in 0.16s`.

The failure was direct and contract-specific:

```text
assert [0.1] == [0.06 +/- 6.0e-08]
```

This kills any implementation that starts with a deadline but gives `join()` a
fresh copy of the original timeout after publication-gate work.

### Retained hypotheses incorrectly hard-limited to one

Temporary mutation:

```python
)[:1]
```

Targeted result: `1 failed in 0.13s`.

The worker completed both final publications, but the supplied capacity-two
queue exposed the mutation immediately:

```text
assert 1 == 2
where 1 = output.qsize()
```

This kills a retained-slice implementation that silently treats every output
queue as capacity one.

After each RED run, the production line was restored. A scoped
`git diff --exit-code -- interview_assistant/stt/worker.py` then succeeded.

## Final GREEN Evidence

```powershell
.venv\Scripts\python.exe -m pytest tests\unit\test_stt_worker.py::test_stop_shares_deadline_between_publication_gate_and_thread_join tests\unit\test_stt_worker.py::test_worker_retains_two_source_finals_in_supplied_plain_queue tests\unit\test_stt_worker.py::test_worker_publication_notifies_consumer_on_supplied_plain_queue -q
# 3 passed in 0.08s
# The same targeted set also passed 10 isolated iterations (30 executions).

.venv\Scripts\python.exe -m pytest tests\unit\test_stable_prefix.py tests\unit\test_language_hysteresis.py tests\unit\test_stt_engine.py tests\unit\test_stt_worker.py -q
# 31 passed in 0.47s

.venv\Scripts\python.exe -m pytest tests\integration\test_stt_fixture.py -q -m "not cuda"
# 1 passed in 0.07s

.venv\Scripts\python.exe -m pytest -q
# 50 passed in 1.13s

.venv\Scripts\python.exe -m compileall -q interview_assistant tests
# exit 0

.venv\Scripts\python.exe -m ruff check interview_assistant tests
# All checks passed!

.venv\Scripts\python.exe -m mypy interview_assistant\stt
# Success: no issues found in 4 source files

git diff --check
git diff --cached --check
# exit 0; Git emitted only the existing LF-to-CRLF checkout warning
```

## Constraints

- Existing assertions were preserved.
- No CUDA path, model load, or model download was invoked.
- STT execution used injected fake engines and the deterministic replay fixture.
- Production code was not changed by the final patch.
