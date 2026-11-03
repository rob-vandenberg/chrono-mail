import base64
import html
import math
import re
from collections.abc import Callable
from functools import partial
from email.utils import parseaddr
from typing import override

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QObject, QPoint, QPointF, QRect, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QFont, QFontMetrics, QGuiApplication, QHideEvent, QImage, QKeyEvent, QKeySequence, QMouseEvent, QPainter, QPaintEvent, QPalette, QResizeEvent, QShowEvent, QTextCursor, QTextDocument, QWheelEvent
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
    QWebEngineUrlRequestJob,
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.engine.commands_mail import AttachmentInfo, InlineImage, ShowResult
from chrono_mail.core.colours import MAIL_PAGE
from chrono_mail.mime.linkdomain import leads_elsewhere
from chrono_mail.ui.flowlayout import FlowRow
from chrono_mail.engine.invitations import InvitationInfo
from chrono_mail.ui.invitationcard import InvitationCard
from chrono_mail.ui.plainboxes import plain_tip
from chrono_mail.ui.formatting import display_name, file_size, initials, avatar_colour, long_date
from chrono_mail.ui.scale import px_for
from chrono_mail.core import displayed_address

# --- Version ------------------------------------------------------------
__version__ = "reader.py 4.4.507"

# --- Version history ----------------------------------------------------
# v4.4.507: Send my busy times… on the card reaches the reading pane (invitation_busy_requested), D-310 step 4
# v4.4.506: the card's buttons for your own meeting reach the reading pane (invitation_organize_requested)
# v4.4.505: the card's Accept, Tentative, Decline, Delegate and apply buttons reach the reading pane (invitation_*_requested); the
#           card on screen (invitation), D-310
# v4.4.504: a mail that holds a calendar message shows its card above the text, and the card alone when the text shows nothing (no
#           white page); the card's part is not offered as a file (D-310)
# v4.3.503: a mailto link to the address of one of the person's accounts has no warning (D-215)
# v4.3.501: Only the web view and the remote content bar have a window of their own, never the parts they sit in (on Windows those stayed black
#            after a restore); the view's window is made at once, before any page loads (a message shown before its window was on screen stayed blank)
# v4.2.479: Every message opens with a folded header of one height: the sender and the people it went to on one line each, "+N more"
#            for the names that do not fit, the subject on one line, one row of files with "+N more"; "+N more" unfolds it all, "less"
#            folds it again; the remote content line lies over the top of the message: a new message never moves the text below; the
#            tags are small squares in their colours under the date (no line of their own); a right-click on "+N more" or "less" shows no menu;
#            folded, the sender and the date never ask for more width than there is (shortened with "…", whole as hover text); the page
#            gets empty room at its top as tall as the remote content line, so it hides nothing; Tab reaches "+N more" and "less", and
#            Enter or Space follows them (a Tab stop only with one); the room is the page's own top padding; the folded lines are as
#            high as Chinese names and emoji need
# v4.2.478: The remote content banner stays away from an empty reader: emptying it forgets the blocked pictures, and the banner shows only while
#            an HTML message is on screen (it came back when the empty page had loaded, after a switch of folder)
# v4.2.477: Every message has the same address host (chrono-message://message/...): the web engine keeps one page process and swaps the old
#            message for the new one at once, without the white and grey page in between (about twice as fast)
# v4.2.467: the message's own parts no longer set the reading pane's width (D-304): the header sits in a scroll area that scrolls sideways only
#           when one word cannot wrap (header_follows_content); the remote content line wraps; the attachments go on in a next row
# v4.2.464: a web view has a window of its own: in the main window's, every small change elsewhere (a busy bar step) made Qt put the
#           whole window together again through OpenGL, in software on a computer without a graphics card; only the view and the parts it
#           sits in get a window of their own, never their neighbours (D-266)
# v4.2.430: the From, To and Cc lines show the addresses as a person reads them (no quotes added for Reply)
# v4.2.396: a right-click on a link of the message (HTML or plain text) offers Copy link address (the link fully encoded; not for a link within
#           the message itself); on a picture that is a link too, after Open attachment and Save as…; in plain text Copy and Select All follow (D-281)
# v3.6.357: linkify finds the addresses in the text as written, so a link in <...> or quotes stops at its closing character; an apostrophe belongs to the address unless it opened with one; punctuation that ends the sentence after an address is not part of it; a scheme in any case (CR-19)
# v3.5.346: An HTML message and its pictures are handed to the page from memory through Chrono Mail's own address (chrono-message:), not
#           inside one data: address, which Qt refuses above 2 MB: a large message showed an empty page (RV-158)
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

MESSAGE_SCHEME: str = "chrono-message"  # the shown message and its pictures, served from memory by the page's own MessageContent
MESSAGE_HOST: str = "message"  # one host for every message: one origin, so the web engine keeps its page process and the old picture until the new one
_LOCAL_SCHEMES: tuple[str, ...] = ("data", "about", "blob", MESSAGE_SCHEME)


_CONTENT_TYPE_PATTERN: re.Pattern[str] = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*/[A-Za-z0-9][A-Za-z0-9.+-]*")


def _safe_type(content_type: str) -> bytes:
    """A picture's content type as the mail named it, only when it is one plain type/subtype (mail is hostile input)."""
    cleaned = content_type.strip().lower()
    return cleaned.encode("ascii") if _CONTENT_TYPE_PATTERN.fullmatch(cleaned) else b"application/octet-stream"


def _register_message_scheme() -> None:
    """The web engine must know the scheme before it starts (before the application object): done when this module is imported."""
    if QWebEngineUrlScheme.schemeByName(QByteArray(MESSAGE_SCHEME.encode())).name().isEmpty():
        scheme = QWebEngineUrlScheme(QByteArray(MESSAGE_SCHEME.encode()))
        scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)  # no flags: not a local scheme, so allowed remote content still loads (D-030)
        QWebEngineUrlScheme.registerScheme(scheme)


_register_message_scheme()


