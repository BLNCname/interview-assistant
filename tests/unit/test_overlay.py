import math
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QEvent, QPointF, QSettings, Qt, QUrl
from PyQt6.QtGui import (
    QFont,
    QMouseEvent,
    QPalette,
    QShowEvent,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PyQt6.QtWidgets import QApplication, QLabel, QStyle, QTextBrowser, QWidget

from interview_assistant.config import OverlayConfig
from interview_assistant.events import EventBus
from interview_assistant.state import ApplicationState, StateMachine
from interview_assistant.ui import overlay as overlay_module
from interview_assistant.ui.overlay import (
    _ANSWER_BACKGROUND_ALPHA,
    _ANSWER_BACKGROUND_RGB,
    _MODEL_CHIP_ALPHA,
    _MODEL_CHIP_RGB,
    _STATUS_CHIP_ALPHA,
    _STATUS_CHIP_RGB,
    LiquidRibbon,
)
from interview_assistant.ui.windows_affinity import AffinityResult, WDA_EXCLUDEFROMCAPTURE


@pytest.fixture(autouse=True)
def _prevent_real_user32_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        overlay_module,
        "apply_capture_exclusion",
        lambda _hwnd: AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None),
    )


def test_streaming_delta_appends_without_replacing(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, settings=None)
    qtbot.addWidget(ribbon)

    bus.answer_reset.emit(7)
    bus.answer_delta.emit(7, "Hello ")
    bus.answer_delta.emit(7, "world")

    assert ribbon.answer_text == "Hello world"


def _rendered_ribbon(qtbot, markdown: str) -> LiquidRibbon:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, settings=None)
    qtbot.addWidget(ribbon)
    bus.answer_reset.emit(1)
    bus.answer_delta.emit(1, markdown)
    return ribbon


def _cursor_at(browser: QTextBrowser, text: str) -> QTextCursor:
    cursor = QTextCursor(browser.document())
    cursor.setPosition(browser.toPlainText().index(text) + 1)
    return cursor


def test_markdown_split_across_deltas_renders_without_delimiters(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, settings=None)
    qtbot.addWidget(ribbon)
    bus.answer_reset.emit(7)
    bus.answer_delta.emit(7, "## **Структура")
    bus.answer_delta.emit(7, " проблемы**\n\n- Первый пункт\n- Второй пункт")

    assert ribbon.answer_text.startswith("## **Структура")
    assert "**" not in ribbon.answer_browser.toPlainText()
    assert "Структура проблемы" in ribbon.answer_browser.toPlainText()
    assert ribbon.answer_browser.document().blockCount() >= 3


def test_markdown_fenced_code_and_inline_code_receive_monospace_format(qtbot) -> None:
    ribbon = _rendered_ribbon(qtbot, "Use `dict`:\n```python\nprint('ok')\n```")

    assert "print('ok')" in ribbon.answer_browser.toPlainText()
    assert _cursor_at(ribbon.answer_browser, "dict").charFormat().fontFixedPitch()
    assert _cursor_at(ribbon.answer_browser, "print").charFormat().fontFixedPitch()


def test_model_markdown_cannot_load_resources_or_activate_links(qtbot) -> None:
    ribbon = _rendered_ribbon(
        qtbot,
        "![x](file:///private.txt) [open](https://evil.invalid) "
        "<img src='https://evil.invalid/pixel'>",
    )
    browser = ribbon.answer_browser

    assert browser.resource_requests == ()
    resource_type = QTextDocument.ResourceType.ImageResource.value
    resource_url = QUrl("file:///private.txt")
    assert (
        browser.loadResource(
            resource_type,
            resource_url,
        )
        is None
    )
    assert browser.resource_requests == ((resource_type, resource_url),)
    assert not browser.openLinks()
    assert not browser.openExternalLinks()
    assert "private.txt" not in browser.toPlainText()
    assert "href=" not in browser.toHtml()


def test_stream_rerender_preserves_manual_scroll_position(qtbot) -> None:
    ribbon = _rendered_ribbon(qtbot, "line\n" * 100)
    ribbon.set_edit_mode(True)
    ribbon.show()
    qtbot.waitExposed(ribbon)
    bar = ribbon.answer_browser.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum() // 3)
    before = bar.value() / bar.maximum()

    ribbon.append_delta(1, "tail")

    after = bar.value() / bar.maximum()
    assert after == pytest.approx(before, abs=0.08)


