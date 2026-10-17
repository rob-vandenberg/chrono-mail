import base64
import html
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QByteArray, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import theme
from chrono_mail.ui.formatting import avatar_colour, initials
from chrono_mail.ui.tagchips import TagColourBook

# --- Version ------------------------------------------------------------
__version__ = "contactdetail.py 1.1.172"

# --- Version history ----------------------------------------------------
# v1.1.172: The initials' colour from the theme (D-158)
# v0.0.62: Initial detail pane of a contact with its mail history

AVATAR: int = 64


def avatar_pixmap(name: str, size: int, photo_base64: str = "") -> QPixmap:
    """The photo of a contact in a circle, or the initials on a colour."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    photo = QPixmap()
    if photo_base64:
        photo.loadFromData(QByteArray(base64.b64decode(photo_base64)))
    if not photo.isNull():
        scaled = photo.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        from PySide6.QtGui import QPainterPath

        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)
        painter.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(avatar_colour(name))
        painter.drawEllipse(0, 0, size, size)
        painter.setPen(QColor(theme().on_colour))
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(int(size * 0.38))
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, initials(name))
    painter.end()
    return pixmap


def _e(text: object) -> str:
    return html.escape(str(text))


def _types(types: tuple[str, ...]) -> str:
    return f" <small>({_e(', '.join(types))})</small>" if types else ""


class ContactDetail(QWidget):
    """The right side of the contacts: a header with the photo, the name and the tags, and the tabs Detail and History."""

    compose_requested = Signal(str)  # an e-mail address was clicked

    def __init__(self, backend: Backend, colours: TagColourBook, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._colours: TagColourBook = colours
        self._current: object | None = None
        self._generation: int = 0
        self.mail_accounts: list[str] = []  # the accounts whose mail is searched for the history; set by the window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 8)
        header = QHBoxLayout()
        self.photo = QLabel(self)
        self.photo.setFixedSize(AVATAR, AVATAR)
        self.name = QLabel(self)
        font = self.name.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.5)
        self.name.setFont(font)
        self.subtitle = QLabel(self)
        self.subtitle.setEnabled(False)
        self.tag_label = QLabel(self)
        self.tag_label.setTextFormat(Qt.TextFormat.RichText)
        titles = QVBoxLayout()
        titles.addWidget(self.name)
        titles.addWidget(self.subtitle)
        header.addWidget(self.photo)
        header.addLayout(titles, 1)
        header.addWidget(self.tag_label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)
        self.tabs = QTabWidget(self)
        self.text = QTextBrowser(self)
        self.text.setOpenLinks(False)
        self.text.anchorClicked.connect(self._link)
        self.text.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.history = QListWidget(self)
        self.history.setFrameShape(QListWidget.Shape.NoFrame)
        self.tabs.addTab(self.text, "Detail")
        self.tabs.addTab(self.history, "History")
        self.tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self.tabs, 1)
        self.show_nothing()

    # --- what is shown ------------------------------------------------------------------------

    def show_nothing(self, text: str = "Select a contact") -> None:
        self._current = None
        self.photo.clear()
        self.name.setText("")
        self.subtitle.setText("")
        self.tag_label.clear()
        self.history.clear()
        self.text.setHtml(f"<p style='margin:30px' align=center><i>{_e(text)}</i></p>")

    def set_contact(self, info: object) -> None:
        self._current = info
        name = str(getattr(info, "name"))
        self.photo.setPixmap(avatar_pixmap(name, AVATAR, str(getattr(info, "photo_base64"))))
        self.name.setText(name)
        line = " · ".join(part for part in (str(getattr(info, "title")), str(getattr(info, "organization"))) if part)
        self.subtitle.setText(line)
        self.tag_label.setText(self._colours.html(getattr(info, "tags")))
        self.text.setHtml(self._html(info))
        self.history.clear()
        if self.tabs.currentIndex() == 1:
            self._load_history()

    def _html(self, c: object) -> str:
        def section(title: str, rows: list[str]) -> str:
            return f"<h4 style='margin-bottom:2px'>{_e(title)}</h4><table cellspacing=4>{''.join(rows)}</table>" if rows else ""

        def row(label: str, value: str) -> str:
            return f"<tr><td style='color:gray' align=right>{_e(label)}</td><td>{value}</td></tr>"

        info: list[str] = []
        for entry in getattr(c, "emails"):
            address = str(getattr(entry, "value"))
            info.append(row("E-mail", f"<a href='mailto:{_e(address)}'>{_e(address)}</a>{_types(getattr(entry, 'types'))}"))
        for entry in getattr(c, "phones"):
            info.append(row("Phone", f"{_e(getattr(entry, 'value'))}{_types(getattr(entry, 'types'))}"))
        for entry in getattr(c, "urls"):
            link = str(getattr(entry, "value"))
            info.append(row("Website", f"<a href='{_e(link)}'>{_e(link)}</a>"))
        work: list[str] = []
        for label, name in (("Company", "organization"), ("Department", "department"), ("Job title", "title"), ("Role", "role")):
            if str(getattr(c, name)):
                work.append(row(label, _e(getattr(c, name))))
        personal: list[str] = []
        for label, name in (("Nickname", "nicknames"), ("Birthday", "birthday"), ("Anniversary", "anniversary")):
            value = getattr(c, name)
            text = ", ".join(value) if isinstance(value, tuple) else str(value)
            if text:
                personal.append(row(label, _e(text)))
        sections = [section("Contact information", info), section("Work details", work), section("Personal details", personal)]
        for address in getattr(c, "addresses"):
            kind = " ".join(getattr(address, "types")).capitalize() or "Address"
            sections.append(section(f"{kind} address", [row("", "<br>".join(_e(line) for line in getattr(address, "lines")))]))
        if str(getattr(c, "note")):
            sections.append(f"<h4 style='margin-bottom:2px'>Note</h4><p>{_e(getattr(c, 'note')).replace(chr(10), '<br>')}</p>")
        return "".join(sections) or "<p><i>Nothing else is known about this contact.</i></p>"

    def _link(self, url: QUrl) -> None:
        if url.scheme() == "mailto":
            self.compose_requested.emit(url.path())

    # --- history ------------------------------------------------------------------------------

    def _tab_changed(self, index: int) -> None:
        if index == 1:
            self._load_history()

    def _load_history(self) -> None:
        """The mail that came from this contact or went to them (a search of the mail of every account, by address)."""
        info = self._current
        self.history.clear()
        if info is None:
            return
        addresses = [str(getattr(entry, "value")) for entry in getattr(info, "emails") if str(getattr(entry, "value"))]
        if not addresses or not self.mail_accounts:
            self.history.addItem("No mail to show: this contact has no e-mail address.")
            return
        self._generation += 1
        generation = self._generation
        query = " OR ".join(f"from:{address} OR to:{address}" for address in addresses[:5])
        found: list[tuple[datetime | None, str, str]] = []
        remaining = {"count": len(self.mail_accounts)}

        def make_done(account: str) -> Callable[[object], None]:
            def done(result: object) -> None:
                if generation != self._generation:
                    return
                for row in list(result) if isinstance(result, (tuple, list)) else []:
                    found.append((getattr(row, "date"), str(getattr(row, "subject")) or "(no subject)", str(getattr(row, "sender"))))
                remaining["count"] -= 1
                if remaining["count"] == 0:
                    self._show_history(found)

            return done

        def failed(error: ChronoMailError) -> None:
            remaining["count"] -= 1
            if remaining["count"] == 0 and generation == self._generation:
                self._show_history(found)

        for account in self.mail_accounts:
            self._backend.call("message.search", {"account": account, "query": query, "limit": 50}, make_done(account), failed, owner=self)

    def _show_history(self, found: list[tuple[datetime | None, str, str]]) -> None:
        self.history.clear()
        if not found:
            self.history.addItem("No mail with this contact yet.")
            return
        for moment, subject, sender in sorted(found, key=lambda entry: entry[0].timestamp() if entry[0] else 0.0, reverse=True)[:100]:
            day = moment.astimezone().strftime("%Y-%m-%d") if moment else ""
            item = QListWidgetItem(f"{day}   {subject}   — {sender}")
            item.setSizeHint(QSize(0, 26))
            self.history.addItem(item)
