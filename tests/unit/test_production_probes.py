from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from threading import Event

import pytest

from interview_assistant.audio.devices import AudioDevice
from interview_assistant.capture.worker import CaptureResult
from interview_assistant.config import AppConfig
from interview_assistant.diagnostics.probes import ProductionReadinessProbes
from interview_assistant.diagnostics.readiness import (
    READINESS_CHECK_NAMES,
    ProbeOutcome,
    ReadinessRunner,
    build_readiness_checks,
)
from interview_assistant.lmstudio.models import (
    LoadedModelInstance,
    ModelDetails,
    ModelInstance,
    ModelLoadConfig,
    ModelSummary,
)
from interview_assistant.ui.windows_affinity import AffinityResult, WDA_EXCLUDEFROMCAPTURE


class _AudioDevices:
    def __init__(self, devices: list[AudioDevice]) -> None:
        self.devices = devices

    def list_devices(self) -> list[AudioDevice]:
        return self.devices


class _Client:
    def __init__(self, *, duplicate: bool = False) -> None:
        config = ModelLoadConfig(context_length=8_192)
        instances = [LoadedModelInstance(id="instance:qwen", config=config)]
        if duplicate:
            instances.append(LoadedModelInstance(id="instance:qwen:2", config=config))
        self.details = [
            ModelDetails(
                type="llm",
                publisher="local",
                key="qwen",
                display_name="Qwen",
                size_bytes=1,
                loaded_instances=instances,
                max_context_length=8_192,
            )
        ]

    async def list_models(self) -> list[ModelSummary]:
        return [ModelSummary(id="qwen")]

    async def list_model_details(self) -> list[ModelDetails]:
        return self.details


class _Registry:
    async def ensure_ready(self, key: str, refresh: bool = False) -> ModelInstance:
        del refresh
        return ModelInstance(
            key=key,
            instance_id=f"instance:{key}",
            state="ready",
            device_name=None,
        )


class _Hotkeys:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


class _BlockingHotkeys(_Hotkeys):
    def __init__(self) -> None:
        super().__init__()
        self.start_entered = Event()
        self.release_start = Event()
        self.running = False
        self.stop_before_start_completed = False

    def start(self) -> None:
        self.start_entered.set()
        if not self.release_start.wait(timeout=2.0):
            raise RuntimeError("test hotkey start was not released")
        self.started += 1
        self.running = True

    def stop(self) -> None:
        if not self.running:
            self.stop_before_start_completed = True
        self.stopped += 1
        self.running = False


class _Capture:
    async def capture_for_event(self, kind: str, *, manual: bool = False) -> CaptureResult:
        assert kind == "screen_analysis"
        assert manual
        return CaptureResult("captured", Path(__file__), None)


class _Ribbon:
    def verify_capture_exclusion(self) -> AffinityResult:
        return AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "audio": {
                "system_device_id": "loopback",
                "microphone_device_id": "microphone",
            },
            "lmstudio": {
                "text_model": "qwen",
                "vision_model": "qwen",
                "preferred_device_name": "Strix Halo",
            },
        }
    )


def _probes(
    *,
    client: _Client | None = None,
    audio_devices: _AudioDevices | None = None,
    devices: list[AudioDevice] | None = None,
    hotkeys: _Hotkeys | None = None,
    stt_fixture: Callable[[str], Awaitable[bool]] | None = None,
    stt_warm_up: Callable[[], Awaitable[None]] | None = None,
    mcp_probe: Callable[[str], Awaitable[bool]] | None = None,
    windows_composition: Callable[[], bool] = lambda: True,
    cuda_device_count: Callable[[], int] = lambda: 1,
    lmlink_status: Callable[[], str] = lambda: '{"device":"Strix Halo"}',
) -> tuple[ProductionReadinessProbes, _Hotkeys]:
    active_hotkeys = hotkeys if hotkeys is not None else _Hotkeys()

    async def fixture(_language: str) -> bool:
        return True

    async def mcp(_integration: str) -> bool:
        return True

    async def warm(_instance: ModelInstance) -> float:
        return 240.0

    async def warm_stt() -> None:
        return None

    return (
        ProductionReadinessProbes(
            _config(),
            audio_devices=audio_devices
            if audio_devices is not None
            else _AudioDevices(
                devices
                or [
                    AudioDevice("loopback", "Speakers", True, 2),
                    AudioDevice("microphone", "Microphone", False, 1),
                ]
            ),
            client=client or _Client(),
            registry=_Registry(),
            hotkeys=active_hotkeys,
            capture=_Capture(),
            ribbon=_Ribbon(),
            warm_up=warm,
            windows_composition=windows_composition,
            cuda_device_count=cuda_device_count,
            stt_warm_up=stt_warm_up or warm_stt,
            lmlink_status=lmlink_status,
            stt_fixture=stt_fixture or fixture,
            mcp_probe=mcp_probe or mcp,
        ),
        active_hotkeys,
    )


