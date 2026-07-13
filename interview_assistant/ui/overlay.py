from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator

from PyQt6.QtCore import QByteArray, QPoint, QRect, QRectF, QSettings, QSize, Qt
from PyQt6.QtGui import (
    QColor,
    QCloseEvent,
    QFont,
    QIcon,
    QLinearGradient,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPen,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QStyle,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.config import OverlayConfig
from interview_assistant.events import EventBus

_MARKDOWN_TOKEN = re.compile(r"\*\*([^*\n]+)\*\*|`([^`\n]+)`")


class _RibbonSurface(QWidget):
    corner_radius = 18

    def __init__(self, opacity: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # The glass remains dark enough to back high-contrast text even at the
        # lowest user setting; opacity still controls the strength of the tint.
        self.background_alpha = max(180, round(166 + (76 * opacity)))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, event: QPaintEvent | None) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self.corner_radius, self.corner_radius)

        background = QLinearGradient(rect.topLeft(), rect.bottomRight())
        background.setColorAt(0.0, QColor(30, 41, 59, self.background_alpha))
        background.setColorAt(1.0, QColor(15, 23, 42, max(180, self.background_alpha - 22)))
        painter.fillPath(path, background)

        painter.setPen(QPen(QColor(255, 255, 255, 56), 1.0))
        painter.drawPath(path)

        edge = QLinearGradient(rect.topLeft(), rect.topRight())
        edge.setColorAt(0.0, QColor(125, 211, 252, 150))
        edge.setColorAt(0.38, QColor(125, 211, 252, 0))
        edge.setColorAt(0.72, QColor(167, 139, 250, 0))
        edge.setColorAt(1.0, QColor(167, 139, 250, 125))
        painter.setPen(QPen(edge, 1.0))
        painter.drawPath(path)


