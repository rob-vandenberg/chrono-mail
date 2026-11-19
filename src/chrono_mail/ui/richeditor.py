from html import unescape
import mimetypes
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import override
from urllib.parse import urlsplit
from uuid import uuid4

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QFont, QResizeEvent, QImage, QImageReader, QImageWriter, QKeySequence, QTextCharFormat, QTextDocument, QTextImageFormat, QTextList, QTextListFormat
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMenu, QMessageBox, QPlainTextEdit, QSizePolicy, QTextEdit, QToolBar, QToolButton, QVBoxLayout, QWidget

from chrono_mail.mime.pictures import MAXIMUM_PICTURE_BYTES, data_address, pictures_out_of
from chrono_mail.ui.fontstyle import FontStyle
from chrono_mail.ui.icons import theme_icon

# --- Version ------------------------------------------------------------
__version__ = "richeditor.py 4.6.640"

# --- Version history ----------------------------------------------------
# v4.6.640: Font Style (the A) after U: the font, size and colours of the selected text; a font, size or colour counts as formatting (D-363)
# v4.6.639: The bar of the reference client: B, I, U and the lists as icons, on the right the three dots with Cut, Copy, Paste, Remove
#           Format, Undo, Redo, Outdent, Indent, Insert Link and Insert Image; what only rich text can do can be greyed (D-363)
# v4.6.634: PlainEditor: in plain text a dropped file and a pasted picture become attachments, never a path or text typed (D-353)
# v4.6.631: with_styles_inside writes an HTML text's own style rules onto its paragraphs and text, so they reach nothing else (D-360)
# v4.4.623: The HTML sent carries the default font and its size (D-354)
# v4.4.622: pictures_in_text gives each picture with its address, so the quoted ones can be told apart (D-353)
# v4.4.621: Pictures in the text: dragged in, pasted or chosen with Picture…, shown no wider than the text, sent unaltered as embedded
#           pictures; a dropped file that is not a picture is attached, never its path typed (D-353)
# v0.0.78: Composer: new look (Send in blue on top, rounded fields, flat toolbar) and rich text is the default for new messages
# v0.0.24: Initial rich text editor for the composer

ALLOWED_LINK_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})
_IMAGE: re.Pattern[str] = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_KEPT_IMAGE: re.Pattern[str] = re.compile(r"""<img\b[^>]*?\bsrc=["'](?:data:image/|cid:picture-)""", re.IGNORECASE)  # pictures that travel inside the message
_OWN_PICTURE: re.Pattern[str] = re.compile(r"""(\bsrc=["'])(cid:picture-[0-9a-f]+@chrono-mail)(["'])""", re.IGNORECASE)
PICTURE_MARK: str = "\ufffc"  # how a picture stands in the editor's plain text
PICTURE_FILTER: str = "Pictures (*.png *.jpg *.jpeg *.gif *.bmp *.webp)"
_QT_META: re.Pattern[str] = re.compile(r"<meta\s+name=\"qrichtext\"[^>]*>\s*", re.IGNORECASE)
_BODY_STYLE: re.Pattern[str] = re.compile(r"<body\s+style=\"[^\"]*\"\s*>", re.IGNORECASE)
_HREF: re.Pattern[str] = re.compile(r"href=\"([^\"]*)\"", re.IGNORECASE)
_FORMATTING: re.Pattern[str] = re.compile(r'font-weight:\s*[6-9]00|font-style:\s*italic|text-decoration:\s*underline|<ul|<ol|<a\s|<img\s|-qt-block-indent:\s*[1-9]|<span style="[^"]*(?:font-size|font-family|color)', re.IGNORECASE)


def safe_link(address: str) -> bool:
    """Only web and mail links may go into a message: never javascript:, file: and the like."""
    text = address.strip()
    try:
        scheme = urlsplit(text).scheme.lower()
    except ValueError:
        return False
    return scheme in ALLOWED_LINK_SCHEMES and len(text) <= 2000 and not any(ord(character) < 32 for character in text)


_BODY_INNER: re.Pattern[str] = re.compile(r"<body\b[^>]*>(.*)</body\s*>", re.IGNORECASE | re.DOTALL)


def with_styles_inside(html: str) -> str:
    """An HTML text with its own style rules written onto each of its paragraphs and pieces of text, the way the editor reads them, and
    no style rules left: put next to other text, its rules reach only itself (a quoted original, D-360)."""
    document = QTextDocument()
    document.setHtml(html)
    found = _BODY_INNER.search(document.toHtml())
    return found.group(1) if found is not None else ""