def test_stream_scroll_restore_uses_final_range_after_ribbon_growth(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(
        bus,
        config=OverlayConfig(max_height=420),
        settings=None,
    )
    qtbot.addWidget(ribbon)
    bus.answer_reset.emit(2)
    bus.answer_delta.emit(2, "line\n" * 120)
    ribbon.show()
    qtbot.waitExposed(ribbon)
    ribbon.resize(ribbon.width(), 150)
    QApplication.processEvents()
    initial_height = ribbon.height()
    bar = ribbon.answer_browser.verticalScrollBar()
    assert initial_height < 420
    assert bar.maximum() > 0
    bar.setValue(bar.maximum() // 3)
    before = bar.value() / bar.maximum()

    ribbon.append_delta(2, "tail")
    QApplication.processEvents()

    assert ribbon.height() > initial_height
    after = bar.value() / bar.maximum()
    assert after == pytest.approx(before, abs=0.01)


def test_stale_delta_is_ignored(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, settings=None)
    qtbot.addWidget(ribbon)

    bus.answer_reset.emit(8)
    bus.answer_delta.emit(7, "stale")

    assert ribbon.answer_text == ""


def test_window_uses_capture_compatible_opacity_and_rounded_mask(qtbot) -> None:
    ribbon = LiquidRibbon(
        EventBus(),
        config=OverlayConfig(opacity=0.2, max_height=280),
        settings=None,
    )
    qtbot.addWidget(ribbon)

    flags = ribbon.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.Tool
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert not ribbon.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert ribbon.effective_window_opacity == 0.75
    assert ribbon.windowOpacity() >= 0.75
    assert math.isclose(ribbon.windowOpacity(), 0.75, abs_tol=1 / 255)
    assert not ribbon.mask().isEmpty()
    assert ribbon.maximumHeight() == 280
    assert ribbon.surface.background_alpha >= 180
    assert ribbon.surface.corner_radius == 18


def test_default_ribbon_uses_approved_graphite_palette(qtbot) -> None:
    ribbon = LiquidRibbon(EventBus(), settings=None)
    qtbot.addWidget(ribbon)

    assert ribbon.effective_window_opacity == 0.88
    assert ribbon.surface.gradient_top_rgb == (52, 54, 58)
    assert ribbon.surface.gradient_bottom_rgb == (37, 38, 42)


def test_settings_opacity_maps_to_distinct_monotonic_effective_values(qtbot) -> None:
    configured_values = (0.2, 0.5, 0.88, 1.0)
    effective_values: list[float] = []

    for configured in configured_values:
        ribbon = LiquidRibbon(
            EventBus(),
            config=OverlayConfig(opacity=configured),
            settings=None,
        )
        qtbot.addWidget(ribbon)
        effective_values.append(ribbon.effective_window_opacity)

    assert effective_values == sorted(set(effective_values))
    assert effective_values[0] == pytest.approx(0.75)
    assert effective_values[2] == pytest.approx(0.88)
    assert effective_values[3] == pytest.approx(1.0)


def test_window_mask_tracks_resize(qtbot) -> None:
    ribbon = LiquidRibbon(EventBus(), settings=None)
    qtbot.addWidget(ribbon)
    ribbon.show()
    qtbot.waitExposed(ribbon)
    ribbon.resize(780, 180)
    QApplication.processEvents()

    assert ribbon.mask().boundingRect() == ribbon.rect()


def test_ribbon_starts_click_through_and_edit_mode_restores_input(qtbot) -> None:
    ribbon = LiquidRibbon(EventBus(), settings=None)
    qtbot.addWidget(ribbon)

    assert ribbon.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert not ribbon.is_edit_mode
    assert not ribbon.edit_mode_label.isVisible()

    ribbon.set_edit_mode(True)

    assert not (ribbon.windowFlags() & Qt.WindowType.WindowTransparentForInput)
    assert ribbon.is_edit_mode
    assert ribbon.property("editMode") is True
    assert ribbon.surface.property("editMode") is True
    assert "#50DE73" in ribbon.edit_mode_label.styleSheet()
    assert not ribbon.edit_mode_label.isHidden()


def test_edit_transition_preserves_geometry_content_and_visibility(qtbot) -> None:
    ribbon = LiquidRibbon(EventBus(), settings=None)
    qtbot.addWidget(ribbon)
    ribbon.setGeometry(100, 120, 780, 220)
    ribbon._reset_answer(1)
    ribbon.append_delta(1, "answer")
    ribbon.show()
    qtbot.waitExposed(ribbon)
    before = ribbon.geometry()

    ribbon.set_edit_mode(True)

    assert ribbon.geometry() == before
    assert ribbon.answer_text == "answer"
    assert ribbon.answer_browser.toPlainText() == "answer"
    assert ribbon.isVisible()


def test_edit_transition_preserves_collapsed_state(qtbot) -> None:
    ribbon = LiquidRibbon(EventBus(), settings=None)
    qtbot.addWidget(ribbon)
    ribbon.toggle_collapsed()
    before = ribbon.geometry()

    ribbon.set_edit_mode(True)

    assert ribbon.is_collapsed
    assert ribbon.geometry() == before
    assert ribbon.body_widget.isHidden()


def test_geometry_is_debounced_into_qsettings(qtbot, tmp_path) -> None:
    settings = QSettings(str(tmp_path / "debounced.ini"), QSettings.Format.IniFormat)
    ribbon = LiquidRibbon(EventBus(), settings=settings)
    qtbot.addWidget(ribbon)
    ribbon.set_edit_mode(True)
    ribbon.setGeometry(140, 160, 720, 240)

    qtbot.waitUntil(lambda: settings.contains(LiquidRibbon.GEOMETRY_KEY))

    restored = LiquidRibbon(EventBus(), settings=settings)
    qtbot.addWidget(restored)
    assert restored.geometry() == ribbon.geometry()


def test_passive_ribbon_does_not_start_move_or_resize_handlers(qtbot) -> None:
    ribbon = _FallbackRibbon(EventBus(), settings=None)
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

    ribbon.mousePressEvent(press)

    assert ribbon._fallback_action is None


def test_failed_edit_transition_restores_previous_window_state(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = EventBus()
    notifications: list[str] = []
    events.notification.connect(notifications.append)
    ribbon = LiquidRibbon(events, settings=None)
    qtbot.addWidget(ribbon)
    ribbon.setGeometry(100, 120, 780, 220)
    ribbon.show()
    qtbot.waitExposed(ribbon)
    before_flags = ribbon.windowFlags()
    before_geometry = ribbon.geometry()

    def fail_transition(_flag: Qt.WindowType, _enabled: bool = True) -> None:
        raise RuntimeError("native transition failed")

    monkeypatch.setattr(ribbon, "setWindowFlag", fail_transition)

    ribbon.set_edit_mode(True)

    assert ribbon.windowFlags() == before_flags
    assert ribbon.geometry() == before_geometry
    assert ribbon.isVisible()
    assert not ribbon.is_edit_mode
    assert ribbon.property("editMode") is False
    assert notifications == ["Unable to change overlay interaction mode."]


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


def test_capture_exclusion_runs_only_after_native_hwnd_exists(qtbot) -> None:
    expected = AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)
    applier = Mock(return_value=expected)
    ribbon = LiquidRibbon(EventBus(), settings=None, affinity_applier=applier)
    qtbot.addWidget(ribbon)

    assert ribbon.affinity_result is None
    applier.assert_not_called()

    ribbon.show()
    qtbot.waitExposed(ribbon)

    native_hwnd = int(ribbon.winId())
    assert native_hwnd != 0
    applier.assert_called_once_with(native_hwnd)
    assert ribbon.affinity_result is expected
    with pytest.raises(AttributeError):
        setattr(ribbon, "affinity_result", expected)


def test_hidden_ribbon_can_verify_affinity_before_readiness_allows_show(qtbot) -> None:
    expected = AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)
    applier = Mock(return_value=expected)
    ribbon = LiquidRibbon(EventBus(), settings=None, affinity_applier=applier)
    qtbot.addWidget(ribbon)

    result = ribbon.verify_capture_exclusion()

    assert not ribbon.isVisible()
    assert result is expected
    applier.assert_called_once_with(int(ribbon.winId()))


def test_capture_exclusion_reapplies_only_when_qt_creates_a_new_hwnd(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handles = [101]
    expected = AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)
    applier = Mock(return_value=expected)
    monkeypatch.setattr(LiquidRibbon, "winId", lambda _ribbon: handles[-1])
    ribbon = LiquidRibbon(EventBus(), settings=None, affinity_applier=applier)
    qtbot.addWidget(ribbon)

    ribbon.show()
    qtbot.waitExposed(ribbon)
    ribbon.hide()
    ribbon.show()
    qtbot.waitExposed(ribbon)

    assert [entry.args[0] for entry in applier.call_args_list] == [101]

    handles.append(202)
    ribbon.hide()
    ribbon.show()
    qtbot.waitExposed(ribbon)

    assert [entry.args[0] for entry in applier.call_args_list] == [101, 202]


def test_win_id_change_reapplies_for_new_handle_without_same_handle_retry(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handles = [101]
    verified = AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)
    applier = Mock(return_value=verified)
    monkeypatch.setattr(LiquidRibbon, "winId", lambda _ribbon: handles[-1])
    ribbon = LiquidRibbon(EventBus(), settings=None, affinity_applier=applier)
    qtbot.addWidget(ribbon)

    applier.assert_not_called()
    ribbon.show()
    qtbot.waitExposed(ribbon)

    handles.append(202)
    QApplication.sendEvent(ribbon, QEvent(QEvent.Type.WinIdChange))
    QApplication.sendEvent(ribbon, QEvent(QEvent.Type.WinIdChange))

    assert [entry.args[0] for entry in applier.call_args_list] == [101, 202]


def test_capture_exclusion_failure_remains_visible_across_state_changes(qtbot) -> None:
    bus = EventBus()
    failure = AffinityResult(False, None, 5)
    ribbon = LiquidRibbon(
        bus,
        settings=None,
        affinity_applier=Mock(return_value=failure),
    )
    qtbot.addWidget(ribbon)

    ribbon.show()
    qtbot.waitExposed(ribbon)

    assert ribbon.affinity_result is failure
    assert ribbon.status_label.text() == "Capture exclusion unavailable"
    assert ribbon.status_dot._color.name() == "#fb7185"

    bus.state_changed.emit("ready")
    bus.state_changed.emit("Generating")

    assert ribbon.status_label.text() == "Capture exclusion unavailable"
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


def test_header_exposes_honest_settings_and_quit_actions(qtbot) -> None:
    events = EventBus()
    ribbon = LiquidRibbon(events, settings=None)
    qtbot.addWidget(ribbon)
    settings: list[bool] = []
    quits: list[bool] = []
    events.settings_requested.connect(lambda: settings.append(True))
    events.quit_requested.connect(lambda: quits.append(True))

    ribbon.settings_button.click()
    ribbon.quit_button.click()

    assert settings == [True]
    assert quits == [True]


def test_notifications_are_visible_plain_text_in_the_ribbon(qtbot) -> None:
    events = EventBus()
    ribbon = LiquidRibbon(events, settings=None)
    qtbot.addWidget(ribbon)
    message = "Protected content <b>must not become markup</b>"

    events.notification.emit(message)

    assert ribbon.notification_label.isVisibleTo(ribbon)
    assert ribbon.notification_label.text() == message
    assert ribbon.notification_label.textFormat() is Qt.TextFormat.PlainText


def test_delta_rerenders_accumulated_answer(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, settings=None)
    qtbot.addWidget(ribbon)
    bus.answer_reset.emit(3)

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
    assert (
        ribbon.sources_label.text() == "Sources: <a href='https://evil.invalid'>source</a> · docs"
    )
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


def test_compact_height_uses_width_aware_label_heights(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, config=OverlayConfig(max_height=360), settings=None)
    qtbot.addWidget(ribbon)
    ribbon.resize(1_888, 180)
    ribbon.set_question("Спроектируйте сервис коротких ссылок")
    ribbon.set_sources(["Context7", "DuckDuckGo"])
    bus.answer_reset.emit(13)
    bus.answer_delta.emit(
        13,
        "1. Уточнить SLA и объём\n"
        "2. Base62 + Redis + sharded DB\n"
        "3. Обсудить hot keys, TTL, analytics",
    )
    ribbon.show()
    qtbot.waitExposed(ribbon)

    body_width = max(320, ribbon.width() - 28 - ribbon.body_layout.spacing())
    question_width = max(120, math.floor(body_width * 105 / 275) - 12)
    answer_width = max(160, math.floor(body_width * 170 / 275) - 12)
    question_layout = ribbon.question_panel.layout()
    assert question_layout is not None
    question_eyebrow = question_layout.itemAt(0).widget()
    assert question_eyebrow is not None
    question_height = (
        question_layout.contentsMargins().top()
        + question_eyebrow.sizeHint().height()
        + question_layout.spacing()
        + ribbon.question_label.heightForWidth(question_width)
        + question_layout.contentsMargins().bottom()
    )
    document = ribbon.answer_browser.document()
    document_layout = document.documentLayout()
    assert document_layout is not None
    sources_height = ribbon.sources_label.heightForWidth(answer_width)
    answer_height = (
        math.ceil(document_layout.documentSize().height())
        + ribbon.answer_layout.spacing()
        + sources_height
    )
    margins = ribbon.content_layout.contentsMargins()
    frame_height = (
        margins.top()
        + margins.bottom()
        + ribbon.header_widget.sizeHint().height()
        + ribbon.content_layout.spacing()
    )
    expected_height = min(
        ribbon.maximumHeight(),
        max(
            ribbon._minimum_expanded_height,
            frame_height + max(question_height, answer_height) + 4,
        ),
    )
    assert ribbon.question_label.sizeHint().height() > ribbon.question_label.heightForWidth(
        question_width
    )
    assert ribbon.sources_label.sizeHint().height() > sources_height

    ribbon._adjust_height(shrink=True)

    assert ribbon.height() == expected_height


def test_unbroken_question_and_sources_cannot_expand_ribbon_past_screen(qtbot) -> None:
    screen = QApplication.primaryScreen()
    assert screen is not None
    available = screen.availableGeometry()
    target_width = min(800, available.width())
    ribbon = LiquidRibbon(EventBus(), settings=None)
    qtbot.addWidget(ribbon)
    ribbon.setGeometry(available.x(), available.y(), target_width, 180)

    long_question = "q" * 2_000
    long_sources = tuple("https://example.invalid/" + (character * 300) for character in ("a", "b"))
    ribbon.set_question(long_question)
    ribbon.set_sources(long_sources)

    assert "…" in ribbon.question_label.text()
    assert "…" in ribbon.sources_label.text()
    assert ribbon.question_text == long_question
    assert ribbon.source_texts == long_sources
    assert ribbon.minimumSizeHint().width() <= target_width
    assert ribbon.width() == target_width
    ribbon.show()
    qtbot.waitExposed(ribbon)
    assert ribbon.width() <= target_width

    ribbon.toggle_collapsed()
    QApplication.processEvents()
    assert ribbon.width() <= target_width
    assert ribbon.collapse_button.isVisible()
    assert ribbon.collapse_button.geometry().right() < ribbon.header_widget.width()

    ribbon.toggle_collapsed()
    QApplication.processEvents()
    assert ribbon.width() <= target_width
    assert ribbon.collapse_button.isVisible()
    assert ribbon.collapse_button.geometry().right() < ribbon.header_widget.width()


def _composite_rgb(
    foreground: tuple[int, int, int],
    alpha: int,
    background: tuple[float, float, float],
) -> tuple[float, float, float]:
    proportion = alpha / 255
    return (
        (foreground[0] * proportion) + (background[0] * (1 - proportion)),
        (foreground[1] * proportion) + (background[1] * (1 - proportion)),
        (foreground[2] * proportion) + (background[2] * (1 - proportion)),
    )


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    def linearize(component: float) -> float:
        normalized = component / 255
        if normalized <= 0.04045:
            return normalized / 12.92
        return ((normalized + 0.055) / 1.055) ** 2.4

    red, green, blue = (linearize(component) for component in rgb)
    return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue)


