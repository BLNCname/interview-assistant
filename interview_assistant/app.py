from dataclasses import dataclass, field

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from .config import OverlayConfig
from .events import EventBus
from .state import ApplicationState, StateMachine
from .ui.overlay import LiquidRibbon


def _overlay_settings() -> QSettings:
    return QSettings("InterviewAssistant", "InterviewAssistant")


@dataclass
class InterviewApplication:
    qt_app: QApplication
    events: EventBus
    states: StateMachine
    overlay_config: OverlayConfig = field(default_factory=OverlayConfig)
    overlay_settings: QSettings | None = field(default_factory=_overlay_settings)
    ribbon: LiquidRibbon = field(init=False)
    is_shutdown: bool = False

    def __post_init__(self) -> None:
        if QApplication.instance() is not self.qt_app:
            raise RuntimeError("QApplication must exist before the Liquid Ribbon")
        self.ribbon = LiquidRibbon(
            self.events,
            config=self.overlay_config,
            settings=self.overlay_settings,
        )

    @classmethod
    def for_test(cls) -> "InterviewApplication":
        instance = QApplication.instance()
        qt_app = instance if isinstance(instance, QApplication) else QApplication([])
        return cls(
            qt_app,
            EventBus(),
            StateMachine(),
            overlay_settings=None,
        )

    def start(self) -> None:
        if self.is_shutdown:
            return
        self.states.transition(ApplicationState.READY)
        self.events.state_changed.emit(self.states.state.value)
        self.ribbon.show()

    def shutdown(self) -> None:
        if self.is_shutdown:
            return
        self.ribbon.close()
        self.states.transition(ApplicationState.STOPPED)
        self.is_shutdown = True
