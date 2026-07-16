from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal
from weakref import ReferenceType, ref

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from interview_assistant.app import InterviewApplication
from interview_assistant.composition import ApplicationController
from interview_assistant.config import AppConfig
from interview_assistant.diagnostics.readiness import CheckResult, ReadinessReport
from interview_assistant.state import ApplicationState
from interview_assistant.ui.settings import SettingsBinding, SettingsChoice, SettingsWindow


def _report(
    status: Literal["ready", "warning", "failed"] = "ready",
) -> ReadinessReport:
    return ReadinessReport(
        (
            CheckResult(
                "display_affinity",
                status,
                "result",
                "fix affinity",
                1.0,
                True,
            ),
        )
    )


class _Secrets:
    def __init__(self) -> None:
        self.get_count = 0

    def get_lm_token(self) -> str:
        self.get_count += 1
        return "secret-token"

    def has_lm_token(self) -> bool:
        return True

    def set_lm_token(self, _value: str) -> None:
        return None


class _FailingSecrets(_Secrets):
    def get_lm_token(self) -> str:
        self.get_count += 1
        raise RuntimeError("secret-token keyring backend detail")


class _Runner:
    def __init__(self, report: ReadinessReport) -> None:
        self.report = report
        self.run_count = 0

    async def run(self) -> ReadinessReport:
        self.run_count += 1
        return self.report


class _BlockingRunner(_Runner):
    def __init__(self) -> None:
        super().__init__(_report())
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def run(self) -> ReadinessReport:
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()
        raise AssertionError("unreachable")


class _Runtime:
    def __init__(self) -> None:
        self.start_count = 0

    async def start(self) -> None:
        self.start_count += 1


class _BlockingRuntime(_Runtime):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def start(self) -> None:
        self.start_count += 1
        self.started.set()
        await self.release.wait()


class _FailingRuntime(_Runtime):
    async def start(self) -> None:
        self.start_count += 1
        raise RuntimeError("sensitive audio backend detail")


class _Components:
    def __init__(self, report: ReadinessReport) -> None:
        self.readiness = _Runner(report)
        self.runtime = _Runtime()
        self.close_count = 0

    async def audio_choices(self) -> tuple[SettingsChoice, ...]:
        return (
            SettingsChoice("loopback", "Speakers (loopback)"),
            SettingsChoice("microphone", "Microphone"),
        )

    async def model_choices(self) -> tuple[SettingsChoice, ...]:
        return (SettingsChoice("qwen", "qwen"),)

    async def aclose(self) -> None:
        self.close_count += 1


class _WarmEngine:
    pass


class _EngineComponents(_Components):
    def __init__(self, report: ReadinessReport) -> None:
        super().__init__(report)
        self.engine = _WarmEngine()


class _FailOnceCloseComponents(_Components):
    async def aclose(self) -> None:
        self.close_count += 1
        if self.close_count == 1:
            raise RuntimeError("sensitive cleanup detail")


class _BlockingCloseComponents(_Components):
    def __init__(self, report: ReadinessReport) -> None:
        super().__init__(report)
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self._close_task: asyncio.Task[None] | None = None

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_once())
        await asyncio.shield(self._close_task)

    async def _close_once(self) -> None:
        self.close_count += 1
        self.close_started.set()
        await self.release_close.wait()


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "audio": {
                "system_device_id": "loopback",
                "microphone_device_id": "microphone",
            },
            "lmstudio": {"text_model": "qwen", "vision_model": "qwen"},
        }
    )


def _settings(qtbot, tmp_path: Path, config: AppConfig, secrets: _Secrets) -> SettingsWindow:
    window = SettingsWindow(
        SettingsBinding(config, secrets),
        audio_devices=(),
        models=(),
        settings=QSettings(str(tmp_path / "controller.ini"), QSettings.Format.IniFormat),
    )
    qtbot.addWidget(window)
    return window


async def test_initialize_runs_readiness_on_provisional_components_without_starting_workers(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    built: list[_Components] = []

    def build(candidate: AppConfig, token: str | None) -> _Components:
        assert candidate is config
        assert token == "secret-token"
        component = _Components(_report())
        built.append(component)
        return component

    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=build,
        loop=asyncio.get_running_loop(),
    )

    await controller.initialize()

    assert settings.isVisible()
    assert settings.readiness_report is not None
    assert settings.readiness_report.can_start
    assert settings.system_device_combo.findData("loopback") >= 0
    assert settings.text_model_combo.findData("qwen") >= 0
    assert built[0].runtime.start_count == 0
    assert secrets.get_count == 1

    await controller.start_session()
    assert built[0].runtime.start_count == 1
    assert not settings.isVisible()
    await controller.shutdown()