def _contrast_ratio(
    foreground: tuple[float, float, float],
    background: tuple[float, float, float],
) -> float:
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


def _widget_text_rgb(widget: QWidget) -> tuple[float, float, float]:
    role = (
        QPalette.ColorRole.Text
        if widget.inherits("QTextBrowser")
        else QPalette.ColorRole.WindowText
    )
    color = widget.palette().color(role)
    return float(color.red()), float(color.green()), float(color.blue())


def _window_composite_rgb(
    foreground: tuple[float, float, float],
    background: tuple[float, float, float],
    opacity: float,
) -> tuple[float, float, float]:
    return tuple(
        (foreground[index] * opacity) + (background[index] * (1 - opacity)) for index in range(3)
    )


def test_minimum_opacity_primary_and_secondary_contrast_meets_wcag_aa(qtbot) -> None:
    ribbon = LiquidRibbon(
        EventBus(),
        config=OverlayConfig(opacity=0.2),
        settings=None,
    )
    qtbot.addWidget(ribbon)
    ribbon.show()
    qtbot.waitExposed(ribbon)

    question_eyebrow = next(
        label for label in ribbon.findChildren(QLabel) if label.text() == "ВОПРОС"
    )
    failures: list[str] = []
    for desktop in ((0.0, 0.0, 0.0), (255.0, 255.0, 255.0)):
        surface_backgrounds = (
            _composite_rgb(
                ribbon.surface.gradient_top_rgb,
                ribbon.surface.background_alpha,
                desktop,
            ),
            _composite_rgb(
                ribbon.surface.gradient_bottom_rgb,
                ribbon.surface.background_bottom_alpha,
                desktop,
            ),
        )
        answer_backgrounds = tuple(
            _composite_rgb(_ANSWER_BACKGROUND_RGB, _ANSWER_BACKGROUND_ALPHA, bg)
            for bg in surface_backgrounds
        )
        status_backgrounds = tuple(
            _composite_rgb(_STATUS_CHIP_RGB, _STATUS_CHIP_ALPHA, bg) for bg in surface_backgrounds
        )
        model_backgrounds = tuple(
            _composite_rgb(_MODEL_CHIP_RGB, _MODEL_CHIP_ALPHA, bg) for bg in surface_backgrounds
        )
        for name, widget, backgrounds in (
            ("question eyebrow", question_eyebrow, surface_backgrounds),
            ("sources", ribbon.sources_label, surface_backgrounds),
            ("status", ribbon.status_label, status_backgrounds),
            ("model", ribbon.model_chip, model_backgrounds),
            ("answer", ribbon.answer_browser, answer_backgrounds),
        ):
            rendered_foreground = _window_composite_rgb(
                _widget_text_rgb(widget),
                desktop,
                ribbon.effective_window_opacity,
            )
            rendered_backgrounds = tuple(
                _window_composite_rgb(
                    background,
                    desktop,
                    ribbon.effective_window_opacity,
                )
                for background in backgrounds
            )
            worst_case = min(
                _contrast_ratio(rendered_foreground, background)
                for background in rendered_backgrounds
            )
            if worst_case < 4.5:
                failures.append(f"{name}={worst_case:.2f}:1 over {desktop}")

    assert failures == [], "WCAG AA contrast failures: " + ", ".join(failures)


