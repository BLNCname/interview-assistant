from __future__ import annotations

import re
from dataclasses import dataclass

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QFont, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat
from PyQt6.QtWidgets import QTextBrowser

_IMAGE = re.compile(
    r"(?<!\\)!\[([^\]\r\n]*)\]\(\s*(?:<[^>\r\n]*>|[^)\r\n]*)\s*\)",
)
_REFERENCE_IMAGE = re.compile(r"(?<!\\)!\[([^\]\r\n]*)\]\[[^\]\r\n]*\]")
_SHORTCUT_IMAGE = re.compile(r"(?<!\\)!\[([^\]\r\n]*)\](?![\[(])")
_ESCAPED_IMAGE_LABEL = re.compile(r"\\!\[([^\]\r\n]*)\](?=[\[(])")
_FENCE_OPEN = re.compile(
    r"(?m)^ {0,3}(?P<fence>`{3,}|~{3,})[^\r\n]*(?=\r?$)",
)
_INDENTED_CODE_LINE = re.compile(r"(?m)^(?: {4}|\t)[^\r\n]*(?=\r?$)")
_INLINE_CODE_OPEN = re.compile(r"(?<!`)(`+)(?!`)")
_MARKDOWN_FEATURES = (
    QTextDocument.MarkdownFeature.MarkdownDialectGitHub
    | QTextDocument.MarkdownFeature.MarkdownNoHTML
)
_COMPACT_STYLESHEET = """
body { margin: 0; }
p { margin-top: 0; margin-bottom: 4px; }
h1, h2, h3, h4, h5, h6 { margin-top: 6px; margin-bottom: 3px; }
ul, ol { margin-top: 2px; margin-bottom: 4px; }
pre { margin-top: 3px; margin-bottom: 5px; }
code, pre { font-family: "Cascadia Mono", "Consolas", monospace; }
"""


