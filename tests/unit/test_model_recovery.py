import asyncio
from dataclasses import FrozenInstanceError
from typing import Generic, TypeVar

import pytest

from interview_assistant.lmstudio.lifecycle import (
    ModelLifecycle,
    ModelOfflineError,
    ModelRecoveryError,
    NoRecoveryRequestError,
    RecoveryRequest,
)
from interview_assistant.lmstudio.models import ModelInstance


T = TypeVar("T")


def _instance(key: str = "qwen3.5", suffix: str = "loaded") -> ModelInstance:
    return ModelInstance(
        key=key,
        instance_id=f"{key}:{suffix}",
        state="ready",
    )


class ScriptedRegistry:
    def __init__(
        self,
        outcomes: list[ModelInstance | BaseException],
        trace: list[str] | None = None,
    ) -> None:
        self.outcomes = outcomes
        self.trace = trace
        self.refresh_calls: list[str] = []

    async def refresh(self, key: str) -> ModelInstance:
        self.refresh_calls.append(key)
        if self.trace is not None:
            self.trace.append(f"refresh:{key}")
        if not self.outcomes:
            raise AssertionError("unexpected registry refresh")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class RecordingActions(Generic[T]):
    def __init__(
        self,
        *,
        trace: list[str] | None = None,
        cancel_error: BaseException | None = None,
        warm_errors: list[BaseException | None] | None = None,
        replay_errors: list[BaseException | None] | None = None,
    ) -> None:
        self.trace = trace
        self.cancel_error = cancel_error
        self.warm_errors = list(warm_errors or [])
        self.replay_errors = list(replay_errors or [])
        self.cancel_calls = 0
        self.warmed: list[ModelInstance] = []
        self.replayed: list[tuple[ModelInstance, RecoveryRequest[T]]] = []
        self.delays: list[float] = []
        self.states: list[str] = []

    async def cancel_active(self) -> None:
        self.cancel_calls += 1
        if self.trace is not None:
            self.trace.append("cancel")
        if self.cancel_error is not None:
            raise self.cancel_error

    async def warm_up(self, instance: ModelInstance) -> None:
        self.warmed.append(instance)
        if self.trace is not None:
            self.trace.append(f"warm:{instance.instance_id}")
        if self.warm_errors:
            error = self.warm_errors.pop(0)
            if error is not None:
                raise error

    async def replay(
        self,
        instance: ModelInstance,
        request: RecoveryRequest[T],
    ) -> None:
        self.replayed.append((instance, request))
        if self.trace is not None:
            self.trace.append(f"replay:{request.request_id}")
        if self.replay_errors:
            error = self.replay_errors.pop(0)
            if error is not None:
                raise error

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        if self.trace is not None:
            self.trace.append(f"sleep:{delay}")

    def on_state(self, state: str) -> None:
        self.states.append(state)
        if self.trace is not None:
            self.trace.append(f"state:{state}")


def _lifecycle(
    registry: ScriptedRegistry,
    actions: RecordingActions[T],
) -> ModelLifecycle[T]:
    return ModelLifecycle(
        registry,
        cancel_active=actions.cancel_active,
        warm_up=actions.warm_up,
        replay=actions.replay,
        sleeper=actions.sleep,
        on_state=actions.on_state,
    )


async def test_recovery_replays_only_latest_request() -> None:
    instance = _instance()
    registry = ScriptedRegistry([instance])
    actions: RecordingActions[object] = RecordingActions()
    lifecycle = _lifecycle(registry, actions)

    lifecycle.submit("qwen3.5", RecoveryRequest(10, "old question"))
    await lifecycle.recover("qwen3.5", RecoveryRequest(11, "latest question"))

    assert [(request.request_id, request.payload) for _, request in actions.replayed] == [
        (11, "latest question")
    ]


async def test_older_or_equal_submit_cannot_overwrite_latest_request() -> None:
    registry = ScriptedRegistry([_instance()])
    actions: RecordingActions[str] = RecordingActions()
    lifecycle = _lifecycle(registry, actions)

    lifecycle.submit("qwen3.5", RecoveryRequest(11, "latest"))
    lifecycle.submit("qwen3.5", RecoveryRequest(10, "older"))
    lifecycle.submit("qwen3.5", RecoveryRequest(11, "equal but different"))
    await lifecycle.recover("qwen3.5")

    assert [request for _, request in actions.replayed] == [
        RecoveryRequest(11, "latest")
    ]


