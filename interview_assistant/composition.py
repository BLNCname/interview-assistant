from __future__ import annotations

import asyncio
import ctypes
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from time import perf_counter
from typing import Protocol, cast

import numpy as np
from platformdirs import user_config_path
from PyQt6.QtCore import QObject, QSettings, pyqtSlot
from PyQt6.QtWidgets import QApplication

from interview_assistant.app import InterviewApplication
from interview_assistant.audio.devices import AudioDeviceService
from interview_assistant.audio.worker import AudioWorker
from interview_assistant.capture.frame_validator import FrameValidator
from interview_assistant.capture.worker import CaptureWorker
from interview_assistant.config import AppConfig, SecretStore
from interview_assistant.diagnostics.probes import ProductionReadinessProbes
from interview_assistant.diagnostics.readiness import (
    ReadinessReport,
    ReadinessRunner,
    build_readiness_checks,
)
from interview_assistant.events import EventBus
from interview_assistant.lmstudio.client import LMStudioClient, is_loopback_host
from interview_assistant.lmstudio.models import ModelInstance
from interview_assistant.lmstudio.payload import build_chat_payload
from interview_assistant.lmstudio.registry import ModelRegistry
from interview_assistant.orchestration.coordinator import RequestCoordinator
from interview_assistant.retrieval.policy import SearchPolicy
from interview_assistant.runtime import (
    ApplicationRuntime,
    RuntimeHypothesisIngress,
    RuntimeServices,
)
from interview_assistant.state import ApplicationState, StateMachine
from interview_assistant.stt.engine import TranscriptionEngine, WhisperEngine
from interview_assistant.stt.worker import StreamingSTTWorker
from interview_assistant.transcript.detector import QuestionDetector
from interview_assistant.transcript.store import TranscriptStore
from interview_assistant.ui.settings import (
    SettingsBinding,
    SettingsChoice,
    SettingsWindow,
)
from interview_assistant.ui.windows_affinity import AffinityApplier
from interview_assistant.utils.hotkeys import HotkeyAction, HotkeyManager


class ControllerSecretStore(Protocol):
    def get_lm_token(self) -> str | None: ...


class ProductionSecretStore(ControllerSecretStore, Protocol):
    def has_lm_token(self) -> bool: ...

    def set_lm_token(self, value: str) -> None: ...


class _CTranslate2Module(Protocol):
    def get_cuda_device_count(self) -> int: ...


class ControllerRuntime(Protocol):
    async def start(self) -> None: ...

    def update_hotkey_bindings(
        self,
        bindings: Mapping[HotkeyAction | str, str],
    ) -> None: ...


class ControllerReadiness(Protocol):
    async def run(self) -> ReadinessReport: ...


class ControllerComponents(Protocol):
    @property
    def runtime(self) -> ControllerRuntime: ...

    @property
    def readiness(self) -> ControllerReadiness: ...

    async def audio_choices(self) -> tuple[SettingsChoice, ...]: ...

    async def model_choices(self) -> tuple[SettingsChoice, ...]: ...

    async def aclose(self) -> None: ...


ComponentFactory = Callable[[AppConfig, str | None], ControllerComponents]


class _UnconfiguredAudioWorker:
    def start(self) -> None:
        raise RuntimeError("Both distinct audio devices must be selected")

    def stop(self) -> None:
        return None

    def pause(self) -> None:
        return None

    def resume(self) -> None:
        return None


async def _warm_model(client: LMStudioClient, instance: ModelInstance) -> float | None:
    payload = build_chat_payload(
        instance.instance_id,
        "Reply with the single word OK.",
    )
    started = perf_counter()
    first_delta_ms: float | None = None
    saw_end = False
    async for event in client.stream_chat(payload):
        if event.type == "message.delta" and first_delta_ms is None:
            first_delta_ms = (perf_counter() - started) * 1_000
        elif event.type == "error":
            raise RuntimeError("LM Studio warm-up returned an error")
        elif event.type == "chat.end":
            saw_end = True
    if not saw_end:
        raise RuntimeError("LM Studio warm-up ended without chat.end")
    return first_delta_ms


