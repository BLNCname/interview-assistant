from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QKeyEvent, QKeySequence, QMouseEvent
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from interview_assistant.utils.hotkeys import HotkeyChord, recording_hotkey


_MOUSE = {
    Qt.MouseButton.LeftButton: "mouse_left",
    Qt.MouseButton.RightButton: "mouse_right",
    Qt.MouseButton.MiddleButton: "mouse_middle",
    Qt.MouseButton.BackButton: "mouse_x1",
    Qt.MouseButton.ForwardButton: "mouse_x2",
}
_MOUSE_LABELS = dict(zip(_MOUSE.values(), ("Left click", "Right click", "Middle click",
                                          "Mouse 4 (Back)", "Mouse 5 (Forward)"), strict=True))
_KEYS: dict[int, str] = {
    Qt.Key.Key_Control: "ctrl", Qt.Key.Key_Shift: "shift", Qt.Key.Key_Alt: "alt",
    Qt.Key.Key_AltGr: "alt", Qt.Key.Key_Meta: "win", Qt.Key.Key_Super_L: "win",
    Qt.Key.Key_Super_R: "win", Qt.Key.Key_CapsLock: "caps_lock", Qt.Key.Key_ScrollLock: "scroll_lock",
    Qt.Key.Key_NumLock: "num_lock", Qt.Key.Key_Print: "print_screen", Qt.Key.Key_Menu: "menu",
    Qt.Key.Key_Plus: "plus", Qt.Key.Key_Backtab: "tab",
}


def _key_name(event: QKeyEvent) -> str:
    vk = event.nativeVirtualKey()
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x60 <= vk <= 0x6F or 0xBA <= vk <= 0xE2:
        return f"vk_{vk:02x}"
    name = _KEYS.get(event.key()) or QKeySequence(event.key()).toString()
    try:
        return HotkeyChord.parse(name).to_portable_text()
    except ValueError:
        if 0 < vk < 256:
            return f"vk_{vk:02x}"
        return ""


class HotkeyRecorder(QDialog):
    def __init__(self, parent: QWidget, action: str):
        super().__init__(parent)
        self.setWindowTitle(action)
        self.setModal(True)
        self.binding = ""
        self._held: set[tuple] = set()
        self._keys: set[str] = set()
        self._releasing = False
        self._finishing = False
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Hold the keys or mouse buttons together, then release them.", self))
        self.preview = QLabel("Listening…", self)
        layout.addWidget(self.preview)
        self.cancel = QPushButton("Cancel", self)
        self.cancel.clicked.connect(self.reject)
        layout.addWidget(self.cancel)

    def record(self) -> str:
        app = QApplication.instance()
        assert app is not None
        recording_hotkey.set()
        app.installEventFilter(self)
        try:
            return self.binding if self.exec() == QDialog.DialogCode.Accepted else ""
        finally:
            app.removeEventFilter(self)
            recording_hotkey.clear()

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:
        if event is None or self._finishing or not self.isVisible():
            return False
        kind = event.type()
        physical: tuple[str, int, int] | tuple[str, str]
        if watched is self and kind == QEvent.Type.WindowDeactivate:
            self._finishing = True
            self.reject()
            return False
        if kind == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        if isinstance(event, QKeyEvent) and kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            if event.isAutoRepeat():
                return True
            key = _key_name(event)
            physical = ("key", event.nativeScanCode(), event.nativeVirtualKey() or event.key())
            pressed = kind == QEvent.Type.KeyPress
        elif isinstance(event, QMouseEvent) and kind in (
            QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseButtonRelease,
        ):
            if watched is self.cancel and event.button() == Qt.MouseButton.LeftButton and not self._held:
                return False
            key = _MOUSE.get(event.button(), "")
            physical = ("mouse", key)
            pressed = kind != QEvent.Type.MouseButtonRelease
        else:
            return False
        if not key:
            return True
        if pressed:
            self._held.add(physical)
            if not self._releasing:
                self._keys.add(key)
                self.binding = HotkeyChord(frozenset(self._keys)).to_portable_text()
                self.preview.setText(self.binding)
        elif physical in self._held:
            self._held.remove(physical)
            self._releasing = True
            if not self._held:
                self._finishing = True
                self.accept()
        return True


class HotkeyButton(QPushButton):
    def __init__(self, action: str, binding: str, parent: QWidget):
        super().__init__(parent)
        self.action = action
        self.setAccessibleName(f"Change shortcut for {action}")
        self.set_binding(binding)
        self.clicked.connect(self._record)

    def set_binding(self, binding: str) -> None:
        self.binding = binding
        self.setText(" + ".join(_MOUSE_LABELS.get(k, k.title()) for k in binding.split("+")))

    def _record(self) -> None:
        dialog = HotkeyRecorder(self, self.action)
        if binding := dialog.record():
            self.set_binding(binding)
        dialog.deleteLater()