async def test_readiness_rebuild_closes_old_components_and_uses_latest_config(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    built: list[tuple[str, _Components]] = []

    def build(candidate: AppConfig, _token: str | None) -> _Components:
        component = _Components(_report())
        built.append((candidate.lmstudio.text_model, component))
        return component

    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=build,
        loop=asyncio.get_running_loop(),
    )
    await controller.initialize()
    config.lmstudio.text_model = "new-model"

    await controller.rerun_readiness()

    assert [key for key, _component in built] == ["qwen", "new-model"]
    assert built[0][1].close_count == 1
    assert built[1][1].close_count == 0
    await controller.shutdown()
    assert built[1][1].close_count == 1
    assert app.is_shutdown


async def test_readiness_rebuild_releases_old_engine_before_new_factory_runs(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    engine_ref: ReferenceType[_WarmEngine] | None = None
    released_before_rebuild: list[bool] = []

    def build(_candidate: AppConfig, _token: str | None) -> _EngineComponents:
        nonlocal engine_ref
        if engine_ref is not None:
            released_before_rebuild.append(engine_ref() is None)
        component = _EngineComponents(_report())
        engine_ref = ref(component.engine)
        return component

    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=build,
        loop=asyncio.get_running_loop(),
    )
    await controller.initialize()

    await controller.rerun_readiness()

    assert released_before_rebuild == [True]
    await controller.shutdown()


async def test_failed_readiness_never_starts_runtime(qtbot, tmp_path: Path) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    components = _Components(_report("failed"))
    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=lambda _config, _token: components,
        loop=asyncio.get_running_loop(),
    )

    await controller.initialize()
    await controller.start_session()

    assert components.runtime.start_count == 0
    assert settings.isVisible()
    await controller.shutdown()


async def test_cancelled_rebuild_drains_runner_before_closing_components(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    components = _Components(_report())
    blocking = _BlockingRunner()
    components.readiness = blocking
    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=lambda _config, _token: components,
        loop=asyncio.get_running_loop(),
    )

    rebuild = asyncio.create_task(controller.rerun_readiness())
    await blocking.started.wait()
    rebuild.cancel()
    try:
        await rebuild
    except asyncio.CancelledError:
        pass

    assert blocking.cancelled.is_set()
    assert components.close_count == 1
    await controller.shutdown()


async def test_shutdown_cancels_initial_readiness_before_closing_components(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    components = _Components(_report())
    blocking = _BlockingRunner()
    components.readiness = blocking
    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=lambda _config, _token: components,
        loop=asyncio.get_running_loop(),
    )

    initialization = asyncio.create_task(controller.initialize())
    try:
        await blocking.started.wait()
        controller.request_shutdown()
        await controller.shutdown()

        assert initialization.cancelled()
        assert blocking.cancelled.is_set()
        assert components.close_count == 1
    finally:
        if not initialization.done():
            initialization.cancel()
        await asyncio.gather(initialization, return_exceptions=True)


async def test_start_failure_closes_poisoned_components_and_rebuilds_for_retry(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    built: list[_Components] = []

    def build(_config: AppConfig, _token: str | None) -> _Components:
        components = _Components(_report())
        if not built:
            components.runtime = _FailingRuntime()
        built.append(components)
        return components

    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=build,
        loop=asyncio.get_running_loop(),
    )
    await controller.initialize()

    await controller.start_session()

    assert len(built) == 2
    assert built[0].close_count == 1
    assert settings.isVisible()
    assert "start failed" in settings.notification_label.text().casefold()
    assert "sensitive" not in settings.notification_label.text().casefold()
    await controller.start_session()
    assert built[1].runtime.start_count == 1
    await controller.shutdown()


async def test_component_construction_failure_stays_in_settings_without_secret_leak(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)

    def fail(_config: AppConfig, _token: str | None) -> _Components:
        raise ValueError("secret-token remote host")

    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=fail,
        loop=asyncio.get_running_loop(),
    )

    await controller.initialize()

    assert settings.isVisible()
    assert not settings.start_button.isEnabled()
    assert "dependencies" in settings.notification_label.text().casefold()
    assert "secret-token" not in settings.notification_label.text()
    await controller.shutdown()


async def test_secret_read_failure_stays_in_settings_without_building_components(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _FailingSecrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    build_count = 0

    def build(_config: AppConfig, _token: str | None) -> _Components:
        nonlocal build_count
        build_count += 1
        return _Components(_report())

    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=build,
        loop=asyncio.get_running_loop(),
    )

    await controller.initialize()

    assert build_count == 0
    assert settings.isVisible()
    assert not settings.start_button.isEnabled()
    assert "dependencies" in settings.notification_label.text().casefold()
    assert "secret-token" not in settings.notification_label.text()
    await controller.shutdown()


