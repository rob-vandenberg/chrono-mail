import base64
import html
import re
from functools import partial
from email.utils import parseaddr
from typing import override

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QFont, QImage, QKeyEvent, QKeySequence, QMouseEvent, QPalette, QResizeEvent, QTextCursor
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.engine.commands_mail import AttachmentInfo, InlineImage, ShowResult
from chrono_mail.core.colours import MAIL_PAGE
from chrono_mail.mime.linkdomain import leads_elsewhere
from chrono_mail.ui.formatting import display_name, file_size, initials, avatar_colour, long_date
from chrono_mail.ui.scale import px_for

# --- Version ------------------------------------------------------------
__version__ = "reader.py 2.2.313"

# --- Version history ----------------------------------------------------
# v2.2.313: the plain-text view, read only, leaves Ctrl+Z and Redo to the window's Undo and Redo (it claimed them with nothing to undo, B-18)
# v2.0.302: the attachment row: a click selects, a double click opens, a right-click offers Open attachment and Save as…; a right-click
#           on a picture of the message offers the same (attachment_open_requested, D-236)
# v2.0.301: the attached pictures the text does not show are shown below it, fitted to the width, never enlarged (B-11, D-235)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.5.257: link_hovered: the address of the link under the pointer (HTML and plain text) and whether it leads to another main domain than the sender's; "" when the pointer leaves it or the message changes (D-215)
# v1.1.172: The page colour from the colour file (D-158)
# v0.5.144: The sender's circle follows the size factor of the window the reader is in (the main window, or a message window: the other windows)
# v0.5.135: The tags of the message below the people (set_tags)
# v0.3.106: The sender's circle follows the size factor of the interface
# v0.0.77: Message list and reading pane: Received instead of Date, blue unread dot (empty circle when read), flat list header, reading pane header and banner
# v0.0.42: settings: remote content per sender, plain text first, zoom
# v0.0.13: Initial reading pane

# Mail may contain anything. The page that shows HTML mail runs no JavaScript, follows no link by
# itself, remembers nothing, and fetches nothing from the internet until the person allows it for
# that message (AGENTS.md section 4.6).

_LOCAL_SCHEMES: tuple[str, ...] = ("data", "about", "blob")
_URL_PATTERN: re.Pattern[str] = re.compile(r"(https?://[^\s<>\"']+|mailto:[^\s<>\"']+)")
_CID_PATTERN: re.Pattern[str] = re.compile(r"cid:([^\s\"'()<>]+)", re.IGNORECASE)


class RemoteContentBlocker(QWebEngineUrlRequestInterceptor):
    """Blocks every request that would leave the computer, and counts them."""

    def __init__(self) -> None:
        super().__init__()
        self.allow_remote: bool = False
        self.blocked: int = 0

    @override
    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:
        if info.requestUrl().scheme() in _LOCAL_SCHEMES:
            return
        if self.allow_remote and info.requestUrl().scheme() in ("http", "https"):
            return
        self.blocked += 1
        info.block(True)


class _LinkCatcher(QWebEnginePage):
    """Stands in for the new window a link asks for (target=_blank): it only reports the address."""

    def __init__(self, owner: "SafePage") -> None:
        super().__init__(owner.profile(), owner)
        self._owner: SafePage = owner

    @override
    def acceptNavigationRequest(self, url: QUrl | str, type: QWebEnginePage.NavigationType, isMainFrame: bool, /) -> bool:
        self._owner.link_clicked.emit(QUrl(url) if isinstance(url, str) else url)
        self.deleteLater()
        return False


class SafePage(QWebEnginePage):
    """A page that shows what it was given and goes nowhere: links are reported, not followed."""

    link_clicked = Signal(QUrl)

    @override
    def acceptNavigationRequest(self, url: QUrl | str, type: QWebEnginePage.NavigationType, isMainFrame: bool, /) -> bool:
        target = QUrl(url) if isinstance(url, str) else url
        if type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            self.link_clicked.emit(target)
            return False
        return target.scheme() in _LOCAL_SCHEMES or target.isEmpty()

    @override
    def createWindow(self, type: QWebEnginePage.WebWindowType, /) -> QWebEnginePage:
        return _LinkCatcher(self)  # no pop-up window: a link that wants one is only reported


