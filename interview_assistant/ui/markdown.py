from __future__ import annotations

import re

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QFont, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat
from PyQt6.QtWidgets import QTextBrowser

_IMAGE = re.compile(
    r"!\[([^\]\r\n]*)\]\(\s*(?:<[^>\r\n]*>|[^)\r\n]*)\s*\)",
)
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
        super().__init__(*args, **kwargs)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)

    def loadResource(self, resource_type: int, name: QUrl) -> object:
        del resource_type, name
        return None


def _neutralize_images(markdown: str) -> str:
    without_images = _IMAGE.sub(lambda match: match.group(1), markdown)
    return re.sub(r"(?<!\n)\n(?!\n)", "  \n", without_images)


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
        elif block_format.property(QTextFormat.Property.BlockCodeFence) is not None:
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
    scrollbar = browser.verticalScrollBar()
    old_maximum = scrollbar.maximum()
    was_at_bottom = scrollbar.value() >= old_maximum
    relative_position = scrollbar.value() / old_maximum if old_maximum else 0.0

    document = browser.document()
    document.setDefaultStyleSheet(_COMPACT_STYLESHEET)
    document.setMarkdown(_neutralize_images(markdown), _MARKDOWN_FEATURES)
    document.setDefaultStyleSheet(_COMPACT_STYLESHEET)
    _remove_anchors(document)
    _apply_compact_block_formatting(document)

    if not preserve_scroll:
        return
    if was_at_bottom:
        scrollbar.setValue(scrollbar.maximum())
    else:
        scrollbar.setValue(round(relative_position * scrollbar.maximum()))