async def test_production_adapter_supplies_full_catalog_without_fake_preferred_success() -> None:
    probes, hotkeys = _probes()

    report = await ReadinessRunner(build_readiness_checks(probes.as_mapping())).run()

    assert tuple(result.name for result in report.checks) == READINESS_CHECK_NAMES
    assert report.can_start
    assert report.by_name("preferred_device").status == "warning"
    assert "cannot" in report.by_name("preferred_device").message.casefold()
    assert report.by_name("model_load_warmup").status == "ready"
    assert report.by_name("streaming_ttft").status == "ready"
    assert hotkeys.started == hotkeys.stopped == 1


async def test_unavailable_firecrawl_is_named_and_does_not_block_interview() -> None:
    requested = []

    async def unavailable(integration: str) -> bool:
        requested.append(integration)
        return False

    probes, _ = _probes(mcp_probe=unavailable)
    checks = build_readiness_checks(probes.as_mapping())
    report = await ReadinessRunner(
        tuple(check for check in checks if check.name == "firecrawl_mcp")
    ).run()
    assert requested == ["mcp/firecrawl"]
    assert report.can_start
    assert report.by_name("firecrawl_mcp").status == "warning"
    assert "Firecrawl MCP" in report.by_name("firecrawl_mcp").message


async def test_firecrawl_readiness_describes_probe_limits() -> None:
    probes, _ = _probes()
    outcome = await probes.as_mapping()["firecrawl_mcp"]()
    assert outcome.status == "ready"
    assert "connection and required tools checked" in outcome.message
    assert "search credentials and credits were not verified" in outcome.message


async def test_unavailable_stt_and_mcp_evidence_is_honest_warning() -> None:
    probes, _ = _probes(stt_fixture=None, mcp_probe=None)
    probes.stt_fixture = None
    probes.mcp_probe = None

    report = await ReadinessRunner(build_readiness_checks(probes.as_mapping())).run()

    for name in ("stt_ru_fixture", "stt_en_fixture", "context7", "firecrawl_mcp"):
        assert report.by_name(name).status == "warning"
        assert "not verified" in report.by_name(name).message.casefold()
    assert report.can_start


async def test_missing_selected_audio_device_is_blocking() -> None:
    probes, _ = _probes(
        devices=[AudioDevice("microphone", "Microphone", False, 1)]
    )

    report = await ReadinessRunner(build_readiness_checks(probes.as_mapping())).run()

    assert report.by_name("system_audio").status == "failed"
    assert not report.can_start


async def test_distinct_loopback_device_is_not_accepted_as_microphone() -> None:
    probes, _ = _probes(
        devices=[
            AudioDevice("loopback", "Speakers", True, 2),
            AudioDevice("other-loopback", "Monitor", True, 2),
        ]
    )
    probes.config.audio.microphone_device_id = "other-loopback"

    report = await ReadinessRunner(build_readiness_checks(probes.as_mapping())).run()

    assert report.by_name("microphone").status == "failed"
    assert not report.can_start


async def test_cuda_stt_shared_inference_is_single_flight_and_required() -> None:
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def warm_stt() -> None:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    probes, _ = _probes(stt_warm_up=warm_stt)
    cuda_stt = probes.as_mapping()["cuda_stt"]
    first = asyncio.create_task(cuda_stt())
    second = asyncio.create_task(cuda_stt())
    await started.wait()

    assert calls == 1
    assert not first.done()
    assert not second.done()
    release.set()
    first_outcome, second_outcome = await asyncio.gather(first, second)

    assert first_outcome.status == second_outcome.status == "ready"
    assert "inference" in first_outcome.message.casefold()
    await probes.aclose()


async def test_cuda_stt_warm_up_failure_blocks_readiness() -> None:
    async def failed_warm_up() -> None:
        raise RuntimeError("synthetic STT inference failed")

    probes, _ = _probes(stt_warm_up=failed_warm_up)

    report = await ReadinessRunner(build_readiness_checks(probes.as_mapping())).run()

    assert report.by_name("cuda_stt").status == "failed"
    assert not report.can_start
    await probes.aclose()


