import pytest
from PyQt6.QtCore import QEvent, QPointF, QSettings, Qt
from PyQt6.QtGui import QFont, QMouseEvent, QTextCursor
from PyQt6.QtWidgets import QApplication, QLabel, QStyle

from interview_assistant.config import OverlayConfig
from interview_assistant.events import EventBus
from interview_assistant.state import ApplicationState
from interview_assistant.ui.overlay import LiquidRibbon


def test_streaming_delta_appends_without_replacing(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, settings=None)
    qtbot.addWidget(ribbon)

    bus.answer_reset.emit(7)
    bus.answer_delta.emit(7, "Hello ")
    bus.answer_delta.emit(7, "world")

    assert ribbon.answer_text == "Hello world"


def test_stale_delta_is_ignored(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, settings=None)
    qtbot.addWidget(ribbon)

    bus.answer_reset.emit(8)
    bus.answer_delta.emit(7, "stale")

    assert ribbon.answer_text == ""


def test_window_uses_overlay_flags_and_translucent_surface(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, config=OverlayConfig(opacity=0.2, max_height=280), settings=None)
    qtbot.addWidget(ribbon)

    flags = ribbon.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.Tool
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert ribbon.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert ribbon.maximumHeight() == 280
    assert ribbon.surface.background_alpha >= 180
    assert ribbon.surface.corner_radius == 18


def test_reference_spacing_and_standard_collapse_icon_are_applied(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, settings=None)
    qtbot.addWidget(ribbon)

    margins = ribbon.content_layout.contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
        14,
        12,
        14,
        12,
    )
    assert ribbon.body_layout.spacing() == 14
    assert ribbon.body_layout.stretch(0) == 105
    assert ribbon.body_layout.stretch(1) == 170
    assert ribbon.collapse_button.text() == ""
    assert not ribbon.collapse_button.icon().isNull()
    expected = ribbon.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp)
    assert ribbon.collapse_button.icon().availableSizes() == expected.availableSizes()
    visible_labels = {label.text() for label in ribbon.findChildren(QLabel)}
    assert "ВОПРОС" in visible_labels
    assert ribbon.question_label.text() == "Ожидание вопроса…"
    assert ribbon.sources_label.text() == "Sources: —"
    assert "border-radius" in ribbon.status_label.styleSheet()


def test_state_signal_is_connected_before_window_is_shown(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, settings=None)
    qtbot.addWidget(ribbon)

    assert not ribbon.isVisible()
    bus.state_changed.emit("Generating")

    assert ribbon.status_label.text() == "Generating"
    assert ribbon.status_label.textFormat() is Qt.TextFormat.PlainText


def test_older_reset_cannot_clear_a_newer_stream(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, settings=None)
    qtbot.addWidget(ribbon)
    bus.answer_reset.emit(10)
    bus.answer_delta.emit(10, "current")

    bus.answer_reset.emit(9)

    assert ribbon.answer_text == "current"
    assert ribbon.answer_browser.toPlainText() == "current"