async def test_first_request_accepts_any_integer_identifier() -> None:
    registry = ScriptedRegistry([_instance()])
    actions: RecordingActions[str] = RecordingActions()
    lifecycle = _lifecycle(registry, actions)

    await lifecycle.recover("qwen3.5", RecoveryRequest(-2, "opaque identifier"))

    assert [request for _, request in actions.replayed] == [
        RecoveryRequest(-2, "opaque identifier")
    ]


async def test_first_success_has_one_cancel_warmup_replay_and_ordered_states() -> None:
    trace: list[str] = []
    instance = _instance()
    registry = ScriptedRegistry([instance], trace)
    actions: RecordingActions[str] = RecordingActions(trace=trace)
    lifecycle = _lifecycle(registry, actions)

    result = await lifecycle.recover("qwen3.5", RecoveryRequest(1, "question"))

    assert result is instance
    assert actions.delays == [0.5]
    assert actions.cancel_calls == 1
    assert actions.warmed == [instance]
    assert [request.request_id for _, request in actions.replayed] == [1]
    assert actions.states == ["recovering", "ready"]
    assert trace == [
        "cancel",
        "state:recovering",
        "sleep:0.5",
        "refresh:qwen3.5",
        "warm:qwen3.5:loaded",
        "replay:1",
        "state:ready",
    ]


async def test_warmup_failure_then_success_uses_next_delay_and_attempt() -> None:
    first = _instance(suffix="first")
    second = _instance(suffix="second")
    registry = ScriptedRegistry([first, second])
    actions: RecordingActions[str] = RecordingActions(
        warm_errors=[RuntimeError("not warm"), None]
    )
    lifecycle = _lifecycle(registry, actions)

    result = await lifecycle.recover("qwen3.5", RecoveryRequest(2, "question"))

    assert result is second
    assert registry.refresh_calls == ["qwen3.5", "qwen3.5"]
    assert actions.delays == [0.5, 1.0]
    assert actions.cancel_calls == 1
    assert actions.warmed == [first, second]
    assert [(instance, request.request_id) for instance, request in actions.replayed] == [
        (second, 2)
    ]
    assert actions.states == ["recovering", "ready"]


async def test_terminal_failure_runs_exactly_three_attempts_and_goes_offline() -> None:
    failures = [RuntimeError(f"failure {number}") for number in range(1, 4)]
    registry = ScriptedRegistry(list(failures))
    actions: RecordingActions[str] = RecordingActions()
    lifecycle = _lifecycle(registry, actions)

    with pytest.raises(ModelRecoveryError) as captured:
        await lifecycle.recover("qwen3.5", RecoveryRequest(3, "question"))

    assert captured.value.key == "qwen3.5"
    assert captured.value.attempts == 3
    assert captured.value.__cause__ is failures[-1]
    assert registry.refresh_calls == ["qwen3.5"] * 3
    assert actions.delays == [0.5, 1.0, 2.0]
    assert actions.cancel_calls == 1
    assert actions.warmed == []
    assert actions.replayed == []
    assert actions.states == ["recovering", "offline"]


async def test_offline_non_manual_recovery_short_circuits_without_callbacks() -> None:
    registry = ScriptedRegistry([RuntimeError("one"), RuntimeError("two"), RuntimeError("three")])
    actions: RecordingActions[str] = RecordingActions()
    lifecycle = _lifecycle(registry, actions)
    request = RecoveryRequest(4, "question")
    with pytest.raises(ModelRecoveryError):
        await lifecycle.recover("qwen3.5", request)
    snapshot = (
        list(registry.refresh_calls),
        list(actions.delays),
        actions.cancel_calls,
        list(actions.states),
    )

    with pytest.raises(ModelOfflineError) as captured:
        await lifecycle.recover("qwen3.5", request)

    assert captured.value.key == "qwen3.5"
    assert (
        registry.refresh_calls,
        actions.delays,
        actions.cancel_calls,
        actions.states,
    ) == snapshot


