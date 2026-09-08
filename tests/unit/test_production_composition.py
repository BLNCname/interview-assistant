from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event

import numpy as np
import pytest

from interview_assistant.app import InterviewApplication
from interview_assistant.audio.worker import AudioWorker
from interview_assistant.capture.worker import CaptureWorker
from interview_assistant.composition import (
    ProductionComponents,
    build_production_components,
)
from interview_assistant.config import AppConfig
from interview_assistant.diagnostics.readiness import READINESS_CHECK_NAMES
from interview_assistant.stt.engine import TranscriptionResult
from interview_assistant.stt.worker import StreamingSTTWorker
from interview_assistant.utils.hotkeys import (
    DEFAULT_HOTKEY_BINDINGS,
    HotkeyAction,
    HotkeyChord,
    HotkeyManager,
)


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "audio": {
                "system_device_id": "1",
                "microphone_device_id": "2",
            },
            "lmstudio": {
                "host": "127.0.0.1",
                "port": 1234,
                "text_model": "qwen-vl",
                "vision_model": "qwen-vl",
            },
            "search": {"mode": "auto", "provider": "firecrawl"},
        }
    )


def test_example_config_documents_all_hotkeys() -> None:
    config = AppConfig.load(Path("config.yaml"))

    assert "hotkeys:" in Path("config.yaml").read_text(encoding="utf-8")
    assert config.hotkeys.as_bindings() == dict(DEFAULT_HOTKEY_BINDINGS)


async def test_real_production_factory_wires_workers_without_starting_hardware(qtbot) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)

    components = build_production_components(
        app,
        _config(),
        token=None,
        loop=asyncio.get_running_loop(),
        windows_composition=lambda: True,
        cuda_device_count=lambda: 1,
        lmlink_status=lambda: "{}",
    )

    assert isinstance(components, ProductionComponents)
    services = components.runtime.services
    assert isinstance(services.audio, AudioWorker)
    assert isinstance(services.stt, StreamingSTTWorker)
    assert services.stt.system_queue is services.audio.system_queue
    assert services.stt.microphone_queue is services.audio.microphone_queue
    assert isinstance(services.capture, CaptureWorker)
    assert components.readiness_capture is not services.capture
    assert isinstance(services.hotkeys, HotkeyManager)
    assert tuple(components.probes.as_mapping()) == READINESS_CHECK_NAMES
    assert not components.runtime.is_started
    assert not services.stt.is_running

    await components.aclose()
    assert not app.is_shutdown
    app.shutdown()


async def test_stt_readiness_uses_worker_engine_and_close_drains_inference_thread(
    qtbot,
) -> None:
    class BlockingEngine:
        def __init__(self) -> None:
            self.started = Event()
            self.release = Event()
            self.calls = 0
            self.audio: np.ndarray | None = None

        def __bool__(self) -> bool:
            return False

        def transcribe(
            self,
            audio: np.ndarray,
            *,
            beam_size: int,
            condition_on_previous_text: bool,
        ) -> TranscriptionResult:
            assert beam_size == 1
            assert not condition_on_previous_text
            self.calls += 1
            self.audio = audio
            self.started.set()
            if not self.release.wait(timeout=2.0):
                raise RuntimeError("test inference was not released")
            return TranscriptionResult("", "unknown", 0.0)

    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    engine = BlockingEngine()
    components = build_production_components(
        app,
        _config(),
        token=None,
        loop=asyncio.get_running_loop(),
        windows_composition=lambda: True,
        cuda_device_count=lambda: 1,
        lmlink_status=lambda: "{}",
        stt_engine=engine,
    )

    assert components.runtime.services.stt.engine is engine
    probe = asyncio.create_task(components.probes.as_mapping()["cuda_stt"]())
    assert await asyncio.to_thread(engine.started.wait, 1.0)
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe

    close = asyncio.create_task(components.probes.aclose())
    await asyncio.sleep(0)
    assert not close.done()
    close.cancel()
    await asyncio.sleep(0)
    close.cancel()
    await asyncio.sleep(0)
    assert not close.done()
    engine.release.set()
    with pytest.raises(asyncio.CancelledError):
        await close

    assert engine.calls == 1
    assert engine.audio is not None
    assert engine.audio.dtype == np.float32
    assert not np.any(engine.audio)
    await components.aclose()
    app.shutdown()


async def test_production_composition_uses_saved_hotkeys(qtbot) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    config.hotkeys.overlay_interaction = "ctrl+alt+f8"

    components = build_production_components(
        app,
        config,
        None,
        loop=asyncio.get_running_loop(),
    )

    assert components.runtime.services.hotkeys.bindings[
        HotkeyAction.OVERLAY_INTERACTION
    ] == HotkeyChord.parse("ctrl+alt+f8")
    await components.aclose()
    app.shutdown()


@pytest.mark.parametrize("legacy_provider", ["exa", "duckduckgo", "searxng"])
async def test_legacy_search_config_routes_to_firecrawl_in_production(
    qtbot, legacy_provider: str,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    raw_config = _config().model_dump()
    raw_config["search"]["provider"] = legacy_provider
    config = AppConfig.model_validate(raw_config)
    components = build_production_components(
        app,
        config,
        token=None,
        loop=asyncio.get_running_loop(),
        windows_composition=lambda: True,
        cuda_device_count=lambda: 1,
        lmlink_status=lambda: "{}",
    )

    integrations = components.runtime.services.search_policy.integrations_for(
        "What is the latest Python release?"
    )
    assert config.search.provider == "firecrawl"
    assert [integration.id for integration in integrations] == ["mcp/firecrawl"]
    assert integrations[0].allowed_tools == ("firecrawl_search",)
    assert "firecrawl_mcp" in components.probes.as_mapping()

    await components.aclose()
    app.shutdown()


async def test_production_component_cleanup_attempts_every_owned_resource() -> None:
    calls: list[str] = []

    class Probes:
        async def aclose(self) -> None:
            calls.append("probes")

    class ReadinessCapture:
        async def shutdown(self) -> None:
            calls.append("readiness-capture")

    class Runtime:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.attempts = 0

        async def shutdown(self, *, close_application: bool = True) -> None:
            assert not close_application
            self.attempts += 1
            calls.append("runtime")
            if self.attempts == 1:
                self.started.set()
                await self.release.wait()
                raise RuntimeError("runtime cleanup failed once")

    runtime = Runtime()
    components = ProductionComponents(
        runtime,
        object(),
        Probes(),
        ReadinessCapture(),
    )

    first = asyncio.create_task(components.aclose())
    await runtime.started.wait()
    second = asyncio.create_task(components.aclose())
    runtime.release.set()
    outcomes = await asyncio.gather(first, second, return_exceptions=True)

    assert all(isinstance(outcome, RuntimeError) for outcome in outcomes)
    assert calls == ["probes", "readiness-capture", "runtime"]
    await components.aclose()
    await components.aclose()
    assert calls == ["probes", "readiness-capture", "runtime", "runtime"]


def test_production_factory_rejects_remote_plain_http_before_building_resources(
    qtbot,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    config.lmstudio.host = "strix-halo.example"
    loop = asyncio.new_event_loop()

    try:
        build_production_components(
            app,
            config,
            token="TOKEN-MUST-NOT-LEAK",
            loop=loop,
        )
    except ValueError as error:
        assert "loopback" in str(error).casefold()
        assert "TOKEN-MUST-NOT-LEAK" not in str(error)
    else:
        raise AssertionError("remote plaintext production host must be rejected")
    finally:
        loop.close()
        app.shutdown()