def make_safe_view(parent: QWidget | None = None) -> tuple[QWebEngineView, SafePage, RemoteContentBlocker]:
    profile = QWebEngineProfile()  # no name: an off-the-record profile that keeps nothing on disk
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
    profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
    blocker = RemoteContentBlocker()
    profile.setUrlRequestInterceptor(blocker)
    page = SafePage(profile, parent)
    profile.setParent(page)  # the profile must outlive its page, so the page owns it
    settings = page.settings()
    for attribute, value in (
        (QWebEngineSettings.WebAttribute.JavascriptEnabled, False),
        (QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False),
        (QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False),
        (QWebEngineSettings.WebAttribute.LocalStorageEnabled, False),
        (QWebEngineSettings.WebAttribute.PluginsEnabled, False),
        (QWebEngineSettings.WebAttribute.WebGLEnabled, False),
        (QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False),
        (QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False),
        (QWebEngineSettings.WebAttribute.ErrorPageEnabled, False),
        (QWebEngineSettings.WebAttribute.DnsPrefetchEnabled, False),
        (QWebEngineSettings.WebAttribute.HyperlinkAuditingEnabled, False),
        (QWebEngineSettings.WebAttribute.PdfViewerEnabled, False),
        (QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, False),
        (QWebEngineSettings.WebAttribute.ReadingFromCanvasEnabled, False),
    ):
        settings.setAttribute(attribute, value)
    page.setBackgroundColor(QColor(MAIL_PAGE))  # mail is written for a white page, whatever the theme
    view = QWebEngineView(parent)
    view.setPage(page)
    view.setContextMenuPolicy(view.contextMenuPolicy().NoContextMenu)
    return view, page, blocker


def linkify(text: str) -> str:
    """Plain text as HTML with its web and mail addresses clickable."""
    escaped = html.escape(text)
    return _URL_PATTERN.sub(lambda match: f'<a href="{match.group(0)}">{match.group(0)}</a>', escaped)


def embed_inline_images(document: str, images: dict[str, tuple[str, str]]) -> str:
    """Replace cid: references with data: URLs of the images that came with the message."""

    def replace(match: re.Match[str]) -> str:
        found = images.get(match.group(1).strip("<>").lower())
        return f"data:{found[0]};base64,{found[1]}" if found is not None else match.group(0)

    return _CID_PATTERN.sub(replace, document)


def picture_url(picture: InlineImage) -> str:
    """The address a picture of the message is shown by (its data), also how a right-click on it is known."""
    return f"data:{html.escape(picture.content_type)};base64,{picture.data_base64}"


def pictures_below(document: str, pictures: tuple[InlineImage, ...]) -> str:
    """The HTML text with the attached pictures it does not show below it, each as wide as the page at most (B-11)."""
    if not pictures:
        return document
    shown = "".join(
        f'<img src="{picture_url(picture)}" alt="" '
        'style="display:block; max-width:100%; height:auto; margin-top:12px">'
        for picture in pictures
    )
    end = document.lower().rfind("</body>")
    return f"{document[:end]}{shown}{document[end:]}" if end >= 0 else document + shown


