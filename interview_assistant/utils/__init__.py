"""Small application utilities with no import-time platform side effects."""

from .hotkeys import (
    DEFAULT_HOTKEY_BINDINGS,
    HotkeyAction,
    HotkeyChord,
    HotkeyManager,
    normalize_bindings,
)

__all__ = [
    "DEFAULT_HOTKEY_BINDINGS",
    "HotkeyAction",
    "HotkeyChord",
    "HotkeyManager",
    "normalize_bindings",
]