def test_delta_uses_incremental_cursor_insertion(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, settings=None)
    qtbot.addWidget(ribbon)
    bus.answer_reset.emit(3)

    def forbidden_replacement(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("streaming must not replace the whole document")

    for method_name in ("setHtml", "setPlainText"):
        monkeypatch.setattr(
            ribbon.answer_browser,
            method_name,
            forbidden_replacement,
        )

    bus.answer_delta.emit(3, "one")
    bus.answer_delta.emit(3, " two")

    assert ribbon.answer_browser.toPlainText() == "one two"
    assert ribbon.answer_browser.verticalScrollBar().value() == (
        ribbon.answer_browser.verticalScrollBar().maximum()
    )


def test_model_markup_is_rendered_as_inert_text(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, settings=None)
    qtbot.addWidget(ribbon)
    payload = '<img src="https://evil.invalid/pixel"><script>alert(1)</script>'
    bus.answer_reset.emit(4)

    bus.answer_delta.emit(4, payload)

    assert ribbon.answer_text == payload
    assert ribbon.answer_browser.toPlainText() == payload
    assert "<img " not in ribbon.answer_browser.toHtml()
    assert "<script>" not in ribbon.answer_browser.toHtml()
    assert not ribbon.answer_browser.openExternalLinks()
    assert not ribbon.answer_browser.openLinks()


def test_small_markdown_subset_formats_bold_and_inline_code_safely(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, settings=None)
    qtbot.addWidget(ribbon)
    payload = "Use **bold** and `code`, not <a href='https://evil.invalid'>HTML</a>."
    bus.answer_reset.emit(5)

    bus.answer_delta.emit(5, payload)

    assert ribbon.answer_text == payload
    assert ribbon.answer_browser.toPlainText() == (
        "Use bold and code, not <a href='https://evil.invalid'>HTML</a>."
    )
    document = ribbon.answer_browser.document()
    bold_cursor = QTextCursor(document)
    bold_cursor.setPosition(ribbon.answer_browser.toPlainText().index("bold") + 1)
    code_cursor = QTextCursor(document)
    code_cursor.setPosition(ribbon.answer_browser.toPlainText().index("code") + 1)
    assert bold_cursor.charFormat().fontWeight() == QFont.Weight.Bold.value
    assert code_cursor.charFormat().fontFixedPitch()
    assert "<a " not in ribbon.answer_browser.toHtml()


def test_question_and_sources_are_plain_text(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, settings=None)
    qtbot.addWidget(ribbon)

    ribbon.set_question("Explain <b>ownership</b>")
    ribbon.set_sources(["<a href='https://evil.invalid'>source</a>", "docs"])

    assert ribbon.question_label.text() == "Explain <b>ownership</b>"
    assert ribbon.question_label.textFormat() is Qt.TextFormat.PlainText
    assert ribbon.sources_label.text() == "Sources: <a href='https://evil.invalid'>source</a> · docs"
    assert ribbon.sources_label.textFormat() is Qt.TextFormat.PlainText


def test_collapse_uses_a_thin_status_bar_and_restores_expanded_height(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, config=OverlayConfig(max_height=320), settings=None)
    qtbot.addWidget(ribbon)
    ribbon.resize(900, 240)
    ribbon.show()
    qtbot.waitExposed(ribbon)

    ribbon.toggle_collapsed()

    assert ribbon.is_collapsed
    assert not ribbon.body_widget.isVisible()
    assert ribbon.height() <= ribbon.collapsed_height

    ribbon.toggle_collapsed()

    assert not ribbon.is_collapsed
    assert ribbon.body_widget.isVisible()
    assert ribbon.collapsed_height < ribbon.height() <= 320


def test_long_answer_grows_only_to_configured_maximum(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, config=OverlayConfig(max_height=220), settings=None)
    qtbot.addWidget(ribbon)
    ribbon.resize(900, 130)
    ribbon.show()
    qtbot.waitExposed(ribbon)
    bus.answer_reset.emit(11)

    bus.answer_delta.emit(11, "line\n" * 200)
    qtbot.waitUntil(lambda: ribbon.height() == 220)

    assert ribbon.height() == ribbon.maximumHeight() == 220
    assert ribbon.answer_browser.verticalScrollBar().maximum() > 0


def test_short_answer_keeps_compact_ribbon_footprint(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, config=OverlayConfig(max_height=360), settings=None)
    qtbot.addWidget(ribbon)
    ribbon.resize(900, 180)
    ribbon.set_question("Design a short-link service")
    ribbon.set_sources(["Context7", "DuckDuckGo"])
    bus.answer_reset.emit(12)

    bus.answer_delta.emit(
        12,
        "**1. Clarify SLA and scale**\n"
        "2. Base62 + `Redis` + sharded DB\n"
        "3. Discuss hot keys, TTL, and analytics",
    )
    ribbon.show()
    qtbot.waitExposed(ribbon)

    assert ribbon._minimum_expanded_height <= ribbon.height() <= 200


def test_geometry_round_trips_through_injected_settings(qtbot, tmp_path) -> None:
    settings_path = tmp_path / "overlay.ini"
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    first = LiquidRibbon(EventBus(), settings=settings)
    qtbot.addWidget(first)
    first.setGeometry(35, 45, 700, 210)

    first.close()
    settings.sync()

    restored_settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    restored = LiquidRibbon(EventBus(), settings=restored_settings)
    qtbot.addWidget(restored)
    assert restored.geometry() == first.geometry()


def test_invalid_or_offscreen_geometry_falls_back_to_visible_default(qtbot, tmp_path) -> None:
    settings_path = tmp_path / "overlay.ini"
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    writer = LiquidRibbon(EventBus(), settings=None)
    qtbot.addWidget(writer)
    writer.setGeometry(100_000, 100_000, 700, 210)
    settings.setValue(LiquidRibbon.GEOMETRY_KEY, writer.saveGeometry())
    settings.sync()

    restored = LiquidRibbon(EventBus(), settings=settings)
    qtbot.addWidget(restored)

    screen = QApplication.primaryScreen()
    assert screen is not None
    assert restored.geometry().intersects(screen.availableGeometry())
    assert restored.geometry().topLeft() != writer.geometry().topLeft()


def _mouse_event(
    event_type: QEvent.Type,
    *,
    local_x: int,
    local_y: int,
    global_x: int,
    global_y: int,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
) -> QMouseEvent:
    return QMouseEvent(
        event_type,
        QPointF(local_x, local_y),
        QPointF(global_x, global_y),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


class _FallbackRibbon(LiquidRibbon):
    def _try_start_system_move(self) -> bool:
        return False

    def _try_start_system_resize(self, edges: Qt.Edge) -> bool:
        del edges
        return False


def test_drag_handler_has_safe_fallback_when_native_move_is_unavailable(qtbot) -> None:
    ribbon = _FallbackRibbon(EventBus(), settings=None)
    qtbot.addWidget(ribbon)
    ribbon.setGeometry(100, 100, 600, 180)
    press = _mouse_event(
        QEvent.Type.MouseButtonPress,
        local_x=200,
        local_y=20,
        global_x=300,
        global_y=120,
        button=Qt.MouseButton.LeftButton,
        buttons=Qt.MouseButton.LeftButton,
    )
    move = _mouse_event(
        QEvent.Type.MouseMove,
        local_x=250,
        local_y=40,
        global_x=350,
        global_y=140,
        button=Qt.MouseButton.NoButton,
        buttons=Qt.MouseButton.LeftButton,
    )

    ribbon.mousePressEvent(press)
    ribbon.mouseMoveEvent(move)

    assert ribbon.geometry().topLeft().x() == 150
    assert ribbon.geometry().topLeft().y() == 120


def test_resize_handler_uses_edges_and_enforces_configured_maximum(qtbot) -> None:
    ribbon = _FallbackRibbon(
        EventBus(),
        config=OverlayConfig(max_height=260),
        settings=None,
    )
    qtbot.addWidget(ribbon)
    ribbon.setGeometry(100, 100, 600, 180)
    press = _mouse_event(
        QEvent.Type.MouseButtonPress,
        local_x=598,
        local_y=178,
        global_x=698,
        global_y=278,
        button=Qt.MouseButton.LeftButton,
        buttons=Qt.MouseButton.LeftButton,
    )
    move = _mouse_event(
        QEvent.Type.MouseMove,
        local_x=798,
        local_y=678,
        global_x=898,
        global_y=778,
        button=Qt.MouseButton.NoButton,
        buttons=Qt.MouseButton.LeftButton,
    )

    ribbon.mousePressEvent(press)
    ribbon.mouseMoveEvent(move)

    assert ribbon.width() == 800
    assert ribbon.height() == 260


def test_application_owns_ribbon_and_shows_it_only_when_started(qtbot) -> None:
    from interview_assistant.app import InterviewApplication

    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)

    assert QApplication.instance() is app.qt_app
    assert not app.ribbon.isVisible()

    app.start()
    qtbot.waitExposed(app.ribbon)

    assert app.ribbon.isVisible()
    assert app.states.state is ApplicationState.READY
    assert app.ribbon.status_label.text() == ApplicationState.READY.value
    app.shutdown()


def test_application_shutdown_closes_ribbon_idempotently(qtbot) -> None:
    from interview_assistant.app import InterviewApplication

    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    app.start()
    assert app.ribbon.isVisible()

    app.shutdown()
    app.shutdown()

    assert app.is_shutdown
    assert not app.ribbon.isVisible()
    assert app.states.state is ApplicationState.STOPPED