def _without_foreign_pictures(html: str) -> str:
    """Only pictures that travel inside the message (embedded, or the editor's own) stay; a picture on the web would be fetched when the
    message is read (it may tell the sender so), and one of a file on this computer means nothing to the receiver."""
    return _IMAGE.sub(lambda match: match.group(0) if _KEPT_IMAGE.match(match.group(0)) else "", html)


_STYLE: re.Pattern[str] = re.compile(r'style="([^"]*)"', re.IGNORECASE)
_BLOCK_INDENT: re.Pattern[str] = re.compile(r"-qt-block-indent:\s*(\d+)", re.IGNORECASE)
_MARGIN_LEFT: re.Pattern[str] = re.compile(r"margin-left:\s*(\d+)px", re.IGNORECASE)
INDENT_PIXELS: int = 40  # one step of Indent, Qt's own indent width


def _indent_as_margin(match: "re.Match[str]") -> str:
    """Qt keeps a paragraph's indent in a style only Qt reads (-qt-block-indent): written as the left margin every reader shows."""
    style = match.group(1)
    found = _BLOCK_INDENT.search(style)
    steps = min(int(found.group(1)), 20) if found is not None else 0  # a number from the text: capped
    if steps == 0:
        return match.group(0)
    margin = _MARGIN_LEFT.search(style)
    pixels = (int(margin.group(1)) if margin is not None else 0) + steps * INDENT_PIXELS
    style = _MARGIN_LEFT.sub(f"margin-left:{pixels}px", style, count=1) if margin is not None else f"margin-left:{pixels}px; {style}"
    return f'style="{style}"'


def clean_html(html: str) -> str:
    """The HTML part of an outgoing message from the editor's HTML: only pictures that travel inside it (D-353), only web and mail
    links, and no editor-specific font settings on the page, so that the reader's own apply."""
    cleaned = _without_foreign_pictures(html)
    cleaned = _QT_META.sub("", cleaned)
    cleaned = _BODY_STYLE.sub("<body>", cleaned)
    cleaned = _STYLE.sub(_indent_as_margin, cleaned)
    return _HREF.sub(lambda match: match.group(0) if safe_link(match.group(1)) else 'href=""', cleaned)


@dataclass(frozen=True, slots=True)
class Picture:
    """A picture in the text, as it was given: its type, its name and its bytes (what is shown may be a smaller copy)."""

    content_type: str
    name: str
    data: bytes


def is_picture_file(path: Path) -> bool:
    """A file of a picture type that Qt can show (Qt can also read a PDF as a picture: that is a document, attached)."""
    guessed = mimetypes.guess_type(path.name)[0] or ""
    return guessed.startswith("image/") and QImageReader(str(path)).canRead()


_TAG: re.Pattern[str] = re.compile(r"<!--.*?-->|<(script|style)\b.*?</\1\s*>|<[^>]*>", re.IGNORECASE | re.DOTALL)


def _readable_text(source: QMimeData) -> bool:
    """Whether what was copied has text to read besides a picture: the text its HTML shows (a picture copied from a web page comes as
    HTML of only that picture), else its plain text."""
    if source.hasHtml():
        return bool(unescape(_TAG.sub(" ", source.html())).strip())
    return source.hasText() and bool(source.text().strip())


def png_picture(image: QImage, name: str) -> Picture:
    """A copied picture (a screenshot, a picture of a web page or another program) as a PNG file of this name."""
    holder = QByteArray()
    buffer = QBuffer(holder)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    QImageWriter(buffer, b"png").write(image)
    buffer.close()
    return Picture("image/png", name, bytes(holder.data()))


class PlainEditor(QPlainTextEdit):
    """The plain text editor: a file dropped onto the text and a pasted picture become attachments, never a path or text typed
    (the owner: "once converted to plain text, the user can add images, but only as an attachment, not inline", D-353)."""

    files_dropped = Signal(list)  # paths of the files dragged onto the text: to be attached
    picture_pasted = Signal(QImage)  # a copied picture: to be attached as a PNG

    @override
    def canInsertFromMimeData(self, source: QMimeData) -> bool:  # noqa: N802 - Qt name
        return source.hasImage() or source.hasUrls() or super().canInsertFromMimeData(source)

    @override
    def insertFromMimeData(self, source: QMimeData) -> None:  # noqa: N802 - Qt name
        local: list[str] = [url.toLocalFile() for url in source.urls() if url.isLocalFile()] if source.hasUrls() else []
        if local:  # files and folders from this computer: the files are attached, a folder cannot be; nothing is typed
            files = [path for path in local if Path(path).is_file()]
            if files:
                self.files_dropped.emit(files)
        elif source.hasImage() and not _readable_text(source):  # only a picture (a screenshot, a picture of a web page)
            image = source.imageData()
            if isinstance(image, QImage) and not image.isNull():
                self.picture_pasted.emit(image)
        else:  # text, also when a picture of it comes along (cells of a spreadsheet, styled text)
            super().insertFromMimeData(source)


