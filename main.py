import asyncio
import sys
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import cast

from PyQt6.QtWidgets import QApplication

from interview_assistant.composition import ApplicationController, create_production_controller


QEventLoop = cast(
    Callable[[QApplication], asyncio.AbstractEventLoop],
    getattr(import_module("qasync"), "QEventLoop"),
)


async def _finalize(
    controller: ApplicationController,
    initialization: asyncio.Task[None],
) -> None:
    if not initialization.done():
        initialization.cancel()
    await asyncio.gather(initialization, return_exceptions=True)
    await controller.shutdown()


def main(
    argv: list[str] | None = None,
    *,
    config_path: Path | None = None,
) -> int:
    qt_app = QApplication(list(sys.argv if argv is None else argv))
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