def test_markdown_answer_uses_readable_size_and_compact_heading(qtbot) -> None:
    ribbon = _rendered_ribbon(qtbot, "## Compact heading\n\nBody")
    browser_font = ribbon.answer_browser.font()
    heading_format = _cursor_at(ribbon.answer_browser, "Compact").charFormat()

    assert browser_font.pixelSize() == 15
    heading_pixels = float(heading_format.property(QTextFormat.Property.FontPixelSize))
    assert 15 <= heading_pixels <= 19


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
    ribbon.set_edit_mode(True)
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
    ribbon.set_edit_mode(True)
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


@pytest.mark.parametrize(
    "preexisting_state",
    [ApplicationState.OFFLINE, ApplicationState.PAUSED],
    ids=["external-offline", "unrelated-paused"],
)
def test_startup_verified_affinity_preserves_preexisting_external_state(
    qtbot,
    preexisting_state: ApplicationState,
) -> None:
    from interview_assistant.app import InterviewApplication

    qt_app = QApplication.instance()
    assert isinstance(qt_app, QApplication)
    verified = AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)
    states = StateMachine()
    states.transition(preexisting_state)
    app = InterviewApplication(
        qt_app,
        EventBus(),
        states,
        overlay_settings=None,
        affinity_applier=Mock(return_value=verified),
        allow_unchecked_start=True,
    )
    qtbot.addWidget(app.ribbon)
    announced_states: list[str] = []
    app.events.state_changed.connect(announced_states.append)

    app.start()
    qtbot.waitExposed(app.ribbon)

    assert app.ribbon.affinity_result is verified
    assert app.states.state is preexisting_state
    assert announced_states == [preexisting_state.value]
    assert app.ribbon.status_label.text() == preexisting_state.value
    app.shutdown()


