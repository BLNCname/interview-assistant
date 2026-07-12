from dataclasses import dataclass

from PyQt6.QtWidgets import QApplication

from .events import EventBus
from .state import ApplicationState, StateMachine


@dataclass
class InterviewApplication:
    qt_app: QApplication
    events: EventBus
    states: StateMachine
    is_shutdown: bool = False

    @classmethod
    def for_test(cls) -> "InterviewApplication":
        qt_app = QApplication.instance() or QApplication([])
        return cls(qt_app, EventBus(), StateMachine())

    def start(self) -> None:
        self.states.transition(ApplicationState.READY)
        self.events.state_changed.emit(self.states.state.value)

    def shutdown(self) -> None:
        if self.is_shutdown:
            return
        self.states.transition(ApplicationState.STOPPED)
        self.is_shutdown = True