class SafeMarkdownBrowser(QTextBrowser):
    """A text browser that never resolves model-provided resources."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._resource_requests: list[tuple[int, QUrl]] = []
        super().__init__(*args, **kwargs)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)

    @property
    def resource_requests(self) -> tuple[tuple[int, QUrl], ...]:
        return tuple((resource_type, QUrl(name)) for resource_type, name in self._resource_requests)

    def loadResource(self, resource_type: int, name: QUrl) -> object:
        self._resource_requests.append((resource_type, QUrl(name)))
        return None


@dataclass(frozen=True)
class ScrollState:
    was_at_bottom: bool
    relative_position: float


def capture_scroll_state(browser: QTextBrowser) -> ScrollState:
    scrollbar = browser.verticalScrollBar()
    maximum = scrollbar.maximum()
    return ScrollState(
        was_at_bottom=scrollbar.value() >= maximum,
        relative_position=scrollbar.value() / maximum if maximum else 0.0,
    )


def restore_scroll_state(browser: QTextBrowser, state: ScrollState) -> None:
    scrollbar = browser.verticalScrollBar()
    if state.was_at_bottom:
        scrollbar.setValue(scrollbar.maximum())
    else:
        scrollbar.setValue(round(state.relative_position * scrollbar.maximum()))


def _protect_literal(
    literal: str,
    marker: str,
    protected: list[tuple[str, str]],
) -> str:
    placeholder = f"{marker}{len(protected)}\ue001"
    protected.append((placeholder, literal))
    return placeholder


def _protect_fenced_blocks(
    markdown: str,
    marker: str,
    protected: list[tuple[str, str]],
) -> str:
    parts: list[str] = []
    position = 0
    while opener := _FENCE_OPEN.search(markdown, position):
        parts.append(markdown[position : opener.start()])
        fence = opener.group("fence")
        closer_pattern = re.compile(
            rf"(?m)^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}"
            r"[ \t]*(?=\r?$)",
        )
        closer = closer_pattern.search(markdown, opener.end())
        end = closer.end() if closer is not None else len(markdown)
        parts.append(_protect_literal(markdown[opener.start() : end], marker, protected))
        position = end
    parts.append(markdown[position:])
    return "".join(parts)


def _protect_pattern_matches(
    markdown: str,
    pattern: re.Pattern[str],
    marker: str,
    protected: list[tuple[str, str]],
) -> str:
    return pattern.sub(
        lambda match: _protect_literal(match.group(0), marker, protected),
        markdown,
    )


def _protect_inline_code(
    markdown: str,
    marker: str,
    protected: list[tuple[str, str]],
) -> str:
    position = 0
    while opener := _INLINE_CODE_OPEN.search(markdown, position):
        delimiter = opener.group(1)
        closer_pattern = re.compile(rf"(?<!`){re.escape(delimiter)}(?!`)")
        closer = closer_pattern.search(markdown, opener.end())
        if closer is None:
            position = opener.end()
            continue
        placeholder = _protect_literal(
            markdown[opener.start() : closer.end()],
            marker,
            protected,
        )
        markdown = markdown[: opener.start()] + placeholder + markdown[closer.end() :]
        position = opener.start() + len(placeholder)
    return markdown


def _neutralize_images(markdown: str) -> str:
    marker = "\ue000codex-literal-"
    while marker in markdown:
        marker += "-"
    protected: list[tuple[str, str]] = []
    prose = _protect_fenced_blocks(markdown, marker, protected)
    prose = _protect_pattern_matches(prose, _INDENTED_CODE_LINE, marker, protected)
    prose = _protect_inline_code(prose, marker, protected)
    prose = _ESCAPED_IMAGE_LABEL.sub(
        lambda match: f"\\!\\[{match.group(1)}\\]",
        prose,
    )
    prose = _IMAGE.sub(lambda match: match.group(1), prose)
    prose = _REFERENCE_IMAGE.sub(lambda match: match.group(1), prose)
    prose = _SHORTCUT_IMAGE.sub(lambda match: match.group(1), prose)
    for placeholder, literal in protected:
        prose = prose.replace(placeholder, literal)
    return prose


def _remove_anchors(document: QTextDocument) -> None:
    cursor = QTextCursor(document)
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    while not cursor.atEnd():
        cursor.movePosition(
            QTextCursor.MoveOperation.NextCharacter,
            QTextCursor.MoveMode.KeepAnchor,
        )
        char_format = cursor.charFormat()
        if char_format.isAnchor() or char_format.anchorHref():
            char_format.setAnchor(False)
            char_format.setAnchorHref("")
            cursor.setCharFormat(char_format)
        cursor.clearSelection()


def _apply_compact_block_formatting(document: QTextDocument) -> None:
    block = document.begin()
    while block.isValid():
        block_format = block.blockFormat()
        if block_format.headingLevel() > 0:
            block_format.setTopMargin(6)
            block_format.setBottomMargin(3)
            cursor = QTextCursor(block)
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            heading_format = QTextCharFormat()
            heading_format.setProperty(QTextFormat.Property.FontPixelSize, 18)
            heading_format.setFontWeight(QFont.Weight.DemiBold.value)
            cursor.mergeCharFormat(heading_format)
        elif block_format.property(QTextFormat.Property.BlockCodeLanguage) is not None:
            block_format.setTopMargin(3)
            block_format.setBottomMargin(5)
            cursor = QTextCursor(block)
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            code_format = QTextCharFormat()
            code_format.setFontFamilies(["Cascadia Mono", "Consolas", "monospace"])
            code_format.setFontFixedPitch(True)
            cursor.mergeCharFormat(code_format)
        elif block_format.marker() != block_format.MarkerType.NoMarker:
            block_format.setTopMargin(1)
            block_format.setBottomMargin(1)
        else:
            block_format.setTopMargin(0)
            block_format.setBottomMargin(4)
        QTextCursor(block).setBlockFormat(block_format)
        block = block.next()


def render_safe_markdown(
    browser: QTextBrowser,
    markdown: str,
    *,
    preserve_scroll: bool = True,
) -> None:
    """Render an accumulated Markdown buffer without activating its resources."""

    browser.setOpenLinks(False)
    browser.setOpenExternalLinks(False)
    scroll_state = capture_scroll_state(browser)

    document = browser.document()
    document.setDefaultStyleSheet(_COMPACT_STYLESHEET)
    document.setMarkdown(_neutralize_images(markdown), _MARKDOWN_FEATURES)
    document.setDefaultStyleSheet(_COMPACT_STYLESHEET)
    _remove_anchors(document)
    _apply_compact_block_formatting(document)

    if not preserve_scroll:
        return
    restore_scroll_state(browser, scroll_state)