def test_application_stays_offline_when_capture_exclusion_is_not_verified(qtbot) -> None:
    from interview_assistant.app import InterviewApplication

    qt_app = QApplication.instance()
    assert isinstance(qt_app, QApplication)
    failure = AffinityResult(False, 0x00000001, None)
    app = InterviewApplication(
        qt_app,
        EventBus(),
        StateMachine(),
        overlay_settings=None,
        affinity_applier=Mock(return_value=failure),
        allow_unchecked_start=True,
    )
    qtbot.addWidget(app.ribbon)
    announced_states: list[str] = []
    app.events.state_changed.connect(announced_states.append)

    app.start()
    qtbot.waitExposed(app.ribbon)

    assert app.states.state is ApplicationState.OFFLINE
    assert announced_states == [ApplicationState.OFFLINE.value]
    assert app.ribbon.affinity_result is failure
    assert app.ribbon.status_label.text() == "Capture exclusion unavailable"
    app.events.state_changed.emit(ApplicationState.READY.value)
    assert app.ribbon.status_label.text() == "Capture exclusion unavailable"
    app.shutdown()


def test_startup_reentrant_handle_failure_wins_over_initial_ready(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interview_assistant.app import InterviewApplication

    handles = [101]
    verified = AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)
    failure = AffinityResult(False, 0x00000001, None)
    events = EventBus()
    applier = Mock(side_effect=[verified, failure])
    monkeypatch.setattr(LiquidRibbon, "winId", lambda _ribbon: handles[-1])
    qt_app = QApplication.instance()
    assert isinstance(qt_app, QApplication)
    app = InterviewApplication(
        qt_app,
        events,
        StateMachine(),
        overlay_settings=None,
        affinity_applier=applier,
        allow_unchecked_start=True,
    )
    qtbot.addWidget(app.ribbon)
    announced_states: list[str] = []

    def change_handle_reentrantly(state: str) -> None:
        announced_states.append(state)
        if state == ApplicationState.READY.value and len(handles) == 1:
            handles.append(202)
            app.ribbon.hide()
            app.ribbon.show()

    events.state_changed.connect(change_handle_reentrantly)

    app.start()
    qtbot.waitExposed(app.ribbon)

    assert [entry.args[0] for entry in applier.call_args_list] == [101, 202]
    assert app.ribbon.affinity_result is failure
    assert app.states.state is ApplicationState.OFFLINE
    assert announced_states == [
        ApplicationState.READY.value,
        ApplicationState.OFFLINE.value,
    ]
    app.shutdown()


