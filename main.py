import sys

from PyQt6.QtWidgets import QApplication

from interview_assistant.app import InterviewApplication
from interview_assistant.events import EventBus
from interview_assistant.state import StateMachine


def main() -> int:
    qt_app = QApplication(sys.argv)
    app = InterviewApplication(qt_app, EventBus(), StateMachine())
    try:
        app.start()
        return qt_app.exec()
    finally:
        app.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
