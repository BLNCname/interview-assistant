from __future__ import annotations

import asyncio
import gc
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from threading import Thread
from time import monotonic
from weakref import ref

import pytest
from PyQt6.QtWidgets import QApplication

from interview_assistant.app import InterviewApplication
from interview_assistant.audio.models import AudioSource
from interview_assistant.capture.worker import CaptureResult
from interview_assistant.config import AppConfig
from interview_assistant.diagnostics.readiness import CheckResult, ReadinessReport
from interview_assistant.events import EventBus
from interview_assistant.lmstudio.models import ChatEvent, ModelInstance
from interview_assistant.orchestration.coordinator import RequestCoordinator
from interview_assistant.retrieval.policy import SearchPolicy
from interview_assistant.stt.engine import TranscriptHypothesis
from interview_assistant.state import StateMachine
from interview_assistant.transcript.detector import QuestionDetector
from interview_assistant.transcript.store import TranscriptStore
from interview_assistant.ui.windows_affinity import AffinityResult


class _Service:
    def __init__(self, *, start_error: BaseException | None = None) -> None:
        self.start_error = start_error
        self.start_count = 0
        self.stop_count = 0
        self.pause_count = 0
        self.resume_count = 0

    def start(self) -> None:
        self.start_count += 1
        if self.start_error is not None:
            raise self.start_error

    def stop(self, timeout: float | None = None) -> bool:
        del timeout
        self.stop_count += 1
        return True

    def pause(self) -> None:
        self.pause_count += 1

    def resume(self) -> None:
        self.resume_count += 1


class _UnstoppableSTT(_Service):
    def stop(self, timeout: float | None = None) -> bool:
        del timeout
        self.stop_count += 1
        return False


class _WarmEngine:
    pass


class _EngineBackedSTT(_Service):
    def __init__(self, engine: _WarmEngine) -> None:
        super().__init__()
        self.engine = engine


class _Registry:
    async def ensure_ready(self, key: str, refresh: bool = False) -> ModelInstance:
        del refresh
        return ModelInstance(key=key, instance_id=f"instance:{key}", state="ready")

    async def refresh(self, key: str) -> ModelInstance:
        return await self.ensure_ready(key, refresh=True)


class _RejectingLoadRegistry(_Registry):
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def ensure_ready(self, key: str, refresh: bool = False) -> ModelInstance:
        del refresh
        self.calls.append(key)
        return ModelInstance(key=key, instance_id="unexpected", state="ready")


