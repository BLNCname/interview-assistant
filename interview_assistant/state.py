from enum import StrEnum


class ApplicationState(StrEnum):
    STARTING = "starting"
    READY = "ready"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    SEARCHING = "searching"
    GENERATING = "generating"
    RECOVERING = "recovering"
    OFFLINE = "offline"
    PAUSED = "paused"
    STOPPED = "stopped"


class StateMachine:
    def __init__(self) -> None:
        self.state = ApplicationState.STARTING

    def transition(self, target: ApplicationState) -> None:
        if self.state is ApplicationState.STOPPED and target is not ApplicationState.STOPPED:
            raise RuntimeError("stopped application cannot transition")
        self.state = target
