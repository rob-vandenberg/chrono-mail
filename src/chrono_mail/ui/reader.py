import html
import re
from email.utils import parseaddr
from typing import override

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core.colours import MAIL_PAGE
from chrono_mail.ui.formatting import display_name, file_size, initials, avatar_colour, long_date
from chrono_mail.ui.scale import px_for

# --- Version ------------------------------------------------------------
__version__ = "reader.py 1.1.172"

# --- Version history ----------------------------------------------------
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


class ReaderWidget(QWidget):
    """Shows one message: the header, its attachments, and the text or HTML."""

    attachment_save_requested = Signal(int, str)  # position in the message, suggested file name
    link_activated = Signal(QUrl)
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
        self._text = QTextBrowser(self._stack)
        self._text.setOpenLinks(False)
        self._text.setFrameShape(QFrame.Shape.NoFrame)
        self._text.anchorClicked.connect(self.link_activated)
        self._web, self._page, self._blocker = make_safe_view(self._stack)
        self._page.link_clicked.connect(self.link_activated)
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
        self._zoom: int = 100
        self._always_remote.clicked.connect(self._allow_sender)
        self.clear()

    # --- showing --------------------------------------------------------------------------------

    def clear(self) -> None:
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

    def show_message(self, shown: object) -> None:
        """Display a message.show result."""
        self._header.show()
        self._subject.setText(str(getattr(shown, "subject")) or "(no subject)")
        senders: tuple[str, ...] = getattr(shown, "sender")
        recipients: tuple[str, ...] = getattr(shown, "to")
        copies: tuple[str, ...] = getattr(shown, "cc")
        who = senders[0] if senders else "(unknown sender)"
        lines = [f"<b>{html.escape(who)}</b>"]
        muted = self.palette().color(QPalette.ColorRole.PlaceholderText).name()
        if recipients:
            lines.append(f'<span style="color:{muted}">to {html.escape(", ".join(recipients))}</span>')
        if copies:
            lines.append(f'<span style="color:{muted}">cc {html.escape(", ".join(copies))}</span>')
        self._people.setText("<br>".join(lines))
        self._date.setText(long_date(getattr(shown, "date")))
        name = display_name(who)
        self._avatar.setText(f'<div style="color:white; font-weight:bold">{html.escape(initials(name))}</div>')
        self._avatar.setAlignment(self._avatar.alignment().AlignCenter)
        self._avatar.setStyleSheet(f"background-color: {avatar_colour(who).name()}; border-radius: {px_for(self, 40) // 2}px;")
        self._fill_attachments(getattr(shown, "attachments"))
        self._images = {str(image.content_id).lower(): (str(image.content_type), str(image.data_base64)) for image in getattr(shown, "inline_images")}
        html_body = getattr(shown, "html_body")
        text_body = getattr(shown, "text_body")
        self._banner.hide()
        self._sender = parseaddr(who)[1].lower() if senders else ""
        self._always_remote.setVisible(bool(self._sender))
        if html_body and self.prefer_plain and text_body:
            html_body = None  # the person prefers the plain text part
        if html_body:
            self._current_html = embed_inline_images(str(html_body), self._images)
            self._blocker.allow_remote = self.always_remote or (bool(self._sender) and self._sender in self.allowed_senders)
            self._blocker.blocked = 0
            self._stack.setCurrentWidget(self._web)
            self._page.setHtml(self._current_html)
        else:
            self._stack.setCurrentWidget(self._text)
            self._text.setHtml(f'<pre style="white-space: pre-wrap; font-family: inherit; margin: 0">{linkify(str(text_body or ""))}</pre>')
            self._text.moveCursor(self._text.textCursor().MoveOperation.Start)

    def set_tags(self, labels: str) -> None:
        """The tags of the shown message (rich text from TagColourBook.html); empty hides the line."""
        self._tags.setText(labels)
        self._tags.setVisible(bool(labels))

    def _fill_attachments(self, attachments: tuple[object, ...]) -> None:
        while self._attachments_layout.count() > 1:
            item = self._attachments_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        shown = 0
        for position, attachment in enumerate(attachments):
            if getattr(attachment, "inline"):
                continue
            button = QPushButton(f"📎 {getattr(attachment, 'suggested_name')} ({file_size(int(getattr(attachment, 'size')))})", self._attachments)
            button.setFlat(True)
            button.setToolTip("Click to save this attachment")
            suggested = str(getattr(attachment, "suggested_name"))
            button.clicked.connect(lambda checked=False, index=position, name=suggested: self.attachment_save_requested.emit(index, name))
            self._attachments_layout.insertWidget(self._attachments_layout.count() - 1, button)
            shown += 1
        self._attachments.setVisible(shown > 0)

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
    def text_view(self) -> QTextBrowser:
        return self._text

    def showing_html(self) -> bool:
        return self._stack.currentWidget() is self._web

    def banner_visible(self) -> bool:
        return not self._banner.isHidden()