async def _warm_stt_engine(engine: TranscriptionEngine) -> None:
    samples = np.zeros(1_600, dtype=np.float32)
    await asyncio.to_thread(
        engine.transcribe,
        samples,
        beam_size=1,
        condition_on_previous_text=False,
    )


def _windows_composition_enabled() -> bool:
    if sys.platform != "win32":
        return False
    enabled = ctypes.c_int(0)
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    result = dwmapi.DwmIsCompositionEnabled(ctypes.byref(enabled))
    return result == 0 and bool(enabled.value)


def _cuda_device_count() -> int:
    module = cast(_CTranslate2Module, import_module("ctranslate2"))
    return int(module.get_cuda_device_count())


def _lmlink_status_json() -> str:
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run(
        ["lms", "link", "status", "--json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=4.0,
        creationflags=creation_flags,
    )
    if result.returncode != 0:
        raise RuntimeError("LM Link status command failed")
    return result.stdout


@dataclass(slots=True)
class ProductionComponents:
    runtime: ApplicationRuntime
    readiness: ControllerReadiness
    probes: ProductionReadinessProbes
    readiness_capture: CaptureWorker
    _close_task: asyncio.Task[None] | None = field(default=None, init=False)
    _closed_operations: set[str] = field(default_factory=set, init=False)

    async def audio_choices(self) -> tuple[SettingsChoice, ...]:
        devices = await self.probes.discovered_devices()
        return tuple(
            SettingsChoice(
                device.id,
                f"{device.name}{' (loopback)' if device.is_loopback else ''}",
            )
            for device in devices
        )

    async def model_choices(self) -> tuple[SettingsChoice, ...]:
        models = await self.probes.discovered_models()
        return tuple(SettingsChoice(model.key, model.key) for model in models)

    async def aclose(self) -> None:
        task = self._close_task
        if task is None:
            task = asyncio.create_task(self._close_once())
            self._close_task = task
            task.add_done_callback(self._close_attempt_done)
        await asyncio.shield(task)

    def _close_attempt_done(self, task: asyncio.Task[None]) -> None:
        try:
            error = task.exception()
        except asyncio.CancelledError:
            error = RuntimeError("Component cleanup attempt was cancelled")
        if error is not None and self._close_task is task:
            self._close_task = None

    async def _close_once(self) -> None:
        errors: list[BaseException] = []
        for name, operation in (
            ("probes", self.probes.aclose),
            ("readiness_capture", self.readiness_capture.shutdown),
            ("runtime", lambda: self.runtime.shutdown(close_application=False)),
        ):
            if name in self._closed_operations:
                continue
            try:
                await operation()
            except BaseException as error:
                errors.append(error)
            else:
                self._closed_operations.add(name)
        if errors:
            raise errors[0]


class _ReadinessWithCaptureCleanup:
    def __init__(
        self,
        runner: ReadinessRunner,
        probes: ProductionReadinessProbes,
        capture: CaptureWorker,
    ) -> None:
        self._runner = runner
        self._probes = probes
        self._capture = capture

    async def run(self) -> ReadinessReport:
        errors: list[BaseException] = []
        report: ReadinessReport | None = None
        try:
            report = await self._runner.run()
        except BaseException as error:
            errors.append(error)
        finally:
            for operation in (self._probes.aclose, self._capture.shutdown):
                try:
                    await operation()
                except BaseException as error:
                    errors.append(error)
        if errors:
            raise errors[0]
        assert report is not None
        return report


def build_production_components(
    application: InterviewApplication,
    config: AppConfig,
    token: str | None,
    *,
    loop: asyncio.AbstractEventLoop,
    windows_composition: Callable[[], bool] = _windows_composition_enabled,
    cuda_device_count: Callable[[], int] = _cuda_device_count,
    lmlink_status: Callable[[], str] = _lmlink_status_json,
    stt_engine: TranscriptionEngine | None = None,
) -> ProductionComponents:
    """Build the real dependency graph without opening hardware or network resources."""

    if not is_loopback_host(config.lmstudio.host):
        raise ValueError("Production LM Studio host must be an explicit loopback address")

    ingress = RuntimeHypothesisIngress(loop)
    system_id = config.audio.system_device_id
    microphone_id = config.audio.microphone_device_id
    if system_id and microphone_id and system_id != microphone_id:
        configured_audio = AudioWorker(
            system_id,
            microphone_id,
        )
        audio: AudioWorker | _UnconfiguredAudioWorker = configured_audio
        system_queue = configured_audio.system_queue
        microphone_queue = configured_audio.microphone_queue
    else:
        audio = _UnconfiguredAudioWorker()
        system_queue = None
        microphone_queue = None

    engine = (
        stt_engine
        if stt_engine is not None
        else WhisperEngine(
            config.audio.stt_model, device="cuda", compute_type="float16"
        )
    )
    stt = StreamingSTTWorker(
        system_queue,
        microphone_queue,
        engine=engine,
        on_hypothesis=ingress.publish,
    )
    capture = CaptureWorker(
        validator=FrameValidator(
            near_black_ratio_threshold=config.capture.black_frame_threshold,
        )
    )
    readiness_capture = CaptureWorker(
        validator=FrameValidator(
            near_black_ratio_threshold=config.capture.black_frame_threshold,
        )
    )
    client = LMStudioClient(
        config.lmstudio.host,
        config.lmstudio.port,
        token,
    )
    registry = ModelRegistry(client, config.lmstudio.preferred_device_name)
    hotkeys = HotkeyManager(application.events, config.hotkeys.as_bindings())
    coordinator = RequestCoordinator(application.events, client)
    search_mode = (
        config.search.mode if config.search.provider == "duckduckgo" else "off"
    )

    async def warm_for_runtime(instance: ModelInstance) -> None:
        await _warm_model(client, instance)

    services = RuntimeServices(
        audio=audio,
        stt=stt,
        hotkeys=hotkeys,
        capture=capture,
        registry=registry,
        client=client,
        coordinator=coordinator,
        transcript_store=TranscriptStore(),
        question_detector=QuestionDetector(),
        search_policy=SearchPolicy(search_mode),
        warm_up=warm_for_runtime,
        hypothesis_ingress=ingress,
    )
    runtime = ApplicationRuntime(application, config, services)
    ingress.bind(runtime.submit_hypothesis)

    async def warm_for_probe(instance: ModelInstance) -> float | None:
        return await _warm_model(client, instance)

    async def warm_stt_for_probe() -> None:
        await _warm_stt_engine(engine)

    probes = ProductionReadinessProbes(
        config,
        audio_devices=AudioDeviceService(),
        client=client,
        registry=registry,
        hotkeys=hotkeys,
        capture=readiness_capture,
        ribbon=application.ribbon,
        warm_up=warm_for_probe,
        windows_composition=windows_composition,
        cuda_device_count=cuda_device_count,
        stt_warm_up=warm_stt_for_probe,
        lmlink_status=lmlink_status,
    )
    readiness_runner = ReadinessRunner(build_readiness_checks(probes.as_mapping()))
    readiness = _ReadinessWithCaptureCleanup(
        readiness_runner,
        probes,
        readiness_capture,
    )
    return ProductionComponents(runtime, readiness, probes, readiness_capture)


class ApplicationController(QObject):
    """Qt-main-thread bootstrap that replaces readiness dependencies atomically."""

    def __init__(
        self,
        application: InterviewApplication,
        settings: SettingsWindow,
        config: AppConfig,
        secret_store: ControllerSecretStore,
        *,
        component_factory: ComponentFactory,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        super().__init__()
        self.application = application
        self.settings = settings
        self.config = config
        self.secret_store = secret_store
        self._component_factory = component_factory
        self._loop = loop
        self._components: ControllerComponents | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._component_generation = 0
        self._session_active = False
        self._readiness_task: asyncio.Task[None] | None = None
        self._action_tasks: set[asyncio.Task[None]] = set()
        self._closing = False
        self._shutdown_task: asyncio.Task[None] | None = None
        self._quit_task: asyncio.Task[None] | None = None
        self.application.qt_app.setQuitOnLastWindowClosed(False)
        self.settings.bind_hotkey_updater(self._update_live_hotkeys)
        self._connect_signals()

    @property
    def components(self) -> ControllerComponents | None:
        return self._components

    def _connect_signals(self) -> None:
        self.settings.readiness_requested.connect(self._on_readiness_requested)
        self.settings.start_requested.connect(self._on_start_requested)
        self.settings.close_requested.connect(self._on_settings_close_requested)
        self.application.events.settings_requested.connect(self._on_settings_requested)
        self.application.events.quit_requested.connect(self._on_quit_requested)
        self.application.events.notification.connect(self.settings.show_notification)
        self.application.qt_app.aboutToQuit.connect(self.request_shutdown)

    def _update_live_hotkeys(
        self,
        bindings: Mapping[HotkeyAction | str, str],
    ) -> None:
        components = self._components
        if components is not None:
            components.runtime.update_hotkey_bindings(bindings)

    async def initialize(self) -> None:
        if self._closing:
            return
        self.settings.show()
        initialization = asyncio.current_task()
        if initialization is None:
            raise RuntimeError("Initialization requires a running event loop task")
        self._readiness_task = initialization
        try:
            await self.rerun_readiness()
        finally:
            if self._readiness_task is initialization:
                self._readiness_task = None

    async def rerun_readiness(self) -> None:
        if self._closing:
            return
        async with self._lifecycle_lock:
            if self._closing:
                return
            old = self._components
            if old is not None:
                cancelled, close_error = await self._drain_component_close(old)
                if close_error is not None:
                    self.application.readiness_report = None
                    self.settings.clear_readiness(
                        "Readiness dependencies unavailable"
                    )
                    self._deactivate_session_ui()
                    self._show_readiness_dependency_failure()
                    if cancelled is not None:
                        raise cancelled
                    return
                self._detach_components(old)
                self._deactivate_session_ui()
                del old
                if cancelled is not None:
                    raise cancelled

            self.application.readiness_report = None
            self.settings.clear_readiness()
            self.application.overlay_config = self.config.overlay
            self.application.ribbon.apply_config(self.config.overlay)

            try:
                token = await asyncio.to_thread(self.secret_store.get_lm_token)
                components = self._component_factory(self.config, token)
            except Exception:
                self._show_readiness_dependency_failure()
                return
            self._components = components
            self._component_generation += 1
            report_task: asyncio.Task[ReadinessReport] | None = None
            try:
                report_task = asyncio.create_task(components.readiness.run())
                choices = await asyncio.gather(
                    components.audio_choices(),
                    components.model_choices(),
                    return_exceptions=True,
                )
                report = await report_task
            except asyncio.CancelledError:
                if report_task is not None and not report_task.done():
                    report_task.cancel()
                    await asyncio.gather(report_task, return_exceptions=True)
                _cancelled, close_error = await self._drain_component_close(components)
                if close_error is None:
                    self._detach_components(components)
                raise
            except Exception:
                if report_task is not None and not report_task.done():
                    report_task.cancel()
                    await asyncio.gather(report_task, return_exceptions=True)
                cancelled, close_error = await self._drain_component_close(components)
                if close_error is None:
                    self._detach_components(components)
                self._show_readiness_dependency_failure()
                if cancelled is not None:
                    raise cancelled
                return

            audio_choices, model_choices = choices
            if isinstance(audio_choices, tuple):
                self.settings.set_audio_choices(audio_choices)
            if isinstance(model_choices, tuple):
                self.settings.set_model_choices(model_choices)
            self.application.set_readiness_report(report)
            self.settings.set_readiness_report(report)
            self.settings.show()

    async def _drain_component_close(
        self,
        components: ControllerComponents,
    ) -> tuple[asyncio.CancelledError | None, BaseException | None]:
        close_task = asyncio.create_task(components.aclose())
        cancellation: asyncio.CancelledError | None = None
        close_error: BaseException | None
        while True:
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError as error:
                if cancellation is None:
                    cancellation = error
                if not close_task.done():
                    continue
            except BaseException as error:
                close_error = error
                break
            else:
                close_error = None
                break
            try:
                close_task.result()
            except BaseException as error:
                close_error = error
            else:
                close_error = None
            break
        return cancellation, close_error

    def _detach_components(self, components: ControllerComponents) -> None:
        if self._components is components:
            self._components = None
            self._component_generation += 1

    def _deactivate_session_ui(self) -> None:
        self._session_active = False
        self.application.ribbon.hide()
        self.settings.show()
        if not self.application.is_shutdown:
            self.application.states.transition(ApplicationState.STARTING)
            self.application.events.state_changed.emit(ApplicationState.STARTING.value)

    def _show_readiness_dependency_failure(self) -> None:
        self.settings.start_button.setEnabled(False)
        self.settings.readiness_status_label.setText("Readiness dependencies unavailable")
        self.settings.show_notification(
            "Readiness dependencies could not be created; check the saved configuration."
        )
        self.settings.show()

    async def start_session(self) -> None:
        if self._closing:
            return
        rebuild_after_failure = False
        deferred_cancellation: asyncio.CancelledError | None = None
        async with self._lifecycle_lock:
            if self._closing:
                return
            if self._session_active:
                self.settings.hide()
                return
            report = self.settings.readiness_report
            components = self._components
            generation = self._component_generation
            if report is None or not report.can_start or components is None:
                self.application.events.notification.emit(
                    "Readiness checks must pass before starting"
                )
                self.settings.show()
                return
            try:
                await components.runtime.start()
            except Exception:
                self.application.events.notification.emit(
                    "Session start failed; readiness was rebuilt."
                )
                deferred_cancellation, close_error = await self._drain_component_close(
                    components
                )
                if close_error is None:
                    self._detach_components(components)
                    rebuild_after_failure = True
                self.application.readiness_report = None
                self.settings.clear_readiness("Readiness dependencies unavailable")
                self._deactivate_session_ui()
            else:
                if (
                    self._components is components
                    and self._component_generation == generation
                    and not self._closing
                ):
                    self._session_active = True
                    self.settings.hide()
        if deferred_cancellation is not None:
            raise deferred_cancellation
        if rebuild_after_failure and not self._closing:
            await self.rerun_readiness()

    @pyqtSlot()
    def _on_readiness_requested(self) -> None:
        if self._closing:
            return
        previous = self._readiness_task
        if previous is not None:
            previous.cancel()
        task = self._loop.create_task(
            self._replace_readiness(previous),
            name="interview-readiness-rebuild",
        )
        self._readiness_task = task
        task.add_done_callback(self._readiness_done)

    async def _replace_readiness(self, previous: asyncio.Task[None] | None) -> None:
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        await self.rerun_readiness()

    def _readiness_done(self, task: asyncio.Task[None]) -> None:
        if self._readiness_task is task:
            self._readiness_task = None
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None and not self._closing:
            self.settings.readiness_status_label.setText("Readiness checks failed")
            self.application.events.notification.emit("Readiness checks failed.")

    @pyqtSlot()
    def _on_start_requested(self) -> None:
        self._schedule_action(self.start_session)

    @pyqtSlot()
    def _on_settings_requested(self) -> None:
        if self._closing:
            return
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    @pyqtSlot()
    def _on_settings_close_requested(self) -> None:
        if self._closing or self._session_active:
            return
        self._on_quit_requested()

    @pyqtSlot()
    def _on_quit_requested(self) -> None:
        if self._quit_task is not None:
            return
        self.request_shutdown()
        task = self._loop.create_task(
            self._quit_after_shutdown(),
            name="interview-controller-quit",
        )
        self._quit_task = task
        task.add_done_callback(self._quit_done)

    async def _quit_after_shutdown(self) -> None:
        try:
            await self.shutdown()
        finally:
            self.application.qt_app.quit()

    def _quit_done(self, task: asyncio.Task[None]) -> None:
        try:
            task.exception()
        except asyncio.CancelledError:
            return

    def _schedule_action(self, operation: Callable[[], Awaitable[None]]) -> None:
        if self._closing:
            return
        if len(self._action_tasks) >= 8:
            self.application.events.notification.emit(
                "Too many application actions are already running."
            )
            return

        async def invoke() -> None:
            await operation()

        task = self._loop.create_task(invoke(), name="interview-controller-action")
        self._action_tasks.add(task)
        task.add_done_callback(self._action_done)

    def _action_done(self, task: asyncio.Task[None]) -> None:
        self._action_tasks.discard(task)
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None and not self._closing:
            self.application.events.notification.emit("Application action failed.")

    async def shutdown(self) -> None:
        self.request_shutdown()
        task = self._shutdown_task
        assert task is not None
        await asyncio.shield(task)

    @pyqtSlot()
    def request_shutdown(self) -> None:
        if self._shutdown_task is not None:
            return
        self._closing = True
        task = self._loop.create_task(
            self._shutdown_once(),
            name="interview-controller-shutdown",
        )
        self._shutdown_task = task
        task.add_done_callback(self._shutdown_done)

    def _shutdown_done(self, task: asyncio.Task[None]) -> None:
        try:
            error = task.exception()
        except asyncio.CancelledError:
            error = RuntimeError("Controller shutdown attempt was cancelled")
        if error is not None and self._shutdown_task is task:
            self._shutdown_task = None

    async def _shutdown_once(self) -> None:
        errors: list[BaseException] = []
        readiness = self._readiness_task
        self._readiness_task = None
        if readiness is not None and readiness is not asyncio.current_task():
            readiness.cancel()
            await asyncio.gather(readiness, return_exceptions=True)
        actions = tuple(
            task for task in self._action_tasks if task is not asyncio.current_task()
        )
        for task in actions:
            task.cancel()
        if actions:
            await asyncio.gather(*actions, return_exceptions=True)

        async with self._lifecycle_lock:
            components = self._components
            if components is not None:
                _cancelled, close_error = await self._drain_component_close(components)
                if close_error is None:
                    self._detach_components(components)
                else:
                    errors.append(close_error)
            self._session_active = False
        try:
            self.settings.close_from_controller()
        except BaseException as error:
            errors.append(error)
        try:
            self.application.shutdown()
        except BaseException as error:
            errors.append(error)
        if errors:
            raise errors[0]


def default_config_path() -> Path:
    return Path(
        user_config_path(
            "InterviewAssistant",
            "InterviewAssistant",
        )
    ) / "config.yaml"


def create_production_controller(
    qt_app: QApplication,
    loop: asyncio.AbstractEventLoop,
    *,
    config_path: Path | None = None,
    settings_store: QSettings | None = None,
    affinity_applier: AffinityApplier | None = None,
    secret_store: ProductionSecretStore | None = None,
) -> ApplicationController:
    """Create the production bootstrap while preserving invalid/missing config files."""

    raw_path = config_path if config_path is not None else default_config_path()
    path = raw_path.expanduser().resolve()
    startup_message: str | None = None
    if not path.exists():
        config = AppConfig()
        startup_message = "Configuration file is missing; choose settings and Save."
    else:
        try:
            config = AppConfig.load(path)
        except Exception:
            config = AppConfig()
            startup_message = (
                "Configuration could not be loaded; the existing file was not changed."
            )

    active_settings = (
        settings_store
        if settings_store is not None
        else QSettings("InterviewAssistant", "InterviewAssistant")
    )
    active_secrets = secret_store if secret_store is not None else SecretStore()
    application = InterviewApplication(
        qt_app,
        EventBus(),
        StateMachine(),
        overlay_config=config.overlay,
        overlay_settings=active_settings,
        affinity_applier=affinity_applier,
    )
    binding = SettingsBinding(
        config,
        active_secrets,
        persist=lambda candidate: candidate.save(path),
    )
    settings = SettingsWindow(
        binding,
        audio_devices=(),
        models=(),
        settings=active_settings,
    )

    def factory(candidate: AppConfig, token: str | None) -> ControllerComponents:
        return build_production_components(
            application,
            candidate,
            token,
            loop=loop,
        )

    controller = ApplicationController(
        application,
        settings,
        config,
        active_secrets,
        component_factory=factory,
        loop=loop,
    )
    if startup_message is not None:
        settings.show_notification(startup_message)
    return controller