class _Editor(QTextEdit):
    """A text editor that takes pasted or dropped text, formatting and pictures; a dropped file that is not a picture is handed on."""

    def __init__(self, owner: "RichEditor") -> None:
        super().__init__(owner)
        self._owner = owner

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt name
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._owner.fit_pictures()

    @override
    def canInsertFromMimeData(self, source: QMimeData) -> bool:  # noqa: N802 - Qt name
        return source.hasImage() or source.hasUrls() or source.hasHtml() or source.hasText()

    @override
    def insertFromMimeData(self, source: QMimeData) -> None:  # noqa: N802 - Qt name
        files: list[Path] = [Path(url.toLocalFile()) for url in source.urls() if url.isLocalFile()] if source.hasUrls() else []
        if files:  # files dragged in: pictures into the text, other files attached
            others: list[str] = []
            for path in files:
                if not (path.is_file() and is_picture_file(path) and self._owner.insert_picture_file(path)):
                    if path.is_file() and not is_picture_file(path):
                        others.append(str(path))
            if others:
                self._owner.files_dropped.emit(others)
        elif source.hasImage():  # a picture copied (a screenshot, a picture of a web page or another program)
            image = source.imageData()
            if isinstance(image, QImage) and not image.isNull():
                self._owner.insert_picture_image(image)
        elif source.hasHtml():
            plain = QMimeData()
            plain.setHtml(self._owner.absorb(_without_foreign_pictures(source.html())))
            super().insertFromMimeData(plain)
        elif source.hasText():
            self.insertPlainText(source.text())


