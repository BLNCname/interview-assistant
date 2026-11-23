from itertools import count

from PyQt6.QtCore import QObject, pyqtSignal


class EventBus(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.next_request_id = count(1).__next__

    state_changed = pyqtSignal(str)
    transcript_partial = pyqtSignal(str, str)
    transcript_final = pyqtSignal(str, str)
    answer_reset = pyqtSignal(int)
    answer_delta = pyqtSignal(int, str)
    answer_finished = pyqtSignal(int)
    notification = pyqtSignal(str)
    force_request = pyqtSignal()
    screenshot_requested = pyqtSignal()
    pause_toggled = pyqtSignal()
    overlay_visibility_toggled = pyqtSignal()
    overlay_interaction_toggled = pyqtSignal()
    forced_search_requested = pyqtSignal()
    answer_clear_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    quit_requested = pyqtSignal()
