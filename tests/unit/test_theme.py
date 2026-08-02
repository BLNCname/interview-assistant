import interview_assistant.ui.theme as theme
from PyQt6.QtWidgets import QWidget

from interview_assistant.ui.theme import (
    GRAPHITE,
    apply_native_dark_title_bar,
    graphite_stylesheet,
)


def test_graphite_theme_exposes_approved_brand_tokens() -> None:
    assert GRAPHITE.accent == "#50DE73"
    assert GRAPHITE.surface_top == (52, 54, 58)
    assert GRAPHITE.surface_bottom == (37, 38, 42)
    assert GRAPHITE.text_primary == "#F8FAF9"


def test_application_stylesheet_covers_controls_and_semantic_states() -> None:
    source = graphite_stylesheet()

    for selector in (
        "QMainWindow",
        "QListWidget",
        "QComboBox",
        "QLineEdit",
        "QPushButton",
        "QTableWidget",
        "QScrollBar",
    ):
        assert selector in source

    assert "#50DE73" in source
    assert '[readinessStatus="warning"]' in source
    assert '[readinessStatus="error"]' in source


def test_dark_title_bar_is_a_safe_noop_off_windows(monkeypatch, qtbot) -> None:
    widget = QWidget()
    qtbot.addWidget(widget)
    monkeypatch.setattr(theme.sys, "platform", "linux")

    assert not apply_native_dark_title_bar(widget)


def test_status_color_maps_readiness_semantics_with_safe_fallback() -> None:
    assert theme.status_color("ready") == "#50DE73"
    assert theme.status_color("warning") == "#F2C66D"
    assert theme.status_color("error") == "#FB7185"
    assert theme.status_color("failed") == "#F8FAF9"
