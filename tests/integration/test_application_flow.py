from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from threading import get_ident
from time import monotonic

import numpy as np
import pytest

from interview_assistant.app import InterviewApplication
from interview_assistant.audio.models import AudioFrame, AudioSource
from interview_assistant.capture.worker import CaptureResult, CaptureStatus
from interview_assistant.config import AppConfig
from interview_assistant.lmstudio.models import ChatError, ChatEvent, ModelInstance
from interview_assistant.orchestration.coordinator import RequestCoordinator
from interview_assistant.retrieval.policy import SearchPolicy
from interview_assistant.stt.engine import (
    TranscriptHypothesis,
    TranscriptionResult,
)
from interview_assistant.stt.worker import StreamingSTTWorker
from interview_assistant.transcript.detector import QuestionDetector
from interview_assistant.transcript.store import TranscriptStore


class _LifecycleService:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.paused = False

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False


class _FakeSTT(_LifecycleService):
    def stop(self, timeout: float = 5.0) -> bool:
        del timeout
        self.stopped += 1
        return True


class _FakeCapture:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.capture_count = 0
        self.shutdown_count = 0

    async def capture_for_event(self, kind: str, *, manual: bool = False) -> CaptureResult:
        del kind, manual
        self.capture_count += 1
        return CaptureResult("captured", self.path, None)

    async def shutdown(self) -> None:
        self.shutdown_count += 1


class _StatusCapture(_FakeCapture):
    def __init__(self, status: CaptureStatus) -> None:
        super().__init__(Path("must-not-leak.jpg"))
        self.status = status

    async def capture_for_event(self, kind: str, *, manual: bool = False) -> CaptureResult:
        del kind, manual
        self.capture_count += 1
        return CaptureResult(self.status, None, None)


class _FakeRegistry:
    def __init__(self) -> None:
        self.load_count = 0

    async def ensure_ready(self, key: str, refresh: bool = False) -> ModelInstance:
        del refresh
        self.load_count += 1
        return ModelInstance(
            key=key,
            instance_id=f"instance:{key}",
            state="ready",
            device_name="Strix Halo",
        )

    async def refresh(self, key: str) -> ModelInstance:
        return await self.ensure_ready(key, refresh=True)


