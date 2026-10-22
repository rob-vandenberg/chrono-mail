import re
from typing import override
from urllib.parse import urlsplit

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QAction, QFont, QKeySequence, QTextCharFormat, QTextListFormat
from PySide6.QtWidgets import QInputDialog, QMessageBox, QTextEdit, QToolBar, QVBoxLayout, QWidget

# --- Version ------------------------------------------------------------
__version__ = "richeditor.py 0.0.78"

# --- Version history ----------------------------------------------------
# v0.0.78: Composer: new look (Send in blue on top, rounded fields, flat toolbar) and rich text is the default for new messages
# v0.0.24: Initial rich text editor for the composer

ALLOWED_LINK_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})
_IMAGE: re.Pattern[str] = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_QT_META: re.Pattern[str] = re.compile(r"<meta\s+name=\"qrichtext\"[^>]*>\s*", re.IGNORECASE)
_BODY_STYLE: re.Pattern[str] = re.compile(r"<body\s+style=\"[^\"]*\"\s*>", re.IGNORECASE)
_HREF: re.Pattern[str] = re.compile(r"href=\"([^\"]*)\"", re.IGNORECASE)
_FORMATTING: re.Pattern[str] = re.compile(r"font-weight:\s*[6-9]00|font-style:\s*italic|text-decoration:\s*underline|<ul|<ol|<a\s", re.IGNORECASE)


def safe_link(address: str) -> bool:
    """Only web and mail links may go into a message: never javascript:, file: and the like."""
    text = address.strip()
    try:
        scheme = urlsplit(text).scheme.lower()
    except ValueError:
        return False
    return scheme in ALLOWED_LINK_SCHEMES and len(text) <= 2000 and not any(ord(character) < 32 for character in text)


def clean_html(html: str) -> str:
    """The HTML part of an outgoing message from the editor's HTML: no images (nothing is fetched from the internet when
    it is read), only web and mail links, and no editor-specific font settings on the page, so that the reader's own apply."""
    cleaned = _IMAGE.sub("", html)
    cleaned = _QT_META.sub("", cleaned)
    cleaned = _BODY_STYLE.sub("<body>", cleaned)
    return _HREF.sub(lambda match: match.group(0) if safe_link(match.group(1)) else 'href=""', cleaned)


class _Editor(QTextEdit):
    """A text editor that takes pasted text and formatting, but no pictures."""

    @override
    def canInsertFromMimeData(self, source: QMimeData) -> bool:  # noqa: N802 - Qt name
        return source.hasHtml() or source.hasText()

    @override
    def insertFromMimeData(self, source: QMimeData) -> None:  # noqa: N802 - Qt name
        if source.hasHtml():
            plain = QMimeData()
            plain.setHtml(_IMAGE.sub("", source.html()))
            super().insertFromMimeData(plain)
        elif source.hasText():
            self.insertPlainText(source.text())