class _StatusDot(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor("#34d399")
        self.setFixedSize(10, 10)

    def set_state(self, state: str) -> None:
        normalized = state.casefold()
        if "offline" in normalized or "protected" in normalized:
            self._color = QColor("#fb7185")
        elif "recover" in normalized or "search" in normalized:
            self._color = QColor("#fbbf24")
        elif "paused" in normalized:
            self._color = QColor("#94a3b8")
        else:
            self._color = QColor("#34d399")
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(10, 10)

    def paintEvent(self, event: QPaintEvent | None) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(QRectF(1.5, 1.5, 7.0, 7.0))


class LiquidRibbon(QMainWindow):
    GEOMETRY_KEY = "overlay/geometry"
    collapsed_height = 48
    resize_margin = 8

    def __init__(
        self,
        events: EventBus,
        *,
        config: OverlayConfig | None = None,
        settings: QSettings | None = None,
    ) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(None, flags)
        self._events = events
        self._config = config or OverlayConfig()
        self._settings = settings
        self._active_request_id: int | None = None
        self._expanded_height = min(180, self._config.max_height)
        self._minimum_expanded_height = min(120, self._config.max_height)
        self.is_collapsed = False
        self.answer_text = ""
        self._fallback_action: str | None = None
        self._fallback_press_global: QPoint | None = None
        self._fallback_press_geometry: QRect | None = None
        self._fallback_resize_edges = Qt.Edge(0)

        self.setWindowTitle("Interview Assistant")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumHeight(self._minimum_expanded_height)
        self.setMaximumHeight(self._config.max_height)
        self._build_content()
        self._set_default_geometry()
        self._restore_geometry()

        # All event connections are established before callers can show the window.
        events.state_changed.connect(self.show_state)
        events.answer_reset.connect(self._reset_answer)
        events.answer_delta.connect(self.append_delta)

    def _build_content(self) -> None:
        self.surface = _RibbonSurface(self._config.opacity, self)
        self.setCentralWidget(self.surface)
        self.content_layout = QVBoxLayout(self.surface)
        self.content_layout.setContentsMargins(14, 12, 14, 12)
        self.content_layout.setSpacing(8)

        font = QFont()
        font.setFamilies(["Segoe UI", "Inter", "sans-serif"])
        font.setPixelSize(13)
        self.surface.setFont(font)

        self.header_widget = QWidget(self.surface)
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(7)
        self.status_dot = _StatusDot(self.header_widget)
        header_layout.addWidget(self.status_dot)

        self.status_label = QLabel("Starting", self.header_widget)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setStyleSheet(
            "QLabel { color: #cbd5e1; background: rgba(255, 255, 255, 14); "
            "border: 1px solid rgba(255, 255, 255, 22); border-radius: 9px; "
            "padding: 2px 7px; font-size: 12px; }"
        )
        header_layout.addWidget(self.status_label)

        self.model_chip = QLabel("Local model", self.header_widget)
        self.model_chip.setTextFormat(Qt.TextFormat.PlainText)
        self.model_chip.setStyleSheet(
            "QLabel { color: #bae6fd; background: rgba(125, 211, 252, 31); "
            "border: 1px solid rgba(125, 211, 252, 46); border-radius: 9px; "
            "padding: 2px 7px; }"
        )
        header_layout.addWidget(self.model_chip)
        header_layout.addStretch(1)

        self.collapse_button = QToolButton(self.header_widget)
        self.collapse_button.setText("")
        self.collapse_button.setToolTip("Collapse")
        self.collapse_button.setAutoRaise(True)
        self.collapse_button.setIcon(self._standard_icon(QStyle.StandardPixmap.SP_ArrowUp))
        self.collapse_button.setStyleSheet(
            "QToolButton { color: #dbeafe; border: 0; padding: 3px; } "
            "QToolButton:hover { background: rgba(255, 255, 255, 18); border-radius: 5px; }"
        )
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        header_layout.addWidget(self.collapse_button)
        self.content_layout.addWidget(self.header_widget)

        self.body_widget = QWidget(self.surface)
        self.body_layout = QHBoxLayout(self.body_widget)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(14)

        self.question_panel = QWidget(self.body_widget)
        question_layout = QVBoxLayout(self.question_panel)
        question_layout.setContentsMargins(0, 0, 0, 0)
        question_layout.setSpacing(4)
        question_eyebrow = QLabel("ВОПРОС", self.question_panel)
        question_eyebrow.setTextFormat(Qt.TextFormat.PlainText)
        question_eyebrow.setStyleSheet("color: #94a3b8; font-size: 11px;")
        question_layout.addWidget(question_eyebrow)
        self.question_label = QLabel("Ожидание вопроса…", self.question_panel)
        self.question_label.setTextFormat(Qt.TextFormat.PlainText)
        self.question_label.setWordWrap(True)
        self.question_label.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self.question_label.setStyleSheet(
            "color: #ffffff; font-size: 14px; font-weight: 600;"
        )
        question_layout.addWidget(self.question_label, 1)
        self.body_layout.addWidget(self.question_panel)

        answer_panel = QWidget(self.body_widget)
        self.answer_layout = QVBoxLayout(answer_panel)
        self.answer_layout.setContentsMargins(0, 0, 0, 0)
        self.answer_layout.setSpacing(5)
        self.answer_browser = QTextBrowser(answer_panel)
        self.answer_browser.setAcceptRichText(False)
        self.answer_browser.setOpenExternalLinks(False)
        self.answer_browser.setOpenLinks(False)
        self.answer_browser.setReadOnly(True)
        self.answer_browser.setUndoRedoEnabled(False)
        self.answer_browser.setFrameShape(QFrame.Shape.NoFrame)
        self.answer_browser.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.answer_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.answer_browser.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.answer_browser.setMinimumHeight(48)
        self.answer_browser.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.answer_browser.setStyleSheet(
            "QTextBrowser { color: #dbeafe; background: rgba(2, 6, 23, 35); "
            "border: 0; border-radius: 8px; padding: 2px 5px; font-size: 13px; }"
            "QScrollBar:vertical { width: 7px; background: transparent; }"
            "QScrollBar::handle:vertical { background: rgba(148, 163, 184, 90); "
            "border-radius: 3px; min-height: 18px; }"
        )
        self.answer_layout.addWidget(self.answer_browser, 1)

        self.sources_label = QLabel("Sources: —", answer_panel)
        self.sources_label.setTextFormat(Qt.TextFormat.PlainText)
        self.sources_label.setStyleSheet("color: #94a3b8; font-size: 11px;")
        self.sources_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.answer_layout.addWidget(self.sources_label)
        self.body_layout.addWidget(answer_panel)
        self.body_layout.setStretch(0, 105)
        self.body_layout.setStretch(1, 170)
        self.content_layout.addWidget(self.body_widget, 1)

    def _standard_icon(self, pixmap: QStyle.StandardPixmap) -> QIcon:
        style = self.style()
        assert style is not None
        return style.standardIcon(pixmap)

    def _set_default_geometry(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(900, self._expanded_height)
            return
        available = screen.availableGeometry()
        width = max(480, available.width() - 32)
        width = min(width, available.width())
        self.setGeometry(
            available.x() + max(0, (available.width() - width) // 2),
            available.y() + 16,
            width,
            self._expanded_height,
        )

    def _restore_geometry(self) -> None:
        if self._settings is None:
            return
        value = self._settings.value(self.GEOMETRY_KEY)
        if value is None:
            return
        default_geometry = self.geometry()
        if isinstance(value, QByteArray):
            encoded = value
        elif isinstance(value, bytes):
            encoded = QByteArray(value)
        else:
            self.setGeometry(default_geometry)
            return
        if not self.restoreGeometry(encoded) or not self._geometry_is_sensible(self.geometry()):
            self.setGeometry(default_geometry)

    def _geometry_is_sensible(self, geometry: QRect) -> bool:
        if geometry.width() < 360 or geometry.height() < self.collapsed_height:
            return False
        if geometry.height() > self._config.max_height:
            return False
        for screen in QApplication.screens():
            visible = geometry.intersected(screen.availableGeometry())
            if visible.width() >= 80 and visible.height() >= 32:
                return True
        return False

    def show_state(self, state: str) -> None:
        self.status_label.setText(state)
        self.status_dot.set_state(state)

    def set_question(self, question: str) -> None:
        self.question_label.setText(question)
        self._adjust_height()

    def set_sources(self, sources: Iterable[str]) -> None:
        safe_sources = [str(source) for source in sources]
        value = " · ".join(safe_sources) if safe_sources else "—"
        self.sources_label.setText(f"Sources: {value}")
        self._adjust_height()

    def _reset_answer(self, request_id: int) -> None:
        if self._active_request_id is not None and request_id < self._active_request_id:
            return
        self._active_request_id = request_id
        self.answer_text = ""
        self.answer_browser.clear()
        self._adjust_height(shrink=True)

    def append_delta(self, request_id: int, delta: str) -> None:
        if request_id != self._active_request_id:
            return
        self.answer_text += delta
        cursor = self.answer_browser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for text, char_format in self._safe_markdown(delta):
            cursor.insertText(text, char_format)
        self.answer_browser.setTextCursor(cursor)
        self._adjust_height()
        self.answer_browser.ensureCursorVisible()
        scrollbar = self.answer_browser.verticalScrollBar()
        assert scrollbar is not None
        scrollbar.setValue(scrollbar.maximum())

    @staticmethod
    def _safe_markdown(delta: str) -> Iterator[tuple[str, QTextCharFormat]]:
        plain_format = QTextCharFormat()
        offset = 0
        for match in _MARKDOWN_TOKEN.finditer(delta):
            if match.start() > offset:
                yield delta[offset : match.start()], plain_format
            formatted = QTextCharFormat()
            if match.group(1) is not None:
                formatted.setFontWeight(QFont.Weight.Bold.value)
                yield match.group(1), formatted
            else:
                formatted.setFontFamilies(["Cascadia Mono", "Consolas", "monospace"])
                formatted.setFontFixedPitch(True)
                formatted.setBackground(QColor(255, 255, 255, 18))
                yield match.group(2), formatted
            offset = match.end()
        if offset < len(delta):
            yield delta[offset:], plain_format

    def toggle_collapsed(self) -> None:
        if self.is_collapsed:
            self.is_collapsed = False
            self.setMinimumHeight(self._minimum_expanded_height)
            self.setMaximumHeight(self._config.max_height)
            self.body_widget.show()
            restored = max(self._minimum_expanded_height, self._expanded_height)
            self.resize(self.width(), min(self._config.max_height, restored))
            self.collapse_button.setIcon(
                self._standard_icon(QStyle.StandardPixmap.SP_ArrowUp)
            )
            self.collapse_button.setToolTip("Collapse")
            self._adjust_height()
            return

        self._expanded_height = max(self._minimum_expanded_height, self.height())
        self.is_collapsed = True
        self.body_widget.hide()
        self.setMinimumHeight(self.collapsed_height)
        self.setMaximumHeight(self.collapsed_height)
        self.resize(self.width(), self.collapsed_height)
        self.collapse_button.setIcon(
            self._standard_icon(QStyle.StandardPixmap.SP_ArrowDown)
        )
        self.collapse_button.setToolTip("Expand")

    def _adjust_height(self, *, shrink: bool = False) -> None:
        if self.is_collapsed:
            return
        document = self.answer_browser.document()
        viewport = self.answer_browser.viewport()
        assert document is not None
        assert viewport is not None
        body_width = max(320, self.width() - 28 - self.body_layout.spacing())
        estimated_answer_width = math.floor(body_width * 170 / 275) - 12
        text_width = max(160, viewport.width() - 4, estimated_answer_width)
        document.setTextWidth(text_width)
        layout = document.documentLayout()
        assert layout is not None
        document_height = math.ceil(layout.documentSize().height())
        margins = self.content_layout.contentsMargins()
        frame_height = margins.top() + margins.bottom()
        frame_height += self.header_widget.sizeHint().height()
        frame_height += self.content_layout.spacing()
        answer_height = document_height + self.sources_label.sizeHint().height()
        answer_height += self.answer_layout.spacing()
        body_height = max(self.question_panel.sizeHint().height(), answer_height)
        target = min(
            self._config.max_height,
            max(self._minimum_expanded_height, frame_height + body_height + 4),
        )
        if shrink or target > self.height():
            self.resize(self.width(), target)

    def _resize_edges_at(self, position: QPoint) -> Qt.Edge:
        edges = Qt.Edge(0)
        if position.x() <= self.resize_margin:
            edges |= Qt.Edge.LeftEdge
        elif position.x() >= self.width() - self.resize_margin:
            edges |= Qt.Edge.RightEdge
        if position.y() <= self.resize_margin:
            edges |= Qt.Edge.TopEdge
        elif position.y() >= self.height() - self.resize_margin:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _try_start_system_move(self) -> bool:
        handle = self.windowHandle()
        if handle is None:
            return False
        try:
            return bool(handle.startSystemMove())
        except (AttributeError, RuntimeError):
            return False

    def _try_start_system_resize(self, edges: Qt.Edge) -> bool:
        handle = self.windowHandle()
        if handle is None:
            return False
        try:
            return bool(handle.startSystemResize(edges))
        except (AttributeError, RuntimeError):
            return False

    def _start_fallback(self, action: str, event: QMouseEvent, edges: Qt.Edge) -> None:
        self._fallback_action = action
        self._fallback_press_global = event.globalPosition().toPoint()
        self._fallback_press_geometry = self.geometry()
        self._fallback_resize_edges = edges
        event.accept()

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            super().mousePressEvent(event)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        edges = self._resize_edges_at(event.position().toPoint())
        if edges:
            if self._try_start_system_resize(edges):
                event.accept()
                return
            self._start_fallback("resize", event, edges)
            return
        if event.position().y() <= 48:
            if self._try_start_system_move():
                event.accept()
                return
            self._start_fallback("move", event, Qt.Edge(0))
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            super().mouseMoveEvent(event)
            return
        if (
            self._fallback_action is None
            or self._fallback_press_global is None
            or self._fallback_press_geometry is None
            or not (event.buttons() & Qt.MouseButton.LeftButton)
        ):
            super().mouseMoveEvent(event)
            return
        delta = event.globalPosition().toPoint() - self._fallback_press_global
        if self._fallback_action == "move":
            self.move(self._fallback_press_geometry.topLeft() + delta)
            event.accept()
            return
        self._apply_fallback_resize(delta)
        event.accept()

    def _apply_fallback_resize(self, delta: QPoint) -> None:
        original = self._fallback_press_geometry
        if original is None:
            return
        left = original.left()
        top = original.top()
        right = original.right()
        bottom = original.bottom()
        minimum_width = min(480, original.width())
        minimum_height = self.collapsed_height if self.is_collapsed else self._minimum_expanded_height

        if self._fallback_resize_edges & Qt.Edge.LeftEdge:
            left = min(original.left() + delta.x(), right - minimum_width + 1)
        elif self._fallback_resize_edges & Qt.Edge.RightEdge:
            right = max(original.right() + delta.x(), left + minimum_width - 1)

        if self._fallback_resize_edges & Qt.Edge.TopEdge:
            proposed_top = min(original.top() + delta.y(), bottom - minimum_height + 1)
            top = max(proposed_top, bottom - self._config.max_height + 1)
        elif self._fallback_resize_edges & Qt.Edge.BottomEdge:
            proposed_bottom = max(original.bottom() + delta.y(), top + minimum_height - 1)
            bottom = min(proposed_bottom, top + self._config.max_height - 1)

        self.setGeometry(QRect(QPoint(left, top), QPoint(right, bottom)))

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        self._fallback_action = None
        self._fallback_press_global = None
        self._fallback_press_geometry = None
        self._fallback_resize_edges = Qt.Edge(0)
        super().mouseReleaseEvent(event)

    def closeEvent(self, event: QCloseEvent | None) -> None:
        if self._settings is not None:
            self._settings.setValue(self.GEOMETRY_KEY, self.saveGeometry())
            self._settings.sync()
        super().closeEvent(event)
