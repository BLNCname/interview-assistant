from __future__ import annotations

import asyncio
from pathlib import Path

import main as main_module


class _QtApplication:
    created_argv: list[str] | None = None

    def __init__(self, argv: list[str]) -> None:
        type(self).created_argv = argv


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