def test_new_hwnd_affinity_failure_revokes_application_readiness(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interview_assistant.app import InterviewApplication

    handles = [101]
    verified = AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)
    failure = AffinityResult(False, 0x00000001, None)
    applier = Mock(side_effect=[verified, failure])
    monkeypatch.setattr(LiquidRibbon, "winId", lambda _ribbon: handles[-1])
    qt_app = QApplication.instance()
    assert isinstance(qt_app, QApplication)
    app = InterviewApplication(
        qt_app,
        EventBus(),
        StateMachine(),
        overlay_settings=None,
        affinity_applier=applier,
        allow_unchecked_start=True,
    )
    qtbot.addWidget(app.ribbon)
    announced_states: list[str] = []
    app.events.state_changed.connect(announced_states.append)

    app.start()
    qtbot.waitExposed(app.ribbon)
    assert app.states.state is ApplicationState.READY

    handles.append(202)
    app.ribbon.hide()
    app.ribbon.show()
    qtbot.waitExposed(app.ribbon)

    assert [entry.args[0] for entry in applier.call_args_list] == [101, 202]
    assert app.ribbon.affinity_result is failure
    assert app.states.state is ApplicationState.OFFLINE
    assert announced_states == [
        ApplicationState.READY.value,
        ApplicationState.OFFLINE.value,
    ]
    assert app.ribbon.status_label.text() == "Capture exclusion unavailable"
    app.shutdown()


