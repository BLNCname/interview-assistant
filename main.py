from __future__ import annotations

import argparse
import asyncio
import json
import sys
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from interview_assistant.diagnostics.selftest import run_fast_self_test

if TYPE_CHECKING:
    from interview_assistant.composition import ApplicationController


# Load GUI/provider dependencies only after the cheap command-line paths exit.
# These small entry points also keep controller/loop substitution in tests simple.
def QApplication(argv: list[str]) -> Any:
    return import_module("PyQt6.QtWidgets").QApplication(argv)


def QIcon(source: str) -> Any:
    return import_module("PyQt6.QtGui").QIcon(source)


def QEventLoop(app: Any) -> asyncio.AbstractEventLoop:
    return import_module("qasync").QEventLoop(app)


def default_config_path() -> Path:
    from platformdirs import user_config_path

    return Path(user_config_path("InterviewAssistant", "InterviewAssistant")) / "config.yaml"


def create_production_controller(*args: Any, **kwargs: Any) -> ApplicationController:
    return import_module("interview_assistant.composition").create_production_controller(
        *args, **kwargs,
    )


def run_no_gui_diagnostics(**kwargs: Any) -> int:
    return import_module("interview_assistant.diagnostics.cli").run_no_gui_diagnostics(**kwargs)


def graphite_stylesheet() -> str:
    return import_module("interview_assistant.ui.theme").graphite_stylesheet()


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

    parser = argparse.ArgumentParser(description="Interview Assistant")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--self-test", action="store_true",
                        help="Run fast offline core checks and print a JSON report")
    try:
        options, qt_arguments = parser.parse_known_args(runtime_argv[1:])
        if "--mcp-web-search-server" in qt_arguments:
            parser.error("--mcp-web-search-server was removed; configure a remote MCP server")
    except SystemExit as exc:
        return int(exc.code or 0)
    selected_config = config_path or options.config or default_config_path()
    report = run_fast_self_test(config_path=selected_config)
    if options.self_test or report["status"] != "ok":
        if sys.stdout is not None:
            sys.stdout.write(json.dumps(report, ensure_ascii=True, sort_keys=True) + "\n")
            sys.stdout.flush()
        elif not options.self_test:
            # Windowed frozen builds have no terminal; PyInstaller presents this
            # explicit error instead of silently disappearing before readiness.
            raise RuntimeError("Offline startup self-test failed. Run --self-test from source.")
        return 0 if report["status"] == "ok" else 1

    qt_app = QApplication([runtime_argv[0], *qt_arguments])
    icon = QIcon(str(_application_icon_path()))
    if icon.isNull():
        raise RuntimeError("Application branding icon could not be loaded")
    qt_app.setWindowIcon(icon)
    qt_app.setStyleSheet(graphite_stylesheet())
    loop = QEventLoop(qt_app)
    asyncio.set_event_loop(loop)
    controller = create_production_controller(
        qt_app,
        loop,
        config_path=selected_config,
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