async def test_manual_retry_replays_newer_queued_request_and_clears_offline() -> None:
    registry = ScriptedRegistry([RuntimeError("one"), RuntimeError("two"), RuntimeError("three")])
    actions: RecordingActions[str] = RecordingActions()
    lifecycle = _lifecycle(registry, actions)
    with pytest.raises(ModelRecoveryError):
        await lifecycle.recover("qwen3.5", RecoveryRequest(5, "failed request"))

    lifecycle.submit("qwen3.5", RecoveryRequest(6, "manual retry request"))
    with pytest.raises(ModelOfflineError):
        await lifecycle.recover("qwen3.5")

    recovered = _instance(suffix="recovered")
    registry.outcomes.append(recovered)
    result = await lifecycle.recover("qwen3.5", manual_retry=True)

    assert result is recovered
    assert actions.delays == [0.5, 1.0, 2.0, 0.5]
    assert actions.cancel_calls == 2
    assert [request for _, request in actions.replayed] == [
        RecoveryRequest(6, "manual retry request")
    ]
    assert actions.states == ["recovering", "offline", "recovering", "ready"]

    lifecycle.submit("qwen3.5", RecoveryRequest(7, "next wave"))
    registry.outcomes.append(_instance(suffix="next"))
    await lifecycle.recover("qwen3.5")


async def test_cancel_active_error_propagates_unchanged_before_recovery_state() -> None:
    error = RuntimeError("cannot cancel stream")
    registry = ScriptedRegistry([_instance()])
    actions: RecordingActions[str] = RecordingActions(cancel_error=error)
    lifecycle = _lifecycle(registry, actions)

    with pytest.raises(RuntimeError) as captured:
        await lifecycle.recover("qwen3.5", RecoveryRequest(8, "question"))

    assert captured.value is error
    assert actions.cancel_calls == 1
    assert registry.refresh_calls == []
    assert actions.delays == []
    assert actions.states == []
    assert actions.replayed == []


@pytest.mark.parametrize("stage", ["refresh", "warm_up"])
async def test_cancellation_from_attempt_propagates_without_offline_retry(stage: str) -> None:
    cancellation = asyncio.CancelledError()
    if stage == "refresh":
        registry = ScriptedRegistry([cancellation])
        actions: RecordingActions[str] = RecordingActions()
    else:
        registry = ScriptedRegistry([_instance()])
        actions = RecordingActions(warm_errors=[cancellation])
    lifecycle = _lifecycle(registry, actions)

    with pytest.raises(asyncio.CancelledError) as captured:
        await lifecycle.recover("qwen3.5", RecoveryRequest(9, "question"))

    assert captured.value is cancellation
    assert registry.refresh_calls == ["qwen3.5"]
    assert actions.delays == [0.5]
    assert actions.cancel_calls == 1
    assert len(actions.warmed) == (stage == "warm_up")
    assert actions.replayed == []
    assert actions.states == ["recovering"]


async def test_newer_request_arriving_during_warmup_is_selected_for_replay() -> None:
    warm_started = asyncio.Event()
    release_warm = asyncio.Event()
    replayed: list[RecoveryRequest[str]] = []

    async def cancel_active() -> None:
        return None

    async def warm_up(_instance: ModelInstance) -> None:
        warm_started.set()
        await release_warm.wait()

    async def replay(_instance: ModelInstance, request: RecoveryRequest[str]) -> None:
        replayed.append(request)

    async def sleep(_delay: float) -> None:
        return None

    lifecycle = ModelLifecycle(
        ScriptedRegistry([_instance()]),
        cancel_active=cancel_active,
        warm_up=warm_up,
        replay=replay,
        sleeper=sleep,
    )
    recovering = asyncio.create_task(
        lifecycle.recover("qwen3.5", RecoveryRequest(10, "old"))
    )
    await warm_started.wait()

    lifecycle.submit("qwen3.5", RecoveryRequest(11, "new"))
    release_warm.set()
    await recovering

    assert replayed == [RecoveryRequest(11, "new")]


