from __future__ import annotations

import asyncio
from pathlib import Path

import main as main_module


class _QtApplication:
    created_argv: list[str] | None = None
    window_icon: object | None = None

    def __init__(self, argv: list[str]) -> None:
        type(self).created_argv = argv
        type(self).window_icon = None

    def setWindowIcon(self, icon: object) -> None:
        type(self).window_icon = icon


class _Icon:
    def __init__(self, source: str) -> None:
        self.source = source

    def isNull(self) -> bool:
        return False


class _Controller:
    def __init__(self) -> None:
        self.initialized = 0
        self.shutdowns = 0

    async def initialize(self) -> None:
        self.initialized += 1

    async def shutdown(self) -> None:
        self.shutdowns += 1


class _EventLoop:
    def __init__(self, _app: _QtApplication) -> None:
        self.forever_count = 0
        self.entered = False
        self._loop = asyncio.new_event_loop()

    def __enter__(self) -> "_EventLoop":
        self.entered = True
        return self

    def __exit__(self, *_args: object) -> None:
        self.entered = False
        self._loop.close()

    def close(self) -> None:
        if not self._loop.is_closed():
            self._loop.close()

    def create_task(self, awaitable, *, name: str | None = None):
        return self._loop.create_task(awaitable, name=name)

    def run_until_complete(self, awaitable) -> object:
        if self.forever_count == 0:
            self._loop.call_soon(self._loop.stop)
        return self._loop.run_until_complete(awaitable)

    def run_forever(self) -> None:
        self.forever_count += 1
        self._loop.call_soon(self._loop.stop)
        self._loop.run_forever()


def test_headless_diagnostics_return_before_qapplication_creation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    diagnostic = getattr(main_module, "run_no_gui_diagnostics", None)
    assert callable(diagnostic), "Task 16 no-GUI diagnostics are not implemented"
    output_path = tmp_path / "diagnostics.json"
    calls: list[tuple[Path | None, Path | None]] = []

    def run_diagnostics(
        *,
        config_path: Path | None,
        output_path: Path | None,
    ) -> int:
        calls.append((config_path, output_path))
        return 0

    monkeypatch.setattr(main_module, "run_no_gui_diagnostics", run_diagnostics)

    class _ForbiddenApplication:
        def __init__(self, _argv: list[str]) -> None:
            raise AssertionError("QApplication must not be created in no-GUI mode")

    monkeypatch.setattr(main_module, "QApplication", _ForbiddenApplication)

    result = main_module.main(
        [
            "InterviewAssistant.exe",
            "--diagnostics",
            "--no-gui",
            "--diagnostics-output",
            str(output_path),
        ],
        config_path=tmp_path / "config.yaml",
    )

    assert result == 0
    assert calls == [(tmp_path / "config.yaml", output_path)]


def test_headless_diagnostics_use_default_config_when_no_override_is_given(
    monkeypatch,
    tmp_path: Path,
) -> None:
    default_path = tmp_path / "default-config.yaml"
    calls: list[Path | None] = []

    monkeypatch.setattr(main_module, "default_config_path", lambda: default_path)
    monkeypatch.setattr(
        main_module,
        "run_no_gui_diagnostics",
        lambda *, config_path, output_path: calls.append(config_path) or 0,
    )

    result = main_module.main(
        ["InterviewAssistant.exe", "--diagnostics", "--no-gui"]
    )

    assert result == 0
    assert calls == [default_path]


def test_main_uses_one_qasync_loop_and_awaits_shutdown(
    monkeypatch,
    tmp_path: Path,
) -> None:
    controller = _Controller()
    loops: list[_EventLoop] = []

    def loop_factory(app: _QtApplication) -> _EventLoop:
        loop = _EventLoop(app)
        loops.append(loop)
        return loop

    monkeypatch.setattr(main_module, "QApplication", _QtApplication)
    monkeypatch.setattr(main_module, "QIcon", _Icon)
    monkeypatch.setattr(main_module, "QEventLoop", loop_factory)
    monkeypatch.setattr(main_module.asyncio, "set_event_loop", lambda _loop: None)
    monkeypatch.setattr(
        main_module,
        "create_production_controller",
        lambda _app, _loop, config_path: controller,
    )

    result = main_module.main(["interview-assistant"], config_path=tmp_path / "config.yaml")

    assert result == 0
    assert controller.initialized == 1
    assert controller.shutdowns == 1
    assert len(loops) == 1
    assert loops[0].forever_count == 1


def test_main_applies_project_branding_to_qapplication(
    monkeypatch,
    tmp_path: Path,
) -> None:
    controller = _Controller()
    monkeypatch.setattr(main_module, "QApplication", _QtApplication)
    monkeypatch.setattr(main_module, "QIcon", _Icon, raising=False)
    monkeypatch.setattr(main_module, "QEventLoop", _EventLoop)
    monkeypatch.setattr(main_module.asyncio, "set_event_loop", lambda _loop: None)
    monkeypatch.setattr(
        main_module,
        "create_production_controller",
        lambda _app, _loop, config_path: controller,
    )

    result = main_module.main(
        ["interview-assistant"],
        config_path=tmp_path / "config.yaml",
    )

    assert result == 0
    assert isinstance(_QtApplication.window_icon, _Icon)
    assert Path(_QtApplication.window_icon.source) == (
        Path(main_module.__file__).resolve().parent
        / "assets"
        / "branding"
        / "interview-assistant.ico"
    )


class _SlowController(_Controller):
    def __init__(self) -> None:
        super().__init__()
        self.initialization_cancelled = False

    async def initialize(self) -> None:
        self.initialized += 1
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.initialization_cancelled = True
            raise


def test_main_exits_cleanly_when_qt_quits_during_initial_readiness(
    monkeypatch,
    tmp_path: Path,
) -> None:
    controller = _SlowController()

    monkeypatch.setattr(main_module, "QApplication", _QtApplication)
    monkeypatch.setattr(main_module, "QIcon", _Icon)
    monkeypatch.setattr(main_module, "QEventLoop", _EventLoop)
    monkeypatch.setattr(main_module.asyncio, "set_event_loop", lambda _loop: None)
    monkeypatch.setattr(
        main_module,
        "create_production_controller",
        lambda _app, _loop, config_path: controller,
    )

    result = main_module.main(["interview-assistant"], config_path=tmp_path / "config.yaml")

    assert result == 0
    assert controller.initialized == 1
    assert controller.initialization_cancelled
    assert controller.shutdowns == 1