class PlainTextView(QTextBrowser):
    """A plain-text message, with the attached pictures below the text: as wide as the view at most, never enlarged (B-11)."""

    picture_menu_requested = Signal(int, QPoint)  # a right-click on a picture: its place among the attachments, where on the screen

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pictures: dict[str, QImage] = {}
        self._positions: dict[str, int] = {}

    def show_text(self, text: str, pictures: tuple[InlineImage, ...]) -> None:
        self._pictures.clear()
        self._positions.clear()
        for picture in pictures:
            image = QImage.fromData(base64.b64decode(picture.data_base64))
            if not image.isNull():  # a picture Qt cannot read stays an attachment only
                name = f"picture:{len(self._pictures)}"
                self._pictures[name] = image
                self._positions[name] = picture.position
        shown = "".join(f'<p><img src="{name}"></p>' for name in self._pictures)
        self.setHtml(f'<pre style="white-space: pre-wrap; font-family: inherit; margin: 0">{linkify(text)}</pre>{shown}')
        self._fit_pictures()
        self.moveCursor(QTextCursor.MoveOperation.Start)

    @override
    def event(self, e: QEvent, /) -> bool:
        """Read only, the text has nothing to undo: the keys of Undo and Redo go to the window's Undo and Redo of mail (B-18)."""
        if e.type() == QEvent.Type.ShortcutOverride and isinstance(e, QKeyEvent) and self.isReadOnly():
            if e.matches(QKeySequence.StandardKey.Undo) or e.matches(QKeySequence.StandardKey.Redo):
                e.ignore()
                return False
        return super().event(e)

    @override
    def loadResource(self, type: int, name: QUrl | str, /) -> object:
        image = self._pictures.get(QUrl(name).toString())
        return image if image is not None else super().loadResource(type, QUrl(name))

    @override
    def resizeEvent(self, e: QResizeEvent, /) -> None:
        super().resizeEvent(e)
        self._fit_pictures()

    def picture_at(self, point: QPoint) -> int:
        """The place among the attachments of the picture at this point of the view, or -1."""
        inside = QPointF(point) + QPointF(self.horizontalScrollBar().value(), self.verticalScrollBar().value())
        return self._positions.get(self.document().documentLayout().imageAt(inside), -1)

    @override
    def contextMenuEvent(self, e: QContextMenuEvent, /) -> None:
        position = self.picture_at(e.pos())
        if position >= 0:
            self.picture_menu_requested.emit(position, e.globalPos())  # a picture: Open attachment and Save as… (D-236)
        else:
            super().contextMenuEvent(e)  # the text: Copy, Select All


    def _fit_pictures(self) -> None:
        """Every picture as wide as the view allows, never wider than itself; the text and the scroll stay as they are."""
        if not self._pictures:
            return
        room = max(1.0, self.viewport().width() - 2 * self.document().documentMargin())
        cursor = QTextCursor(self.document())
        block = self.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                image = self._pictures.get(fragment.charFormat().toImageFormat().name()) if fragment.charFormat().isImageFormat() else None
                if image is not None:
                    shape = fragment.charFormat().toImageFormat()
                    width = min(float(image.width()), room)
                    if abs(shape.width() - width) > 0.5:
                        shape.setWidth(width)
                        shape.setHeight(image.height() * width / max(1, image.width()))
                        cursor.setPosition(fragment.position())
                        cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
                        cursor.setCharFormat(shape)
                iterator += 1
            block = block.next()


