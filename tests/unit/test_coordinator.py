import asyncio
import gc
from collections.abc import AsyncIterator, Mapping

import pytest

from interview_assistant.lmstudio.models import ChatEvent


class RecordingSignal:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def emit(self, *args: object) -> None:
        self.calls.append(args)


class RecordingEvents:
    def __init__(self) -> None:
        self.answer_reset = RecordingSignal()
        self.answer_delta = RecordingSignal()
        self.notification = RecordingSignal()


class RaisingSignal:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def emit(self, *_args: object) -> None:
        raise self._error


class ImmediateClient:
    def __init__(self) -> None:
        self.finished: asyncio.Queue[None] = asyncio.Queue()
        self.payloads: list[dict[str, object]] = []

    async def stream_chat(
        self,
        payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        self.payloads.append(dict(payload))
        try:
            yield ChatEvent(type="chat.start")
            yield ChatEvent(type="tool_call.start", tool="lookup")
            yield ChatEvent(type="message.delta", content=str(payload["question"]))
            yield ChatEvent(type="chat.end")
        finally:
            self.finished.put_nowait(None)


class CancellationSuppressingClient:
    def __init__(self) -> None:
        self.old_started = asyncio.Event()
        self.release_old = asyncio.Event()
        self.old_cancelled = asyncio.Event()
        self.old_attempted_delta = asyncio.Event()
        self.new_finished = asyncio.Event()

    async def stream_chat(
        self,
        payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        if payload["question"] == "old":
            self.old_started.set()
            try:
                await self.release_old.wait()
            except asyncio.CancelledError:
                self.old_cancelled.set()
            self.old_attempted_delta.set()
            yield ChatEvent(type="message.delta", content="stale")
            return

        try:
            yield ChatEvent(type="message.delta", content="current")
        finally:
            self.new_finished.set()


class OverlappingCompletionClient:
    def __init__(self) -> None:
        self.old_started = asyncio.Event()
        self.finish_old = asyncio.Event()
        self.old_finished = asyncio.Event()
        self.new_started = asyncio.Event()
        self.release_new = asyncio.Event()

    async def stream_chat(
        self,
        payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        if payload["question"] == "old":
            self.old_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass
            await self.finish_old.wait()
            self.old_finished.set()
            return

        self.new_started.set()
        await self.release_new.wait()
        yield ChatEvent(type="message.delta", content="current")


class ExplodingClient:
    def __init__(self) -> None:
        self.finished = asyncio.Event()

    async def stream_chat(
        self,
        _payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        try:
            raise RuntimeError("payload-secret-marker top-secret-token")
        finally:
            self.finished.set()
        yield ChatEvent(type="chat.end")  # pragma: no cover


class StaleExplodingClient:
    def __init__(self) -> None:
        self.old_started = asyncio.Event()
        self.old_raising = asyncio.Event()
        self.new_finished = asyncio.Event()

    async def stream_chat(
        self,
        payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        if payload["question"] == "old":
            self.old_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass
            self.old_raising.set()
            raise RuntimeError("stale payload-secret-marker top-secret-token")

        try:
            yield ChatEvent(type="message.delta", content="current")
        finally:
            self.new_finished.set()


class CancellableClient:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self.cancellations = 0

    async def stream_chat(
        self,
        _payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        try:
            self.started.set()
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancellations += 1
            raise
        finally:
            await asyncio.sleep(0)
            self.closed.set()
        yield ChatEvent(type="chat.end")  # pragma: no cover


class CancellationSuppressingDeltaClient:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.attempted_delta = asyncio.Event()
        self.closed = asyncio.Event()

    async def stream_chat(
        self,
        _payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        try:
            self.started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass
            self.attempted_delta.set()
            yield ChatEvent(type="message.delta", content="too late")
        finally:
            await asyncio.sleep(0)
            self.closed.set()


async def test_submit_returns_monotonic_ids_resets_and_emits_current_deltas() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    events = RecordingEvents()
    client = ImmediateClient()
    coordinator = RequestCoordinator(events, client)

    first_id = coordinator.submit({"question": "first"})
    await client.finished.get()
    second_id = coordinator.submit({"question": "second"})
    await client.finished.get()

    assert (first_id, second_id) == (1, 2)
    assert events.answer_reset.calls == [(1,), (2,)]
    assert events.answer_delta.calls == [(1, "first"), (2, "second")]
    assert events.notification.calls == []


async def test_submit_copies_payload_before_background_task_starts() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    events = RecordingEvents()
    client = ImmediateClient()
    coordinator = RequestCoordinator(events, client)
    payload: dict[str, object] = {"question": "before"}

    coordinator.submit(payload)
    payload["question"] = "after"
    await client.finished.get()

    assert client.payloads == [{"question": "before"}]
    assert events.answer_delta.calls == [(1, "before")]


async def test_replacement_cancels_prior_and_rejects_cancellation_suppressed_delta() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    events = RecordingEvents()
    client = CancellationSuppressingClient()
    coordinator = RequestCoordinator(events, client)

    coordinator.submit({"question": "old"})
    await client.old_started.wait()
    current_id = coordinator.submit({"question": "new"})
    client.release_old.set()
    await asyncio.wait_for(client.old_attempted_delta.wait(), timeout=1)
    await asyncio.wait_for(client.new_finished.wait(), timeout=1)
    await asyncio.sleep(0)

    assert current_id == 2
    assert client.old_cancelled.is_set()
    assert events.answer_reset.calls == [(1,), (2,)]
    assert events.answer_delta.calls == [(2, "current")]


async def test_completed_current_task_releases_active_reference() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    client = ImmediateClient()
    coordinator = RequestCoordinator(RecordingEvents(), client)

    coordinator.submit({"question": "done"})
    await client.finished.get()
    await asyncio.sleep(0)

    assert coordinator._active_task is None


async def test_old_task_completion_cannot_clear_newer_active_task() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    client = OverlappingCompletionClient()
    coordinator = RequestCoordinator(RecordingEvents(), client)
    coordinator.submit({"question": "old"})
    await client.old_started.wait()

    coordinator.submit({"question": "new"})
    new_task = coordinator._active_task
    await client.new_started.wait()
    client.finish_old.set()
    await client.old_finished.wait()
    await asyncio.sleep(0)

    assert new_task is not None
    assert coordinator._active_task is new_task
    assert not new_task.done()

    client.release_new.set()
    await new_task


async def test_current_error_emits_only_a_generic_notification() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    events = RecordingEvents()
    client = ExplodingClient()
    coordinator = RequestCoordinator(events, client)

    coordinator.submit({"secret": "top-secret-token"})
    await client.finished.wait()
    await asyncio.sleep(0)

    assert len(events.notification.calls) == 1
    message = events.notification.calls[0][0]
    assert isinstance(message, str)
    assert "payload-secret-marker" not in message
    assert "top-secret-token" not in message


async def test_stale_error_after_replacement_emits_no_notification() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    events = RecordingEvents()
    client = StaleExplodingClient()
    coordinator = RequestCoordinator(events, client)

    coordinator.submit({"question": "old"})
    await client.old_started.wait()
    coordinator.submit({"question": "new"})
    await client.old_raising.wait()
    await client.new_finished.wait()
    await asyncio.sleep(0)

    assert events.answer_delta.calls == [(2, "current")]
    assert events.notification.calls == []


async def test_cancel_active_awaits_closure_is_idempotent_and_does_not_reset() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    events = RecordingEvents()
    client = CancellableClient()
    coordinator = RequestCoordinator(events, client)
    request_id = coordinator.submit({"question": "cancel me"})
    await client.started.wait()

    await coordinator.cancel_active()

    assert request_id == 1
    assert client.closed.is_set()
    assert client.cancellations == 1
    assert coordinator._active_id is None
    assert coordinator._active_task is None
    assert events.answer_reset.calls == [(1,)]
    assert events.answer_delta.calls == []

    await coordinator.cancel_active()
    assert client.cancellations == 1
    assert events.answer_reset.calls == [(1,)]


async def test_cancel_active_rejects_delta_when_generator_suppresses_cancellation() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    events = RecordingEvents()
    client = CancellationSuppressingDeltaClient()
    coordinator = RequestCoordinator(events, client)
    coordinator.submit({"question": "cancel me"})
    await client.started.wait()

    await coordinator.cancel_active()

    assert client.attempted_delta.is_set()
    assert client.closed.is_set()
    assert events.answer_delta.calls == []
    assert events.notification.calls == []
    assert coordinator._active_task is None


async def test_cancel_active_propagates_non_cancellation_task_error() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    error = RuntimeError("must propagate")

    async def fail() -> None:
        raise error

    coordinator = RequestCoordinator(RecordingEvents(), ImmediateClient())
    failed_task = asyncio.create_task(fail())
    await asyncio.sleep(0)
    coordinator._active_id = 99
    coordinator._active_task = failed_task

    with pytest.raises(RuntimeError) as captured:
        await coordinator.cancel_active()

    assert captured.value is error
    assert coordinator._active_id is None
    assert coordinator._active_task is None


async def test_done_callback_consumes_background_exception() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    reports: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _loop, context: reports.append(context))
    try:
        events = RecordingEvents()
        events.notification = RaisingSignal(RuntimeError("signal failed"))
        client = ExplodingClient()
        coordinator = RequestCoordinator(events, client)
        coordinator.submit({})
        await client.finished.wait()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert coordinator._active_task is None
        del coordinator
        gc.collect()
        await asyncio.sleep(0)
        assert reports == []
    finally:
        loop.set_exception_handler(previous_handler)
