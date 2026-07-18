import argparse
import asyncio
import sys
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import cast

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from interview_assistant.composition import (
    ApplicationController,
    create_production_controller,
    default_config_path,
)
from interview_assistant.diagnostics.cli import run_no_gui_diagnostics


QEventLoop = cast(
    Callable[[QApplication], asyncio.AbstractEventLoop],
    getattr(import_module("qasync"), "QEventLoop"),
)


def _application_icon_path() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    root = (
        Path(frozen_root)
        if isinstance(frozen_root, str)
        else Path(__file__).resolve().parent
    )
    return root / "assets" / "branding" / "interview-assistant.ico"


async def _finalize(
    controller: ApplicationController,
    initialization: asyncio.Task[None],
) -> None:
    if not initialization.done():
        initialization.cancel()
    await asyncio.gather(initialization, return_exceptions=True)
    await controller.shutdown()


def _headless_diagnostic_exit_code(
    argv: list[str],
    *,
    config_path: Path | None,
) -> int | None:
    arguments = argv[1:]
    diagnostic_flags = {"--diagnostics", "--no-gui", "--diagnostics-output"}
    if not any(flag in arguments for flag in diagnostic_flags):
        return None

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--no-gui", action="store_true")
    parser.add_argument("--diagnostics-output", type=Path)
    parser.add_argument("--config", type=Path)
    try:
        options = parser.parse_args(arguments)
    except SystemExit:
        return 2
    if not options.diagnostics or not options.no_gui:
        return 2
    selected_config = config_path or options.config or default_config_path()
    return run_no_gui_diagnostics(
        config_path=selected_config,
        output_path=options.diagnostics_output,
    )


def main(
    argv: list[str] | None = None,
    *,
    config_path: Path | None = None,
) -> int:
    runtime_argv = list(sys.argv if argv is None else argv)
    diagnostic_exit_code = _headless_diagnostic_exit_code(
        runtime_argv,
        config_path=config_path,
    )
    if diagnostic_exit_code is not None:
        return diagnostic_exit_code

    qt_app = QApplication(runtime_argv)
    icon = QIcon(str(_application_icon_path()))
    if icon.isNull():
        raise RuntimeError("Application branding icon could not be loaded")
    qt_app.setWindowIcon(icon)
    loop = QEventLoop(qt_app)
    asyncio.set_event_loop(loop)
    controller = create_production_controller(
        qt_app,
        loop,
        config_path=config_path,
    )
    initialization = loop.create_task(
        controller.initialize(),
        name="interview-initial-readiness",
    )

    def initialization_done(task: asyncio.Task[None]) -> None:
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            controller.settings.show_notification("Application initialization failed.")
            controller.settings.show()

    initialization.add_done_callback(initialization_done)
    try:
        loop.run_forever()
    finally:
        try:
            loop.run_until_complete(_finalize(controller, initialization))
        finally:
            loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