class AttachmentChip(QPushButton):
    """One file of the attachment row: a click selects it, a double click opens it, a right-click offers Open attachment and Save as… (D-236)."""

    open_requested = Signal()

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFlat(True)
        self.setCheckable(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setToolTip("Double-click to open; right-click to open or save")
        self.setStyleSheet("QPushButton:checked { background: palette(highlight); color: palette(highlighted-text); border-radius: 4px; }")

    @override
    def mouseDoubleClickEvent(self, e: QMouseEvent, /) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.setChecked(True)
            self.open_requested.emit()
        else:
            super().mouseDoubleClickEvent(e)


class ReaderWidget(QWidget):
    """Shows one message: the header, its attachments, and the text or HTML."""

    attachment_save_requested = Signal(int, str)  # position in the message, suggested file name
    attachment_open_requested = Signal(int, str)  # position in the message, its file name: open it in the program for its kind (D-236)
    link_activated = Signal(QUrl)
    link_hovered = Signal(str, bool)  # the address of the link under the pointer ("" when it leaves one), and whether it leads elsewhere (D-215)
    sender_allowed = Signal(str)  # the person chose to always show remote content from this address

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QFrame(self)
        self._header.setObjectName("readerHeader")
        header_layout = QVBoxLayout(self._header)
        header_layout.setContentsMargins(16, 12, 16, 8)
        self._subject = QLabel(self._header)
        subject_font = QFont(self.font())
        subject_font.setPointSizeF(subject_font.pointSizeF() * 1.35)
        subject_font.setBold(True)
        self._subject.setFont(subject_font)
        self._subject.setWordWrap(True)
        self._subject.setTextFormat(Qt.TextFormat.PlainText)  # a subject is hostile input: never markup
        self._subject.setTextInteractionFlags(self._subject.textInteractionFlags().TextSelectableByMouse)
        self._avatar = QLabel(self._header)
        self._avatar.setFixedSize(px_for(self, 40), px_for(self, 40))
        self._people = QLabel(self._header)
        self._people.setWordWrap(True)
        self._people.setTextFormat(Qt.TextFormat.RichText)  # the lines below are built with html.escape
        self._people.setTextInteractionFlags(self._people.textInteractionFlags().TextSelectableByMouse)
        self._date = QLabel(self._header)
        self._date.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        row = QHBoxLayout()
        row.addWidget(self._avatar)
        row.addWidget(self._people, 1)
        row.addWidget(self._date, 0)
        header_layout.addWidget(self._subject)
        header_layout.addLayout(row)
        self._tags = QLabel(self._header)  # the message's tags as small coloured labels; hidden without tags
        self._tags.setTextFormat(Qt.TextFormat.RichText)
        self._tags.hide()
        header_layout.addWidget(self._tags)
        layout.addWidget(self._header)

        self._attachments = QWidget(self)
        self._attachments_layout = QHBoxLayout(self._attachments)
        self._attachments_layout.setContentsMargins(16, 0, 16, 8)
        self._attachments_layout.addStretch(1)
        layout.addWidget(self._attachments)

        self._banner = QFrame(self)
        self._banner.setObjectName("remoteBanner")
        self._banner.setFrameShape(QFrame.Shape.StyledPanel)
        banner_layout = QHBoxLayout(self._banner)
        self._banner_label = QLabel("Pictures and other content from the internet are not shown, to protect your privacy.", self._banner)
        self._load_remote = QPushButton("Show remote content", self._banner)
        self._always_remote = QPushButton("Always for this sender", self._banner)
        banner_layout.addWidget(self._banner_label, 1)
        banner_layout.addWidget(self._load_remote)
        banner_layout.addWidget(self._always_remote)
        self._banner.hide()
        layout.addWidget(self._banner)

        self._stack = QStackedWidget(self)
        self._placeholder = QLabel("Select a message to read it", self._stack)
        self._placeholder.setAlignment(self._placeholder.alignment().AlignCenter)
        self._placeholder.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self._text = PlainTextView(self._stack)
        self._text.setOpenLinks(False)
        self._text.setFrameShape(QFrame.Shape.NoFrame)
        self._text.anchorClicked.connect(self.link_activated)
        self._text.highlighted.connect(self._text_hovered)
        self._text.picture_menu_requested.connect(self._picture_menu)
        self._web, self._page, self._blocker = make_safe_view(self._stack)
        self._web.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)  # only a picture of the message has a menu (D-236)
        self._web.customContextMenuRequested.connect(self._page_menu)
        self._picture_positions: dict[str, int] = {}  # the address a picture is shown by -> its place among the attachments
        self._chips: QButtonGroup = QButtonGroup(self)
        self._names: dict[int, str] = {}
        self._page.link_clicked.connect(self.link_activated)
        self._page.linkHovered.connect(self._hover)
        self._stack.addWidget(self._placeholder)
        self._stack.addWidget(self._text)
        self._stack.addWidget(self._web)
        layout.addWidget(self._stack, 1)

        self._load_remote.clicked.connect(self._show_remote_content)
        self._page.loadFinished.connect(self._after_load)
        self._current_html: str = ""
        self._images: dict[str, tuple[str, str]] = {}
        self.always_remote: bool = False  # show remote content without asking (a setting)
        self.allowed_senders: set[str] = set()  # addresses (lower case) whose remote content is shown without asking
        self.prefer_plain: bool = False  # show the text part of a message that has both
        self._sender: str = ""
        self._hovered: str = ""  # the address of the link under the pointer
        self._zoom: int = 100
        self._always_remote.clicked.connect(self._allow_sender)
        self.clear()

    # --- showing --------------------------------------------------------------------------------

    def _text_hovered(self, url: QUrl) -> None:
        self._hover(url.toString())

    def _hover(self, address: str) -> None:
        """The pointer came on a link (its address) or left it (""): told with whether it leads to another main domain than the sender's."""
        if address == self._hovered:
            return
        self._hovered = address
        self.link_hovered.emit(address, bool(address) and leads_elsewhere(address, self._sender))

    def clear(self) -> None:
        self._hover("")  # the link under the pointer goes with the message
        self._header.hide()
        self._attachments.hide()
        self._banner.hide()
        self._stack.setCurrentWidget(self._placeholder)
        self._page.setHtml("")

    def apply_scale(self) -> None:
        """The sender's circle at the size factor of the window the reader is in (the subject and the lines follow the text)."""
        self._avatar.setFixedSize(px_for(self, 40), px_for(self, 40))
        sheet = self._avatar.styleSheet()
        if "border-radius" in sheet:  # a circle: half its size
            self._avatar.setStyleSheet(sheet[: sheet.index("border-radius")] + f"border-radius: {px_for(self, 40) // 2}px;")

    def show_message(self, shown: ShowResult) -> None:
        """Display a message.show result."""
        self._hover("")  # the link under the pointer went with the message before
        self._header.show()
        self._subject.setText(shown.subject or "(no subject)")
        senders: tuple[str, ...] = shown.sender
        recipients: tuple[str, ...] = shown.to
        copies: tuple[str, ...] = shown.cc
        who = senders[0] if senders else "(unknown sender)"
        lines = [f"<b>{html.escape(who)}</b>"]
        muted = self.palette().color(QPalette.ColorRole.PlaceholderText).name()
        if recipients:
            lines.append(f'<span style="color:{muted}">to {html.escape(", ".join(recipients))}</span>')
        if copies:
            lines.append(f'<span style="color:{muted}">cc {html.escape(", ".join(copies))}</span>')
        self._people.setText("<br>".join(lines))
        self._date.setText(long_date(shown.date))
        name = display_name(who)
        self._avatar.setText(f'<div style="color:white; font-weight:bold">{html.escape(initials(name))}</div>')
        self._avatar.setAlignment(self._avatar.alignment().AlignCenter)
        self._avatar.setStyleSheet(f"background-color: {avatar_colour(who).name()}; border-radius: {px_for(self, 40) // 2}px;")
        self._fill_attachments(shown.attachments)
        self._images = {image.content_id.lower(): (image.content_type, image.data_base64) for image in shown.inline_images}
        html_body = shown.html_body
        text_body = shown.text_body
        self._banner.hide()
        self._sender = parseaddr(who)[1].lower() if senders else ""
        self._always_remote.setVisible(bool(self._sender))
        if html_body and self.prefer_plain and text_body:
            html_body = None  # the person prefers the plain text part
        pictures = shown.pictures
        if not html_body and shown.html_body:  # the HTML text is not shown: neither are the pictures it showed (B-11)
            pictures = (*shown.inline_images, *pictures)
        self._picture_positions = {picture_url(image): image.position for image in (*shown.inline_images, *shown.pictures) if image.position >= 0}
        if html_body:
            self._current_html = pictures_below(embed_inline_images(html_body, self._images), pictures)
            self._blocker.allow_remote = self.always_remote or (bool(self._sender) and self._sender in self.allowed_senders)
            self._blocker.blocked = 0
            self._stack.setCurrentWidget(self._web)
            self._page.setHtml(self._current_html)
        else:
            self._stack.setCurrentWidget(self._text)
            self._text.show_text(text_body or "", pictures)

    def set_tags(self, labels: str) -> None:
        """The tags of the shown message (rich text from TagColourBook.html); empty hides the line."""
        self._tags.setText(labels)
        self._tags.setVisible(bool(labels))

    def _fill_attachments(self, attachments: tuple[AttachmentInfo, ...]) -> None:
        while self._attachments_layout.count() > 1:
            item = self._attachments_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._chips = QButtonGroup(self._attachments)  # one file selected at a time
        self._names = {position: attachment.suggested_name for position, attachment in enumerate(attachments)}
        shown = 0
        for position, attachment in enumerate(attachments):
            if attachment.inline:
                continue
            chip = AttachmentChip(f"📎 {attachment.suggested_name} ({file_size(attachment.size)})", self._attachments)
            self._chips.addButton(chip, position)
            chip.open_requested.connect(partial(self._open_attachment, position))
            chip.customContextMenuRequested.connect(partial(self._chip_menu, chip, position))
            self._attachments_layout.insertWidget(self._attachments_layout.count() - 1, chip)
            shown += 1
        self._attachments.setVisible(shown > 0)

    def chips(self) -> list[AttachmentChip]:
        """The files of the attachment row, in their order."""
        return [chip for chip in self._chips.buttons() if isinstance(chip, AttachmentChip)]

    def _open_attachment(self, position: int) -> None:
        self.attachment_open_requested.emit(position, self._names.get(position, ""))

    def _chip_menu(self, chip: AttachmentChip, position: int, point: QPoint) -> None:
        chip.setChecked(True)  # the file the menu is about is the selected one
        self.attachment_menu(position).popup(chip.mapToGlobal(point))

    def attachment_menu(self, position: int) -> QMenu:
        """What a right-click on a file of the message offers: Open attachment and Save as… (D-236)."""
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        name = self._names.get(position, "")
        menu.addAction("Open attachment", partial(self.attachment_open_requested.emit, position, name))
        menu.addAction("Save as…", partial(self.attachment_save_requested.emit, position, name))
        return menu

    def _picture_menu(self, position: int, where: QPoint) -> None:
        self.attachment_menu(position).popup(where)

    def _page_menu(self, point: QPoint) -> None:
        """A right-click in an HTML message: a picture of the message offers Open attachment and Save as…; elsewhere nothing, as before."""
        request = self._web.lastContextMenuRequest()
        if request is None or request.mediaType() != request.MediaType.MediaTypeImage:
            return
        position = self._picture_positions.get(request.mediaUrl().toString(), -1)
        if position >= 0:
            self._picture_menu(position, self._web.mapToGlobal(point))

    def _after_load(self, ok: bool) -> None:
        if ok and self._blocker.blocked and not self._blocker.allow_remote:
            self._banner.show()

    def set_zoom(self, percent: int) -> None:
        """The size of the text of the message, in percent (50 to 300)."""
        self._zoom = max(50, min(300, percent))
        self._web.setZoomFactor(self._zoom / 100)
        font = QFont(self.font())
        font.setPointSizeF(max(6.0, font.pointSizeF() * self._zoom / 100) if font.pointSizeF() > 0 else 10.0 * self._zoom / 100)
        self._text.setFont(font)

    @property
    def zoom(self) -> int:
        return self._zoom

    def _allow_sender(self) -> None:
        if self._sender:
            self.allowed_senders.add(self._sender)
            self.sender_allowed.emit(self._sender)
        self._show_remote_content()

    def _show_remote_content(self) -> None:
        self._blocker.allow_remote = True
        self._banner.hide()
        self._page.setHtml(self._current_html)

    # --- for tests and for the window ----------------------------------------------------------

    @property
    def web_page(self) -> SafePage:
        return self._page

    @property
    def blocker(self) -> RemoteContentBlocker:
        return self._blocker

    @property
    def text_view(self) -> PlainTextView:
        return self._text

    def showing_html(self) -> bool:
        return self._stack.currentWidget() is self._web

    def banner_visible(self) -> bool:
        return not self._banner.isHidden()