async def test_concurrent_same_key_callers_join_one_logical_recovery_wave() -> None:
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()
    instance = _instance()

    class BlockingRegistry:
        def __init__(self) -> None:
            self.calls = 0

        async def refresh(self, key: str) -> ModelInstance:
            assert key == "qwen3.5"
            self.calls += 1
            refresh_started.set()
            await release_refresh.wait()
            return instance

    actions: RecordingActions[str] = RecordingActions()
    registry = BlockingRegistry()
    lifecycle = ModelLifecycle(
        registry,
        cancel_active=actions.cancel_active,
        warm_up=actions.warm_up,
        replay=actions.replay,
        sleeper=actions.sleep,
        on_state=actions.on_state,
    )
    request = RecoveryRequest(12, "same request")
    first = asyncio.create_task(lifecycle.recover("qwen3.5", request))
    await refresh_started.wait()
    second = asyncio.create_task(lifecycle.recover("qwen3.5", request))
    await asyncio.sleep(0)

    release_refresh.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result is instance
    assert second_result is instance
    assert registry.calls == 1
    assert actions.cancel_calls == 1
    assert actions.delays == [0.5]
    assert len(actions.warmed) == 1
    assert [request.request_id for _, request in actions.replayed] == [12]
    assert actions.states == ["recovering", "ready"]


async def test_sequential_same_request_id_starts_a_fresh_recovery_wave() -> None:
    first_instance = _instance(suffix="first")
    second_instance = _instance(suffix="second")
    registry = ScriptedRegistry([first_instance, second_instance])
    actions: RecordingActions[str] = RecordingActions()
    lifecycle = _lifecycle(registry, actions)
    request = RecoveryRequest(42, "same request")

    first = await lifecycle.recover("qwen3.5", request)
    second = await lifecycle.recover("qwen3.5", request)

    assert first is first_instance
    assert second is second_instance
    assert registry.refresh_calls == ["qwen3.5", "qwen3.5"]
    assert actions.cancel_calls == 2
    assert [request.request_id for _, request in actions.replayed] == [42, 42]


async def test_waiter_does_not_consume_later_equal_id_submission() -> None:
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()
    first_instance = _instance(suffix="first")
    second_instance = _instance(suffix="second")

    class BlockingRegistry:
        def __init__(self) -> None:
            self.calls = 0

        async def refresh(self, _key: str) -> ModelInstance:
            self.calls += 1
            if self.calls == 1:
                refresh_started.set()
                await release_refresh.wait()
                return first_instance
            return second_instance

    actions: RecordingActions[str] = RecordingActions()
    submitted_later = False
    lifecycle: ModelLifecycle[str]

    def on_state(state: str) -> None:
        nonlocal submitted_later
        actions.on_state(state)
        if state == "ready" and not submitted_later:
            submitted_later = True
            lifecycle.submit(
                "qwen3.5",
                RecoveryRequest(42, "genuinely later equal-id event"),
            )

    registry = BlockingRegistry()
    lifecycle = ModelLifecycle(
        registry,
        cancel_active=actions.cancel_active,
        warm_up=actions.warm_up,
        replay=actions.replay,
        sleeper=actions.sleep,
        on_state=on_state,
    )
    first = asyncio.create_task(
        lifecycle.recover("qwen3.5", RecoveryRequest(42, "running event"))
    )
    await refresh_started.wait()
    waiter = asyncio.create_task(
        lifecycle.recover("qwen3.5", RecoveryRequest(42, "overlapping waiter"))
    )
    await asyncio.sleep(0)

    release_refresh.set()
    first_result, waiter_result = await asyncio.gather(first, waiter)
    later_result = await lifecycle.recover("qwen3.5")

    assert first_result is first_instance
    assert waiter_result is first_instance
    assert later_result is second_instance
    assert registry.calls == 2
    assert actions.cancel_calls == 2
    assert [request.payload for _, request in actions.replayed] == [
        "running event",
        "genuinely later equal-id event",
    ]


async def test_stale_lower_id_after_success_returns_prior_instance_without_new_wave() -> None:
    instance = _instance()
    registry = ScriptedRegistry([instance])
    actions: RecordingActions[str] = RecordingActions()
    lifecycle = _lifecycle(registry, actions)
    first = await lifecycle.recover("qwen3.5", RecoveryRequest(51, "completed"))
    snapshot = (
        list(registry.refresh_calls),
        actions.cancel_calls,
        list(actions.delays),
        list(actions.warmed),
        list(actions.replayed),
        list(actions.states),
    )

    stale = await lifecycle.recover("qwen3.5", RecoveryRequest(50, "stale"))

    assert first is instance
    assert stale is instance
    assert (
        registry.refresh_calls,
        actions.cancel_calls,
        actions.delays,
        actions.warmed,
        actions.replayed,
        actions.states,
    ) == snapshot