class RichEditor(QWidget):
    """A small word processor: bold, italic, underline, lists and links. The message is sent as text and HTML."""

    files_dropped = Signal(list)  # paths of files that are not pictures, dragged onto the text: to be attached

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pictures: dict[str, Picture] = {}  # the pictures of the text by their address in it (cid:picture-…@chrono-mail)
        self.sent_font: tuple[str, int] | None = None  # the font and size in points the message carries (D-354)
        self._images: dict[str, QImage] = {}  # each picture as read, so the copy shown follows the width of the text
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.edit = _Editor(self)
        self.edit.setAcceptRichText(True)
        self.target: Callable[[], QTextEdit | QPlainTextEdit] = lambda: self.edit  # where Cut, Copy, Paste, Undo and Redo act (a message: its editor in use)
        self.toolbar = QToolBar(self)  # the bar of the reference client (D-363); a message window puts it above its text and adds its own buttons
        self.toolbar.setObjectName("richToolbar")
        self.bold = self._action("B", "Bold", "Ctrl+B", checkable=True, icon="format-text-bold")
        self.italic = self._action("I", "Italic", "Ctrl+I", checkable=True, icon="format-text-italic")
        self.underline = self._action("U", "Underline", "Ctrl+U", checkable=True, icon="format-text-underline")
        self.bullets = self._action("Bulleted list", "Bulleted list", None, icon="format-list-unordered")
        self.numbers = self._action("Numbered list", "Numbered list", None, icon="format-list-ordered")
        self.cut = self._action("Cut", "Cut the selected text", None, icon="edit-cut")
        self.copy = self._action("Copy", "Copy the selected text", None, icon="edit-copy")
        self.paste = self._action("Paste", "Paste at the cursor", None, icon="edit-paste")
        self.plain = self._action("Remove Format", "Remove the formatting of the selected text", None, icon="edit-clear-all")
        self.undo = self._action("Undo", "Undo the last change", None, icon="edit-undo")
        self.redo = self._action("Redo", "Redo what was undone", None, icon="edit-redo")
        self.outdent = self._action("Outdent", "Move the paragraph to the left", None, icon="format-indent-less")
        self.indent = self._action("Indent", "Move the paragraph to the right", None, icon="format-indent-more")
        self.link = self._action("Insert Link", "Make the selected text a link", "Ctrl+K", icon="insert-link")
        self.picture = self._action("Insert Image", "Insert a picture at the cursor", None, icon="insert-image")
        self.formatting: tuple[QAction, ...] = (self.bold, self.italic, self.underline, self.bullets, self.numbers, self.plain, self.outdent, self.indent, self.link, self.picture)  # only for rich text
        for action in (self.bold, self.italic, self.underline):
            self.toolbar.addAction(action)
        self.font_style = FontStyle(self.edit, self.toolbar)  # the A: font, size and colours (D-363)
        self.toolbar.addWidget(self.font_style)
        for action in (self.bullets, self.numbers):
            self.toolbar.addAction(action)
        spacer = QWidget(self.toolbar)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer)
        self.more_menu = QMenu(self.toolbar)  # the three dots: what does not fit on the bar (Q71); it belongs to the bar, as its button
        for action in (self.cut, self.copy, self.paste, self.plain, self.undo, self.redo, self.outdent, self.indent, self.link, self.picture):
            self.more_menu.addAction(action)
        self.more = QToolButton(self.toolbar)
        self.more.setIcon(theme_icon("overflow-menu"))
        self.more.setToolTip("More")
        self.more.setMenu(self.more_menu)
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.toolbar.addWidget(self.more)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.edit, 1)
        self.bold.triggered.connect(self._toggle_bold)
        self.italic.triggered.connect(self._toggle_italic)
        self.underline.triggered.connect(self._toggle_underline)
        self.bullets.triggered.connect(self._bullets)
        self.numbers.triggered.connect(self._numbers)
        self.cut.triggered.connect(lambda: self.target().cut())
        self.copy.triggered.connect(lambda: self.target().copy())
        self.paste.triggered.connect(lambda: self.target().paste())
        self.undo.triggered.connect(lambda: self.target().undo())
        self.redo.triggered.connect(lambda: self.target().redo())
        self.outdent.triggered.connect(lambda: self._shift(-1))
        self.indent.triggered.connect(lambda: self._shift(1))
        self.link.triggered.connect(self._ask_link)
        self.plain.triggered.connect(self._clear_formatting)
        self.picture.triggered.connect(self._choose_picture)
        self.edit.currentCharFormatChanged.connect(self._follow_format)

    def _action(self, text: str, tip: str, shortcut: str | None, checkable: bool = False, icon: str = "") -> QAction:
        action = QAction(theme_icon(icon), text, self.toolbar) if icon else QAction(text, self.toolbar)
        action.setToolTip(tip)
        action.setCheckable(checkable)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        return action

    # --- content ------------------------------------------------------------------------------------------

    def toPlainText(self) -> str:  # noqa: N802 - the name of QTextEdit's method, so that it can stand in for the plain editor
        """The text, each picture as [picture: its name]."""
        names = iter(self.picture_names())
        return "".join(f"[picture: {next(names, 'picture')}]" if character == PICTURE_MARK else character for character in self.edit.toPlainText())

    def picture_names(self) -> list[str]:
        """The name of each picture in the text, in its order (one per place it stands)."""
        names: list[str] = []
        block = self.edit.document().begin()
        while block.isValid():
            part = block.begin()
            while not part.atEnd():
                fragment = part.fragment()
                if fragment.isValid() and fragment.charFormat().isImageFormat():
                    known = self.pictures.get(fragment.charFormat().toImageFormat().name())
                    names.extend([known.name if known is not None else "picture"] * fragment.length())
                part += 1
            block = block.next()
        return names

    def pictures_in_text(self) -> dict[str, Picture]:
        """The pictures that stand in the text now, by their address, each once, in their order."""
        found: dict[str, Picture] = {}
        for address in _OWN_PICTURE.findall(self.edit.toHtml()):
            if address[1] in self.pictures:
                found.setdefault(address[1], self.pictures[address[1]])
        return found

    def setPlainText(self, text: str) -> None:  # noqa: N802
        self.edit.setPlainText(text)

    def set_html(self, html: str) -> None:
        self.edit.setHtml(self.absorb(html))

    def outgoing_html(self) -> str:
        """The HTML that is saved and sent: every picture of the text embedded, as it was given (D-353)."""
        def embedded(match: "re.Match[str]") -> str:
            picture = self.pictures.get(match.group(2))
            return match.group(0) if picture is None else f"{match.group(1)}{data_address(picture.content_type, picture.name, picture.data)}{match.group(3)}"
        cleaned = clean_html(_OWN_PICTURE.sub(embedded, self.edit.toHtml()))
        if self.sent_font is None:
            return cleaned
        family, points = self.sent_font  # the receiver sees the font it was written in, not the serif default of a reader (D-354)
        quoted = family.replace("\\", "").replace("'", "").replace('"', "").replace(";", "").replace("<", "").replace(">", "")
        return cleaned.replace("<body>", f"<body style=\"font-family:'{quoted}', sans-serif; font-size:{points}pt;\">", 1)

    # --- pictures -----------------------------------------------------------------------------------------

    def absorb(self, html: str) -> str:
        """HTML with embedded pictures (a draft, a quoted original, a paste) as HTML that refers to the editor's own pictures."""
        changed, pictures = pictures_out_of(html, lambda: f"picture-{uuid4().hex}@chrono-mail", leave_out_bad=True)  # a broken one is left out
        for picture in pictures:
            image = QImage.fromData(picture.data)
            if image.isNull():  # a picture Qt cannot show is left out with its tag, never a reference with nothing behind it
                changed = changed.replace(f"cid:{picture.content_id}", "")
            else:
                self._keep(f"cid:{picture.content_id}", Picture(picture.content_type, picture.name, picture.data), image)
        return _without_foreign_pictures(changed)

    def _keep(self, address: str, picture: Picture, image: QImage) -> None:
        self.pictures[address] = picture
        self._images[address] = image
        self.edit.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(address), self._shown(image))

    def fit_pictures(self) -> None:
        """The text area got another width: every picture is shown again no wider than it (also a draft opened before its window had a size)."""
        if not self._images:
            return
        document = self.edit.document()
        for address, image in self._images.items():
            document.addResource(QTextDocument.ResourceType.ImageResource, QUrl(address), self._shown(image))
        document.markContentsDirty(0, document.characterCount())

    def _shown(self, image: QImage) -> QImage:
        """What is shown of a picture: no wider than the text area; the picture itself is never changed."""
        widest = max(200, self.edit.viewport().width() - 24)
        return image if image.width() <= widest else image.scaledToWidth(widest, Qt.TransformationMode.SmoothTransformation)

    def _insert(self, picture: Picture, image: QImage) -> None:
        address = f"cid:picture-{uuid4().hex}@chrono-mail"
        self._keep(address, picture, image)
        picture_format = QTextImageFormat()
        picture_format.setName(address)
        self.edit.textCursor().insertImage(picture_format)

    def insert_picture_file(self, path: Path) -> bool:
        """Put the picture of this file at the cursor, unaltered. False (and said) when it is too large or cannot be read."""
        try:
            if path.stat().st_size > MAXIMUM_PICTURE_BYTES:
                QMessageBox.information(self, "Picture", f"{path.name} is larger than {MAXIMUM_PICTURE_BYTES // (1024 * 1024)} MB, so it cannot go into the text.")
                return False
            data = path.read_bytes()
        except OSError as error:
            QMessageBox.information(self, "Picture", f"{path.name} could not be read: {error.strerror}.")
            return False
        image = QImage.fromData(data)
        if image.isNull():
            QMessageBox.information(self, "Picture", f"{path.name} is not a picture Chrono Mail can show.")
            return False
        guessed = mimetypes.guess_type(path.name)[0]
        content_type = guessed if guessed and guessed.startswith("image/") else "image/png"
        self._insert(Picture(content_type, path.name, data), image)
        return True

    def insert_picture_image(self, image: QImage) -> None:
        """Put a copied picture at the cursor, as a PNG file."""
        self._insert(png_picture(image, f"picture-{len(self.pictures) + 1}.png"), image)

    def _choose_picture(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Insert a picture", "", PICTURE_FILTER)
        if path:
            self.insert_picture_file(Path(path))
            self.edit.setFocus()

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

    def set_formatting_enabled(self, on: bool) -> None:
        """Plain text: what only rich text can do is greyed, never hidden (OR-15)."""
        for action in self.formatting:
            action.setEnabled(on)
        self.font_style.setEnabled(on)

    def _shift(self, step: int) -> None:
        """Indent or Outdent: the paragraph one step to the right or left, never past the margin; in a list only this item, which joins
        the list of its new level just above it, or starts one there (sent as a list inside the list)."""
        cursor = self.edit.textCursor()
        current = cursor.currentList()
        if current is not None:
            level = max(1, current.format().indent() + step)
            if level == current.format().indent():
                return
            block = cursor.block()
            joined: QTextList | None = None
            above = block.previous()
            while above.isValid() and above.textList() is not None:
                found = above.textList()
                if found.format().indent() == level:
                    joined = found
                    break
                if found.format().indent() < level:
                    break
                above = above.previous()
            list_format = QTextListFormat(current.format())  # read before the item leaves: a list left empty is gone
            list_format.setIndent(level)
            current.remove(block)
            block_format = cursor.blockFormat()
            block_format.setIndent(0)  # Qt gives an item that leaves its list that list's indent as its own: it moves by its list alone
            cursor.setBlockFormat(block_format)
            if joined is not None:
                joined.add(block)
            else:
                cursor.createList(list_format)
            return
        block_format = cursor.blockFormat()
        block_format.setIndent(max(0, block_format.indent() + step))
        cursor.setBlockFormat(block_format)

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
