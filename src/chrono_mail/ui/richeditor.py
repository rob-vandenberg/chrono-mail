import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import override
from urllib.parse import urlsplit
from uuid import uuid4

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QFont, QResizeEvent, QImage, QImageReader, QImageWriter, QKeySequence, QTextCharFormat, QTextDocument, QTextImageFormat, QTextListFormat
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QTextEdit, QToolBar, QVBoxLayout, QWidget

from chrono_mail.mime.pictures import MAXIMUM_PICTURE_BYTES, data_address, pictures_out_of

# --- Version ------------------------------------------------------------
__version__ = "richeditor.py 4.4.621"

# --- Version history ----------------------------------------------------
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
_FORMATTING: re.Pattern[str] = re.compile(r"font-weight:\s*[6-9]00|font-style:\s*italic|text-decoration:\s*underline|<ul|<ol|<a\s|<img\s", re.IGNORECASE)


def safe_link(address: str) -> bool:
    """Only web and mail links may go into a message: never javascript:, file: and the like."""
    text = address.strip()
    try:
        scheme = urlsplit(text).scheme.lower()
    except ValueError:
        return False
    return scheme in ALLOWED_LINK_SCHEMES and len(text) <= 2000 and not any(ord(character) < 32 for character in text)


def _without_foreign_pictures(html: str) -> str:
    """Only pictures that travel inside the message (embedded, or the editor's own) stay; a picture on the web would be fetched when the
    message is read (it may tell the sender so), and one of a file on this computer means nothing to the receiver."""
    return _IMAGE.sub(lambda match: match.group(0) if _KEPT_IMAGE.match(match.group(0)) else "", html)


def clean_html(html: str) -> str:
    """The HTML part of an outgoing message from the editor's HTML: only pictures that travel inside it (D-353), only web and mail
    links, and no editor-specific font settings on the page, so that the reader's own apply."""
    cleaned = _without_foreign_pictures(html)
    cleaned = _QT_META.sub("", cleaned)
    cleaned = _BODY_STYLE.sub("<body>", cleaned)
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
        self._images: dict[str, QImage] = {}  # each picture as read, so the copy shown follows the width of the text
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
        self.picture = self._action("Picture…", "Insert a picture at the cursor", None)
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
        for action in (self.bullets, self.numbers, self.link, self.picture, self.plain):
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
        self.picture.triggered.connect(self._choose_picture)
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

    def pictures_in_text(self) -> list[Picture]:
        """The pictures that stand in the text now, each once, in their order."""
        found: dict[str, Picture] = {}
        for address in _OWN_PICTURE.findall(self.edit.toHtml()):
            if address[1] in self.pictures:
                found.setdefault(address[1], self.pictures[address[1]])
        return list(found.values())

    def setPlainText(self, text: str) -> None:  # noqa: N802
        self.edit.setPlainText(text)

    def set_html(self, html: str) -> None:
        self.edit.setHtml(self.absorb(html))

    def outgoing_html(self) -> str:
        """The HTML that is saved and sent: every picture of the text embedded, as it was given (D-353)."""
        def embedded(match: "re.Match[str]") -> str:
            picture = self.pictures.get(match.group(2))
            return match.group(0) if picture is None else f"{match.group(1)}{data_address(picture.content_type, picture.name, picture.data)}{match.group(3)}"
        return clean_html(_OWN_PICTURE.sub(embedded, self.edit.toHtml()))

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
        holder = QByteArray()
        buffer = QBuffer(holder)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        QImageWriter(buffer, b"png").write(image)
        buffer.close()
        self._insert(Picture("image/png", f"picture-{len(self.pictures) + 1}.png", bytes(holder.data())), image)

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