class MessageContent(QWebEngineUrlSchemeHandler):
    """What the page of one reader shows: the HTML text of the message and its pictures, handed over from memory. setHtml would put it all
    into one data: address, and Qt shows nothing above 2 MB (RV-158). Every message gets a new number, so a late request of the message
    before gets nothing of this one."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._number: int = 0
        self._page: bytes = b""
        self._parts: list[tuple[bytes, bytes]] = []  # content type, data

    def show(self, parts: list[tuple[str, bytes]]) -> list[str]:
        """A new message: its pictures (content type, data) get their addresses, in order; the page itself follows with set_page."""
        self._number += 1
        self._page = b""
        self._parts = [(_safe_type(content_type), data) for content_type, data in parts]
        return [f"{MESSAGE_SCHEME}://{MESSAGE_HOST}/{self._number}/part/{place}" for place in range(len(parts))]

    def set_page(self, document: str) -> QUrl:
        self._page = document.encode("utf-8", "replace")
        return QUrl(f"{MESSAGE_SCHEME}://{MESSAGE_HOST}/{self._number}/page")

    def clear(self) -> None:
        self.show([])

    @override
    def requestStarted(self, job: QWebEngineUrlRequestJob, /) -> None:
        address = job.requestUrl()
        pieces = address.path().strip("/").split("/")
        if address.host() != MESSAGE_HOST or not pieces or pieces[0] != str(self._number):
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
        elif pieces[1:] == ["page"]:
            self._reply(job, b"text/html;charset=utf-8", self._page)
        elif len(pieces) == 3 and pieces[1] == "part" and pieces[2].isascii() and pieces[2].isdigit() and int(pieces[2]) < len(self._parts):
            content_type, data = self._parts[int(pieces[2])]
            self._reply(job, content_type, data)
        else:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)

    @staticmethod
    def _reply(job: QWebEngineUrlRequestJob, content_type: bytes, data: bytes) -> None:
        buffer = QBuffer(job)  # lives as long as the request reads it
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(QByteArray(content_type), buffer)
# An address that opens with an apostrophe ends at the next one; any other keeps its apostrophes (O'Brien).
_URL_PATTERN: re.Pattern[str] = re.compile(r"(?<=')(?:https?://|mailto:)[^\s<>\"']+|(?<!')(?:https?://|mailto:)[^\s<>\"]+", re.IGNORECASE)  # a scheme in any case (RFC 3986)
_SENTENCE_END: str = ".,;:!?'"
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


def make_safe_view(parent: QWidget | None = None) -> tuple[QWebEngineView, SafePage, RemoteContentBlocker, MessageContent]:
    profile = QWebEngineProfile()  # no name: an off-the-record profile that keeps nothing on disk
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
    profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
    blocker = RemoteContentBlocker()
    profile.setUrlRequestInterceptor(blocker)
    content = MessageContent(profile)
    profile.installUrlSchemeHandler(QByteArray(MESSAGE_SCHEME.encode()), content)
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
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)  # only the view gets a window, never its neighbours
    view.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)  # nor the parts it sits in: on Windows those stayed black after a restore (4.3.501)
    view.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)  # its own window: a change elsewhere never makes Qt put the whole window together again (4.2.464)
    view.setPage(page)
    view.setContextMenuPolicy(view.contextMenuPolicy().NoContextMenu)
    view.winId()  # the window made now, before any page loads: a page loaded before the window is on screen was drawn for no window and stayed blank (4.3.501)
    return view, page, blocker, content


def _sentence_end_cut(found: str) -> int:
    """Where an address ends without the punctuation of the sentence after it, never inside its scheme; one pass, also over hostile text."""
    stop = len(found)
    scheme = len(found.split(":", 1)[0]) + 1 + (2 if found[:4].lower() == "http" else 0)  # "mailto:", "http://", "https://"
    unmatched = found.count(")") - found.count("(")
    while stop > scheme:
        last = found[stop - 1]
        if last in _SENTENCE_END:
            stop -= 1
        elif last == ")" and unmatched > 0:
            unmatched -= 1
            stop -= 1
        else:
            break
    return stop


def linkify(text: str) -> str:
    """Plain text as HTML with its web and mail addresses clickable.

    The addresses are found in the text as written, so one stops at its closing bracket or double quote; an
    apostrophe belongs to the address (O'Brien) unless the address opened with one. Punctuation that ends the
    sentence after an address (. , ; : ! ? ' and a ")" without its "(") is not part of it. The text around
    them and each address are made safe for HTML apart (CR-19).
    """
    pieces: list[str] = []
    end = 0
    for match in _URL_PATTERN.finditer(text):
        found = match.group(0)[:_sentence_end_cut(match.group(0))]  # what is cut is only punctuation: no other address in it
        if found.lower() in ("http://", "https://", "mailto:"):
            continue  # nothing but the scheme: plain text
        address = html.escape(found)
        pieces.append(f'{html.escape(text[end:match.start()])}<a href="{address}">{address}</a>')
        end = match.start() + len(found)
    pieces.append(html.escape(text[end:]))
    return "".join(pieces)


def embed_inline_images(document: str, addresses: dict[str, str]) -> str:
    """Replace cid: references with the addresses of the images that came with the message (content id in lower case -> address)."""

    def replace(match: re.Match[str]) -> str:
        return addresses.get(match.group(1).strip("<>").lower(), match.group(0))

    return _CID_PATTERN.sub(replace, document)


def pictures_below(document: str, addresses: list[str]) -> str:
    """The HTML text with the attached pictures it does not show below it (by their addresses), each as wide as the page at most (B-11)."""
    if not addresses:
        return document
    shown = "".join(
        f'<img src="{html.escape(address)}" alt="" '
        'style="display:block; max-width:100%; height:auto; margin-top:12px">'
        for address in addresses
    )
    end = document.lower().rfind("</body>")
    return f"{document[:end]}{shown}{document[end:]}" if end >= 0 else document + shown


def _decoded(data_base64: str) -> bytes:
    try:
        return base64.b64decode(data_base64)
    except ValueError:
        return b""  # a picture whose data cannot be read shows as a broken picture, never as a failure of the message


class PlainTextView(QTextBrowser):
    """A plain-text message, with the attached pictures below the text: as wide as the view at most, never enlarged (B-11)."""

    menu_requested = Signal(int, str, QPoint)  # a right-click on a picture or a link: the picture's place among the attachments (-1: none), the link ("": none), where on the screen

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
        position, link = self.picture_at(e.pos()), self.anchorAt(e.pos())
        if position >= 0 or link:
            self.menu_requested.emit(position, link, e.globalPos())  # a picture: Open attachment and Save as… (D-236); a link: Copy link address (D-281)
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


class HeaderArea(QScrollArea):
    """The message's header, as high as its lines need at the width there is. While it follows its content (the standard), its smallest
    width is the header's own, as before; otherwise it never sets the width around it, and only a word too long to wrap brings a
    scrollbar to move sideways (D-304). It never scrolls up and down."""

    def __init__(self, header: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.follows_content: bool = True
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # as the header always was: a click or Tab never stops here (OR-16)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # following its content, it is always wide enough
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)  # its lines' height when there is room, as the header always had
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setWidget(header)

    @override
    def resizeEvent(self, e: QResizeEvent, /) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(e)
        if e.size().width() != e.oldSize().width():
            self.updateGeometry()  # another width: the header's lines wrap otherwise, so it needs another height

    @override
    def wheelEvent(self, e: QWheelEvent, /) -> None:  # noqa: N802 - Qt's name
        pixels, angle = e.pixelDelta(), e.angleDelta()
        bar = self.horizontalScrollBar()
        if bar.isVisible() and abs(angle.x()) > abs(angle.y()):  # mostly sideways (a touchpad swipe carries a little up and down too)
            step = pixels.x() if not pixels.isNull() else round(angle.x() / 120 * bar.singleStep() * QApplication.wheelScrollLines())
            bar.setValue(bar.value() - step)
            e.accept()
        else:
            e.ignore()  # up and down passes on, as over the header before: it never scrolls up and down

    @override
    def keyPressEvent(self, e: QKeyEvent, /) -> None:  # noqa: N802 - Qt's name
        e.ignore()  # the arrows and page keys of a header line with the focus act as before, never scroll the header (OR-16)

    def set_follows_content(self, follows: bool) -> None:
        self.follows_content = follows
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff if follows else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.updateGeometry()

    @override
    def eventFilter(self, watched: QObject, event: QEvent, /) -> bool:  # noqa: N802 - Qt's name
        if watched is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            self.updateGeometry()  # the header's lines changed: the size it asks of the reader changes with them
        return super().eventFilter(watched, event)

    def _content_height(self, width: int) -> int:
        """The height the header needs at this width, with the sideways scrollbar when its smallest width is more."""
        header = self.widget()
        if header is None:
            return 0
        needed = header.minimumSizeHint().width()
        height = header.heightForWidth(max(width, needed)) if header.hasHeightForWidth() else -1
        if height < 0:
            height = header.sizeHint().height()
        if width < needed and not self.follows_content:
            height += self.horizontalScrollBar().sizeHint().height()
        return height

    @override
    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt's name
        return True

    @override
    def heightForWidth(self, width: int, /) -> int:  # noqa: N802 - Qt's name
        return self._content_height(width)

    @override
    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        header = self.widget()
        return QSize(header.sizeHint().width() if header is not None else 0, self._content_height(self.width()))

    @override
    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        header = self.widget()
        if header is None:
            return QSize(0, 0)
        width = header.minimumSizeHint().width() if self.follows_content else 0
        sideways = self.horizontalScrollBar().sizeHint().height() if not self.follows_content and self.width() < header.minimumSizeHint().width() else 0
        return QSize(width, header.minimumSizeHint().height() + sideways)  # never taller than the header itself ever asked: a long list of addresses never makes the window grow


class DateLabel(QLabel):
    """The message's date. Folded (as every message opens): always one line, shortened with "…" when the room runs out, the whole date
    as hover text; so line 1 is as high whatever the room. Unfolded and allowed to wrap (set_width_rule), it goes on two lines only when
    the room runs out, never sooner (D-304)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full: str = ""
        self.folded: bool = True
        self.wraps: bool = False  # set_width_rule: allowed to wrap while unfolded

    def set_date(self, text: str) -> None:
        self._full = text
        self._apply()
        self.updateGeometry()

    def set_folded(self, folded: bool) -> None:
        self.folded = folded
        self._apply()
        self.updateGeometry()

    def set_wraps(self, wraps: bool) -> None:
        self.wraps = wraps
        self._apply()
        self.updateGeometry()

    def _apply(self) -> None:
        wrap = self.wraps and not self.folded
        if self.wordWrap() != wrap:
            self.setWordWrap(wrap)
        text, tip = self._full, ""
        metrics = self.fontMetrics()
        room = self.contentsRect().width()
        if self.folded and self.isVisible() and metrics.horizontalAdvance(self._full) > room:
            text, tip = metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, room), plain_tip(self._full)
        if text != self.text():
            self.setText(text)
        if tip != self.toolTip():
            self.setToolTip(tip)

    def _one_line(self) -> int:
        margins = self.contentsMargins()
        return self.fontMetrics().horizontalAdvance(self._full) + 2 * self.margin() + margins.left() + margins.right() + 1

    @override
    def resizeEvent(self, e: QResizeEvent, /) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(e)
        if self.folded:
            self._apply()

    @override
    def showEvent(self, e: QShowEvent, /) -> None:  # noqa: N802 - Qt's name
        super().showEvent(e)
        if self.folded:
            self._apply()

    @override
    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        if self.folded:
            return QSize(self._one_line(), self.fontMetrics().height())  # the whole date, on one line
        if not self.wordWrap():
            return super().sizeHint()
        width = self._one_line()
        return QSize(width, self.heightForWidth(width))  # the whole date on one line: a wrapping label would ask for less

    @override
    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        if self.folded:
            return QSize(min(self._one_line(), self.fontMetrics().horizontalAdvance("…") * 4), self.fontMetrics().height())
        return super().minimumSizeHint()


class AttachmentChip(QPushButton):
    """One file of the attachment row: a click selects it, a double click opens it, a right-click offers Open attachment and Save as… (D-236).
    Given less room than its full text needs, it shortens the file's name in the middle; the size stays, the hover text names the file (D-304)."""

    open_requested = Signal()
    HINT: str = "Double-click to open; right-click to open or save"

    def __init__(self, name: str, size: str, parent: QWidget | None = None) -> None:
        super().__init__(f"📎 {name} ({size})", parent)
        self._name: str = name
        self._size: str = size
        self.setFlat(True)
        self.setCheckable(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setToolTip(self.HINT)
        self.setStyleSheet("QPushButton:checked { background: palette(highlight); color: palette(highlighted-text); border-radius: 4px; }")
        self._full_hint: QSize = super().sizeHint()

    @override
    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return QSize(self._full_hint)  # the full text's, also while shortened; a copy, which the row may change

    @override
    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        metrics = self.fontMetrics()
        around = self._full_hint.width() - metrics.horizontalAdvance(self._name)  # the clip, the size and the button's own room
        return QSize(min(self._full_hint.width(), max(0, around) + metrics.horizontalAdvance("…") * 3), self._full_hint.height())

    @override
    def resizeEvent(self, e: QResizeEvent, /) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(e)
        metrics = self.fontMetrics()
        room = self.width() - (self._full_hint.width() - metrics.horizontalAdvance(self._name))
        shown = self._name if room >= metrics.horizontalAdvance(self._name) else metrics.elidedText(self._name, Qt.TextElideMode.ElideMiddle, max(0, room))
        self.setText(f"📎 {shown} ({self._size})")
        self.setToolTip(self.HINT if shown == self._name else plain_tip(f"{self._name}\n{self.HINT}"))

    @override
    def mouseDoubleClickEvent(self, e: QMouseEvent, /) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.setChecked(True)
            self.open_requested.emit()
        else:
            super().mouseDoubleClickEvent(e)


_NO_LIMIT: int = 16777215  # Qt's largest size of a widget (QWIDGETSIZE_MAX)
_LINK_KEYS: tuple[Qt.Key, ...] = (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)
_LINK_PATTERN: re.Pattern[str] = re.compile(r'<a href="(more|less)">')
_KEYBOARD_LINKS: Qt.TextInteractionFlag = Qt.TextInteractionFlag.LinksAccessibleByMouse | Qt.TextInteractionFlag.LinksAccessibleByKeyboard


def follow_link_key(label: QLabel, e: QKeyEvent) -> bool:
    """Enter or Space on a header line with "+N more" or "less" (its only link) follows it, as a click does; True when it did."""
    found = _LINK_PATTERN.search(label.text())
    if found is None or e.key() not in _LINK_KEYS or e.modifiers() not in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier):
        return False
    e.accept()
    label.linkActivated.emit(found.group(1))
    return True


_BANNER_ROOM_SCRIPT: str = """document.documentElement.style.setProperty("padding-top", "%.3fpx", "important");"""  # run in Chrono Mail's own script world: the mail's own scripts never run (JavascriptEnabled is off for them)


def _all_fits(width: int) -> tuple[str, str]:
    return "", ""


def fold_names(names: list[tuple[str, str]], metrics: QFontMetrics, width: int) -> tuple[str, int]:
    """The names (each with what goes before it: "to ", ", ") that fit on one line of this width, with room left for "+N more"; and N,
    the names not shown. The first name, when even it does not fit, is shortened with "…" and counted as not shown. -1: all of them."""
    if width < 0:
        return "".join(lead + name for lead, name in names), 0
    room = width - metrics.horizontalAdvance("  ")  # what the markup around the names may add (a space, rounding)

    def more(count: int) -> int:
        return metrics.horizontalAdvance(f" +{count} more") if count else 0

    shown, used = "", 0
    for place, (lead, name) in enumerate(names):
        needed = metrics.horizontalAdvance(lead + name)
        if used + needed + more(len(names) - place - 1) > room:
            if place > 0:
                return shown, len(names) - place
            left = room - metrics.horizontalAdvance(lead) - more(len(names))
            part = metrics.elidedText(name, Qt.TextElideMode.ElideRight, left) if left > metrics.horizontalAdvance("…") else ""
            return lead + part, len(names)
        shown, used = shown + lead + name, used + needed
    return shown, 0


_TALL_SAMPLE: str = "Xgjpqy 李明 😀 ญี่ปุ่น مرحبا नमस्ते"  # the scripts that reach highest and lowest, from their fallback fonts
_FOLDED_HEIGHTS: dict[tuple[str, int, bool], int] = {}  # FoldedLine.folded_height, measured once per font


class FoldedLine(QLabel):
    """A part of the header that, folded (as every message opens), shows what fits on its lines, and is always as high as those lines
    whatever the message: a new message never moves the text below it. Unfolded it shows all of it, wrapped. What fits is asked of the
    function given with the content, at the width there is (-1: all of it, while the line is not on screen); it gives the text and the
    hover text."""

    def __init__(self, lines: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lines: int = lines
        self._fit: Callable[[int], tuple[str, str]] = _all_fits
        self._full: str = ""
        self._smallest: int = 0
        self.folded: bool = True
        self._on_link: str = ""  # the link under the pointer ("+N more", "less"): a right-click there shows no menu
        self.linkHovered.connect(self._link_hovered)

    def _link_hovered(self, link: str) -> None:
        self._on_link = link

    @override
    def keyPressEvent(self, ev: QKeyEvent, /) -> None:  # noqa: N802 - Qt's name
        if not follow_link_key(self, ev):
            super().keyPressEvent(ev)

    @override
    def contextMenuEvent(self, ev: QContextMenuEvent, /) -> None:  # noqa: N802 - Qt's name
        if self._on_link and ev.reason() == QContextMenuEvent.Reason.Mouse:
            ev.accept()  # "+N more" and "less" are no address: nothing to copy
            return
        super().contextMenuEvent(ev)

    def set_content(self, fit: Callable[[int], tuple[str, str]], full: str, smallest: int) -> None:
        """A new message: folded, as every message opens. smallest: the width it never goes below while folded."""
        self._fit, self._full, self._smallest = fit, full, smallest
        self.folded = True
        self._apply()

    def set_folded(self, folded: bool, full: str | None = None) -> None:
        if full is not None:
            self._full = full
        self.folded = folded
        self._apply()
        self.updateGeometry()

    def folded_height(self) -> int:
        """One height for every message: that of the tallest lines a name or subject is likely to have at this font (Latin letters that
        reach below the line, Chinese, an emoji, Thai with its tone marks, Arabic, Devanagari, all on one line, which come from other fonts and
        need more room than the font's own height), and a little."""
        key = (self.font().key(), self._lines, self.textFormat() == Qt.TextFormat.RichText)
        if key not in _FOLDED_HEIGHTS:
            sample = QTextDocument()
            sample.setDocumentMargin(0)
            sample.setDefaultFont(self.font())
            if key[2]:
                sample.setHtml("<br>".join([f"<b>{_TALL_SAMPLE}</b>"] + [_TALL_SAMPLE] * (self._lines - 1)))
            else:
                sample.setPlainText("\n".join([_TALL_SAMPLE] * self._lines))
            _FOLDED_HEIGHTS[key] = math.ceil(sample.size().height() * 1.06) + 2  # fonts mixed on one line can each add a little
        return _FOLDED_HEIGHTS[key]

    def _apply(self) -> None:
        if self.folded:
            if self.wordWrap():
                self.setWordWrap(False)
            self.setFixedHeight(self.folded_height())
            text, tip = self._fit(self.contentsRect().width() if self.isVisible() else -1)
        else:
            if not self.wordWrap():
                self.setWordWrap(True)
            self.setMinimumHeight(0)
            self.setMaximumHeight(_NO_LIMIT)
            text, tip = self._full, ""
        if text != self.text():
            self.setText(text)
        if tip != self.toolTip():
            self.setToolTip(tip)
        if self.textInteractionFlags() & Qt.TextInteractionFlag.LinksAccessibleByKeyboard:
            wanted = Qt.FocusPolicy.StrongFocus if _LINK_PATTERN.search(text) else Qt.FocusPolicy.ClickFocus  # a Tab stop only with "+N more" or "less"
            if self.focusPolicy() != wanted:
                self.setFocusPolicy(wanted)

    @override
    def resizeEvent(self, e: QResizeEvent, /) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(e)
        if self.folded and e.size().width() != e.oldSize().width():
            self._apply()  # another width: what fits is asked again

    @override
    def showEvent(self, e: QShowEvent, /) -> None:  # noqa: N802 - Qt's name
        super().showEvent(e)
        if self.folded:
            self._apply()

    @override
    def changeEvent(self, e: QEvent, /) -> None:  # noqa: N802 - Qt's name
        super().changeEvent(e)
        if e.type() == QEvent.Type.FontChange:
            self._apply()

    @override
    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return QSize(self._smallest, self.folded_height()) if self.folded else super().sizeHint()

    @override
    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return QSize(self._smallest, self.folded_height()) if self.folded else super().minimumSizeHint()


class LinkLabel(QLabel):
    """"+N more" or "less" after the files: Enter or Space follows it, as a click does."""

    @override
    def keyPressEvent(self, ev: QKeyEvent, /) -> None:  # noqa: N802 - Qt's name
        if not follow_link_key(self, ev):
            super().keyPressEvent(ev)


class AttachmentRow(FlowRow):
    """The files of the message. Folded (as every message opens): one row, always as high, with the files that fit and "+N more" for
    the others (the first file is shortened when even it does not fit). Unfolded: every file, in as many rows as they need, and "less"
    at the end when there were more."""

    def __init__(self, parent: QWidget | None = None, left: int = 0, right: int = 0, bottom: int = 0) -> None:
        super().__init__(parent, left=left, right=right, bottom=bottom)
        self.folded: bool = True
        self.less: bool = False  # unfolded: "less" after the last file
        self.hidden_count: int = 0  # folded: the files behind "+N more"
        self.line_height: int = 0  # the height of the folded row (the reader measures a file's button)
        self.link: LinkLabel = LinkLabel(self)  # "+N more" or "less"
        self.link.setTextFormat(Qt.TextFormat.RichText)
        self.link.setTextInteractionFlags(_KEYBOARD_LINKS)  # Tab reaches it
        self.link.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)  # "+N more" and "less" are no address: no menu, also not the window's
        self.link.hide()

    def set_folded(self, folded: bool, less: bool = False) -> None:
        self.folded, self.less = folded, less and not folded
        self._place()
        self.updateGeometry()

    def _link_width(self, text: str) -> int:
        return self.link.fontMetrics().horizontalAdvance(text) + 4

    def _show_link(self, href: str, text: str, x: int, y: int, height: int) -> None:
        """The link at x, in the middle of a row of this height."""
        self.link.setText(f'<a href="{href}">{html.escape(text)}</a>')
        link_height = self.link.sizeHint().height()
        self.link.setGeometry(QRect(x, y + max(0, height - link_height) // 2, self._link_width(text), link_height))
        self.link.show()

    @override
    def _lay_out(self, width: int, place: bool) -> int:
        left, top, right, bottom = self._margins
        end = width - right
        parts = self._parts
        if not self.folded:
            x, y, row_height = left, top, 0
            for part in parts:
                size = part.sizeHint()
                if self.wraps:
                    size.setWidth(max(1, min(size.width(), end - left)))
                if self.wraps and x > left and x + size.width() > end:
                    x, y, row_height = left, y + row_height + self._spacing, 0
                if place:
                    part.setGeometry(QRect(x, y, size.width(), size.height()))
                    part.show()
                x += size.width() + self._spacing
                row_height = max(row_height, size.height())
            if self.less:
                if x > left and x + self._link_width("less") > end:
                    x, y, row_height = left, y + row_height + self._spacing, 0
                row_height = max(row_height, self.line_height)
                if place:
                    self._show_link("less", "less", x, y, row_height)
            elif place:
                self.link.hide()
            return y + row_height + bottom
        line = self.line_height or max((part.sizeHint().height() for part in parts), default=0)
        x, shown = left, 0
        for place_in_row, part in enumerate(parts):
            size = part.sizeHint()
            others = len(parts) - place_in_row - 1
            reserve = self._spacing + self._link_width(f"+{others} more") if others else 0
            if place_in_row == 0 and self.wraps:
                size.setWidth(max(1, min(size.width(), end - left - reserve)))  # the first file always shows, shortened if need be
            elif place_in_row > 0 and x + size.width() + reserve > end:
                break
            if place:
                part.setGeometry(QRect(x, top + max(0, line - size.height()) // 2, size.width(), size.height()))
                part.show()
            x += size.width() + self._spacing
            shown += 1
        if place:
            self.hidden_count = len(parts) - shown
            for part in parts[shown:]:
                part.hide()
            if self.hidden_count:
                self._show_link("more", f"+{self.hidden_count} more", x, top, line)
            else:
                self.link.hide()
        return top + line + bottom

    def folded_height(self) -> int:
        _, top, _, bottom = self._margins
        return top + self.line_height + bottom


class TagSquares(QWidget):
    """The message's tags under the date: a small square in each tag's own colour, right-aligned, as many as fit, then "+N"; the tag
    names as hover text. Always as high as one line, with or without tags, so the header is as high for a tagged message."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tags: tuple[tuple[str, QColor], ...] = ()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set_tags(self, tags: tuple[tuple[str, QColor], ...]) -> None:
        self._tags = tags
        self.setToolTip(plain_tip(", ".join(name for name, _ in tags)) if tags else "")
        self.update()

    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self._tags)

    def _side(self) -> int:
        return max(4, round(self.fontMetrics().height() * 0.6))

    def squares(self) -> tuple[list[tuple[QRect, QColor]], str]:
        """Where each shown square goes with its colour, and the "+N" after them ("" when all fit)."""
        side, gap = self._side(), max(2, self._side() // 3)
        count = len(self._tags)

        def width_of(shown: int, rest: str) -> int:
            return shown * side + max(0, shown - 1) * gap + (gap + self.fontMetrics().horizontalAdvance(rest) if rest else 0)

        shown, rest = count, ""
        while shown > 0 and width_of(shown, rest) > self.width():
            shown -= 1
            rest = f"+{count - shown}"
        x = self.width() - width_of(shown, rest)
        y = (self.height() - side) // 2
        placed = [(QRect(x + place * (side + gap), y, side, side), colour) for place, (_, colour) in enumerate(self._tags[:shown])]
        return placed, rest

    @override
    def paintEvent(self, event: QPaintEvent, /) -> None:  # noqa: N802 - Qt's name
        painter = QPainter(self)
        placed, rest = self.squares()
        for rect, colour in placed:
            painter.fillRect(rect, colour)
        if rest:
            painter.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), rest)
        painter.end()

    @override
    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return QSize(0, self.fontMetrics().height())  # never widens the header: the date's width is its room

    @override
    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return self.sizeHint()


class OverlayBanner(QFrame):
    """The remote content line. It lies over the top of the message area, never in the layout: its coming and going moves neither the
    header nor the message. As wide as the message area, as high as its lines need at that width. While the message may set the reader's
    width (sets_width, set_width_rule 0), its one line sets the message area's smallest width, as it did in the layout."""

    placed = Signal(int)  # its height where it lies now: the page keeps room as tall at its top

    def __init__(self, area: QWidget) -> None:
        super().__init__(area)
        self.sets_width: bool = True
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)  # never the parts it sits in (4.3.501)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)  # the HTML message's view has a window of its own (4.2.464): only one of its own lies above it
        area.installEventFilter(self)

    def set_sets_width(self, sets: bool) -> None:
        self.sets_width = sets
        self._follow_width()

    def _follow_width(self) -> None:
        area = self.parentWidget()
        if area is not None:
            area.setMinimumWidth(self.minimumSizeHint().width() if self.sets_width and not self.isHidden() else 0)

    def place(self) -> None:
        """At the top of the message area, over its full width."""
        area = self.parentWidget()
        if area is None:
            return
        width = area.width()
        height = self.heightForWidth(width) if self.hasHeightForWidth() else -1
        self.setGeometry(0, 0, width, height if height >= 0 else self.sizeHint().height())
        self.raise_()
        self.placed.emit(self.height())

    @override
    def eventFilter(self, watched: QObject, event: QEvent, /) -> bool:  # noqa: N802 - Qt's name
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize and not self.isHidden():
            self.place()
        return super().eventFilter(watched, event)

    @override
    def event(self, e: QEvent, /) -> bool:
        handled = super().event(e)
        if e.type() == QEvent.Type.LayoutRequest and not self.isHidden():
            self.place()  # its lines changed (a button shown or hidden, another direction)
        return handled

    @override
    def showEvent(self, e: QShowEvent, /) -> None:  # noqa: N802 - Qt's name
        super().showEvent(e)
        self.place()
        self._follow_width()

    @override
    def hideEvent(self, e: QHideEvent, /) -> None:  # noqa: N802 - Qt's name
        super().hideEvent(e)
        self._follow_width()


class CardArea(QScrollArea):
    """The invitation card's scroll area: it asks for the card's whole height (a scroll area otherwise asks for a few lines), and has no
    smallest height of its own, so a low reader gives it less and the card scrolls; it never makes the reader higher."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.wanted_height: int = 0  # 0: no wish (the card alone takes the room the layout gives)

    @override
    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        hint = super().sizeHint()
        return QSize(hint.width(), self.wanted_height) if self.wanted_height > 0 else hint

    @override
    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return QSize(super().minimumSizeHint().width(), 0)


class CardHolder(QWidget):
    """What the card's scroll area holds: as high as the card needs at the width it is given, never the height every line would need wrapped at
    its narrowest (which a scroll area takes from its widget, so the card scrolled while there was room)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.fitted_height: int = 0

    def fit(self, width: int) -> None:
        layout = self.layout()
        smallest = super().minimumSizeHint().width()
        height = layout.totalHeightForWidth(max(width, smallest)) if layout is not None and layout.hasHeightForWidth() else -1
        fitted = height if height >= 0 else self.sizeHint().height()
        if fitted != self.fitted_height:
            self.fitted_height = fitted
            self.updateGeometry()

    @override
    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return QSize(super().minimumSizeHint().width(), self.fitted_height)


class ReaderWidget(QWidget):
    """Shows one message: the header, its attachments, and the text or HTML."""

    attachment_save_requested = Signal(int, str)  # position in the message, suggested file name
    attachment_open_requested = Signal(int, str)  # position in the message, its file name: open it in the program for its kind (D-236)
    link_activated = Signal(QUrl)
    link_hovered = Signal(str, bool)  # the address of the link under the pointer ("" when it leaves one), and whether it leads elsewhere (D-215)
    sender_allowed = Signal(str)  # the person chose to always show remote content from this address
    invitation_answer_requested = Signal(str)  # Accept, Tentative, Decline or Add to calendar on the invitation card (D-310)
    invitation_apply_requested = Signal()  # Update calendar, Remove from calendar, Add to calendar, Ask for the latest version (D-310)
    invitation_delegate_requested = Signal()  # Delegate… on the invitation card
    invitation_organize_requested = Signal(str)  # your own meeting: take, accept_proposal, decline_proposal, send_latest (D-310 step 3)
    invitation_busy_requested = Signal()  # Send my busy times… on the card (D-310 step 4)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QFrame(self)
        self._header.setObjectName("readerHeader")
        self._header_layout = QVBoxLayout(self._header)
        header_layout = self._header_layout
        header_layout.setContentsMargins(16, 12, 16, 8)
        self._subject = FoldedLine(1, self._header)  # folded: one line, shortened with "…"; the whole subject as hover text
        subject_font = QFont(self.font())
        subject_font.setPointSizeF(subject_font.pointSizeF() * 1.35)
        subject_font.setBold(True)
        self._subject.setFont(subject_font)
        self._subject.setTextFormat(Qt.TextFormat.PlainText)  # a subject is hostile input: never markup
        self._subject.setTextInteractionFlags(self._subject.textInteractionFlags().TextSelectableByMouse)
        self._avatar = QLabel(self._header)
        self._avatar.setFixedSize(px_for(self, 40), px_for(self, 40))
        self._people = FoldedLine(2, self._header)  # folded: the sender, and the people it went to that fit on one line, "+N more"
        self._people.setTextFormat(Qt.TextFormat.RichText)  # the lines below are built with html.escape
        self._people.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | _KEYBOARD_LINKS)  # Tab reaches "+N more" and "less"
        self._people.linkActivated.connect(self._fold_link)
        self._date = DateLabel(self._header)
        self._date.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        row = QHBoxLayout()
        self._header_row = row
        row.addWidget(self._avatar)
        row.addWidget(self._people, 1)
        self._tags = TagSquares(self._header)  # under the date, beside the "to" line: a tagged message's header is as high
        dated = QVBoxLayout()  # the date, and the tags beside the second line of the people
        self._dated = dated
        dated.setSpacing(0)
        dated.addStretch(1)
        dated.addWidget(self._date)
        dated.addWidget(self._tags)
        dated.addStretch(1)
        row.addLayout(dated, 0)
        header_layout.addLayout(row)
        header_layout.addWidget(self._subject)
        self._header_area = HeaderArea(self._header, self)
        layout.addWidget(self._header_area)

        self._attachment_line = QWidget(self)  # the row of files, or its empty room: folded, every message's header is as high
        attachment_layout = QVBoxLayout(self._attachment_line)
        self._attachment_layout = attachment_layout
        attachment_layout.setContentsMargins(0, 0, 0, 0)
        self._attachments = AttachmentRow(self._attachment_line, left=16, right=16, bottom=8)  # unfolded, a file that no longer fits goes on in the next row (D-304)
        self._attachments.link.linkActivated.connect(self._fold_link)
        attachment_layout.addWidget(self._attachments)
        self._chip_probe = AttachmentChip("x", "1 B", self._attachment_line)  # never shown: the height of a file's button
        self._chip_probe.hide()
        layout.addWidget(self._attachment_line)

        self._card_area = CardArea(self)  # the invitation card (D-310): above the text, scrolled on its own when the reader is low
        self._card_area.setFrameShape(QFrame.Shape.NoFrame)
        self._card_area.setWidgetResizable(True)
        # its lines wrap; a word longer than the card is wide (a meeting's link as its place) scrolls sideways, never cut off
        self._card_area.viewport().setBackgroundRole(QPalette.ColorRole.Base)
        card_holder = CardHolder(self._card_area)
        card_holder.setBackgroundRole(QPalette.ColorRole.Base)
        card_rows = QVBoxLayout(card_holder)
        card_row = QHBoxLayout()
        self._card = InvitationCard(card_holder)
        self._card.link_activated.connect(self.link_activated)
        self._card.link_hovered.connect(self._hover)  # the status line and the warning of D-215, as every link
        self._card.answer_requested.connect(self.invitation_answer_requested)
        self._card.apply_requested.connect(self.invitation_apply_requested)
        self._card.delegate_requested.connect(self.invitation_delegate_requested)
        self._card.organize_requested.connect(self.invitation_organize_requested)
        self._card.busy_requested.connect(self.invitation_busy_requested)
        self._card_area.viewport().installEventFilter(self)  # its height follows its width (_fit_card)
        card_row.addWidget(self._card, 1)
        card_row.addStretch(0)
        card_rows.addLayout(card_row)
        card_rows.addStretch(1)
        self._card_holder = card_holder
        self._card_rows = card_rows
        self._card_sizes()
        self._card_area.setWidget(card_holder)
        self._card_area.hide()
        layout.addWidget(self._card_area)
        self._people_shown: tuple[str, tuple[str, ...], tuple[str, ...]] = ("", (), ())  # the sender, to, cc
        self._people_hidden: int = 0  # the names behind "+N more" in the folded header

        self._stack = QStackedWidget(self)
        self._placeholder = QLabel("Select a message to read it", self._stack)
        self._placeholder.setAlignment(self._placeholder.alignment().AlignCenter)
        self._placeholder.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self._text = PlainTextView(self._stack)
        self._text.setOpenLinks(False)
        self._text.setFrameShape(QFrame.Shape.NoFrame)
        self._text.anchorClicked.connect(self.link_activated)
        self._text.highlighted.connect(self._text_hovered)
        self._text.menu_requested.connect(self._message_menu)
        self._web, self._page, self._blocker, self._content = make_safe_view(self._stack)
        self._web.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)  # only a picture of the message and a link have a menu (D-236, D-281)
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

        # Over the top of the message: its coming and going never moves the header or the message. Made after the web view, whose rule
        # keeps every other part without a window of its own (4.2.464).
        self._banner = OverlayBanner(self._stack)
        self._banner.setObjectName("remoteBanner")
        self._banner.setFrameShape(QFrame.Shape.StyledPanel)
        self._banner_layout = QHBoxLayout(self._banner)  # the text beside its buttons; above them while the reader keeps its width (D-304)
        self._banner_label = QLabel("Pictures and other content from the internet are not shown, to protect your privacy.", self._banner)
        self._banner_buttons = FlowRow(self._banner, spacing=self._banner_layout.spacing())  # a next row only when one does not fit
        self._load_remote = QPushButton("Show remote content", self._banner_buttons)
        self._always_remote = QPushButton("Always for this sender", self._banner_buttons)
        self._banner_buttons.add(self._load_remote)
        self._banner_buttons.add(self._always_remote)
        self._banner_layout.addWidget(self._banner_label, 1)
        self._banner_layout.addWidget(self._banner_buttons)
        self._banner.hide()
        self._banner.placed.connect(self._reserve_banner_room)

        self._load_remote.clicked.connect(self._show_remote_content)
        self._page.loadFinished.connect(self._after_load)
        self._current_html: str = ""
        self._page_url: QUrl = QUrl()  # where the page of the shown HTML message is served (MessageContent)
        self.always_remote: bool = False  # show remote content without asking (a setting)
        self.allowed_senders: set[str] = set()  # addresses (lower case) whose remote content is shown without asking
        self.prefer_plain: bool = False  # show the text part of a message that has both
        self._sender: str = ""
        self.account_addresses: Callable[[], frozenset[str]] = frozenset  # the addresses (lower case) of the person's accounts, as they are now
        self._hovered: str = ""  # the address of the link under the pointer
        self._zoom: int = 100
        self._always_remote.clicked.connect(self._allow_sender)
        self._smallest_width: int = 0  # set_width_rule
        self.set_width_rule(0)  # as before: what the message needs counts (a message window; the reading pane sets its own)
        self.clear()

    # --- showing --------------------------------------------------------------------------------

    def _text_hovered(self, url: QUrl) -> None:
        self._hover(url.toString())

    def _hover(self, address: str) -> None:
        """The pointer came on a link (its address) or left it (""): told with whether it leads to another main domain than the sender's."""
        if address == self._hovered:
            return
        self._hovered = address
        self.link_hovered.emit(address, bool(address) and leads_elsewhere(address, self._sender, self.account_addresses()))  # never the To line: the sender writes it

    def clear(self) -> None:
        self._hover("")  # the link under the pointer goes with the message
        self._header.hide()
        self._header_area.hide()
        self.setMinimumWidth(0)  # nothing shown: no width of its own
        self._attachments.hide()
        self._attachment_line.hide()
        self._stack.show()  # first: the keyboard focus can go back to it before the card area goes (OR-16)
        self._card_area.hide()
        self._card.clear()  # nothing of the message before stays
        self._banner.hide()
        self._blocker.blocked = 0  # the blocked pictures belonged to the message that goes
        self._stack.setCurrentWidget(self._placeholder)
        self._content.clear()
        self._page.setHtml("")

    @override
    def eventFilter(self, watched: QObject, event: QEvent, /) -> bool:  # noqa: N802 - Qt's name
        if watched is self._card_area.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_card()  # another width: the card's lines wrap anew
        return super().eventFilter(watched, event)

    def apply_scale(self) -> None:
        """The sender's circle at the size factor of the window the reader is in (the subject and the lines follow the text)."""
        self._card_sizes()
        if self._card.info is not None:
            self._card.show_invitation(self._card.info)  # the card's sizes follow the size factor too
            self._fit_card()
        self._avatar.setFixedSize(px_for(self, 40), px_for(self, 40))
        sheet = self._avatar.styleSheet()
        if "border-radius" in sheet:  # a circle: half its size
            self._avatar.setStyleSheet(sheet[: sheet.index("border-radius")] + f"border-radius: {px_for(self, 40) // 2}px;")

    def show_message(self, shown: ShowResult) -> None:
        """Display a message.show result."""
        self._hover("")  # the link under the pointer went with the message before
        self._header.show()
        self._header_area.show()
        self._header_area.horizontalScrollBar().setValue(0)  # every message starts at its left edge
        self._header_area.verticalScrollBar().setValue(0)  # and at its top
        self.setMinimumWidth(self._smallest_width)
        subject = shown.subject or "(no subject)"
        self._subject.set_content(partial(self._subject_fit, subject), subject, 0)  # every message opens folded
        senders: tuple[str, ...] = tuple(displayed_address(text) for text in shown.sender)  # the quotes Reply needs are left out on screen
        recipients: tuple[str, ...] = tuple(displayed_address(text) for text in shown.to)
        copies: tuple[str, ...] = tuple(displayed_address(text) for text in shown.cc)
        who = senders[0] if senders else "(unknown sender)"
        self._people_shown = (who, recipients, copies)
        bold = QFont(self._people.font())
        bold.setBold(True)
        smallest = QFontMetrics(bold).horizontalAdvance("…") * 4  # folded, the sender is shortened: it never asks for its whole width
        self._people.set_content(partial(self._people_fit, who, recipients, copies), self._people_full(less=False), smallest)
        self._date.folded = True  # every message opens folded
        self._date.set_date(long_date(shown.date))
        name = display_name(who)
        self._avatar.setText(f'<div style="color:white; font-weight:bold">{html.escape(initials(name))}</div>')
        self._avatar.setAlignment(self._avatar.alignment().AlignCenter)
        self._avatar.setStyleSheet(f"background-color: {avatar_colour(who).name()}; border-radius: {px_for(self, 40) // 2}px;")
        self._fill_attachments(shown.attachments)
        self._attachment_line.show()
        self._show_card(shown)
        self._lay_out_header()
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
        if html_body:
            images = list(dict.fromkeys((*shown.inline_images, *pictures)))
            addresses = self._content.show([(image.content_type, _decoded(image.data_base64)) for image in images])
            address_of = dict(zip(images, addresses, strict=True))
            self._picture_positions = {address_of[image]: image.position for image in images if image.position >= 0}
            cids = {image.content_id.lower(): address_of[image] for image in shown.inline_images if image.content_id}
            self._current_html = pictures_below(embed_inline_images(html_body, cids), [address_of[image] for image in pictures])
            self._page_url = self._content.set_page(self._current_html)
            self._blocker.allow_remote = self.always_remote or (bool(self._sender) and self._sender in self.allowed_senders)
            self._blocker.blocked = 0
            self._stack.setCurrentWidget(self._web)
            self._page.load(self._page_url)
        else:
            self._picture_positions = {}
            self._content.clear()
            self._stack.setCurrentWidget(self._text)
            self._text.show_text(text_body or "", pictures)

    def _show_card(self, shown: ShowResult) -> None:
        """The invitation card above the text; alone, taking the reader's room, when the mail's own text shows nothing (D-310)."""
        invitation = shown.invitation
        layout = self.layout()
        if invitation is None:
            self._stack.show()  # first: the keyboard focus can go back to it before the card area goes (OR-16)
            self._card_area.hide()
            self._card.clear()
            return
        self._card.show_invitation(invitation)
        self._card_area.verticalScrollBar().setValue(0)
        self._card_area.horizontalScrollBar().setValue(0)  # every card opens at its top left
        alone = invitation.text_shows_nothing
        self._card_area.show()  # first: a hidden part cannot take the keyboard focus
        focus = QApplication.focusWidget()
        if alone and focus is not None and self._stack.isAncestorOf(focus):
            self._card_area.setFocus()  # the text area goes: the keyboard focus stays in the reader (OR-16)
        self._stack.setVisible(not alone)
        if isinstance(layout, QVBoxLayout):
            layout.setStretchFactor(self._card_area, 1 if alone else 0)
        self._fit_card()

    @property
    def invitation(self) -> InvitationInfo | None:
        """The card on screen, if any."""
        return self._card.info

    def _card_sizes(self) -> None:
        """The room around the card and its widest, at the size factor of now."""
        self._card_rows.setContentsMargins(px_for(self, 16), px_for(self, 12), px_for(self, 16), px_for(self, 12))
        self._card.setMaximumWidth(px_for(self, 760))

    def _fit_card(self) -> None:
        """With text below, the card area is as high as the card at the width it has now (its lines wrap), never higher; alone it takes the
        reader's room. Worked out again whenever its width changes."""
        info = self._card.info
        if not self._card_area.isVisible() or info is None or info.text_shows_nothing:
            self._card_area.setMaximumHeight(16777215)
            self._card_area.wanted_height = 0
            self._card_holder.fit(self._card_area.viewport().width())
            return
        holder = self._card_holder
        holder.fit(self._card_area.viewport().width())
        sideways = self._card_area.horizontalScrollBar()
        extra = sideways.sizeHint().height() if holder.minimumSizeHint().width() > self._card_area.viewport().width() else 0
        needed = holder.fitted_height + extra + 2 * self._card_area.frameWidth()
        self._card_area.wanted_height = needed  # what it asks for; a low reader gives it less, and the card scrolls
        self._card_area.setMaximumHeight(needed)
        self._card_area.updateGeometry()

    def set_width_rule(self, smallest: int) -> None:
        """0: what the message needs sets the reader's smallest width (a message window, or the reading pane when it may widen to fit the
        message); otherwise, while a message is shown, its smallest width is this fixed one, whatever the message, and a word too long
        to wrap brings a sideways scrollbar to the header (D-304)."""
        self._smallest_width = smallest
        follows = smallest == 0
        self._header_area.set_follows_content(follows)
        self._date.set_wraps(not follows)  # unfolded, a narrow pane puts the date on two lines rather than scroll sideways
        self._banner_label.setWordWrap(not follows)  # the remote content line wraps
        self._banner_layout.setDirection(QBoxLayout.Direction.LeftToRight if follows else QBoxLayout.Direction.TopToBottom)  # its buttons below it
        self._banner_buttons.set_wraps(not follows)
        self._banner.set_sets_width(follows)  # its one line sets the smallest width of the message, as it did in the layout
        self._attachments.set_wraps(not follows)  # the files go on in a next row
        self.setMinimumWidth(smallest if smallest and self._header_area.isVisibleTo(self) else 0)  # 0: what its parts need counts

    def header_scrolls_sideways(self) -> bool:
        return self._header_area.horizontalScrollBar().isVisible()

    def set_tags(self, names: tuple[str, ...], colour_of: Callable[[str], QColor]) -> None:
        """The tags of the shown message, each with its own colour: small squares under the date."""
        self._tags.set_tags(tuple((name, colour_of(name)) for name in names))

    def _subject_fit(self, subject: str, width: int) -> tuple[str, str]:
        """The folded subject: on one line, shortened with "…" when too long; then the whole subject is its hover text."""
        line = subject.replace("\r", " ").replace("\n", " ")
        metrics = self._subject.fontMetrics()
        if width < 0 or metrics.horizontalAdvance(line) <= width:
            return line, ""
        return metrics.elidedText(line, Qt.TextElideMode.ElideRight, width), plain_tip(subject)

    def _people_fit(self, who: str, recipients: tuple[str, ...], copies: tuple[str, ...], width: int) -> tuple[str, str]:
        """The folded sender and people: the sender on one line (shortened with "…", the whole sender as hover text), and the names it
        went to that fit on the next line, then "+N more" for the others."""
        bold = QFont(self._people.font())
        bold.setBold(True)
        bold_metrics = QFontMetrics(bold)
        sender = who if width < 0 or bold_metrics.horizontalAdvance(who) <= width else bold_metrics.elidedText(who, Qt.TextElideMode.ElideRight, width)
        names = [("to " if place == 0 else ", ", name) for place, name in enumerate(recipients)]
        names += [(("; cc " if recipients else "cc ") if place == 0 else ", ", name) for place, name in enumerate(copies)]
        shown, hidden = fold_names(names, self._people.fontMetrics(), width)
        self._people_hidden = hidden
        muted = self.palette().color(QPalette.ColorRole.PlaceholderText).name()
        line = f'<span style="color:{muted}">{html.escape(shown)}</span>' if shown else ""
        if hidden:
            line += f' <a href="more">+{hidden} more</a>'
        return f"<b>{html.escape(sender)}</b><br>{line}", plain_tip(who) if sender != who else ""

    def _people_full(self, less: bool) -> str:
        """The unfolded sender and people: every name, wrapped; "less" at the end when the folded line had more."""
        who, recipients, copies = self._people_shown
        lines = [f"<b>{html.escape(who)}</b>"]
        muted = self.palette().color(QPalette.ColorRole.PlaceholderText).name()
        if recipients:
            lines.append(f'<span style="color:{muted}">to {html.escape(", ".join(recipients))}</span>')
        if copies:
            lines.append(f'<span style="color:{muted}">cc {html.escape(", ".join(copies))}</span>')
        if less:
            lines[-1] += ' <a href="less">less</a>'
        return "<br>".join(lines)

    def _fold_link(self, link: str) -> None:
        self.set_header_folded(link != "more")

    def set_header_folded(self, folded: bool) -> None:
        """Unfolded ("+N more"), the header shows every name, the whole subject and every file, and grows with them; folded ("less") it
        is as high as for every message again. Only this changes the header's height; a new message always opens folded."""
        people_less = not folded and self._people.folded and self._people_hidden > 0
        files_less = not folded and self._attachments.folded and self._attachments.hidden_count > 0
        if folded == self._people.folded:
            return
        self._people.set_folded(folded, self._people_full(less=people_less))
        self._subject.set_folded(folded)
        self._date.set_folded(folded)
        self._attachments.set_folded(folded, less=files_less)
        self._lay_out_header()

    def _lay_out_header(self) -> None:
        """The header's lines changed: its height follows them at once, and its lines get their width now, so what fits on them is
        known before the message is painted."""
        self._dated.invalidate()  # Qt keeps the height of the sender's row until the header is laid out again
        self._header_row.invalidate()
        self._header_layout.invalidate()
        self._header_area.updateGeometry()
        self._attachment_layout.invalidate()
        self._attachment_line.updateGeometry()
        layout = self.layout()
        if layout is not None:
            layout.activate()
        self._header_layout.activate()

    def header_folded(self) -> bool:
        return self._people.folded

    def _fill_attachments(self, attachments: tuple[AttachmentInfo, ...]) -> None:
        self._attachments.clear()
        self._attachments.set_folded(True)
        self._attachments.line_height = max(QPushButton.sizeHint(self._chip_probe).height(), self._attachments.link.fontMetrics().height())
        self._attachment_line.setMinimumHeight(self._attachments.folded_height())
        self._chips = QButtonGroup(self._attachments)  # one file selected at a time
        self._names = {position: attachment.suggested_name for position, attachment in enumerate(attachments)}
        shown = 0
        for position, attachment in enumerate(attachments):
            if attachment.inline or attachment.card:  # shown in the text, or as the invitation card
                continue
            chip = AttachmentChip(attachment.suggested_name, file_size(attachment.size), self._attachments)
            self._chips.addButton(chip, position)
            chip.open_requested.connect(partial(self._open_attachment, position))
            chip.customContextMenuRequested.connect(partial(self._chip_menu, chip, position))
            self._attachments.add(chip)
            QWidget.setTabOrder(chip, self._attachments.link)  # Tab: the files, then "+N more"
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

    def message_menu(self, position: int, link: str) -> QMenu:
        """What a right-click in the text offers: on a picture of the message Open attachment and Save as… (D-236), on a link
        Copy link address (D-281), which copies the link fully encoded, so it works wherever it is pasted; a picture that is a link
        has both. A link within the message itself (#top, an empty one, a picture's own address) is no address to copy. In plain text
        Copy (of the selected text) and Select All follow, as on the rest of the text."""
        menu = self.attachment_menu(position) if position >= 0 else QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        address = QUrl(link)
        if link and address.scheme().lower() not in _LOCAL_SCHEMES:
            menu.addAction("Copy link address", partial(QGuiApplication.clipboard().setText, address.toString(QUrl.ComponentFormattingOption.FullyEncoded)))
        if link and self._stack.currentWidget() is self._text:
            menu.addSeparator()
            copy = menu.addAction("Copy", self._text.copy)
            copy.setEnabled(self._text.textCursor().hasSelection())
            menu.addAction("Select All", self._text.selectAll)
        return menu

    def _message_menu(self, position: int, link: str, where: QPoint) -> None:
        menu = self.message_menu(position, link)
        if menu.actions():
            menu.popup(where)
        else:
            menu.deleteLater()  # a link within the message itself: nothing to offer, as before

    def _page_menu(self, point: QPoint) -> None:
        """A right-click in an HTML message: a picture of the message offers Open attachment and Save as…, a link Copy link address;
        elsewhere nothing, as before."""
        request = self._web.lastContextMenuRequest()
        if request is None:
            return
        position = -1
        if request.mediaType() == request.MediaType.MediaTypeImage:
            position = self._picture_positions.get(request.mediaUrl().toString(), -1)
        link = request.linkUrl().toString() if request.linkUrl().isValid() else ""
        if position >= 0 or link:
            self._message_menu(position, link, self._web.mapToGlobal(point))

    def _after_load(self, ok: bool) -> None:
        if ok and self._blocker.blocked and not self._blocker.allow_remote and self._stack.currentWidget() is self._web:  # only with a message on screen
            self._banner.show()

    def _reserve_banner_room(self, height: int) -> None:
        """The page's own top gets empty room as tall as the remote content line lying over it: nothing of the message is under it.
        The room is set in the page by Chrono Mail's own script (its own world; the mail's scripts never run), so no reload is needed."""
        if self._stack.currentWidget() is self._web:
            css_pixels = height / max(0.01, self._web.zoomFactor())
            self._page.runJavaScript(_BANNER_ROOM_SCRIPT % css_pixels, QWebEngineScript.ScriptWorldId.ApplicationWorld)

    def set_zoom(self, percent: int) -> None:
        """The size of the text of the message, in percent (50 to 300)."""
        self._zoom = max(50, min(300, percent))
        self._web.setZoomFactor(self._zoom / 100)
        if self.banner_visible():
            self._reserve_banner_room(self._banner.height())
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
        self._page.load(self._page_url)  # the same message again, now with what comes from the internet

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