async def test_failed_old_component_cleanup_keeps_reference_for_shutdown_retry(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    old = _FailOnceCloseComponents(_report())
    build_count = 0

    def build(_config: AppConfig, _token: str | None) -> _Components:
        nonlocal build_count
        build_count += 1
        return old if build_count == 1 else _Components(_report())

    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=build,
        loop=asyncio.get_running_loop(),
    )
    await controller.initialize()
    await controller.start_session()
    app.start()
    app.states.transition(ApplicationState.LISTENING)
    app.events.state_changed.emit(ApplicationState.LISTENING.value)

    await controller.rerun_readiness()

    assert controller.components is old
    assert build_count == 1
    assert old.close_count == 1
    assert "dependencies" in settings.notification_label.text().casefold()
    assert "sensitive" not in settings.notification_label.text().casefold()
    assert not app.ribbon.isVisible()
    assert app.states.state is ApplicationState.STARTING
    assert app.readiness_report is None
    assert settings.readiness_report is None
    await controller.shutdown()
    assert old.close_count == 2


async def test_cancelled_old_cleanup_is_drained_before_replacement_graph_is_built(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    old = _BlockingCloseComponents(_report())
    built: list[_Components] = []

    def build(_config: AppConfig, _token: str | None) -> _Components:
        component = old if not built else _Components(_report())
        built.append(component)
        return component

    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=build,
        loop=asyncio.get_running_loop(),
    )
    await controller.initialize()

    first = asyncio.create_task(controller.rerun_readiness())
    await old.close_started.wait()
    first.cancel()
    await asyncio.sleep(0)
    assert not first.done()
    first.cancel()
    second_entered = asyncio.Event()

    async def rebuild_again() -> None:
        second_entered.set()
        await controller.rerun_readiness()

    second = asyncio.create_task(rebuild_again())
    await second_entered.wait()
    await asyncio.sleep(0)

    assert not first.done()
    assert len(built) == 1
    old.release_close.set()
    await asyncio.gather(first, return_exceptions=True)
    await second
    assert len(built) == 2
    assert old.close_count == 1
    await controller.shutdown()


async def test_concurrent_starts_join_one_controller_session_transition(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    components = _Components(_report())
    runtime = _BlockingRuntime()
    components.runtime = runtime
    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=lambda _config, _token: components,
        loop=asyncio.get_running_loop(),
    )
    await controller.initialize()

    first = asyncio.create_task(controller.start_session())
    await runtime.started.wait()
    second = asyncio.create_task(controller.start_session())
    await asyncio.sleep(0)
    assert runtime.start_count == 1

    runtime.release.set()
    await asyncio.gather(first, second)
    assert runtime.start_count == 1
    await controller.shutdown()


async def test_rebuild_waits_for_inflight_start_before_closing_old_graph(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    old = _Components(_report())
    runtime = _BlockingRuntime()
    old.runtime = runtime
    built: list[_Components] = []

    def build(_config: AppConfig, _token: str | None) -> _Components:
        component = old if not built else _Components(_report())
        built.append(component)
        return component

    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=build,
        loop=asyncio.get_running_loop(),
    )
    await controller.initialize()
    start = asyncio.create_task(controller.start_session())
    await runtime.started.wait()

    rebuild = asyncio.create_task(controller.rerun_readiness())
    await asyncio.sleep(0)
    assert old.close_count == 0
    assert len(built) == 1

    runtime.release.set()
    await start
    await rebuild
    assert old.close_count == 1
    assert len(built) == 2
    await controller.shutdown()


async def test_shutdown_waits_for_direct_inflight_start_before_component_close(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    components = _Components(_report())
    runtime = _BlockingRuntime()
    components.runtime = runtime
    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=lambda _config, _token: components,
        loop=asyncio.get_running_loop(),
    )
    await controller.initialize()
    start = asyncio.create_task(controller.start_session())
    await runtime.started.wait()

    shutdown = asyncio.create_task(controller.shutdown())
    await asyncio.sleep(0)
    assert components.close_count == 0

    runtime.release.set()
    await asyncio.gather(start, shutdown)
    assert components.close_count == 1
    assert app.is_shutdown


