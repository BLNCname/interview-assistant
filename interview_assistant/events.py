from PyQt6.QtCore import QObject, pyqtSignal


class EventBus(QObject):
    state_changed = pyqtSignal(str)
    transcript_partial = pyqtSignal(str, str)
    transcript_final = pyqtSignal(str, str)
    answer_reset = pyqtSignal(int)
    answer_delta = pyqtSignal(int, str)
    notification = pyqtSignal(str)
