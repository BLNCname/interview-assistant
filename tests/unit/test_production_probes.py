from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

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
    devices: list[AudioDevice] | None = None,
    stt_fixture: Callable[[str], Awaitable[bool]] | None = None,
    mcp_probe: Callable[[str], Awaitable[bool]] | None = None,
) -> tuple[ProductionReadinessProbes, _Hotkeys]:
    hotkeys = _Hotkeys()

    async def fixture(_language: str) -> bool:
        return True

    async def mcp(_integration: str) -> bool:
        return True

    async def warm(_instance: ModelInstance) -> float:
        return 240.0

    return (
        ProductionReadinessProbes(
            _config(),
            audio_devices=_AudioDevices(
                devices
                or [
                    AudioDevice("loopback", "Speakers", True, 2),
                    AudioDevice("microphone", "Microphone", False, 1),
                ]
            ),
            client=client or _Client(),
            registry=_Registry(),
            hotkeys=hotkeys,
            capture=_Capture(),
            ribbon=_Ribbon(),
            warm_up=warm,
            windows_composition=lambda: True,
            cuda_device_count=lambda: 1,
            lmlink_status=lambda: '{"device":"Strix Halo"}',
            stt_fixture=stt_fixture or fixture,
            mcp_probe=mcp_probe or mcp,
        ),
        hotkeys,
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


async def test_unavailable_stt_and_mcp_evidence_is_honest_warning() -> None:
    probes, _ = _probes(stt_fixture=None, mcp_probe=None)
    probes.stt_fixture = None
    probes.mcp_probe = None

    report = await ReadinessRunner(build_readiness_checks(probes.as_mapping())).run()

    for name in ("stt_ru_fixture", "stt_en_fixture", "context7", "duckduckgo_mcp"):
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


async def test_invalid_lmlink_json_is_blocking_without_guessing_its_schema() -> None:
    probes, _ = _probes()
    probes.lmlink_status = lambda: "LM Link is probably running"

    report = await ReadinessRunner(build_readiness_checks(probes.as_mapping())).run()

    assert report.by_name("lmlink_status").status == "failed"


async def test_selected_key_missing_from_native_details_is_not_called_duplicate_free() -> None:
    client = _Client()
    client.details = []
    probes, _ = _probes(client=client)

    report = await ReadinessRunner(build_readiness_checks(probes.as_mapping())).run()

    assert report.by_name("duplicate_instances").status == "failed"