async def test_active_rebuild_clears_session_ui_and_applies_saved_overlay_config(
    qtbot,
    tmp_path: Path,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    first = _Components(_report())
    second = _Components(_report())
    blocking = _BlockingRunner()
    second.readiness = blocking
    built: list[_Components] = []

    def build(_config: AppConfig, _token: str | None) -> _Components:
        component = first if not built else second
        built.append(component)
        return component

    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=build,
        loop=asyncio.get_running_loop(),
    )
    await controller.initialize()
    await controller.start_session()
    app.start()
    app.states.transition(ApplicationState.LISTENING)
    app.events.state_changed.emit(ApplicationState.LISTENING.value)
    assert app.ribbon.isVisible()
    assert app.states.state.value == "listening"
    old_alpha = app.ribbon.surface.background_alpha
    config.overlay.opacity = 0.2
    config.overlay.max_height = 222

    rebuild = asyncio.create_task(controller.rerun_readiness())
    await blocking.started.wait()

    assert first.close_count == 1
    assert not app.ribbon.isVisible()
    assert settings.isVisible()
    assert app.states.state.value == "starting"
    assert app.readiness_report is None
    assert settings.readiness_report is None
    assert app.ribbon.maximumHeight() == 222
    assert app.ribbon.surface.background_alpha != old_alpha

    rebuild.cancel()
    await asyncio.gather(rebuild, return_exceptions=True)
    await controller.shutdown()


async def test_quit_request_awaits_cleanup_before_qapplication_quit(
    qtbot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    components = _Components(_report())
    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=lambda _config, _token: components,
        loop=asyncio.get_running_loop(),
    )
    quit_after_close_counts: list[int] = []
    monkeypatch.setattr(
        app.qt_app,
        "quit",
        lambda: quit_after_close_counts.append(components.close_count),
    )
    await controller.initialize()

    app.events.quit_requested.emit()

    async def wait_for_shutdown() -> None:
        while not app.is_shutdown:
            await asyncio.sleep(0)

    async def wait_for_quit() -> None:
        while not quit_after_close_counts:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_shutdown(), timeout=1.0)
    await asyncio.wait_for(wait_for_quit(), timeout=1.0)
    assert quit_after_close_counts == [1]
    assert components.close_count == 1
    await controller.shutdown()
    assert components.close_count == 1


async def test_settings_x_during_active_session_only_hides_settings(
    qtbot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = InterviewApplication.for_test()
    app.qt_app.setQuitOnLastWindowClosed(True)
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    components = _Components(_report())
    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=lambda _config, _token: components,
        loop=asyncio.get_running_loop(),
    )
    quit_calls: list[bool] = []
    monkeypatch.setattr(app.qt_app, "quit", lambda: quit_calls.append(True))
    await controller.initialize()
    await controller.start_session()
    app.start()
    app.states.transition(ApplicationState.LISTENING)
    app.events.state_changed.emit(ApplicationState.LISTENING.value)
    settings.show()
    QApplication.processEvents()

    assert not app.qt_app.quitOnLastWindowClosed()
    assert settings.close()
    QApplication.processEvents()
    await asyncio.sleep(0)

    assert not settings.isVisible()
    assert app.ribbon.isVisible()
    assert app.states.state is ApplicationState.LISTENING
    assert components.close_count == 0
    assert quit_calls == []
    await controller.shutdown()


async def test_settings_x_before_session_runs_explicit_awaited_quit(
    qtbot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = InterviewApplication.for_test()
    app.qt_app.setQuitOnLastWindowClosed(True)
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    components = _Components(_report())
    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=lambda _config, _token: components,
        loop=asyncio.get_running_loop(),
    )
    quit_after_close_counts: list[int] = []
    monkeypatch.setattr(
        app.qt_app,
        "quit",
        lambda: quit_after_close_counts.append(components.close_count),
    )
    await controller.initialize()

    assert settings.close()
    QApplication.processEvents()

    async def wait_for_quit() -> None:
        while not quit_after_close_counts:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_quit(), timeout=1.0)
    assert quit_after_close_counts == [1]
    assert app.is_shutdown
    await controller.shutdown()


async def test_settings_x_during_readiness_cancels_probe_and_controller_close_does_not_recurse(
    qtbot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = InterviewApplication.for_test()
    app.qt_app.setQuitOnLastWindowClosed(True)
    qtbot.addWidget(app.ribbon)
    config = _config()
    secrets = _Secrets()
    settings = _settings(qtbot, tmp_path, config, secrets)
    components = _Components(_report())
    blocking = _BlockingRunner()
    components.readiness = blocking
    controller = ApplicationController(
        app,
        settings,
        config,
        secrets,
        component_factory=lambda _config, _token: components,
        loop=asyncio.get_running_loop(),
    )
    close_requests: list[bool] = []
    settings.close_requested.connect(lambda: close_requests.append(True))
    quit_calls: list[bool] = []
    monkeypatch.setattr(app.qt_app, "quit", lambda: quit_calls.append(True))
    initialization = asyncio.create_task(controller.initialize())
    await blocking.started.wait()

    assert settings.close()
    QApplication.processEvents()
    await asyncio.gather(initialization, return_exceptions=True)
    await controller.shutdown()
    await asyncio.sleep(0)

    assert blocking.cancelled.is_set()
    assert components.close_count == 1
    assert close_requests == [True]
    assert quit_calls == [True]
