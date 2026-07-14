from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from interview_assistant.audio.devices import AudioDevice
from interview_assistant.capture.worker import CaptureResult
from interview_assistant.config import AppConfig
from interview_assistant.lmstudio.models import ModelDetails, ModelInstance, ModelSummary
from interview_assistant.transcript.detector import QuestionKind
from interview_assistant.ui.windows_affinity import AffinityResult

from .readiness import (
    READINESS_CHECK_NAMES,
    ProbeOutcome,
    ReadinessProbe,
    streaming_ttft_outcome,
)


class AudioDeviceProvider(Protocol):
    def list_devices(self) -> list[AudioDevice]: ...


class ReadinessLMClient(Protocol):
    async def list_models(self) -> list[ModelSummary]: ...

    async def list_model_details(self) -> list[ModelDetails]: ...


class ReadinessRegistry(Protocol):
    async def ensure_ready(self, key: str, refresh: bool = False) -> ModelInstance: ...


class ReadinessHotkeys(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


class ReadinessCapture(Protocol):
    async def capture_for_event(
        self,
        kind: QuestionKind,
        *,
        manual: bool = False,
    ) -> CaptureResult: ...


class ReadinessRibbon(Protocol):
    def verify_capture_exclusion(self) -> AffinityResult: ...


WarmUpProbe = Callable[[ModelInstance], Awaitable[float | None]]
BooleanAsyncProbe = Callable[[str], Awaitable[bool]]


class ProductionReadinessProbes:
    """Production probe adapters with all hardware access deferred until run()."""

    def __init__(
        self,
        config: AppConfig,
        *,
        audio_devices: AudioDeviceProvider,
        client: ReadinessLMClient,
        registry: ReadinessRegistry,
        hotkeys: ReadinessHotkeys,
        capture: ReadinessCapture,
        ribbon: ReadinessRibbon,
        warm_up: WarmUpProbe,
        windows_composition: Callable[[], bool],
        cuda_device_count: Callable[[], int],
        lmlink_status: Callable[[], str],
        stt_fixture: BooleanAsyncProbe | None = None,
        mcp_probe: BooleanAsyncProbe | None = None,
        ttft_warning_ms: float = 2_000.0,
    ) -> None:
        self.config = config
        self.audio_devices = audio_devices
        self.client = client
        self.registry = registry
        self.hotkeys = hotkeys
        self.capture = capture
        self.ribbon = ribbon
        self.warm_up = warm_up
        self.windows_composition = windows_composition
        self.cuda_device_count = cuda_device_count
        self.lmlink_status = lmlink_status
        self.stt_fixture = stt_fixture
        self.mcp_probe = mcp_probe
        self.ttft_warning_ms = ttft_warning_ms
        self._devices_task: asyncio.Task[list[AudioDevice]] | None = None
        self._models_task: asyncio.Task[list[ModelSummary]] | None = None
        self._details_task: asyncio.Task[list[ModelDetails]] | None = None
        self._lmlink_task: asyncio.Task[str] | None = None
        self._warm_task: asyncio.Task[dict[str, float | None]] | None = None
        self._fixture_lock = asyncio.Lock()

    def as_mapping(self) -> Mapping[str, ReadinessProbe]:
        probes: dict[str, ReadinessProbe] = {
            "windows_dwm": self._windows_dwm,
            "system_audio": self._system_audio,
            "microphone": self._microphone,
            "cuda_stt": self._cuda_stt,
            "stt_ru_fixture": lambda: self._stt_language("ru"),
            "stt_en_fixture": lambda: self._stt_language("en"),
            "lmstudio_auth": self._lmstudio_auth,
            "lmlink_status": self._lmlink,
            "preferred_device": self._preferred_device,
            "model_discovery": self._model_discovery,
            "duplicate_instances": self._duplicate_instances,
            "model_load_warmup": self._model_load_warmup,
            "context7": lambda: self._mcp("mcp/context7"),
            "duckduckgo_mcp": lambda: self._mcp("mcp/duckduckgo"),
            "hotkeys": self._hotkeys,
            "display_affinity": self._display_affinity,
            "event_capture": self._event_capture,
            "streaming_ttft": self._streaming_ttft,
        }
        assert tuple(probes) == READINESS_CHECK_NAMES
        return probes

    async def aclose(self) -> None:
        """Cancel and drain cached single-flight work before a settings rebuild."""

        tasks = tuple(
            task
            for task in (
                self._devices_task,
                self._models_task,
                self._details_task,
                self._lmlink_task,
                self._warm_task,
            )
            if task is not None and not task.done()
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _windows_dwm(self) -> ProbeOutcome:
        enabled = await asyncio.to_thread(self.windows_composition)
        if not enabled:
            return ProbeOutcome("failed", "Windows DWM composition is unavailable")
        return ProbeOutcome("ready", "Windows DWM composition is enabled")

    async def _devices(self) -> list[AudioDevice]:
        if self._devices_task is None:
            self._devices_task = asyncio.create_task(
                asyncio.to_thread(self.audio_devices.list_devices)
            )
        return await asyncio.shield(self._devices_task)

    async def discovered_devices(self) -> tuple[AudioDevice, ...]:
        return tuple(await self._devices())

    async def _system_audio(self) -> ProbeOutcome:
        selected = self.config.audio.system_device_id
        if not selected:
            return ProbeOutcome("failed", "No system-audio device is selected")
        device = next((item for item in await self._devices() if item.id == selected), None)
        if device is None or not device.is_loopback:
            return ProbeOutcome("failed", "Selected system-audio loopback is unavailable")
        return ProbeOutcome("ready", "Selected system-audio loopback is available")

    async def _microphone(self) -> ProbeOutcome:
        selected = self.config.audio.microphone_device_id
        if not selected:
            return ProbeOutcome("failed", "No microphone is selected")
        if selected == self.config.audio.system_device_id:
            return ProbeOutcome("failed", "Microphone and system audio must be distinct")
        device = next((item for item in await self._devices() if item.id == selected), None)
        if device is None or device.max_input_channels <= 0:
            return ProbeOutcome("failed", "Selected microphone is unavailable")
        return ProbeOutcome("ready", "Selected microphone is available")

    async def _cuda_stt(self) -> ProbeOutcome:
        count = await asyncio.to_thread(self.cuda_device_count)
        if count <= 0:
            return ProbeOutcome("failed", "No CUDA device is available for streaming STT")
        return ProbeOutcome("ready", f"CUDA exposes {count} device(s) for STT")

    async def _stt_language(self, language: str) -> ProbeOutcome:
        fixture = self.stt_fixture
        if fixture is None:
            return ProbeOutcome(
                "warning",
                f"{language.upper()} STT fixture was not verified on this machine",
            )
        async with self._fixture_lock:
            passed = await fixture(language)
        if not passed:
            return ProbeOutcome("failed", f"{language.upper()} STT fixture did not pass")
        return ProbeOutcome("ready", f"{language.upper()} STT fixture passed")

    async def _models(self) -> list[ModelSummary]:
        if self._models_task is None:
            self._models_task = asyncio.create_task(self.client.list_models())
        return await asyncio.shield(self._models_task)

    async def discovered_models(self) -> tuple[ModelSummary, ...]:
        return tuple(await self._models())

    async def _details(self) -> list[ModelDetails]:
        if self._details_task is None:
            self._details_task = asyncio.create_task(self.client.list_model_details())
        return await asyncio.shield(self._details_task)

    async def _lmstudio_auth(self) -> ProbeOutcome:
        await self._models()
        return ProbeOutcome("ready", "LM Studio API authentication succeeded")

    async def _lmlink_output(self) -> str:
        if self._lmlink_task is None:
            self._lmlink_task = asyncio.create_task(
                asyncio.to_thread(self.lmlink_status)
            )
        return await asyncio.shield(self._lmlink_task)

    async def _lmlink(self) -> ProbeOutcome:
        output = (await self._lmlink_output()).strip()
        if not output:
            return ProbeOutcome("failed", "LM Link status returned no data")
        try:
            decoded = json.loads(output)
        except json.JSONDecodeError:
            return ProbeOutcome("failed", "LM Link CLI did not return valid JSON")
        if not isinstance(decoded, dict):
            return ProbeOutcome("failed", "LM Link CLI did not return a JSON object")
        return ProbeOutcome("ready", "LM Link CLI is reachable")

    async def _preferred_device(self) -> ProbeOutcome:
        output = (await self._lmlink_output()).casefold()
        preferred = self.config.lmstudio.preferred_device_name.strip()
        if preferred and preferred.casefold() in output:
            return ProbeOutcome(
                "warning",
                "Configured device appears in LM Link status, but REST routing cannot be verified",
            )
        return ProbeOutcome(
            "warning",
            "Preferred LM Link device cannot be verified from the documented REST API",
        )

    def _selected_model_keys(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                key
                for key in (
                    self.config.lmstudio.text_model,
                    self.config.lmstudio.vision_model,
                )
                if key
            )
        )

    async def _model_discovery(self) -> ProbeOutcome:
        selected = self._selected_model_keys()
        if not selected:
            return ProbeOutcome("failed", "No LM Studio model is selected")
        available = {model.key for model in await self._models()}
        missing = [key for key in selected if key not in available]
        if missing:
            return ProbeOutcome("failed", "One or more selected models are unavailable")
        vision_key = self.config.lmstudio.vision_model
        if vision_key:
            details = next(
                (model for model in await self._details() if model.key == vision_key),
                None,
            )
            if details is not None and details.capabilities is not None:
                if not details.capabilities.vision:
                    return ProbeOutcome(
                        "failed",
                        "Selected vision model reports no vision capability",
                    )
            elif details is not None:
                return ProbeOutcome(
                    "warning",
                    "Selected model was discovered, but vision capability is not verified",
                )
        return ProbeOutcome("ready", f"Discovered {len(selected)} selected model key(s)")

    async def _duplicate_instances(self) -> ProbeOutcome:
        selected = self._selected_model_keys()
        if not selected:
            return ProbeOutcome("failed", "No model selection is available to inspect")
        details = await self._details()
        detailed_keys = {model.key for model in details}
        if any(key not in detailed_keys for key in selected):
            return ProbeOutcome(
                "failed",
                "A selected model is missing from native instance details",
            )
        duplicated = [
            model.key
            for model in details
            if model.key in selected and len(model.loaded_instances) > 1
        ]
        if duplicated:
            return ProbeOutcome("failed", "A selected model has duplicate loaded instances")
        return ProbeOutcome("ready", "Selected model keys have at most one loaded instance")

    async def _warm_models(self) -> dict[str, float | None]:
        if self._warm_task is None:
            self._warm_task = asyncio.create_task(self._warm_models_once())
        return await asyncio.shield(self._warm_task)

    async def _warm_models_once(self) -> dict[str, float | None]:
        keys = self._selected_model_keys()
        if not keys:
            raise RuntimeError("No model selected for warm-up")
        warmed: dict[str, float | None] = {}
        for key in keys:
            instance = await self.registry.ensure_ready(key)
            warmed[key] = await self.warm_up(instance)
        return warmed

    async def _model_load_warmup(self) -> ProbeOutcome:
        warmed = await self._warm_models()
        return ProbeOutcome("ready", f"Loaded and warmed {len(warmed)} unique model(s)")

    async def _mcp(self, integration: str) -> ProbeOutcome:
        if (
            integration == "mcp/duckduckgo"
            and self.config.search.provider != "duckduckgo"
        ):
            return ProbeOutcome(
                "warning",
                f"Configured provider {self.config.search.provider} is not supported; retrieval is disabled",
            )
        probe = self.mcp_probe
        if probe is None:
            return ProbeOutcome(
                "warning",
                f"{integration} connectivity was not verified without a tool request",
            )
        if not await probe(integration):
            return ProbeOutcome("failed", f"{integration} connectivity check failed")
        return ProbeOutcome("ready", f"{integration} connectivity check passed")

    async def _hotkeys(self) -> ProbeOutcome:
        await asyncio.to_thread(self.hotkeys.start)
        try:
            return ProbeOutcome("ready", "Global hotkey listener started successfully")
        finally:
            await asyncio.to_thread(self.hotkeys.stop)

    async def _display_affinity(self) -> ProbeOutcome:
        result = self.ribbon.verify_capture_exclusion()
        if not result.ok:
            return ProbeOutcome("failed", "Overlay capture exclusion could not be verified")
        return ProbeOutcome("ready", "Overlay capture exclusion is verified")

    async def _event_capture(self) -> ProbeOutcome:
        if self.config.capture.persistent_screenshots:
            return ProbeOutcome(
                "warning",
                "Persistent screenshots are unsupported; captures remain temporary",
            )
        result = await self.capture.capture_for_event("screen_analysis", manual=True)
        if result.status == "captured" and result.path is not None and result.path.is_file():
            return ProbeOutcome("ready", "Event-driven capture produced a usable frame")
        return ProbeOutcome(
            "warning",
            f"Event-driven capture returned {result.status} instead of a usable frame",
        )

    async def _streaming_ttft(self) -> ProbeOutcome:
        warmed = await self._warm_models()
        key = self.config.lmstudio.text_model or self.config.lmstudio.vision_model
        first_delta_ms = warmed.get(key)
        return streaming_ttft_outcome(
            first_delta_ms,
            warning_threshold_ms=self.ttft_warning_ms,
        )