@pytest.mark.parametrize(
    ("applier_error", "expected_error_code"),
    [(OSError(5, "access denied"), 5), (RuntimeError("boom"), 31)],
    ids=["oserror", "generic-exception"],
)
def test_new_hwnd_applier_exception_fails_closed_without_retry(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    applier_error: Exception,
    expected_error_code: int,
) -> None:
    from interview_assistant.app import InterviewApplication

    handles = [101]
    verified = AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)
    failure = AffinityResult(False, None, expected_error_code)
    applier = Mock(side_effect=[verified, applier_error])
    monkeypatch.setattr(LiquidRibbon, "winId", lambda _ribbon: handles[-1])
    qt_app = QApplication.instance()
    assert isinstance(qt_app, QApplication)
    app = InterviewApplication(
        qt_app,
        EventBus(),
        StateMachine(),
        overlay_settings=None,
        affinity_applier=applier,
        allow_unchecked_start=True,
    )
    qtbot.addWidget(app.ribbon)
    app.start()
    qtbot.waitExposed(app.ribbon)
    observed_results: list[AffinityResult] = []
    app.ribbon.affinity_changed.connect(observed_results.append)

    handles.append(202)
    try:
        app.ribbon.showEvent(QShowEvent())
    except Exception as error:
        pytest.fail(f"applier exception escaped the Ribbon boundary: {error}")

    assert app.ribbon.affinity_result == failure
    assert observed_results == [failure]
    assert app.states.state is ApplicationState.OFFLINE
    assert app.ribbon.status_label.text() == "Capture exclusion unavailable"
    app.events.state_changed.emit(ApplicationState.READY.value)
    assert app.ribbon.status_label.text() == "Capture exclusion unavailable"

    app.ribbon.showEvent(QShowEvent())
    assert [entry.args[0] for entry in applier.call_args_list] == [101, 202]
    app.shutdown()


def test_new_hwnd_verified_affinity_restores_application_readiness(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interview_assistant.app import InterviewApplication

    handles = [101]
    failure = AffinityResult(False, None, 5)
    verified = AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)
    applier = Mock(side_effect=[failure, verified])
    monkeypatch.setattr(LiquidRibbon, "winId", lambda _ribbon: handles[-1])
    qt_app = QApplication.instance()
    assert isinstance(qt_app, QApplication)
    app = InterviewApplication(
        qt_app,
        EventBus(),
        StateMachine(),
        overlay_settings=None,
        affinity_applier=applier,
        allow_unchecked_start=True,
    )
    qtbot.addWidget(app.ribbon)
    announced_states: list[str] = []
    app.events.state_changed.connect(announced_states.append)

    app.start()
    qtbot.waitExposed(app.ribbon)
    assert app.states.state is ApplicationState.OFFLINE

    handles.append(202)
    app.ribbon.hide()
    app.ribbon.show()
    qtbot.waitExposed(app.ribbon)

    assert app.ribbon.affinity_result is verified
    assert app.states.state is ApplicationState.READY
    assert announced_states == [
        ApplicationState.OFFLINE.value,
        ApplicationState.READY.value,
    ]
    assert app.ribbon.status_label.text() == ApplicationState.READY.value
    app.shutdown()


def test_startup_affinity_failure_does_not_own_external_offline_state(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interview_assistant.app import InterviewApplication

    handles = [101]
    failure = AffinityResult(False, None, 5)
    verified = AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)
    applier = Mock(side_effect=[failure, verified])
    monkeypatch.setattr(LiquidRibbon, "winId", lambda _ribbon: handles[-1])
    qt_app = QApplication.instance()
    assert isinstance(qt_app, QApplication)
    states = StateMachine()
    states.transition(ApplicationState.OFFLINE)
    app = InterviewApplication(
        qt_app,
        EventBus(),
        states,
        overlay_settings=None,
        affinity_applier=applier,
        allow_unchecked_start=True,
    )
    qtbot.addWidget(app.ribbon)
    announced_states: list[str] = []
    app.events.state_changed.connect(announced_states.append)

    app.start()
    qtbot.waitExposed(app.ribbon)
    assert app.states.state is ApplicationState.OFFLINE

    handles.append(202)
    app.ribbon.hide()
    app.ribbon.show()
    qtbot.waitExposed(app.ribbon)

    assert app.ribbon.affinity_result is verified
    assert app.states.state is ApplicationState.OFFLINE
    assert announced_states == [ApplicationState.OFFLINE.value]
    app.shutdown()