class _Client:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []
        self.close_count = 0

    async def stream_chat(
        self,
        payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        self.payloads.append(dict(payload))
        yield ChatEvent(type="chat.start")
        yield ChatEvent(type="message.delta", content="answer")
        yield ChatEvent(type="chat.end", result={})

    async def aclose(self) -> None:
        self.close_count += 1


class _BlockingClient(_Client):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def stream_chat(
        self,
        payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        self.payloads.append(dict(payload))
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        yield ChatEvent(type="chat.start")
        yield ChatEvent(type="message.delta", content="answer")
        yield ChatEvent(type="chat.end", result={})


class _TimeoutRetrievalClient(_Client):
    def __init__(self) -> None:
        super().__init__()
        self.retrieval_cancelled = asyncio.Event()

    async def stream_chat(
        self,
        payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        self.payloads.append(dict(payload))
        if "integrations" in payload:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.retrieval_cancelled.set()
                raise
        yield ChatEvent(type="chat.start")
        yield ChatEvent(type="message.delta", content="answer")
        yield ChatEvent(type="chat.end", result={})


class _SlowShutdownCapture:
    def __init__(self) -> None:
        self.shutdown_started = asyncio.Event()
        self.release_shutdown = asyncio.Event()
        self.shutdown_count = 0

    async def capture_for_event(self, kind: str, *, manual: bool = False) -> CaptureResult:
        del kind, manual
        return CaptureResult("disallowed", None, None)

    async def shutdown(self) -> None:
        self.shutdown_count += 1
        self.shutdown_started.set()
        await self.release_shutdown.wait()


class _ReplacingCapture:
    def __init__(self) -> None:
        self.first_started = asyncio.Event()
        self.first_cancelled = asyncio.Event()
        self.calls = 0

    async def capture_for_event(self, kind: str, *, manual: bool = False) -> CaptureResult:
        del kind, manual
        self.calls += 1
        if self.calls == 1:
            self.first_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.first_cancelled.set()
                raise
        return CaptureResult("disallowed", None, None)

    async def shutdown(self) -> None:
        return None


class _ParityCapture:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.manual_count = 0
        self.automatic_count = 0
        self.shutdown_count = 0

    async def capture_for_event(self, kind: str, *, manual: bool = False) -> CaptureResult:
        del kind
        if manual:
            self.manual_count += 1
            return CaptureResult("captured", self.path, None)
        self.automatic_count += 1
        return CaptureResult("disallowed", None, None)

    async def shutdown(self) -> None:
        self.shutdown_count += 1


class _FailOnceShutdownCapture(_ParityCapture):
    async def shutdown(self) -> None:
        self.shutdown_count += 1
        if self.shutdown_count == 1:
            raise RuntimeError("capture close failed once")


class _BlockingActionCapture(_ParityCapture):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.manual_started = asyncio.Event()
        self.manual_cancelled = asyncio.Event()

    async def capture_for_event(self, kind: str, *, manual: bool = False) -> CaptureResult:
        if not manual:
            return await super().capture_for_event(kind, manual=manual)
        self.manual_count += 1
        self.manual_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.manual_cancelled.set()
            raise


class _FailingActionCapture(_ParityCapture):
    async def capture_for_event(self, kind: str, *, manual: bool = False) -> CaptureResult:
        if manual:
            raise RuntimeError("sensitive backend detail")
        return await super().capture_for_event(kind, manual=manual)


class _ReplacingFailureCapture(_ParityCapture):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.fail_next = False

    async def capture_for_event(self, kind: str, *, manual: bool = False) -> CaptureResult:
        if manual and self.fail_next:
            self.manual_count += 1
            return CaptureResult("disallowed", None, None)
        return await super().capture_for_event(kind, manual=manual)


class _OverlappingManualCapture(_ParityCapture):
    def __init__(self, first_path: Path, second_path: Path) -> None:
        super().__init__(first_path)
        self.first_path = first_path
        self.second_path = second_path
        self.first_started = asyncio.Event()
        self.second_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.release_second = asyncio.Event()
        self.first_error = False

    async def capture_for_event(self, kind: str, *, manual: bool = False) -> CaptureResult:
        if not manual:
            return await super().capture_for_event(kind, manual=manual)
        self.manual_count += 1
        if self.manual_count == 1:
            self.first_started.set()
            await self.release_first.wait()
            if self.first_error:
                raise RuntimeError("sensitive first capture failure")
            return CaptureResult("captured", self.first_path, None)
        self.second_started.set()
        await self.release_second.wait()
        return CaptureResult("captured", self.second_path, None)


class _FailingQuestionCapture(_ParityCapture):
    async def capture_for_event(self, kind: str, *, manual: bool = False) -> CaptureResult:
        if not manual:
            raise RuntimeError("sensitive capture failure")
        return await super().capture_for_event(kind, manual=manual)


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "audio": {"system_device_id": "system", "microphone_device_id": "mic"},
            "lmstudio": {"text_model": "qwen", "vision_model": "qwen"},
            "search": {"mode": "off"},
        }
    )


def _hypothesis(text: str) -> TranscriptHypothesis:
    ended_at = monotonic()
    return TranscriptHypothesis(
        source=AudioSource.SYSTEM,
        text=text,
        language="en",
        is_final=True,
        started_at=ended_at - 1,
        ended_at=ended_at,
    )


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    deadline = monotonic() + timeout
    while not predicate():
        QApplication.processEvents()
        if monotonic() >= deadline:
            raise AssertionError("condition did not become true")
        await asyncio.sleep(0)


def _runtime(
    app: InterviewApplication,
    *,
    audio: _Service | None = None,
    stt: _Service | None = None,
    hotkeys: _Service | None = None,
    capture: _SlowShutdownCapture | _ReplacingCapture | _ParityCapture | None = None,
    client: _Client | None = None,
    registry: _Registry | None = None,
):
    from interview_assistant.runtime import ApplicationRuntime, RuntimeServices

    active_client = client or _Client()
    active_capture = capture or _SlowShutdownCapture()
    services = RuntimeServices(
        audio=audio or _Service(),
        stt=stt or _Service(),
        hotkeys=hotkeys or _Service(),
        capture=active_capture,
        registry=registry or _Registry(),
        client=active_client,
        coordinator=RequestCoordinator(app.events, active_client),
        transcript_store=TranscriptStore(),
        question_detector=QuestionDetector(cooldown_seconds=0),
        search_policy=SearchPolicy("off"),
    )
    return ApplicationRuntime(app, _config(), services), services, active_client


def test_runtime_toggles_overlay_edit_mode_from_event(qtbot) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    runtime, _, _ = _runtime(app)

    assert not app.ribbon.is_edit_mode
    app.events.overlay_interaction_toggled.emit()
    assert app.ribbon.is_edit_mode
    app.events.overlay_interaction_toggled.emit()
    assert not app.ribbon.is_edit_mode

    del runtime
    app.shutdown()


async def test_concurrent_shutdown_callers_join_one_cleanup_barrier(qtbot) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    capture = _SlowShutdownCapture()
    runtime, services, client = _runtime(app, capture=capture)
    await runtime.start()

    first = asyncio.create_task(runtime.shutdown())
    await capture.shutdown_started.wait()
    second = asyncio.create_task(runtime.shutdown())
    await asyncio.sleep(0)

    assert not first.done()
    assert not second.done()

    capture.release_shutdown.set()
    await asyncio.gather(first, second)

    assert services.hotkeys.stop_count == 1
    assert services.audio.stop_count == 1
    assert services.stt.stop_count == 1
    assert capture.shutdown_count == 1
    assert client.close_count == 1


async def test_failed_shutdown_retries_only_unfinished_resources_and_then_is_idempotent(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    capture = _FailOnceShutdownCapture(tmp_path / "unused.jpg")
    runtime, services, client = _runtime(app, capture=capture)
    await runtime.start()

    first = asyncio.create_task(runtime.shutdown(close_application=False))
    second = asyncio.create_task(runtime.shutdown(close_application=False))
    outcomes = await asyncio.gather(first, second, return_exceptions=True)

    assert all(isinstance(outcome, RuntimeError) for outcome in outcomes)
    assert capture.shutdown_count == 1
    assert client.close_count == 1
    assert services.hotkeys.stop_count == 1
    assert services.audio.stop_count == 1
    assert services.stt.stop_count == 1

    await runtime.shutdown(close_application=False)
    await runtime.shutdown(close_application=False)
    assert capture.shutdown_count == 2
    assert client.close_count == 1
    assert services.hotkeys.stop_count == 1
    assert services.audio.stop_count == 1
    assert services.stt.stop_count == 1
    app.shutdown()


async def test_partial_start_failure_rolls_back_already_started_workers(qtbot) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    stt = _Service()
    audio_error = RuntimeError("audio start failed")
    audio = _Service(start_error=audio_error)
    hotkeys = _Service()
    capture = _SlowShutdownCapture()
    runtime, _, _ = _runtime(
        app,
        stt=stt,
        audio=audio,
        hotkeys=hotkeys,
        capture=capture,
    )

    with pytest.raises(RuntimeError) as captured:
        await runtime.start()

    assert captured.value is audio_error
    assert stt.start_count == 1
    assert stt.stop_count == 1
    assert audio.start_count == 1
    assert hotkeys.start_count == 0

    shutdown = asyncio.create_task(runtime.shutdown())
    await capture.shutdown_started.wait()
    capture.release_shutdown.set()
    await shutdown


async def test_failed_readiness_blocks_model_loads_and_workers(qtbot) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    app.set_readiness_report(
        ReadinessReport(
            (
                CheckResult(
                    "display_affinity",
                    "failed",
                    "unavailable",
                    "enable affinity",
                    1.0,
                    True,
                ),
            )
        )
    )
    registry_calls: list[str] = []
    runtime, services, _ = _runtime(
        app,
        capture=_ParityCapture(Path("unused.jpg")),
        registry=_RejectingLoadRegistry(registry_calls),
    )

    with pytest.raises(RuntimeError, match="Readiness"):
        await runtime.start()

    assert registry_calls == []
    assert services.stt.start_count == 0
    assert services.audio.start_count == 0
    assert services.hotkeys.start_count == 0
    await runtime.shutdown()


async def test_show_time_affinity_failure_blocks_model_loads_and_workers(
    qapp,
    qtbot,
) -> None:
    ready = ReadinessReport(
        (
            CheckResult(
                "display_affinity",
                "ready",
                "precheck passed",
                "enable affinity",
                1.0,
                True,
            ),
        )
    )
    app = InterviewApplication(
        qapp,
        EventBus(),
        StateMachine(),
        overlay_settings=None,
        affinity_applier=lambda _hwnd: AffinityResult(False, None, 5),
        readiness_report=ready,
    )
    qtbot.addWidget(app.ribbon)
    registry_calls: list[str] = []
    runtime, services, _ = _runtime(
        app,
        capture=_ParityCapture(Path("unused.jpg")),
        registry=_RejectingLoadRegistry(registry_calls),
    )

    with pytest.raises(RuntimeError, match="capture exclusion"):
        await runtime.start()

    assert registry_calls == []
    assert services.stt.start_count == 0
    assert services.audio.start_count == 0
    assert services.hotkeys.start_count == 0
    await runtime.shutdown()


async def test_new_question_cancels_stale_capture_before_generation(qtbot) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    capture = _ReplacingCapture()
    runtime, _, client = _runtime(app, capture=capture)
    await runtime.start()

    first = runtime.submit_hypothesis(_hypothesis("Design a scalable chat service"))
    await capture.first_started.wait()
    second = runtime.submit_hypothesis(_hypothesis("Design a URL shortener service"))

    await asyncio.wait_for(capture.first_cancelled.wait(), timeout=1)
    await asyncio.wait_for(second, timeout=1)

    assert first.cancelled()
    assert len(client.payloads) == 1
    assert "URL shortener" in str(client.payloads[0]["input"])

    await runtime.shutdown()


@pytest.mark.parametrize(
    "non_question",
    [
        TranscriptHypothesis(
            source=AudioSource.MICROPHONE,
            text="My clarification",
            language="en",
            is_final=True,
            started_at=1,
            ended_at=2,
        ),
        TranscriptHypothesis(
            source=AudioSource.SYSTEM,
            text="partial words",
            language="en",
            is_final=False,
            started_at=1,
            ended_at=2,
        ),
        TranscriptHypothesis(
            source=AudioSource.SYSTEM,
            text="We have five minutes left.",
            language="en",
            is_final=True,
            started_at=1,
            ended_at=2,
        ),
    ],
    ids=["microphone-final", "system-partial", "system-final-statement"],
)
async def test_non_question_transcript_never_cancels_active_generation(
    qtbot,
    non_question: TranscriptHypothesis,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    client = _BlockingClient()
    runtime, services, _ = _runtime(app, client=client)
    await runtime.start()

    generation = runtime.submit_hypothesis(_hypothesis("Design a scalable chat service"))
    await client.started.wait()

    transcript_task = runtime.submit_hypothesis(non_question)
    await asyncio.wait_for(transcript_task, timeout=1)

    assert not generation.done()
    assert not client.cancelled.is_set()

    client.release.set()
    await asyncio.wait_for(generation, timeout=1)
    shutdown = asyncio.create_task(runtime.shutdown())
    capture = services.capture
    assert isinstance(capture, _SlowShutdownCapture)
    await capture.shutdown_started.wait()
    capture.release_shutdown.set()
    await shutdown


async def test_hotkey_actions_cover_pause_overlay_capture_search_clear_and_history(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    capture = _ParityCapture(tmp_path / "manual.jpg")
    capture.path.write_bytes(b"manual")
    runtime, services, _ = _runtime(app, capture=capture)
    await runtime.start()
    assert app.ribbon.isVisible()

    app.events.pause_toggled.emit()
    qtbot.waitUntil(lambda: services.audio.pause_count == 1)
    assert app.states.state.value == "paused"
    app.events.pause_toggled.emit()
    qtbot.waitUntil(lambda: services.audio.resume_count == 1)
    assert app.states.state.value == "listening"

    worker = Thread(target=app.events.overlay_visibility_toggled.emit)
    worker.start()
    worker.join(timeout=1)
    qtbot.waitUntil(lambda: not app.ribbon.isVisible())

    app.events.screenshot_requested.emit()
    await _wait_until(lambda: capture.manual_count == 1)
    assert runtime.manual_image_path == capture.path

    app.events.forced_search_requested.emit()
    qtbot.wait(10)
    forced = services.search_policy.integrations_for("Explain a binary search tree")
    assert [integration.id for integration in forced] == ["mcp/duckduckgo"]
    assert services.search_policy.integrations_for("Explain a binary search tree") == []

    app.events.answer_reset.emit(99)
    app.events.answer_delta.emit(99, "temporary answer")
    assert app.ribbon.answer_text == "temporary answer"
    await runtime.handle_hypothesis(
        TranscriptHypothesis(
            AudioSource.MICROPHONE,
            "context",
            "en",
            True,
            monotonic() - 1,
            monotonic(),
        )
    )
    assert len(services.transcript_store) == 1
    app.events.answer_clear_requested.emit()
    qtbot.waitUntil(lambda: app.ribbon.answer_text == "" and len(services.transcript_store) == 0)

    await runtime.shutdown()
    app.events.screenshot_requested.emit()
    qtbot.wait(10)
    assert capture.manual_count == 1


async def test_force_request_uses_latest_final_system_text_and_empty_is_visible(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    notifications: list[str] = []
    app.events.notification.connect(notifications.append)
    capture = _ParityCapture(tmp_path / "manual.jpg")
    runtime, _, client = _runtime(app, capture=capture)
    await runtime.start()

    app.events.force_request.emit()
    await _wait_until(lambda: bool(notifications))
    assert notifications[-1] == "No final interviewer transcript is available."

    await runtime.handle_hypothesis(_hypothesis("We have five minutes left."))
    assert client.payloads == []
    app.events.force_request.emit()
    await _wait_until(lambda: len(client.payloads) == 1)

    assert "We have five minutes left" in str(client.payloads[0]["input"])
    await runtime.shutdown()


async def test_shutdown_cancels_and_drains_queued_action_tasks(qtbot, tmp_path: Path) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    capture = _BlockingActionCapture(tmp_path / "manual.jpg")
    runtime, _, _ = _runtime(app, capture=capture)
    await runtime.start()

    app.events.screenshot_requested.emit()
    await _wait_until(capture.manual_started.is_set)

    await asyncio.wait_for(runtime.shutdown(), timeout=1)

    assert capture.manual_cancelled.is_set()
    assert capture.shutdown_count == 1


async def test_manual_screenshot_is_consumed_once_without_redundant_capture(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    capture = _ParityCapture(tmp_path / "manual.jpg")
    capture.path.write_bytes(b"manual image")
    runtime, _, client = _runtime(app, capture=capture)
    await runtime.start()

    app.events.screenshot_requested.emit()
    await _wait_until(lambda: runtime.manual_image_path == capture.path)
    await runtime.submit_hypothesis(_hypothesis("Explain a binary search tree"))

    assert capture.manual_count == 1
    assert capture.automatic_count == 0
    assert runtime.manual_image_path is None
    assert "data:image/jpeg;base64," in str(client.payloads[0]["input"])
    assert str(capture.path) not in str(client.payloads[0])

    await runtime.shutdown()


async def test_manual_screenshot_emits_creating_ready_and_attached_states(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    messages: list[str] = []
    app.events.notification.connect(messages.append)
    capture = _ParityCapture(tmp_path / "manual.jpg")
    capture.path.write_bytes(b"manual image")
    runtime, _, client = _runtime(app, capture=capture)
    await runtime.start()

    await runtime.capture_manual_screenshot()

    assert messages[-2:] == [
        "Снимок создаётся",
        "Снимок готов для следующего запроса",
    ]
    await runtime.submit_hypothesis(_hypothesis("Explain a binary search tree"))

    assert "Снимок добавлен в запрос" in messages
    assert runtime.manual_image_path is None
    assert "data:image/jpeg;base64," in str(client.payloads[-1]["input"])
    await runtime.shutdown()


async def test_pending_screenshot_is_consumed_only_once(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    capture = _ParityCapture(tmp_path / "manual.jpg")
    capture.path.write_bytes(b"manual image")
    runtime, _, client = _runtime(app, capture=capture)
    await runtime.start()

    await runtime.capture_manual_screenshot()
    await runtime.submit_hypothesis(_hypothesis("What is shown?"))
    await runtime.submit_hypothesis(_hypothesis("Explain it again"))

    assert "data:image/jpeg;base64," in str(client.payloads[-2]["input"])
    assert "data:image/jpeg;base64," not in str(client.payloads[-1]["input"])
    await runtime.shutdown()


async def test_failed_new_screenshot_clears_stale_pending_image(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    messages: list[str] = []
    app.events.notification.connect(messages.append)
    capture = _ReplacingFailureCapture(tmp_path / "manual.jpg")
    capture.path.write_bytes(b"manual image")
    runtime, _, _ = _runtime(app, capture=capture)
    await runtime.start()

    await runtime.capture_manual_screenshot()
    capture.fail_next = True
    await runtime.capture_manual_screenshot()

    assert runtime.manual_image_path is None
    assert "Снимок не создан" in messages[-1]
    await runtime.shutdown()


async def test_newer_manual_capture_wins_when_older_capture_finishes_late(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    messages: list[str] = []
    app.events.notification.connect(messages.append)
    first_path = tmp_path / "first.jpg"
    second_path = tmp_path / "second.jpg"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    capture = _OverlappingManualCapture(first_path, second_path)
    runtime, _, client = _runtime(app, capture=capture)
    await runtime.start()

    first = asyncio.create_task(runtime.capture_manual_screenshot())
    await capture.first_started.wait()
    second = asyncio.create_task(runtime.capture_manual_screenshot())
    await capture.second_started.wait()
    capture.release_second.set()
    await second
    ready_messages = list(messages)

    capture.release_first.set()
    await first

    assert runtime.manual_image_path == second_path
    assert messages == ready_messages
    await runtime.submit_hypothesis(_hypothesis("What is shown?"))

    assert runtime.manual_image_path is None
    assert "data:image/jpeg;base64,c2Vjb25k" in str(client.payloads[-1]["input"])
    await runtime.shutdown()


async def test_late_failed_manual_capture_does_not_replace_newer_ready_capture(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    messages: list[str] = []
    app.events.notification.connect(messages.append)
    first_path = tmp_path / "first.jpg"
    second_path = tmp_path / "second.jpg"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    capture = _OverlappingManualCapture(first_path, second_path)
    capture.first_error = True
    runtime, _, _ = _runtime(app, capture=capture)
    await runtime.start()

    first = asyncio.create_task(runtime.capture_manual_screenshot())
    await capture.first_started.wait()
    second = asyncio.create_task(runtime.capture_manual_screenshot())
    await capture.second_started.wait()
    capture.release_second.set()
    await second
    ready_messages = list(messages)

    capture.release_first.set()
    await first

    assert runtime.manual_image_path == second_path
    assert messages == ready_messages
    await runtime.shutdown()


async def test_payload_build_failure_consumes_manual_image_without_attached_state(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import interview_assistant.runtime as runtime_module

    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    messages: list[str] = []
    app.events.notification.connect(messages.append)
    capture = _ParityCapture(tmp_path / "manual.jpg")
    capture.path.write_bytes(b"manual image")
    runtime, _, _ = _runtime(app, capture=capture)
    await runtime.start()
    await runtime.capture_manual_screenshot()

    def fail_payload(*_args: object) -> dict[str, object]:
        raise RuntimeError("sensitive payload failure")

    monkeypatch.setattr(runtime_module, "build_chat_payload", fail_payload)
    with pytest.raises(RuntimeError, match="sensitive payload failure"):
        await runtime.submit_hypothesis(_hypothesis("What is shown?"))
    await _wait_until(lambda: "Request processing failed." in messages)

    assert runtime.manual_image_path is None
    assert "Снимок добавлен в запрос" not in messages
    assert str(capture.path) not in " ".join(messages)
    assert "sensitive" not in " ".join(messages)
    await runtime.shutdown()


async def test_action_failure_is_visible_without_leaking_backend_details(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    notifications: list[str] = []
    app.events.notification.connect(notifications.append)
    runtime, _, _ = _runtime(
        app,
        capture=_FailingActionCapture(tmp_path / "manual.jpg"),
    )
    await runtime.start()

    app.events.screenshot_requested.emit()
    await _wait_until(lambda: bool(notifications))

    assert notifications == [
        "Снимок создаётся",
        "Снимок не создан: ошибка захвата",
    ]
    assert "sensitive" not in " ".join(notifications)
    await runtime.shutdown()


async def test_paused_runtime_ignores_late_stt_finals(qtbot, tmp_path: Path) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    capture = _ParityCapture(tmp_path / "manual.jpg")
    runtime, services, client = _runtime(app, capture=capture)
    await runtime.start()
    app.events.pause_toggled.emit()
    await _wait_until(lambda: app.states.state.value == "paused")

    await runtime.handle_hypothesis(_hypothesis("Explain a binary search tree"))

    assert client.payloads == []
    assert len(services.transcript_store) == 0
    await runtime.shutdown()


async def test_pause_during_generation_stays_paused_until_resumed(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    client = _BlockingClient()
    capture = _ParityCapture(tmp_path / "manual.jpg")
    runtime, _, _ = _runtime(app, capture=capture, client=client)
    await runtime.start()
    generation = runtime.submit_hypothesis(_hypothesis("Design a scalable chat service"))
    await client.started.wait()

    app.events.pause_toggled.emit()
    await _wait_until(lambda: app.states.state.value == "paused")
    client.release.set()
    await generation

    assert app.states.state.value == "paused"
    app.events.pause_toggled.emit()
    await _wait_until(lambda: app.states.state.value == "listening")
    await runtime.shutdown()


async def test_pipeline_exception_is_sanitized_and_returns_to_listening(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    notifications: list[str] = []
    app.events.notification.connect(notifications.append)
    runtime, _, _ = _runtime(
        app,
        capture=_FailingQuestionCapture(tmp_path / "unused.jpg"),
    )
    await runtime.start()

    task = runtime.submit_hypothesis(_hypothesis("Design a scalable chat service"))
    with pytest.raises(RuntimeError, match="sensitive capture"):
        await task
    await asyncio.sleep(0)

    assert notifications == ["Request processing failed."]
    assert "sensitive" not in " ".join(notifications)
    assert app.states.state.value == "listening"
    await runtime.shutdown()


async def test_resource_only_shutdown_preserves_ui_for_settings_rebuild(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    runtime, _, _ = _runtime(app, capture=_ParityCapture(tmp_path / "unused.jpg"))
    await runtime.start()

    await runtime.shutdown(close_application=False)

    assert not app.is_shutdown
    assert app.states.state.value != "stopped"
    assert not runtime.is_started
    app.shutdown()


async def test_resource_only_shutdown_disconnects_actions_before_runtime_rebuild(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    from interview_assistant.runtime import RuntimeHypothesisIngress

    engine = _WarmEngine()
    old_stt = _EngineBackedSTT(engine)
    old_runtime, old_services, old_client = _runtime(
        app,
        stt=old_stt,
        capture=_ParityCapture(tmp_path / "old.jpg"),
    )
    ingress = RuntimeHypothesisIngress(asyncio.get_running_loop())
    old_services.hypothesis_ingress = ingress
    ingress.bind(old_runtime.submit_hypothesis)
    lifecycle = old_runtime._model_lifecycle
    action_signals = (
        app.events.force_request,
        app.events.screenshot_requested,
        app.events.pause_toggled,
        app.events.overlay_visibility_toggled,
        app.events.overlay_interaction_toggled,
        app.events.forced_search_requested,
        app.events.answer_clear_requested,
    )
    assert all(app.events.receivers(signal) == 1 for signal in action_signals)
    await old_runtime.start()
    await old_runtime.shutdown(close_application=False)
    assert all(app.events.receivers(signal) == 0 for signal in action_signals)

    replacement, replacement_services, _ = _runtime(
        app,
        capture=_ParityCapture(tmp_path / "replacement.jpg"),
    )
    await replacement.start()
    app.events.pause_toggled.emit()
    await _wait_until(lambda: replacement_services.audio.pause_count == 1)

    assert old_services.audio.pause_count == 0

    runtime_ref = ref(old_runtime)
    engine_ref = ref(engine)
    del old_runtime, old_services, old_client, old_stt, engine
    gc.collect()
    QApplication.processEvents()
    gc.collect()

    assert runtime_ref() is None
    assert engine_ref() is None
    del lifecycle
    await replacement.shutdown()


async def test_stt_stop_timeout_is_reported_after_remaining_cleanup(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    stt = _UnstoppableSTT()
    capture = _ParityCapture(tmp_path / "unused.jpg")
    runtime, _, client = _runtime(app, stt=stt, capture=capture)
    await runtime.start()

    with pytest.raises(RuntimeError, match="STT worker did not stop"):
        await runtime.shutdown()

    assert capture.shutdown_count == 1
    assert client.close_count == 1
    assert app.is_shutdown


async def test_search_timeout_cancels_and_drains_retrieval_then_answers_without_it(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    notifications: list[str] = []
    app.events.notification.connect(notifications.append)
    client = _TimeoutRetrievalClient()
    runtime, services, _ = _runtime(
        app,
        client=client,
        capture=_ParityCapture(tmp_path / "unused.jpg"),
    )
    runtime.config.search.timeout_seconds = 0.01
    services.search_policy = SearchPolicy("auto")
    await runtime.start()

    await asyncio.wait_for(
        runtime.submit_hypothesis(_hypothesis("What is the latest Python release?")),
        timeout=0.5,
    )

    assert client.retrieval_cancelled.is_set()
    assert len(client.payloads) == 2
    assert "integrations" in client.payloads[0]
    assert "integrations" not in client.payloads[1]
    assert "Web retrieval unavailable" in " ".join(notifications)
    assert app.states.state.value == "listening"
    await runtime.shutdown()
