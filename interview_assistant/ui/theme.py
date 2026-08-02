"""Shared Qt styling and native Windows title-bar support."""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

from PyQt6.QtWidgets import QWidget


@dataclass(frozen=True, slots=True)
class GraphitePalette:
    """Semantic colors for the Graphite Glass application theme."""

    accent: str = "#50DE73"
    surface_top: tuple[int, int, int] = (52, 54, 58)
    surface_bottom: tuple[int, int, int] = (37, 38, 42)
    surface_deep: str = "#171A19"
    text_primary: str = "#F8FAF9"
    text_secondary: str = "#AEB8B2"
    warning: str = "#F2C66D"
    error: str = "#FB7185"


GRAPHITE = GraphitePalette()


def graphite_stylesheet() -> str:
    """Return the shared application stylesheet for native Qt widgets."""
    return f"""
    QMainWindow, QWidget#settingsRoot {{
        color: {GRAPHITE.text_primary};
        background-color: {GRAPHITE.surface_deep};
    }}
    QWidget {{
        color: {GRAPHITE.text_primary};
    }}
    QLabel {{
        color: {GRAPHITE.text_primary};
        background: transparent;
    }}
    QLabel[readinessStatus="warning"] {{ color: {GRAPHITE.warning}; }}
    QLabel[readinessStatus="error"] {{ color: {GRAPHITE.error}; }}

    QListWidget#settingsNavigation {{
        background: #121514;
        border: 0;
        border-right: 1px solid rgba(255, 255, 255, 24);
        padding: 8px;
    }}
    QListWidget#settingsNavigation::item {{
        color: {GRAPHITE.text_secondary};
        padding: 9px 10px;
        border-radius: 7px;
    }}
    QListWidget#settingsNavigation::item:hover {{
        background: rgba(255, 255, 255, 12);
    }}
    QListWidget#settingsNavigation::item:selected {{
        color: {GRAPHITE.text_primary};
        background: rgba(80, 222, 115, 28);
        border-left: 2px solid {GRAPHITE.accent};
    }}

    QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {{
        color: {GRAPHITE.text_primary};
        background: #202322;
        border: 1px solid rgba(255, 255, 255, 38);
        border-radius: 6px;
        padding: 6px 8px;
        selection-background-color: rgba(80, 222, 115, 102);
    }}
    QComboBox::drop-down {{ border: 0; width: 24px; }}
    QComboBox QAbstractItemView {{
        color: {GRAPHITE.text_primary};
        background: #202322;
        border: 1px solid rgba(255, 255, 255, 38);
        selection-background-color: rgba(80, 222, 115, 48);
    }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus,
    QListWidget:focus, QTableWidget:focus, QPushButton:focus {{
        border: 1px solid {GRAPHITE.accent};
    }}
    QLineEdit:disabled, QComboBox:disabled, QPlainTextEdit:disabled,
    QTextEdit:disabled {{
        color: #718078;
        background: #1A1D1C;
    }}

    QPushButton {{
        color: {GRAPHITE.text_primary};
        background: #2A2E2C;
        border: 1px solid rgba(255, 255, 255, 44);
        border-radius: 6px;
        padding: 7px 12px;
    }}
    QPushButton:hover {{ background: #343937; }}
    QPushButton:pressed {{ background: #202421; }}
    QPushButton:disabled {{
        color: #718078;
        background: #1A1D1C;
        border-color: rgba(255, 255, 255, 20);
    }}
    QPushButton#startButton[readyToStart="true"] {{
        background: {GRAPHITE.accent};
        color: #07110A;
        border: 0;
    }}
    QPushButton#startButton[readyToStart="true"]:hover {{
        background: #71E98A;
    }}

    QTableWidget {{
        background: #1D201F;
        alternate-background-color: #202422;
        border: 1px solid rgba(255, 255, 255, 32);
        gridline-color: rgba(255, 255, 255, 20);
        selection-background-color: rgba(80, 222, 115, 48);
        selection-color: {GRAPHITE.text_primary};
    }}
    QHeaderView::section {{
        color: {GRAPHITE.text_secondary};
        background: #292D2B;
        border: 0;
        border-bottom: 1px solid rgba(255, 255, 255, 32);
        padding: 7px;
    }}

    QScrollBar:vertical {{
        background: transparent;
        width: 9px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: rgba(174, 184, 178, 104);
        min-height: 24px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{ background: rgba(174, 184, 178, 168); }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ height: 0; }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 9px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: rgba(174, 184, 178, 104);
        min-width: 24px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: rgba(174, 184, 178, 168); }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ width: 0; }}
    """


def apply_native_dark_title_bar(widget: QWidget) -> bool:
    """Request a dark native Windows title bar when DWM supports it."""
    if sys.platform != "win32":
        return False

    try:
        hwnd = ctypes.c_void_p(int(widget.winId()))
        enabled = ctypes.c_int(1)
        set_window_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        for attribute in (20, 19):
            result = set_window_attribute(
                hwnd,
                ctypes.c_uint(attribute),
                ctypes.byref(enabled),
                ctypes.sizeof(enabled),
            )
            if result == 0:
                return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return False