def test_external_offline_does_not_transfer_ownership_to_affinity(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interview_assistant.app import InterviewApplication

    handles = [101]
    verified = AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)
    failure = AffinityResult(False, 0x00000001, None)
    applier = Mock(side_effect=[verified, failure, verified])
    monkeypatch.setattr(LiquidRibbon, "winId", lambda _ribbon: handles[-1])
    qt_app = QApplication.instance()
    assert isinstance(qt_app, QApplication)
    app = InterviewApplication(
        qt_app,
        EventBus(),
        StateMachine(),
        overlay_settings=None,
        affinity_applier=applier,
        allow_unchecked_start=True,
    )
    qtbot.addWidget(app.ribbon)
    announced_states: list[str] = []
    app.events.state_changed.connect(announced_states.append)
    app.start()
    qtbot.waitExposed(app.ribbon)

    app.states.transition(ApplicationState.OFFLINE)
    app.events.state_changed.emit(ApplicationState.OFFLINE.value)
    handles.append(202)
    app.ribbon.hide()
    app.ribbon.show()
    handles.append(303)
    app.ribbon.hide()
    app.ribbon.show()

    assert app.ribbon.affinity_result is verified
    assert app.states.state is ApplicationState.OFFLINE
    assert announced_states == [
        ApplicationState.READY.value,
        ApplicationState.OFFLINE.value,
    ]
    app.shutdown()


def test_external_state_after_affinity_offline_is_not_overwritten_on_success(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interview_assistant.app import InterviewApplication

    handles = [101]
    verified = AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)
    failure = AffinityResult(False, None, 5)
    applier = Mock(side_effect=[verified, failure, verified])
    monkeypatch.setattr(LiquidRibbon, "winId", lambda _ribbon: handles[-1])
    qt_app = QApplication.instance()
    assert isinstance(qt_app, QApplication)
    app = InterviewApplication(
        qt_app,
        EventBus(),
        StateMachine(),
        overlay_settings=None,
        affinity_applier=applier,
        allow_unchecked_start=True,
    )
    qtbot.addWidget(app.ribbon)
    announced_states: list[str] = []
    app.events.state_changed.connect(announced_states.append)
    app.start()
    qtbot.waitExposed(app.ribbon)

    handles.append(202)
    app.ribbon.hide()
    app.ribbon.show()
    app.states.transition(ApplicationState.PAUSED)
    app.events.state_changed.emit(ApplicationState.PAUSED.value)
    handles.append(303)
    app.ribbon.hide()
    app.ribbon.show()

    assert app.ribbon.affinity_result is verified
    assert app.states.state is ApplicationState.PAUSED
    assert app.ribbon.status_label.text() == ApplicationState.PAUSED.value
    assert announced_states == [
        ApplicationState.READY.value,
        ApplicationState.OFFLINE.value,
        ApplicationState.PAUSED.value,
    ]
    app.shutdown()


def test_affinity_recovery_restores_previous_non_offline_state(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interview_assistant.app import InterviewApplication

    handles = [101]
    verified = AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)
    failure = AffinityResult(False, None, 5)
    applier = Mock(side_effect=[verified, failure, verified])
    monkeypatch.setattr(LiquidRibbon, "winId", lambda _ribbon: handles[-1])
    qt_app = QApplication.instance()
    assert isinstance(qt_app, QApplication)
    app = InterviewApplication(
        qt_app,
        EventBus(),
        StateMachine(),
        overlay_settings=None,
        affinity_applier=applier,
        allow_unchecked_start=True,
    )
    qtbot.addWidget(app.ribbon)
    announced_states: list[str] = []
    app.events.state_changed.connect(announced_states.append)
    app.start()
    qtbot.waitExposed(app.ribbon)

    app.states.transition(ApplicationState.LISTENING)
    app.events.state_changed.emit(ApplicationState.LISTENING.value)
    handles.append(202)
    app.ribbon.hide()
    app.ribbon.show()
    handles.append(303)
    app.ribbon.hide()
    app.ribbon.show()

    assert app.ribbon.affinity_result is verified
    assert app.states.state is ApplicationState.LISTENING
    assert announced_states == [
        ApplicationState.READY.value,
        ApplicationState.LISTENING.value,
        ApplicationState.OFFLINE.value,
        ApplicationState.LISTENING.value,
    ]
    app.shutdown()


def test_application_for_test_never_calls_real_user32(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interview_assistant.app import InterviewApplication

    default_applier = Mock(return_value=AffinityResult(False, None, 999))
    monkeypatch.setattr(
        overlay_module,
        "apply_capture_exclusion",
        default_applier,
    )
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)

    app.start()
    qtbot.waitExposed(app.ribbon)

    default_applier.assert_not_called()
    assert app.states.state is ApplicationState.READY
    assert app.ribbon.affinity_result == AffinityResult(
        True,
        WDA_EXCLUDEFROMCAPTURE,
        None,
    )
    app.shutdown()


def test_application_missing_affinity_result_is_non_ready_and_visible(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interview_assistant.app import InterviewApplication

    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    monkeypatch.setattr(app.ribbon, "show", lambda: None)

    app.start()

    assert app.states.state is ApplicationState.OFFLINE
    assert app.ribbon.affinity_result is None
    assert app.ribbon.status_label.text() == "Capture exclusion unavailable"
    app.events.state_changed.emit(ApplicationState.READY.value)
    assert app.ribbon.status_label.text() == "Capture exclusion unavailable"
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