class RichEditor(QWidget):
    """A small word processor: bold, italic, underline, lists and links. The message is sent as text and HTML."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.edit = _Editor(self)
        self.edit.setAcceptRichText(True)
        self.toolbar = QToolBar(self)
        self.toolbar.setObjectName("richToolbar")
        self.bold = self._action("B", "Bold", "Ctrl+B", checkable=True)
        self.italic = self._action("I", "Italic", "Ctrl+I", checkable=True)
        self.underline = self._action("U", "Underline", "Ctrl+U", checkable=True)
        self.bullets = self._action("• List", "Bulleted list", None)
        self.numbers = self._action("1. List", "Numbered list", None)
        self.link = self._action("Link…", "Make the selected text a link", "Ctrl+K")
        self.plain = self._action("Clear", "Remove the formatting of the selected text", None)
        font = QFont(self.bold.font())
        font.setBold(True)
        self.bold.setFont(font)
        font = QFont(self.italic.font())
        font.setItalic(True)
        self.italic.setFont(font)
        font = QFont(self.underline.font())
        font.setUnderline(True)
        self.underline.setFont(font)
        for action in (self.bold, self.italic, self.underline):
            self.toolbar.addAction(action)
        self.toolbar.addSeparator()
        for action in (self.bullets, self.numbers, self.link, self.plain):
            self.toolbar.addAction(action)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.edit, 1)
        self.bold.triggered.connect(self._toggle_bold)
        self.italic.triggered.connect(self._toggle_italic)
        self.underline.triggered.connect(self._toggle_underline)
        self.bullets.triggered.connect(self._bullets)
        self.numbers.triggered.connect(self._numbers)
        self.link.triggered.connect(self._ask_link)
        self.plain.triggered.connect(self._clear_formatting)
        self.edit.currentCharFormatChanged.connect(self._follow_format)

    def _action(self, text: str, tip: str, shortcut: str | None, checkable: bool = False) -> QAction:
        action = QAction(text, self)
        action.setToolTip(tip)
        action.setCheckable(checkable)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        return action

    # --- content ------------------------------------------------------------------------------------------

    def toPlainText(self) -> str:  # noqa: N802 - the name of QTextEdit's method, so that it can stand in for the plain editor
        return self.edit.toPlainText()

    def setPlainText(self, text: str) -> None:  # noqa: N802
        self.edit.setPlainText(text)

    def set_html(self, html: str) -> None:
        self.edit.setHtml(html)

    def outgoing_html(self) -> str:
        return clean_html(self.edit.toHtml())

    def snapshot(self) -> str:
        """Everything that can differ between two states of the editor (the text and its formatting)."""
        return self.edit.toHtml()

    def has_formatting(self) -> bool:
        return _FORMATTING.search(self.edit.toHtml()) is not None

    # --- formatting ---------------------------------------------------------------------------------------

    def _merge(self, change: QTextCharFormat) -> None:
        self.edit.mergeCurrentCharFormat(change)  # the selection, or what is typed next

    def _toggle_bold(self, on: bool) -> None:
        change = QTextCharFormat()
        change.setFontWeight(QFont.Weight.Bold if on else QFont.Weight.Normal)
        self._merge(change)

    def _toggle_italic(self, on: bool) -> None:
        change = QTextCharFormat()
        change.setFontItalic(on)
        self._merge(change)

    def _toggle_underline(self, on: bool) -> None:
        change = QTextCharFormat()
        change.setFontUnderline(on)
        self._merge(change)

    def _list(self, style: QTextListFormat.Style) -> None:
        cursor = self.edit.textCursor()
        current = cursor.currentList()
        if current is not None and current.format().style() == style:
            block_format = cursor.blockFormat()
            block_format.setIndent(0)
            cursor.setBlockFormat(block_format)
            current.remove(cursor.block())  # a second click on the same list button ends the list
            return
        list_format = QTextListFormat()
        list_format.setStyle(style)
        cursor.createList(list_format)

    def _bullets(self) -> None:
        self._list(QTextListFormat.Style.ListDisc)

    def _numbers(self) -> None:
        self._list(QTextListFormat.Style.ListDecimal)

    def _clear_formatting(self) -> None:
        cursor = self.edit.textCursor()
        cursor.setCharFormat(QTextCharFormat())
        self.edit.setTextCursor(cursor)

    def _follow_format(self, text_format: QTextCharFormat) -> None:
        self.bold.setChecked(text_format.fontWeight() >= QFont.Weight.Bold)
        self.italic.setChecked(text_format.fontItalic())
        self.underline.setChecked(text_format.fontUnderline())

    def _ask_link(self) -> None:
        address, ok = QInputDialog.getText(self, "Link", "Web or mail address (for example https://example.org):")
        if ok and address.strip():
            self.make_link(address.strip())

    def make_link(self, address: str) -> bool:
        """Turn the selected text (or the address itself, with nothing selected) into a link. False for an unsafe address."""
        if not safe_link(address):
            QMessageBox.warning(self, "Link", "Only web addresses (http, https) and mail addresses (mailto) can be used as a link.")
            return False
        cursor = self.edit.textCursor()
        link_format = QTextCharFormat()
        link_format.setAnchor(True)
        link_format.setAnchorHref(address)
        link_format.setFontUnderline(True)
        if cursor.hasSelection():
            cursor.mergeCharFormat(link_format)
        else:
            cursor.insertText(address, link_format)
            cursor.insertText(" ", QTextCharFormat())
        self.edit.setTextCursor(cursor)
        return True
