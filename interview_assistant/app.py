from dataclasses import dataclass, field

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from .config import OverlayConfig
from .events import EventBus
from .state import ApplicationState, StateMachine
from .ui.overlay import LiquidRibbon
from .ui.windows_affinity import AffinityApplier, AffinityResult, WDA_EXCLUDEFROMCAPTURE


def _overlay_settings() -> QSettings:
    return QSettings("InterviewAssistant", "InterviewAssistant")


@dataclass
class InterviewApplication:
    qt_app: QApplication
    events: EventBus
    states: StateMachine
    overlay_config: OverlayConfig = field(default_factory=OverlayConfig)
    overlay_settings: QSettings | None = field(default_factory=_overlay_settings)
    affinity_applier: AffinityApplier | None = None
    ribbon: LiquidRibbon = field(init=False)
    is_shutdown: bool = False
    _affinity_signal_connected: bool = field(default=False, init=False, repr=False)
    _affinity_restore_state: ApplicationState | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if QApplication.instance() is not self.qt_app:
            raise RuntimeError("QApplication must exist before the Liquid Ribbon")
        self.ribbon = LiquidRibbon(
            self.events,
            config=self.overlay_config,
            settings=self.overlay_settings,
            affinity_applier=self.affinity_applier,
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
            affinity_applier=lambda _hwnd: AffinityResult(
                True,
                WDA_EXCLUDEFROMCAPTURE,
                None,
            ),
        )

    def start(self) -> None:
        if self.is_shutdown:
            return
        self.ribbon.show()
        affinity_result = self.ribbon.affinity_result
        if affinity_result is None:
            self.ribbon.mark_capture_exclusion_unavailable()
        current_state = self.states.state
        if affinity_result is not None and affinity_result.ok:
            target = (
                ApplicationState.READY
                if current_state is ApplicationState.STARTING
                else current_state
            )
        else:
            target = ApplicationState.OFFLINE
        if target is ApplicationState.OFFLINE and current_state is not ApplicationState.OFFLINE:
            self._affinity_restore_state = (
                ApplicationState.READY
                if current_state is ApplicationState.STARTING
                else current_state
            )
        else:
            self._affinity_restore_state = None
        self.states.transition(target)
        if not self._affinity_signal_connected:
            self.ribbon.affinity_changed.connect(self._on_affinity_changed)
            self._affinity_signal_connected = True
        self.events.state_changed.emit(self.states.state.value)

    def _on_affinity_changed(self, result: AffinityResult) -> None:
        if self.is_shutdown:
            return
        if result.ok:
            restore_state = self._affinity_restore_state
            self._affinity_restore_state = None
            if restore_state is None or self.states.state is not ApplicationState.OFFLINE:
                return
            self.states.transition(restore_state)
            self.events.state_changed.emit(self.states.state.value)
            return
        if self.states.state is ApplicationState.OFFLINE:
            return
        self._affinity_restore_state = self.states.state
        self.states.transition(ApplicationState.OFFLINE)
        self.events.state_changed.emit(self.states.state.value)

    def shutdown(self) -> None:
        if self.is_shutdown:
            return
        self.ribbon.close()
        self.states.transition(ApplicationState.STOPPED)
        self.is_shutdown = True