class _RecoveringRegistry(_FakeRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.refresh_count = 0

    async def ensure_ready(self, key: str, refresh: bool = False) -> ModelInstance:
        if refresh:
            return await self.refresh(key)
        self.load_count += 1
        return ModelInstance(key=key, instance_id="old-instance", state="ready")

    async def refresh(self, key: str) -> ModelInstance:
        self.refresh_count += 1
        return ModelInstance(key=key, instance_id="reloaded-instance", state="ready")


class _FakeLMStudioClient:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []
        self.close_count = 0

    async def stream_chat(
        self,
        payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        self.payloads.append(dict(payload))
        yield ChatEvent(type="chat.start")
        yield ChatEvent(type="message.delta", content="Use an ")
        yield ChatEvent(type="message.delta", content="API Gateway")
        yield ChatEvent(type="chat.end", result={})

    async def aclose(self) -> None:
        self.close_count += 1


class _UnloadThenRecoverClient(_FakeLMStudioClient):
    async def stream_chat(
        self,
        payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        self.payloads.append(dict(payload))
        yield ChatEvent(type="chat.start")
        if len(self.payloads) == 1:
            yield ChatEvent(
                type="error",
                error=ChatError(type="model_not_found", message="instance unloaded"),
            )
        else:
            yield ChatEvent(type="message.delta", content="Recovered API Gateway")
        yield ChatEvent(type="chat.end", result={})


class _SearchThenAnswerClient(_FakeLMStudioClient):
    async def stream_chat(
        self,
        payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        self.payloads.append(dict(payload))
        yield ChatEvent(type="chat.start")
        if "integrations" in payload:
            yield ChatEvent(
                type="message.delta",
                content="Python 3.14 — https://python.example/release",
            )
        else:
            yield ChatEvent(type="message.delta", content="Final API Gateway")
        yield ChatEvent(type="chat.end", result={})


class _UnloadDuringSearchClient(_FakeLMStudioClient):
    async def stream_chat(
        self,
        payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        self.payloads.append(dict(payload))
        yield ChatEvent(type="chat.start")
        retrieval_count = sum("integrations" in item for item in self.payloads)
        if "integrations" in payload and retrieval_count == 1:
            yield ChatEvent(
                type="error",
                error=ChatError(type="model_not_found", message="unloaded"),
            )
        elif "integrations" in payload:
            yield ChatEvent(type="message.delta", content="Recovered search result")
        else:
            yield ChatEvent(type="message.delta", content="Recovered final answer")
        yield ChatEvent(type="chat.end", result={})


def _final(source: AudioSource, text: str) -> TranscriptHypothesis:
    ended_at = monotonic()
    return TranscriptHypothesis(
        source=source,
        text=text,
        language="ru",
        is_final=True,
        started_at=ended_at - 1.0,
        ended_at=ended_at,
    )


async def test_question_to_streaming_overlay_loads_shared_model_once(
    qtbot,
    tmp_path: Path,
) -> None:
    from interview_assistant.runtime import ApplicationRuntime, RuntimeServices

    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg-fixture")
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = AppConfig.model_validate(
        {
            "audio": {
                "system_device_id": "system",
                "microphone_device_id": "microphone",
            },
            "lmstudio": {
                "text_model": "qwen-vl",
                "vision_model": "qwen-vl",
            },
            "search": {"mode": "off"},
        }
    )
    audio = _LifecycleService()
    stt = _FakeSTT()
    hotkeys = _LifecycleService()
    capture = _FakeCapture(frame)
    registry = _FakeRegistry()
    client = _FakeLMStudioClient()
    coordinator = RequestCoordinator(app.events, client)
    runtime = ApplicationRuntime(
        app,
        config,
        RuntimeServices(
            audio=audio,
            stt=stt,
            hotkeys=hotkeys,
            capture=capture,
            registry=registry,
            client=client,
            coordinator=coordinator,
            transcript_store=TranscriptStore(),
            question_detector=QuestionDetector(),
            search_policy=SearchPolicy("off"),
        ),
    )

    await runtime.start()
    await runtime.handle_hypothesis(
        _final(AudioSource.SYSTEM, "Спроектируйте сервис коротких ссылок")
    )

    assert app.ribbon.answer_text.endswith("API Gateway")
    assert capture.capture_count == 1
    assert registry.load_count == 1
    assert client.payloads[0]["model"] == "instance:qwen-vl"
    assert client.payloads[0]["store"] is False
    assert "подготовленного кандидата" in str(client.payloads[0]["input"])
    assert "обычно не превышай 120 слов" in str(client.payloads[0]["input"])
    assert audio.started == stt.started == hotkeys.started == 1

    await runtime.shutdown()


async def test_manual_screenshot_lifecycle_is_visible_and_attaches_once(
    qtbot,
    tmp_path: Path,
) -> None:
    from interview_assistant.runtime import ApplicationRuntime, RuntimeServices

    frame = tmp_path / "manual.jpg"
    frame.write_bytes(b"jpeg-fixture")
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    notifications: list[str] = []
    app.events.notification.connect(notifications.append)
    config = AppConfig.model_validate(
        {
            "audio": {
                "system_device_id": "system",
                "microphone_device_id": "microphone",
            },
            "lmstudio": {
                "text_model": "qwen-vl",
                "vision_model": "qwen-vl",
            },
            "search": {"mode": "off"},
        }
    )
    client = _FakeLMStudioClient()
    runtime = ApplicationRuntime(
        app,
        config,
        RuntimeServices(
            audio=_LifecycleService(),
            stt=_FakeSTT(),
            hotkeys=_LifecycleService(),
            capture=_FakeCapture(frame),
            registry=_FakeRegistry(),
            client=client,
            coordinator=RequestCoordinator(app.events, client),
            transcript_store=TranscriptStore(),
            question_detector=QuestionDetector(cooldown_seconds=0),
            search_policy=SearchPolicy("off"),
        ),
    )

    await runtime.start()
    await runtime.capture_manual_screenshot()
    await runtime.handle_hypothesis(_final(AudioSource.SYSTEM, "Describe this screen"))

    assert notifications == [
        "Снимок создаётся",
        "Снимок готов для следующего запроса",
        "Снимок добавлен в запрос",
    ]
    assert runtime.manual_image_path is None
    assert "data:image/jpeg;base64," in str(client.payloads[0]["input"])
    await runtime.shutdown()


async def test_microphone_clarification_generates_with_both_roles_in_context(
    qtbot,
    tmp_path: Path,
) -> None:
    from interview_assistant.runtime import ApplicationRuntime, RuntimeServices

    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg-fixture")
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = AppConfig.model_validate(
        {
            "audio": {
                "system_device_id": "system",
                "microphone_device_id": "microphone",
            },
            "lmstudio": {
                "text_model": "qwen-vl",
                "vision_model": "qwen-vl",
            },
            "search": {"mode": "off"},
        }
    )
    capture = _FakeCapture(frame)
    registry = _FakeRegistry()
    client = _FakeLMStudioClient()
    services = RuntimeServices(
        audio=_LifecycleService(),
        stt=_FakeSTT(),
        hotkeys=_LifecycleService(),
        capture=capture,
        registry=registry,
        client=client,
        coordinator=RequestCoordinator(app.events, client),
        transcript_store=TranscriptStore(),
        question_detector=QuestionDetector(),
        search_policy=SearchPolicy("off"),
    )
    runtime = ApplicationRuntime(app, config, services)

    await runtime.start()
    await runtime.handle_hypothesis(_final(AudioSource.SYSTEM, "Тема интервью — CAP theorem"))
    await runtime.handle_hypothesis(
        _final(AudioSource.MICROPHONE, "То есть про consistency trade-offs?")
    )

    assert len(services.transcript_store) == 2
    assert capture.capture_count == 1
    assert len(client.payloads) == 1
    prompt = str(client.payloads[0]["input"][0]["content"])
    assert "Latest interviewer request:\nТема интервью — CAP theorem" in prompt
    assert "Candidate clarification trigger:\nТо есть про consistency trade-offs?" in prompt

    await runtime.shutdown()


async def test_external_unload_recovers_and_replays_only_recovery_context(
    qtbot,
    tmp_path: Path,
) -> None:
    from interview_assistant.runtime import ApplicationRuntime, RuntimeServices

    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg-fixture")
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = AppConfig.model_validate(
        {
            "audio": {
                "system_device_id": "system",
                "microphone_device_id": "microphone",
            },
            "lmstudio": {
                "text_model": "qwen-vl",
                "vision_model": "qwen-vl",
            },
            "search": {"mode": "off"},
        }
    )
    registry = _RecoveringRegistry()
    client = _UnloadThenRecoverClient()
    warmed: list[str] = []

    async def warm_up(instance: ModelInstance) -> None:
        warmed.append(instance.instance_id)

    async def no_sleep(_delay: float) -> None:
        return None

    services = RuntimeServices(
        audio=_LifecycleService(),
        stt=_FakeSTT(),
        hotkeys=_LifecycleService(),
        capture=_FakeCapture(frame),
        registry=registry,
        client=client,
        coordinator=RequestCoordinator(app.events, client),
        transcript_store=TranscriptStore(),
        question_detector=QuestionDetector(),
        search_policy=SearchPolicy("off"),
        warm_up=warm_up,
        recovery_sleeper=no_sleep,
    )
    runtime = ApplicationRuntime(app, config, services)

    await runtime.start()
    await runtime.handle_hypothesis(
        _final(AudioSource.SYSTEM, "This old context must never be replayed")
    )
    await runtime.handle_hypothesis(
        _final(AudioSource.SYSTEM, "Спроектируйте сервис коротких ссылок")
    )

    assert registry.refresh_count == 1
    assert warmed == ["reloaded-instance"]
    assert len(client.payloads) == 2
    assert client.payloads[1]["model"] == "reloaded-instance"
    replay_input = client.payloads[1]["input"]
    assert isinstance(replay_input, list)
    replay_prompt = replay_input[0]["content"]
    assert "подготовленного кандидата" in replay_prompt
    assert "Спроектируйте сервис коротких ссылок" in replay_prompt
    assert "This old context must never be replayed" not in replay_prompt
    assert app.ribbon.answer_text.endswith("Recovered API Gateway")

    await runtime.shutdown()


class _SyntheticEngine:
    def transcribe(
        self,
        audio: np.ndarray,
        *,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> TranscriptionResult:
        del audio, beam_size, condition_on_previous_text
        return TranscriptionResult(
            "Спроектируйте сервис коротких ссылок",
            "ru",
            0.99,
        )


async def test_synthetic_pcm_crosses_real_stt_worker_and_bounded_thread_bridge(
    qtbot,
    tmp_path: Path,
) -> None:
    from interview_assistant.runtime import (
        ApplicationRuntime,
        RuntimeHypothesisIngress,
        RuntimeServices,
    )

    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"jpeg-fixture")
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    loop = asyncio.get_running_loop()
    ingress = RuntimeHypothesisIngress(loop)
    stt = StreamingSTTWorker(
        engine=_SyntheticEngine(),
        on_hypothesis=ingress.publish,
        partial_interval_seconds=0.05,
        silence_duration_seconds=0.05,
    )
    client = _FakeLMStudioClient()
    config = AppConfig.model_validate(
        {
            "audio": {
                "system_device_id": "system",
                "microphone_device_id": "microphone",
            },
            "lmstudio": {
                "text_model": "qwen-vl",
                "vision_model": "qwen-vl",
            },
            "search": {"mode": "off"},
        }
    )
    services = RuntimeServices(
        audio=_LifecycleService(),
        stt=stt,
        hotkeys=_LifecycleService(),
        capture=_FakeCapture(frame_path),
        registry=_FakeRegistry(),
        client=client,
        coordinator=RequestCoordinator(app.events, client),
        transcript_store=TranscriptStore(),
        question_detector=QuestionDetector(),
        search_policy=SearchPolicy("off"),
        hypothesis_ingress=ingress,
    )
    runtime = ApplicationRuntime(app, config, services)
    ingress.bind(runtime.submit_hypothesis)
    main_thread = get_ident()
    transcript_threads: list[int] = []
    app.events.transcript_final.connect(
        lambda _source, _text: transcript_threads.append(get_ident())
    )

    await runtime.start()
    started = monotonic()
    stt.submit(
        AudioFrame(
            AudioSource.SYSTEM,
            started,
            np.ones(1_600, dtype=np.float32),
        )
    )
    stt.submit(
        AudioFrame(
            AudioSource.SYSTEM,
            started + 0.1,
            np.zeros(1_600, dtype=np.float32),
        )
    )

    async def wait_for_answer() -> None:
        while not app.ribbon.answer_text.endswith("API Gateway"):
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait_for_answer(), timeout=2)

    assert transcript_threads == [main_thread]
    assert services.capture.capture_count == 1
    assert len(client.payloads) == 1

    await runtime.shutdown()


async def test_auto_search_uses_sealed_two_stage_retrieval_without_overlay_leakage(
    qtbot,
    tmp_path: Path,
) -> None:
    from interview_assistant.runtime import ApplicationRuntime, RuntimeServices

    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg-fixture")
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = AppConfig.model_validate(
        {
            "audio": {
                "system_device_id": "system",
                "microphone_device_id": "microphone",
            },
            "lmstudio": {"text_model": "qwen", "vision_model": "qwen"},
            "search": {"mode": "auto"},
        }
    )
    client = _SearchThenAnswerClient()
    services = RuntimeServices(
        audio=_LifecycleService(),
        stt=_FakeSTT(),
        hotkeys=_LifecycleService(),
        capture=_FakeCapture(frame),
        registry=_FakeRegistry(),
        client=client,
        coordinator=RequestCoordinator(app.events, client),
        transcript_store=TranscriptStore(),
        question_detector=QuestionDetector(),
        search_policy=SearchPolicy("auto"),
    )
    runtime = ApplicationRuntime(app, config, services)

    await runtime.start()
    await runtime.handle_hypothesis(
        _final(AudioSource.SYSTEM, "What is the latest Python release?")
    )

    assert len(client.payloads) == 2
    retrieval, answer = client.payloads
    assert retrieval == {
        "model": "instance:qwen",
        "input": "What is the latest Python release?",
        "integrations": [
            {
                "type": "plugin",
                "id": "mcp/duckduckgo",
                "allowed_tools": ["search"],
            }
        ],
        "store": False,
    }
    assert "Python 3.14" in str(answer["input"])
    assert "untrusted reference data" in str(answer["input"])
    assert app.ribbon.answer_text == "Final API Gateway"
    assert "Python 3.14" not in app.ribbon.answer_text
    assert app.ribbon.source_texts == ("mcp/duckduckgo",)

    await runtime.shutdown()


async def test_search_model_unload_replays_same_sealed_retrieval_before_answer(
    qtbot,
    tmp_path: Path,
) -> None:
    from interview_assistant.runtime import ApplicationRuntime, RuntimeServices

    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg-fixture")
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = AppConfig.model_validate(
        {
            "audio": {
                "system_device_id": "system",
                "microphone_device_id": "microphone",
            },
            "lmstudio": {"text_model": "qwen", "vision_model": "qwen"},
            "search": {"mode": "auto"},
        }
    )
    registry = _RecoveringRegistry()
    client = _UnloadDuringSearchClient()

    async def no_sleep(_delay: float) -> None:
        return None

    services = RuntimeServices(
        audio=_LifecycleService(),
        stt=_FakeSTT(),
        hotkeys=_LifecycleService(),
        capture=_FakeCapture(frame),
        registry=registry,
        client=client,
        coordinator=RequestCoordinator(app.events, client),
        transcript_store=TranscriptStore(),
        question_detector=QuestionDetector(),
        search_policy=SearchPolicy("auto"),
        recovery_sleeper=no_sleep,
    )
    runtime = ApplicationRuntime(app, config, services)

    await runtime.start()
    await runtime.handle_hypothesis(
        _final(AudioSource.SYSTEM, "What is the latest Python release?")
    )

    assert registry.refresh_count == 1
    assert len(client.payloads) == 3
    first_retrieval, replayed_retrieval, answer = client.payloads
    assert first_retrieval["integrations"] == replayed_retrieval["integrations"]
    assert first_retrieval["input"] == replayed_retrieval["input"]
    assert replayed_retrieval["model"] == "reloaded-instance"
    assert set(replayed_retrieval) == {"model", "input", "integrations", "store"}
    assert "Recovered search result" in str(answer["input"])
    assert app.ribbon.answer_text == "Recovered final answer"

    await runtime.shutdown()


@pytest.mark.parametrize(
    ("capture_status", "expected_notification"),
    [
        ("protected", "Protected content; continuing without an image."),
        ("duplicate", None),
        ("disallowed", None),
    ],
)
async def test_unusable_capture_never_leaks_a_path_or_fake_image_to_lm_studio(
    qtbot,
    capture_status: CaptureStatus,
    expected_notification: str | None,
) -> None:
    from interview_assistant.runtime import ApplicationRuntime, RuntimeServices

    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    notifications: list[str] = []
    app.events.notification.connect(notifications.append)
    client = _FakeLMStudioClient()
    capture = _StatusCapture(capture_status)
    config = AppConfig.model_validate(
        {
            "audio": {
                "system_device_id": "system",
                "microphone_device_id": "microphone",
            },
            "lmstudio": {"text_model": "qwen", "vision_model": "qwen"},
            "search": {"mode": "off"},
        }
    )
    runtime = ApplicationRuntime(
        app,
        config,
        RuntimeServices(
            audio=_LifecycleService(),
            stt=_FakeSTT(),
            hotkeys=_LifecycleService(),
            capture=capture,
            registry=_FakeRegistry(),
            client=client,
            coordinator=RequestCoordinator(app.events, client),
            transcript_store=TranscriptStore(),
            question_detector=QuestionDetector(cooldown_seconds=0),
            search_policy=SearchPolicy("off"),
        ),
    )

    await runtime.start()
    await runtime.handle_hypothesis(_final(AudioSource.SYSTEM, "Design a scalable chat service"))

    assert capture.capture_count == 1
    assert isinstance(client.payloads[0]["input"], str)
    assert "data:image/" not in str(client.payloads[0])
    assert "must-not-leak" not in str(client.payloads[0])
    assert notifications == ([expected_notification] if expected_notification else [])
    await runtime.shutdown()