async def test_lower_caller_targets_greater_pending_submission() -> None:
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()
    instance = _instance()

    class BlockingRegistry:
        def __init__(self) -> None:
            self.calls = 0

        async def refresh(self, _key: str) -> ModelInstance:
            self.calls += 1
            refresh_started.set()
            await release_refresh.wait()
            return instance

    registry = BlockingRegistry()
    actions: RecordingActions[str] = RecordingActions()
    lifecycle = ModelLifecycle(
        registry,
        cancel_active=actions.cancel_active,
        warm_up=actions.warm_up,
        replay=actions.replay,
        sleeper=actions.sleep,
    )
    greater = asyncio.create_task(
        lifecycle.recover("qwen3.5", RecoveryRequest(61, "greater pending"))
    )
    await refresh_started.wait()
    lower = asyncio.create_task(
        lifecycle.recover("qwen3.5", RecoveryRequest(60, "lower caller"))
    )
    await asyncio.sleep(0)

    release_refresh.set()
    greater_result, lower_result = await asyncio.gather(greater, lower)

    assert greater_result is instance
    assert lower_result is instance
    assert registry.calls == 1
    assert actions.cancel_calls == 1
    assert [request for _, request in actions.replayed] == [
        RecoveryRequest(61, "greater pending")
    ]


async def test_newer_concurrent_caller_before_replay_joins_covering_wave() -> None:
    warm_started = asyncio.Event()
    release_warm = asyncio.Event()
    instance = _instance()
    registry = ScriptedRegistry([instance])
    actions: RecordingActions[str] = RecordingActions()

    async def warm_up(current: ModelInstance) -> None:
        actions.warmed.append(current)
        warm_started.set()
        await release_warm.wait()

    lifecycle = ModelLifecycle(
        registry,
        cancel_active=actions.cancel_active,
        warm_up=warm_up,
        replay=actions.replay,
        sleeper=actions.sleep,
        on_state=actions.on_state,
    )
    first = asyncio.create_task(
        lifecycle.recover("qwen3.5", RecoveryRequest(50, "old"))
    )
    await warm_started.wait()
    second = asyncio.create_task(
        lifecycle.recover("qwen3.5", RecoveryRequest(51, "new"))
    )
    await asyncio.sleep(0)

    release_warm.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result is instance
    assert second_result is instance
    assert registry.refresh_calls == ["qwen3.5"]
    assert [request.request_id for _, request in actions.replayed] == [51]


async def test_newer_request_after_replay_starts_a_second_wave() -> None:
    replay_started = asyncio.Event()
    release_first_replay = asyncio.Event()
    instance = _instance()
    refresh_calls = 0
    cancel_calls = 0
    replayed: list[int] = []

    class Registry:
        async def refresh(self, _key: str) -> ModelInstance:
            nonlocal refresh_calls
            refresh_calls += 1
            return instance

    async def cancel_active() -> None:
        nonlocal cancel_calls
        cancel_calls += 1

    async def warm_up(_instance: ModelInstance) -> None:
        return None

    async def replay(_instance: ModelInstance, request: RecoveryRequest[str]) -> None:
        replayed.append(request.request_id)
        if request.request_id == 13:
            replay_started.set()
            await release_first_replay.wait()

    async def sleep(_delay: float) -> None:
        return None

    lifecycle = ModelLifecycle(
        Registry(),
        cancel_active=cancel_active,
        warm_up=warm_up,
        replay=replay,
        sleeper=sleep,
    )
    first = asyncio.create_task(
        lifecycle.recover("qwen3.5", RecoveryRequest(13, "first"))
    )
    await replay_started.wait()
    second = asyncio.create_task(
        lifecycle.recover("qwen3.5", RecoveryRequest(14, "second"))
    )
    await asyncio.sleep(0)

    release_first_replay.set()
    await asyncio.gather(first, second)

    assert replayed == [13, 14]
    assert refresh_calls == 2
    assert cancel_calls == 2