async def test_aclose_does_not_mistake_internal_task_cancellation_for_its_own() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class _BlockingClient(_Client):
        async def list_models(self) -> list[ModelSummary]:
            started.set()
            await release.wait()
            return await super().list_models()

    probes, _ = _probes(client=_BlockingClient())
    discovery = asyncio.create_task(probes.discovered_models())
    await started.wait()

    async def close_after_suppressed_cancellation() -> None:
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass
        assert current.cancelling() == 1
        await probes.aclose()

    await asyncio.create_task(close_after_suppressed_cancellation())

    assert discovery.cancelled()


async def test_cancelled_hotkey_probe_is_stopped_after_physical_start_completes() -> None:
    hotkeys = _BlockingHotkeys()
    probes, _ = _probes(hotkeys=hotkeys)
    probe = asyncio.create_task(probes.as_mapping()["hotkeys"]())
    close: asyncio.Task[None] | None = None
    close_waited_for_start = False
    try:
        assert await asyncio.to_thread(hotkeys.start_entered.wait, 1.0)
        probe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await probe

        close = asyncio.create_task(probes.aclose())
        await asyncio.sleep(0.05)
        close_waited_for_start = not close.done()
    finally:
        hotkeys.release_start.set()
    if close is not None:
        await close

    assert close_waited_for_start
    assert hotkeys.started == hotkeys.stopped == 1
    assert not hotkeys.stop_before_start_completed
    assert not hotkeys.running


@pytest.mark.parametrize("probe_name", ["system_audio", "lmlink_status"])
async def test_aclose_drains_cancelled_thread_backed_probe(probe_name: str) -> None:
    started = Event()
    release = Event()
    finished = Event()

    def blocking_value(value):
        started.set()
        try:
            if not release.wait(timeout=2.0):
                raise RuntimeError("test readiness call was not released")
            return value
        finally:
            finished.set()

    devices = [
        AudioDevice("loopback", "Speakers", True, 2),
        AudioDevice("microphone", "Microphone", False, 1),
    ]
    audio_devices = _AudioDevices(devices)

    def default_lmlink_status() -> str:
        return '{"device":"Strix Halo"}'

    lmlink_status: Callable[[], str] = default_lmlink_status
    if probe_name == "system_audio":
        audio_devices.list_devices = lambda: blocking_value(devices)  # type: ignore[method-assign]
    else:
        def blocked_lmlink_status() -> str:
            return blocking_value('{"device":"Strix Halo"}')

        lmlink_status = blocked_lmlink_status
    probes, _ = _probes(
        audio_devices=audio_devices,
        lmlink_status=lmlink_status,
    )
    probe = asyncio.create_task(probes.as_mapping()[probe_name]())
    close: asyncio.Task[None] | None = None
    close_waited_for_thread = False
    try:
        assert await asyncio.to_thread(started.wait, 1.0)
        probe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await probe

        close = asyncio.create_task(probes.aclose())
        await asyncio.sleep(0.05)
        close_waited_for_thread = not close.done()
    finally:
        release.set()
    assert await asyncio.to_thread(finished.wait, 1.0)
    if close is not None:
        await close

    assert close_waited_for_thread


async def test_duplicate_loaded_model_instances_are_blocking() -> None:
    probes, _ = _probes(client=_Client(duplicate=True))

    report = await ReadinessRunner(build_readiness_checks(probes.as_mapping())).run()

    assert report.by_name("duplicate_instances").status == "failed"
    assert not report.can_start


async def test_probe_mapping_values_are_async_and_side_effects_are_deferred() -> None:
    probes, hotkeys = _probes()

    mapping = probes.as_mapping()

    assert tuple(mapping) == READINESS_CHECK_NAMES
    assert hotkeys.started == 0
    outcome = await mapping["windows_dwm"]()
    assert outcome == ProbeOutcome("ready", "Windows DWM composition is enabled")

    await probes.aclose()


async def test_invalid_optional_lmlink_json_warns_without_guessing_its_schema() -> None:
    probes, _ = _probes()
    probes.lmlink_status = lambda: "LM Link is probably running"

    report = await ReadinessRunner(build_readiness_checks(probes.as_mapping())).run()

    assert report.by_name("lmlink_status").status == "warning"
    assert report.can_start


async def test_selected_key_missing_from_native_details_is_not_called_duplicate_free() -> None:
    client = _Client()
    client.details = []
    probes, _ = _probes(client=client)

    report = await ReadinessRunner(build_readiness_checks(probes.as_mapping())).run()

    assert report.by_name("duplicate_instances").status == "failed"
