from PyQt6.QtCore import QObject, pyqtSignal


class EventBus(QObject):
    state_changed = pyqtSignal(str)
    transcript_partial = pyqtSignal(str, str)
    transcript_final = pyqtSignal(str, str)
    answer_reset = pyqtSignal(int)
    answer_delta = pyqtSignal(int, str)
    notification = pyqtSignal(str)
    force_request = pyqtSignal()
    screenshot_requested = pyqtSignal()
    pause_toggled = pyqtSignal()
    overlay_visibility_toggled = pyqtSignal()
    forced_search_requested = pyqtSignal()
    answer_clear_requested = pyqtSignal()