async def test_different_keys_recover_independently() -> None:
    first_refresh_started = asyncio.Event()
    release_first_refresh = asyncio.Event()
    replayed: list[tuple[str, int]] = []

    class IndependentRegistry:
        async def refresh(self, key: str) -> ModelInstance:
            if key == "model-a":
                first_refresh_started.set()
                await release_first_refresh.wait()
            return _instance(key)

    async def no_op() -> None:
        return None

    async def warm_up(_instance: ModelInstance) -> None:
        return None

    async def replay(instance: ModelInstance, request: RecoveryRequest[str]) -> None:
        replayed.append((instance.key, request.request_id))

    async def sleep(_delay: float) -> None:
        return None

    lifecycle = ModelLifecycle(
        IndependentRegistry(),
        cancel_active=no_op,
        warm_up=warm_up,
        replay=replay,
        sleeper=sleep,
    )
    first = asyncio.create_task(
        lifecycle.recover("model-a", RecoveryRequest(15, "first"))
    )
    await first_refresh_started.wait()

    second = asyncio.create_task(
        lifecycle.recover("model-b", RecoveryRequest(16, "second"))
    )
    second_result = await asyncio.wait_for(second, timeout=0.5)

    assert second_result.key == "model-b"
    assert not first.done()
    release_first_refresh.set()
    await first
    assert sorted(replayed) == [("model-a", 15), ("model-b", 16)]


async def test_replay_error_terminalizes_wave_until_manual_retry() -> None:
    replay_error = RuntimeError("partial replay failure")
    recovered = _instance(suffix="manual")
    registry = ScriptedRegistry([_instance(suffix="failed"), recovered])
    actions: RecordingActions[str] = RecordingActions(
        replay_errors=[replay_error, None]
    )
    lifecycle = _lifecycle(registry, actions)
    request = RecoveryRequest(60, "question")

    with pytest.raises(ModelRecoveryError) as captured:
        await lifecycle.recover("qwen3.5", request)

    assert captured.value.key == "qwen3.5"
    assert captured.value.attempts == 1
    assert captured.value.__cause__ is replay_error
    assert registry.refresh_calls == ["qwen3.5"]
    assert actions.states == ["recovering", "offline"]

    with pytest.raises(ModelOfflineError):
        await lifecycle.recover("qwen3.5")
    assert registry.refresh_calls == ["qwen3.5"]

    result = await lifecycle.recover("qwen3.5", manual_retry=True)

    assert result is recovered
    assert registry.refresh_calls == ["qwen3.5", "qwen3.5"]
    assert [request for _, request in actions.replayed] == [request, request]
    assert actions.states == ["recovering", "offline", "recovering", "ready"]


async def test_replay_cancellation_propagates_without_offlining() -> None:
    cancellation = asyncio.CancelledError()
    recovered = _instance(suffix="after-cancellation")
    registry = ScriptedRegistry([_instance(suffix="cancelled"), recovered])
    actions: RecordingActions[str] = RecordingActions(
        replay_errors=[cancellation, None]
    )
    lifecycle = _lifecycle(registry, actions)
    request = RecoveryRequest(61, "question")

    with pytest.raises(asyncio.CancelledError) as captured:
        await lifecycle.recover("qwen3.5", request)

    assert captured.value is cancellation
    assert actions.states == ["recovering"]

    result = await lifecycle.recover("qwen3.5")

    assert result is recovered
    assert registry.refresh_calls == ["qwen3.5", "qwen3.5"]
    assert actions.states == ["recovering", "recovering", "ready"]


async def test_recover_without_queued_or_explicit_request_raises_typed_error() -> None:
    registry = ScriptedRegistry([])
    actions: RecordingActions[str] = RecordingActions()
    lifecycle = _lifecycle(registry, actions)

    with pytest.raises(NoRecoveryRequestError) as captured:
        await lifecycle.recover("qwen3.5")

    assert captured.value.key == "qwen3.5"
    assert registry.refresh_calls == []
    assert actions.cancel_calls == 0
    assert actions.states == []


def test_recovery_request_is_frozen() -> None:
    request = RecoveryRequest(17, {"opaque": "payload"})

    with pytest.raises(FrozenInstanceError):
        setattr(request, "request_id", 18)
