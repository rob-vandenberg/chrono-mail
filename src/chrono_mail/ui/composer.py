import html
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import override

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.pim.times import moment_at
from chrono_mail.engine.mailbox import GoneMeanwhile, SentAlready
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.commands_mail import ComposeResult
from chrono_mail.engine.commands_accounts import AccountSummary
from chrono_mail.core import ChronoMailError, ConfigurationError, MessageFormatError
from chrono_mail.engine.commands_drafts import DRAFT_SAVE, DraftKind, DraftOpenResult, DraftSaveParameters, DraftSaveResult
from chrono_mail.engine.commands_mail import MAIL_SEND, MESSAGE_DELETE, DeleteParameters, SendParameters, SendResult
from chrono_mail.engine.commands_schedule import MAIL_EDIT_SCHEDULED, ScheduledEdit, ScheduledInfo
from chrono_mail.ui.addresscompleter import AddressCompleter
from chrono_mail.ui import scale, theme
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import red_style
from chrono_mail.ui.icons import application_icon
from chrono_mail.ui.prefs import read_preferences
from chrono_mail.mime.pictures import data_address, pictures_out_of
from chrono_mail.ui.richeditor import Picture, RichEditor
from chrono_mail.ui.composertexts import KEYED, TextsInMessage, signature_block, with_sent_from, with_signature
from chrono_mail.config.model import AccountTexts
from chrono_mail.engine.commands_signatures import SNIPPET_ALL, NoParameters as SnippetNoParameters, SnippetText
from chrono_mail.ui.windowstate import restore_geometry, save_geometry
from chrono_mail.ui.whendialog import WhenDialog

# --- Version ------------------------------------------------------------
__version__ = "composer.py 4.5.628"

# --- Version history ----------------------------------------------------
# v4.5.628: A text that cannot be read is left out of the keys and the Signature button (D-358)
# v4.5.626: Templates and signatures from the library: the account's template and signature when a message opens, Ctrl+1 to
#           Ctrl+7, the Signature button, a default signature follows From; "Sent from Chrono Mail" when Settings asks for it (B-24, D-358)
# v4.4.623: The rich text is written in Settings > Writing > Default font, which the message carries (D-354)
# v4.4.622: A reply or forward in rich text quotes the original with its formatting and pictures; a forward in plain text carries them as
#           attachments; a reply switched to plain text leaves the quoted pictures out (D-353)
# v4.4.621: A file that is not a picture dragged onto the text is attached; going to plain text makes the pictures attachments (D-353)
# v4.2.442: a Send stopped by the outgoing server's certificate closes the window without its box and says so (certificate_stopped): the Verify certificate window asks then (D-299)
# v4.2.430: an escaped quote inside a quoted name never ends it when the addresses are split
# v4.2.429: save_and_close_quietly and close_for_quit: after an update Chrono Mail closes without a Cancel; what is not saved goes to Drafts first
# v3.6.358: after a save only the attachments it took are marked as stored, at the places the save gives, in place; what was added or removed
#           during the save stays so and still counts as a change; no reading the draft back; Save, Send and Discard asked while a save
#           runs follow it, never dropped; the newest decision replaces the one waiting (CR-03, D-271)
# v3.6.351: a send that failed after its copy may have been stored says to look in the Outbox and Sent first, never "nothing was stored"
# v3.6.349: the Schedule window opens on the message's time without the platform's conversion (Windows refuses past 3000, RV-204)
# v3.1.341: a send that failed hands the message to the Outbox and closes the window (Send again sent a second copy, RV-155)
# v2.2.314: Send follows Settings > General: a delay with a notice in the main window (waiting), or the Outbox until the next refresh; a message
#           taken back from the Outbox (Cancel) opens as one not saved yet, so Discard removes it (B-21); a failed send is tried again at the next refresh
# v1.9.274: R-19 round 4: the accounts and the draft or reply it starts from (a reply's and a draft's own fields told apart) are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v1.9.270: R-19: saving, opening, sending and deleting are typed calls, their parameters built as the engine's dataclasses
# v1.9.267: A failed automatic save is told in red on the bottom line, without a box (only Save shows the box too; the owner)
# v1.1.208: A closed message window is deleted: it leaves the list of open message windows, so Edit opens it again (D-178)
# v1.1.207: Edit mode for a scheduled message: Save keeps its time, Schedule… opens on its time and repeat, Send and Save draft greyed, no autosave to Drafts, closing asks to save the changes; it keeps its id (D-177)
# v1.1.203: Send later… is Schedule…; the confirmation names the Scheduled folder
# v1.1.199: The window is finished (no more autosaves) the moment the engine has the message, before any box is shown
# v1.1.198: schedule_only: a window that schedules (Send is Schedule…, no Send later); scheduled tells the id of the message now waiting
# v1.1.180: The account to send from is a From row above To (it was at the right of the buttons)
# v0.5.144: Its look at the size of the Other windows and dialogs slider
# v0.3.108: The way of writing is a button at the bottom left that shows Rich text or Plain text and chooses in its menu (was an on and off button at the right)
# v0.3.108: The text size is in points with a fraction (the Writing size slider) and can change while the window is open (set_text_size)
# v0.0.86: Forward as attachment: a checked box attaches the original message as an .eml file
# v0.0.83: The size and position of the main window, the message windows, the composers and the Settings window are remembered and only used when the window would be reachable on a connected screen
# v0.0.83: The size and position of the main window, the message windows, the composers and the Settings window are remembered and only used when the window would be reachable on a connected screen
# v0.0.78: Composer: new look (Send in blue on top, rounded fields, flat toolbar) and rich text is the default for new messages
# v0.0.69: Accounts are told apart by user name, incoming server and protocol
# v0.0.67: Messages that are sent again and again
# v0.0.53: Standard keyboard shortcuts
# v0.0.42: settings: autosave interval, subject warning, font size, reply under the quote
# v0.0.35: Send later and snooze
# v0.0.24: Rich text composing
# v0.0.23: address completion
# v0.0.20: Settings window, account editing, signatures
# v0.0.16: drafts: save, autosave, resume, keep attachments, save/discard/cancel on close
# v0.0.14: window icon
# v0.0.13: Initial composer; recipients are parsed one by one


def _split_addresses(text: str) -> list[str]:
    """Split on commas, semicolons and line breaks that are not inside quotes or angle brackets."""
    parts: list[str] = []
    current: list[str] = []
    quoted = angle = escaped = False
    for character in text:
        if escaped:
            escaped = False  # the character after a backslash inside quotes is part of the name (written_address)
        elif character == "\\" and quoted:
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif character == "<" and not quoted:
            angle = True
        elif character == ">" and not quoted:
            angle = False
        if character in ",;\n" and not quoted and not angle:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def parse_recipients(text: str) -> list[str] | None:
    """'Alice <a@x.org>, b@y.org' as a list of addresses, or None when something in it is not an address.

    Each address is read on its own: getaddresses() of Python 3.14 silently returns nothing for the
    whole field when one part is malformed, which would drop valid addresses.
    """
    found: list[str] = []
    for part in _split_addresses(text):
        name, address = parseaddr(part)
        if address.count("@") != 1 or address.startswith("@") or address.endswith("@") or any(character.isspace() for character in address):
            return None
        found.append(formataddr((name, address)) if name else address)
    return found


AUTOSAVE_SECONDS: int = 30
@dataclass(frozen=True, slots=True)
class ComposeOptions:
    """What the settings say about writing: how often to save, whether to warn about a missing subject, the font size
    of the editor (0 is the system's) and whether replies are written under the quote."""

    autosave_seconds: int = AUTOSAVE_SECONDS
    warn_no_subject: bool = True
    font_size: float = 0.0  # points, as shown (the size slider included)
    reply_below: bool = False
    font_family: str = ""  # Settings > Writing > Default font: written in and sent with an HTML message (D-354); "" Qt's own
    font_points: int = 0  # its size in points as sent; 0: none sent
    sent_from_line: bool = False  # every message sent ends with "Sent from Chrono Mail" (B-24, D-358)


def _paragraphs_of(text: str) -> str:
    """Plain text as HTML paragraphs, one per line."""
    return "".join(f"<p>{html.escape(line) or '<br />'}</p>" for line in text.split("\n"))


DRAFT_KINDS: dict[str, DraftKind] = {"new": "new", "reply": "reply", "reply_all": "reply_all", "forward": "forward", "forward_attachment": "forward_attachment"}


ROLE_PATH: int = 256  # an attachment that is a file on this computer
ROLE_KEPT: int = 257  # an attachment that is already inside the stored draft (its position there)


class ComposeWindow(QMainWindow):
    """Writes a new message, a reply, or a forward, and sends it through the engine.

    The message is saved as a draft when asked and automatically every 30 seconds while it changes; a
    message that cannot be sent is kept in the Outbox folder by the engine. Nothing the person wrote
    is lost, and nothing is sent again by itself.
    """

    sent = Signal(str)  # account id
    draft_saved = Signal(str)  # account id
    scheduled = Signal(str)  # the id of the message that now waits in the Outbox to be sent at its time
    scheduled_changed = Signal(object)  # edit mode: the ScheduledInfo of the message as it waits now (the same id)
    waiting = Signal(str, str, str, int)  # a delayed send waits in the Outbox: account, message id, subject, seconds of its countdown (0: none, B-21)
    certificate_stopped = Signal(object, str)  # the outgoing server's certificate could not be verified: its CertificateTrouble, the message id (D-299)

    def __init__(
        self,
        backend: Backend,
        accounts: list[AccountSummary],
        account_id: str,
        draft: DraftOpenResult | ComposeResult | None = None,
        source: tuple[str, str] | None = None,
        resume: tuple[str, str] | None = None,
        rich: bool = False,
        options: ComposeOptions = ComposeOptions(),
        parent: QWidget | None = None,
        settings: QSettings | None = None,
        schedule_only: bool = False,
        editing: ScheduledInfo | None = None,
        taken_back: bool = False,
        template: str = "",
    ) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._schedule_only: bool = schedule_only  # made from Settings > Scheduled messages: the message is scheduled, never sent at once
        self._editing: ScheduledInfo | None = editing  # a message that waits in the Scheduled folder: it stays scheduled, never a draft
        self._options: ComposeOptions = options
        resumed_source = (str(draft.source_folder), draft.source_message) if isinstance(draft, DraftOpenResult) and draft.source_message else None  # a draft taken up again
        self._source: tuple[str, str] | None = source or resumed_source
        self._kind: str = draft.kind if draft is not None else "new"
        self._in_reply_to: str | None = draft.in_reply_to if draft is not None else None
        self._references: tuple[str, ...] = tuple(draft.references) if draft is not None else ()
        self._stored: tuple[str, str] | None = resume  # the saved version of this message (folder, id), if there is one
        self._created_here: bool = resume is None  # a draft this window made itself is removed again on "Discard"
        self._sending: bool = False
        self._delay: int = 0  # the countdown of the send under way (Settings > General, B-21)
        self._saving: bool = False
        self._waiting: Callable[[], None] | None = None  # the decision made while a save ran, done once it ended; a newer one replaces it (CR-03)
        self._save_failed: bool = False  # the last save failed (its box or red line said why)
        self._discarding: bool = False  # Discard was chosen while a save ran: the window is gone from the screen and closes once it ended
        self._finished: bool = False
        self._account_id: str = account_id
        self.setWindowTitle("New message")
        self.setWindowIcon(application_icon())
        self.setObjectName("composeWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)  # a closed window is gone: it leaves the main window's list of open message windows
        self.setStyleSheet(theme.stylesheet(scale.windows()))  # a window of its own does not inherit the look of the main window; its sizes follow Other windows and dialogs
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 10, 12, 10)
        form = QFormLayout()
        self.fields: QFormLayout = form  # From, To, Cc, Bcc and Subject
        self.from_box = QComboBox(central)
        for account in accounts:
            self.from_box.addItem(f"{account.display_name} <{account.email_address}> - {account.server_label}", account.account_id)
        index = self.from_box.findData(account_id)
        self.from_box.setCurrentIndex(max(0, index))
        self.to = QLineEdit(central)
        self.cc = QLineEdit(central)
        self.bcc = QLineEdit(central)
        self.subject = QLineEdit(central)
        form.addRow("From", self.from_box)
        form.addRow("To", self.to)
        form.addRow("Cc", self.cc)
        form.addRow("Bcc", self.bcc)
        form.addRow("Subject", self.subject)
        self.keep_button = QPushButton("Save", central)  # edit mode: the changes are saved, its time and repeat stay
        self.keep_button.setObjectName("sendButton")
        self.keep_button.setToolTip("Save the changes; the message keeps its time")
        self.keep_button.hide()
        self.save_button = QPushButton("Save draft", central)
        self.later_button = QPushButton("Schedule…", central)
        self.send_button = QPushButton("Send", central)
        self.send_button.setObjectName("sendButton")
        self.send_button.setDefault(True)
        for flat in (self.save_button, self.later_button):
            flat.setObjectName("flatButton")
        top = QHBoxLayout()  # what to do with the message
        top.addWidget(self.keep_button)
        top.addWidget(self.send_button)
        top.addWidget(self.later_button)
        top.addWidget(self.save_button)
        top.addStretch(1)
        if schedule_only:  # the main action asks for the time; there is no way to send it at once by mistake
            self.send_button.setText("Schedule…")
            self.later_button.hide()
            self.setWindowTitle("New scheduled message")
        layout.addLayout(top)
        layout.addLayout(form)
        self.body = QPlainTextEdit(central)
        self.rich = RichEditor(central)
        self.rich.files_dropped.connect(self._attach_dropped)
        self.editor_stack = QStackedWidget(central)
        self.editor_stack.addWidget(self.body)
        self.editor_stack.addWidget(self.rich)
        layout.addWidget(self.editor_stack, 1)

        files = QHBoxLayout()
        self.attachments = QListWidget(central)
        self.attachments.setMaximumHeight(70)
        side = QVBoxLayout()
        self.attach_button = QPushButton("Attach a file…", central)
        self.remove_button = QPushButton("Remove", central)
        side.addWidget(self.attach_button)
        side.addWidget(self.remove_button)
        side.addStretch(1)
        files.addWidget(self.attachments, 1)
        files.addLayout(side)
        layout.addLayout(files)
        self.include_original = QCheckBox("", central)
        self.include_original.hide()
        layout.addWidget(self.include_original)

        buttons = QHBoxLayout()
        self.status = QLabel("", central)
        self.rich_button = QPushButton("Plain text", central)  # shows the way of writing; its menu chooses the other
        self.rich_button.setToolTip("Rich text: bold, italic, lists and links, sent as text and as HTML. Plain text: text only.")
        self.rich_button.setObjectName("flatButton")
        mode_menu = QMenu(self.rich_button)
        modes = QActionGroup(mode_menu)
        self.rich_action = QAction("Rich text", modes)
        self.plain_action = QAction("Plain text", modes)
        for action, rich_wanted in ((self.rich_action, True), (self.plain_action, False)):
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, wanted=rich_wanted: self._mode_chosen(wanted))
            mode_menu.addAction(action)
        self.plain_action.setChecked(True)
        self.rich_button.setMenu(mode_menu)
        buttons.addWidget(self.rich_button)
        self.signature_button = QPushButton("Signature", central)  # every text of the library, with its key (B-24, D-358)
        self.signature_button.setObjectName("flatButton")
        self.signature_button.setMenu(QMenu(self.signature_button))
        self.signature_button.setEnabled(False)
        buttons.addWidget(self.signature_button)
        buttons.addWidget(self.status, 1)
        layout.addLayout(buttons)
        self.setCentralWidget(central)
        self.resize(760, 640)
        self._settings: QSettings | None = settings
        restore_geometry(self, settings, "composer_geometry")

        if draft is not None:
            self.to.setText(", ".join(draft.to))
            self.cc.setText(", ".join(draft.cc))
            self.bcc.setText(", ".join(draft.bcc) if isinstance(draft, DraftOpenResult) else "")  # a reply starts without Bcc
            self.subject.setText(draft.subject)
            self.body.setPlainText(draft.body)
            if isinstance(draft, ComposeResult):  # what a forward takes along
                self._original_names = draft.attachment_names
                if draft.attachment_names:
                    self.include_original.setChecked(True)
            for stored in draft.attachments if isinstance(draft, DraftOpenResult) else ():  # a draft's own attachments
                self._add_kept(stored.position, stored.name, stored.size)
            self.setWindowTitle(draft.subject or "New message")
            self.body.moveCursor(self.body.textCursor().MoveOperation.Start)
        if self._kind == "forward_attachment" and self.include_original.isHidden():  # a draft taken up again: the original still goes along
            self.include_original.setText("Attach the original message")
            self.include_original.setChecked(True)
            self.include_original.show()
        self._accounts: list[AccountSummary] = accounts
        self.body.moveCursor(self.body.textCursor().MoveOperation.End if options.reply_below and self._kind in ("reply", "reply_all") and resume is None else self.body.textCursor().MoveOperation.Start)
        self.body.document().setModified(False)
        if options.font_family:
            written = self.rich.edit.font()
            written.setFamily(options.font_family)
            self.rich.edit.setFont(written)
        self.rich.sent_font = (options.font_family, options.font_points) if options.font_family and options.font_points > 0 else None
        if options.font_size > 0:
            self.set_text_size(options.font_size)
        stored_html = draft.html if isinstance(draft, DraftOpenResult) else None
        quoted = draft.quoted_html if isinstance(draft, ComposeResult) and resume is None else None
        if stored_html:  # a rich text draft comes back as rich text
            self.set_rich(True, stored_html)
        elif rich and quoted:  # the original quoted with its formatting and pictures (D-353)
            below = options.reply_below and self._kind in ("reply", "reply_all")
            own = _paragraphs_of("\n")
            self.set_rich(True, quoted + own if below else own + quoted)
            self._quoted_pictures = set(self.rich.pictures)  # the own part has none: these are the original's
            self.rich.edit.moveCursor(self.rich.edit.textCursor().MoveOperation.End if below else self.rich.edit.textCursor().MoveOperation.Start)
        elif rich:
            self.set_rich(True)
        if quoted and not self.is_rich() and self._kind == "forward":  # a forward in plain text carries the original's pictures as attachments
            for picture in pictures_out_of(quoted, lambda: "picture", leave_out_bad=True)[1]:
                self._add_picture(Picture(picture.content_type, picture.name, picture.data))
        self._show_original_names()
        if editing is not None:  # it stays scheduled: Save keeps its time, Schedule… changes it; sending now and drafts are on the page
            self.keep_button.show()
            self.keep_button.setDefault(True)
            self.send_button.setDefault(False)
            self.send_button.setObjectName("flatButton")
            self.send_button.setToolTip("Send now is on Settings > Scheduled messages")
            self.save_button.setToolTip("Move back to drafts is on Settings > Scheduled messages")
            self._grey_edit_buttons()
            self.setWindowTitle("Edit scheduled message")
        self.texts = TextsInMessage(self.body, self.rich, self.is_rich, self._kind, options.reply_below)  # templates and signatures (B-24, D-358)
        self._saved_snapshot: tuple[object, ...] = self._snapshot()
        backend.ask(SNIPPET_ALL, SnippetNoParameters(), lambda found: self._library_arrived(found, resume is None and editing is None, template), self._library_failed, owner=self)
        if taken_back:  # a send cancelled (B-21): as a message not saved yet, closing asks, and Discard removes its draft
            self._created_here = True
            self._saved_snapshot = ()

        for place in range(KEYED):  # Ctrl+1 to Ctrl+7: the first seven texts of the library as the signature (B-24, D-358)
            keyed = QAction(f"Signature {place + 1}", self)
            keyed.setShortcut(QKeySequence(f"Ctrl+{place + 1}"))
            keyed.triggered.connect(lambda checked=False, chosen=place: self._signature_by_key(chosen))
            self.addAction(keyed)
        for text, shortcut, handler in (
            ("Send", "Ctrl+Return", self._send_clicked), ("Send", "Ctrl+Enter", self._send_clicked), ("Save draft", "Ctrl+S", self._save_clicked), ("Close", "Esc", self.close),
        ):
            action = QAction(text, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(handler)
            self.addAction(action)
        self.from_box.currentIndexChanged.connect(self._account_changed)
        self.later_button.clicked.connect(self._later_clicked)
        self.send_button.clicked.connect(self._send_clicked)
        self.keep_button.clicked.connect(self._keep_clicked)
        self.save_button.clicked.connect(self._save_clicked)
        self.attach_button.clicked.connect(self._choose_files)
        self.remove_button.clicked.connect(self._remove_selected)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self._autosave)
        if options.autosave_seconds > 0:
            self.autosave_timer.start(options.autosave_seconds * 1000)
        self.completers = [AddressCompleter(backend, field) for field in (self.to, self.cc, self.bcc)]
        self.to.setFocus()

    # --- state ------------------------------------------------------------------------------------

    def set_text_size(self, points: float) -> None:
        """The size of the text being written (the Writing size slider)."""
        for editor in (self.body, self.rich.edit):
            font = editor.font()
            font.setPointSizeF(points)
            editor.setFont(font)

    def _texts_of(self, account_id: str) -> AccountTexts:
        return next((account.texts for account in self._accounts if account.account_id == account_id), AccountTexts())

    def _library_arrived(self, found: tuple[SnippetText, ...], fresh: bool, template: str) -> None:
        """The library: the Signature button's list and the keys; a new message gets the account's template and signature (a draft
        taken up again keeps what it has). A window nobody changed meanwhile still counts as unchanged."""
        self.texts.library = tuple(entry for entry in found if not entry.problem)  # one that could not be read is left out
        self._fill_signature_menu()
        if not fresh:
            self.texts.resumed = True  # a draft taken up again: its "-- " block is its signature
            return
        unchanged = self._snapshot() == self._saved_snapshot
        self.texts.open_with(self._texts_of(str(self.from_box.currentData())), template)
        if unchanged:
            self.body.document().setModified(False)
            self._saved_snapshot = self._snapshot()

    def _library_failed(self, error: ChronoMailError) -> None:
        """The templates and signatures could not be read: said, so the person knows why none was put in."""
        self._say(f"Your templates and signatures could not be read: {error}")

    def _fill_signature_menu(self) -> None:
        menu = self.signature_button.menu()
        assert menu is not None
        menu.clear()
        for place, entry in enumerate(self.texts.library):
            action = menu.addAction(entry.name + (f"\tCtrl+{place + 1}" if place < KEYED else ""))
            action.triggered.connect(lambda checked=False, chosen=entry.item_id: self.texts.put(chosen))
        self.signature_button.setEnabled(bool(self.texts.library))
        self.signature_button.setToolTip("Put in a signature" if self.texts.library else "Make templates and signatures in Settings > Templates and Signatures")

    def _signature_by_key(self, place: int) -> None:
        entry = self.texts.keyed(place)
        if entry is not None:
            self.texts.put(entry.item_id)

    def _account_changed(self) -> None:
        """Another sender was chosen: a default signature follows it; one put in by key stays (B-24, D-358)."""
        self.texts.account_changed(self._texts_of(str(self.from_box.currentData())))

    def _as_sent(self) -> tuple[str, str | None]:
        """The text and HTML as sent: with "Sent from Chrono Mail" at the end when Settings asks for it (B-24, D-358)."""
        text, html_part = self._text(), self.rich.outgoing_html() if self.is_rich() else None
        return with_sent_from(text, html_part) if self._options.sent_from_line else (text, html_part)

    def is_rich(self) -> bool:
        return self.editor_stack.currentWidget() is self.rich

    def _text(self) -> str:
        """The text of the message, whichever editor is in use."""
        return self.rich.toPlainText() if self.is_rich() else self.body.toPlainText()

    def set_rich(self, on: bool, html: str | None = None) -> None:
        """Switch between plain text and rich text. The text stays; going back to plain text drops the formatting."""
        if on == self.is_rich():
            if on and html is not None:
                self.rich.set_html(html)
            return
        signature = self.texts.before_switch() if hasattr(self, "texts") else ""  # the signature goes along (B-24, D-358)
        if on:
            if html is not None:
                self.rich.set_html(html)
            else:
                self.rich.setPlainText(self.body.toPlainText())
            self.editor_stack.setCurrentWidget(self.rich)
        else:
            answering = self._kind in ("reply", "reply_all")
            for address, picture in self.rich.pictures_in_text().items():  # nothing put in is lost: the pictures become attachments (D-353)
                if not (answering and address in self._quoted_pictures):  # the quoted original's pictures: in a plain reply they go (the owner)
                    self._add_picture(picture)
            self.body.setPlainText(self.rich.toPlainText())
            self.editor_stack.setCurrentWidget(self.body)
        if signature:
            self.texts.after_switch(signature)
        self._show_mode()
        (self.rich if on else self.body).setFocus()

    _original_names: tuple[str, ...] = ()  # a forward: the original's attachments (its pictures stand in the text or in the attachment list)
    _quoted_pictures: set[str] = set()  # the editor's pictures that came with the quoted original  # noqa: RUF012 - replaced, never changed in place

    def _show_original_names(self) -> None:
        """What a forward takes along, named on its tick box."""
        names = self._original_names
        whole = self._kind == "forward_attachment"
        if names:
            wording = "Attach the original message" if whole else "Include the attachments of the original message"
            self.include_original.setText(f"{wording} ({', '.join(names)})")
            self.include_original.setVisible(True)
        elif not whole:
            self.include_original.setVisible(False)

    def _show_mode(self) -> None:
        self._show_original_names()
        rich = self.is_rich()
        self.rich_button.setText("Rich text" if rich else "Plain text")
        (self.rich_action if rich else self.plain_action).setChecked(True)

    def _mode_chosen(self, rich: bool) -> None:
        """Rich text or Plain text was chosen in the menu of the button."""
        if not rich and self.is_rich() and self.rich.has_formatting():
            pictures = " The pictures become attachments." if self.rich.pictures_in_text() else ""
            answer = QMessageBox.question(self, "Plain text", f"Changing to plain text removes the formatting (bold, lists, links).{pictures} Continue?")
            if answer != QMessageBox.StandardButton.Yes:
                self._show_mode()
                return
        self.set_rich(rich)
        self._show_mode()

    def stored_as(self) -> tuple[str, str] | None:
        """The folder and id of the saved version of this message, if there is one."""
        return self._stored

    def _fields(self) -> tuple[str, str, str, str, str]:
        body = self.rich.snapshot() if self.is_rich() else self.body.toPlainText()
        return (self.to.text(), self.cc.text(), self.bcc.text(), self.subject.text(), body)

    def _snapshot(self) -> tuple[object, ...]:
        return (self._fields(), tuple((self.attachments.item(row).data(ROLE_PATH), self.attachments.item(row).data(ROLE_KEPT)) for row in range(self.attachments.count())), str(self.from_box.currentData()))

    def is_modified(self) -> bool:
        return self._snapshot() != self._saved_snapshot

    def attached_paths(self) -> list[str]:
        return [str(self.attachments.item(row).data(ROLE_PATH)) for row in range(self.attachments.count()) if self.attachments.item(row).data(ROLE_PATH) is not None]

    def kept_positions(self) -> list[str]:
        return [str(self.attachments.item(row).data(ROLE_KEPT)) for row in range(self.attachments.count()) if self.attachments.item(row).data(ROLE_KEPT) is not None]

    def add_attachment(self, path: str) -> None:
        item_text = f"{Path(path).name}  ({Path(path).stat().st_size // 1024 + 1} KB)" if Path(path).exists() else Path(path).name
        self.attachments.addItem(item_text)
        self.attachments.item(self.attachments.count() - 1).setData(ROLE_PATH, path)

    def _add_picture(self, picture: Picture) -> None:
        """A picture of the text as an attachment: it travels as its data, never as a file on this computer."""
        self.attachments.addItem(f"{picture.name}  ({len(picture.data) // 1024 + 1} KB)")
        self.attachments.item(self.attachments.count() - 1).setData(ROLE_PATH, data_address(picture.content_type, picture.name, picture.data))

    def _attach_dropped(self, paths: list[str]) -> None:
        for path in paths:
            self.add_attachment(path)

    def _add_kept(self, position: int, name: str, size: int) -> None:
        self.attachments.addItem(f"{name}  ({size // 1024 + 1} KB)")
        self.attachments.item(self.attachments.count() - 1).setData(ROLE_KEPT, position)

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach files")
        for path in paths:
            self.add_attachment(path)

    def _remove_selected(self) -> None:
        for item in self.attachments.selectedItems():
            self.attachments.takeItem(self.attachments.row(item))

    def _recipients(self) -> tuple[list[str], list[str], list[str]] | None:
        parsed = {name: parse_recipients(field.text()) for name, field in (("To", self.to), ("Cc", self.cc), ("Bcc", self.bcc))}
        for name, value in parsed.items():
            if value is None:
                self._bad_field = name
                return None
        return parsed["To"] or [], parsed["Cc"] or [], parsed["Bcc"] or []

    _bad_field: str = ""

    # --- drafts -------------------------------------------------------------------------------------

    def _draft_values(self, to: list[str], cc: list[str], bcc: list[str]) -> DraftSaveParameters:
        source_folder, source_message = self._source if self._source is not None else (None, None)
        replace_folder, replace_message = self._stored if self._stored is not None else (None, None)
        return DraftSaveParameters(
            account=str(self.from_box.currentData()), to=tuple(to), cc=tuple(cc), bcc=tuple(bcc), subject=self.subject.text(), body=self._text(),
            html=self.rich.outgoing_html() if self.is_rich() else None, attach=tuple(self.attached_paths()), keep_attachments=tuple(self.kept_positions()),
            kind=DRAFT_KINDS.get(self._kind, "new"), in_reply_to=self._in_reply_to or None, references=tuple(self._references),
            source_folder=source_folder, source_message=source_message, replace_folder=replace_folder, replace_message=replace_message,
        )

    def _save_clicked(self, checked: bool = False) -> None:
        if self._editing is not None:
            self.save_scheduled()  # Ctrl+S: never a draft, the message stays scheduled
            return
        self.save_draft()

    def _keep_clicked(self, checked: bool = False) -> None:
        self.save_scheduled()

    def _grey_edit_buttons(self) -> None:
        """Edit mode: Send and Save draft are there but cannot be used (Send now and Move back to drafts are on the page)."""
        self.send_button.setEnabled(False)
        self.save_button.setEnabled(False)

    def _send_clicked(self, checked: bool = False) -> None:
        if self._editing is not None:
            self.save_scheduled()  # Ctrl+Return: the main action of this window
            return
        if self._schedule_only:
            self.send_later()
        else:
            self.send()

    def save_draft(self, quiet: bool = False) -> None:
        """Save the message as a draft (replacing the version saved before). Asked while a save runs, it follows that save."""
        if self._sending:
            return
        if self._saving:
            if self._waiting is None:  # never over Send, Discard or Save on close: each stores or drops everything itself
                self._waiting = lambda: self._save_if_changed(quiet)
            return
        recipients = self._recipients()
        if recipients is None:
            if not quiet and not self._closing_for_update:
                QMessageBox.warning(self, "Save draft", f"The {self._bad_field} field contains something that is not an e-mail address.")
            else:
                self._say("Not saved yet: check the addresses")
            return
        values = self._draft_values(*recipients)
        snapshot = self._snapshot()
        sent = self._sent_items()  # the entries this save takes, in the order it gives them (kept ones, then files)
        self._saving = True
        self._save_failed = False
        self._say("Saving the draft…")

        def saved(result: DraftSaveResult) -> None:
            self._saving = False
            self._stored = (result.folder, result.message_id)
            self._say(f"Draft saved at {datetime.now().strftime('%H:%M')}")
            self._saved_snapshot = self._place_saved(snapshot, sent, result.attachment_positions)
            self.draft_saved.emit(values.account)
            self._decide_waiting()

        def failed(error: ChronoMailError) -> None:
            self._saving = False
            self._save_failed = True
            self._say(f"The draft could not be saved: {error}", error=True)
            if not quiet and not self._discarding and not self._closing_for_update:  # Save asked for it; an automatic save never interrupts the writing
                QMessageBox.warning(self, "Save draft", f"The draft could not be saved: {error}")
            self._decide_waiting()

        self._backend.ask(DRAFT_SAVE, values, saved, failed, owner=self)

    def busy(self) -> bool:
        """A save or a send runs, or a decision waits for one, or the window waits to close."""
        return self._saving or self._sending or self._discarding or self._waiting is not None

    def _after_the_save(self, decision: Callable[[], None]) -> None:
        """Send, Save on close or Discard: done now, or, while a save runs, once it ended; the newest decision replaces the one waiting."""
        if self._saving:
            self._waiting = decision
        else:
            decision()

    def _decide_waiting(self) -> None:
        decision = self._waiting
        self._waiting = None
        if decision is not None:
            decision()

    def _save_if_changed(self, quiet: bool) -> None:
        if self._stored is None or self.is_modified():
            self.save_draft(quiet)

    def _save_and_close(self) -> None:
        """Save on close: the window closes once everything is safely stored; a failed save keeps it open (its box said why)."""
        if self._stored is not None and not self.is_modified():
            self._close_for_good()
            return
        self.save_draft()
        if self._saving:
            self._waiting = self._close_when_saved

    def _close_when_saved(self) -> None:
        if not self._save_failed:
            self._save_and_close()  # what was typed during the save is saved first

    def _sent_items(self) -> tuple[list[QListWidgetItem], list[QListWidgetItem]]:
        """The attachment entries as listed now, and in the order a save gives them to the engine: the kept ones, then the files."""
        listed = [self.attachments.item(row) for row in range(self.attachments.count())]
        return listed, [item for item in listed if item.data(ROLE_KEPT) is not None] + [item for item in listed if item.data(ROLE_PATH) is not None]

    def _place_saved(self, snapshot: tuple[object, ...], sent: tuple[list[QListWidgetItem], list[QListWidgetItem]], positions: tuple[int, ...]) -> tuple[object, ...]:
        """After a save every attachment it took is inside the stored draft, at the place the engine gave: mark exactly those entries so, in place.
        An entry added during the save stays a file, one removed during it stays removed; the saved state is the list as the save took it, so
        either still counts as a change (CR-03). Returns that saved state."""
        listed, given = sent
        place = {id(item): position for item, position in zip(given, positions, strict=False)}
        for item in given:
            position = place.get(id(item), -1)
            row = self.attachments.row(item)
            if row < 0:
                continue  # removed during the save
            if position < 0:
                self.attachments.takeItem(row)  # not in the stored draft (a part its text shows): nothing there to keep
                continue
            item.setData(ROLE_PATH, None)
            item.setData(ROLE_KEPT, position)
        stored = tuple((None, place[id(item)]) for item in listed if place.get(id(item), -1) >= 0)
        return (snapshot[0], stored, snapshot[2])

    def _autosave(self) -> None:
        if self._editing is not None:
            return  # a scheduled message is never saved as a draft by itself: it would stop being scheduled
        if self._finished or self._sending or self._saving or not self.is_modified():
            return
        if not (self.to.text().strip() or self.cc.text().strip() or self.bcc.text().strip() or self.subject.text().strip() or self._text().strip() or self.attachments.count()):
            return  # nothing worth saving yet
        self.save_draft(quiet=True)

    # --- sending ------------------------------------------------------------------------------------

    def _later_clicked(self, checked: bool = False) -> None:
        self.send_later()

    def send_later(self) -> None:
        """Ask for a time and keep the message in the Scheduled folder until then."""
        dialog = WhenDialog("Schedule", "Send this message at:", "Schedule", self, repeat=True)
        editing = self._editing
        if editing is not None:  # it opens on what the message has now
            end = moment_at(editing.end).replace(tzinfo=None) if editing.end is not None else None  # never the platform's conversion (RV-204)
            dialog.show_current(moment_at(editing.seconds).replace(tzinfo=None), editing.months, editing.weeks, editing.days, end)
        if dialog.exec() != WhenDialog.DialogCode.Accepted:
            return
        if editing is not None:
            self.save_scheduled(dialog.chosen(), dialog.repeat_values())
        else:
            self.send(dialog.chosen(), dialog.repeat_values())

    def save_scheduled(self, send_at: str | None = None, repeat: dict[str, object] | None = None) -> None:
        """Edit mode: the message is changed where it waits, under its own id; without a new time it keeps its time and repeat."""
        editing = self._editing
        if editing is None or self._sending or self._saving:
            return
        recipients = self._recipients()
        if recipients is None:
            QMessageBox.warning(self, "Save", f"The {self._bad_field} field contains something that is not an e-mail address.")
            return
        to, cc, bcc = recipients
        if not (to or cc or bcc):
            QMessageBox.warning(self, "Save", "Add at least one recipient.")
            return
        chosen = repeat or {}
        until = chosen.get("repeat_until")
        parameters = ScheduledEdit(
            account=str(self.from_box.currentData()), to=tuple(to), cc=tuple(cc), bcc=tuple(bcc), subject=self.subject.text(), body=self._as_sent()[0],
            html=self._as_sent()[1], attach=tuple(self.attached_paths()), in_reply_to=self._in_reply_to,
            references=self._references, keep_attachments=tuple(self.kept_positions()), send_at=send_at,
            repeat_months=int(str(chosen.get("repeat_months", 0))), repeat_weeks=int(str(chosen.get("repeat_weeks", 0))), repeat_days=int(str(chosen.get("repeat_days", 0))),
            repeat_until=str(until) if until else None, message=editing.message_id, keep_time=send_at is None,
        )
        self._set_sending(True)
        self._say("Saving…")
        self._backend.ask(MAIL_EDIT_SCHEDULED, parameters, self._edited, self._edit_failed, owner=self)

    def _edited(self, info: ScheduledInfo) -> None:
        self._set_sending(False)
        self._handed_over()
        self._editing = info
        self.scheduled_changed.emit(info)
        self.close()

    def _edit_failed(self, error: ChronoMailError) -> None:
        self._set_sending(False)
        self._tell(QMessageBox.warning, "Not saved", f"{error}\n\nThe scheduled message is as it was. You can correct this and try again.")

    def send(self, send_at: str | None = None, repeat: dict[str, object] | None = None) -> None:
        if self._sending:
            return
        if self._saving:  # a save runs: the message is sent right after it, from what it stored
            self._waiting = lambda: self.send(send_at, repeat)
            return
        recipients = self._recipients()
        if recipients is None:
            self._tell(QMessageBox.warning, "Send", f"The {self._bad_field} field contains something that is not an e-mail address.")
            return
        to, cc, bcc = recipients
        if not (to or cc or bcc):
            if self._closing_for_update:
                self.save_draft(quiet=True)  # closing after an update: not sendable, kept as a draft instead, without a question
                return
            self._tell(QMessageBox.warning, "Send", "Add at least one recipient.")
            return
        if not self.subject.text().strip() and self._options.warn_no_subject and not self._closing_for_update:  # closing after an update: the Send given goes
            answer = QMessageBox.question(self, "Send", "This message has no subject. Send it anyway?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        repeat_choice = repeat or {} if send_at else {}
        until = repeat_choice.get("repeat_until")
        answering = self._source is not None and self._kind in ("reply", "reply_all", "forward", "forward_attachment")
        source_folder, source_message = self._source if self._source is not None and answering else (None, None)
        stored_folder, stored_message = self._stored if self._stored is not None else (None, None)  # the draft (or Outbox message) becomes the sent message
        keeping = self._stored is not None and bool(self.kept_positions())
        chosen = read_preferences(self._settings) if self._settings is not None and not send_at else None  # Settings > General: send at once, or wait (B-21)
        delay = chosen.send_delay_seconds if chosen is not None and chosen.send_delay else 0
        parameters = SendParameters(
            account=str(self.from_box.currentData()), to=tuple(to), subject=self.subject.text(), body=self._as_sent()[0], cc=tuple(cc), bcc=tuple(bcc),
            html=self._as_sent()[1], attach=tuple(self.attached_paths()), in_reply_to=self._in_reply_to or None,
            references=tuple(self._references), source_folder=source_folder, source_message=source_message,
            include_source_attachments=answering and self._kind == "forward" and self.include_original.isChecked(),
            attach_source_message=answering and self._kind == "forward_attachment" and self.include_original.isChecked(),
            mark_replied=answering and self._kind in ("reply", "reply_all"),
            keep_folder=stored_folder if keeping else None, keep_message=stored_message if keeping else None,
            keep_attachments=tuple(self.kept_positions()) if keeping else (), replaces_folder=stored_folder, replaces_message=stored_message,
            send_at=send_at or None, repeat_months=int(str(repeat_choice.get("repeat_months", 0))), repeat_weeks=int(str(repeat_choice.get("repeat_weeks", 0))),
            repeat_days=int(str(repeat_choice.get("repeat_days", 0))), repeat_until=str(until) if until else None,
            delay_seconds=delay, hold=chosen is not None and chosen.send_on_refresh,
        )
        self._delay = delay
        self._set_sending(True)
        self._backend.ask(MAIL_SEND, parameters, self._sent, self._failed, owner=self)

    def _say(self, text: str, error: bool = False) -> None:
        """The bottom line of the window: what happens, an error in the red of the theme in use (until the next text)."""
        self.status.setText(text)
        self.status.setStyleSheet(red_style() if error else "")

    def _set_sending(self, sending: bool) -> None:
        self._sending = sending
        for widget in (self.keep_button, self.send_button, self.later_button, self.save_button, self.to, self.cc, self.bcc, self.subject, self.editor_stack, self.rich_button, self.from_box, self.attach_button, self.remove_button):
            widget.setEnabled(not sending)
        self._say("Sending…" if sending else "")
        if self._editing is not None:
            self._grey_edit_buttons()

    def _handed_over(self) -> None:
        """The engine has the message now (scheduled, or sent): this window no longer holds it. Done at once, before any box
        is shown: a box waits for the person while the timers go on, and an autosave then would save a message that is no
        longer here (a stray draft, or "Message not found" for the draft that the send removed)."""
        self._finished = True
        self.autosave_timer.stop()

    def _sent(self, result: SendResult) -> None:
        self._set_sending(False)
        if result.scheduled:
            self._handed_over()
            self.sent.emit(str(self.from_box.currentData()))
            self.scheduled.emit(result.message_id)
            self._tell(QMessageBox.information, "Scheduled", f"The message will be sent at {result.scheduled}. It waits in your Scheduled folder until then; the program has to be running at that time.")
            self.close()
            return
        if result.waiting:  # a delayed send: it waits in the Outbox (B-21)
            self._handed_over()
            self.sent.emit(str(self.from_box.currentData()))
            self.waiting.emit(str(self.from_box.currentData()), result.message_id, self.subject.text(), self._delay)
            self.close()
            return
        if not result.sent and result.certificate is not None:  # the Verify certificate window asks once this one has closed (D-299)
            self._handed_over()
            self.sent.emit(str(self.from_box.currentData()))  # the Outbox changed
            self.certificate_stopped.emit(result.certificate, result.message_id)
            self.close()
            return
        if not result.sent:  # the message is in the Outbox now, sent from there: this window no longer holds it (a second Send sent it twice, RV-155)
            self._handed_over()
            self.sent.emit(str(self.from_box.currentData()))  # the Outbox changed
            self._tell(
                QMessageBox.warning,
                "The message was not sent",
                f"{result.error}\n\nIt was kept in your Outbox, so nothing is lost, and is sent again with the next Send and Receive. "
                "To change it, open it from the Outbox.",
            )
            self.close()
            return
        self._handed_over()
        self.sent.emit(str(self.from_box.currentData()))
        problems = list(result.refused) + ([result.warning] if result.warning else [])
        if problems:
            self._tell(QMessageBox.information, "Sent", "The message was sent, with remarks:\n\n" + "\n".join(problems))
        self.close()

    def _failed(self, error: ChronoMailError) -> None:
        self._set_sending(False)
        if isinstance(error, SentAlready):  # what it replaces was sent meanwhile: nothing more is sent
            self._tell(QMessageBox.warning, "The message was not sent again", str(error))
        elif isinstance(error, GoneMeanwhile):  # what it replaces left the Outbox meanwhile: nothing was done, never "try again"
            self._tell(QMessageBox.warning, "The message was not sent", str(error))
        elif isinstance(error, (UsageError, ConfigurationError, MessageFormatError)):  # refused before anything was stored (or built)
            self._tell(QMessageBox.warning, "The message was not sent", f"{error}\n\nNothing was stored or sent. You can correct this and try again.")
        else:  # it may have got further: never invite a second Send without a look
            self._tell(QMessageBox.warning, "The message may not have been sent", f"{error}\n\nLook in the Outbox and in Sent before you send it again.")

    # --- closing ------------------------------------------------------------------------------------

    def _close_for_good(self) -> None:
        self._finished = True
        self.close()

    def save_and_close_quietly(self) -> None:
        """Chrono Mail closes after an update, without a Cancel and without a question (the owner): what is not saved goes to Drafts (a
        scheduled message keeps its place), then the window closes; what cannot be saved (an address typed wrong, a full disk) is thrown
        away (the owner). A Send or a decision already given goes on; no box appears meanwhile."""
        self._closing_for_update = True
        if self._discarding or self._sending or (self._saving and self._waiting is not None):
            return  # under way, or the person's own decision waits for the running save: it goes on, the window closes after it
        if self._finished or not self.is_modified():
            self.autosave_timer.stop()
            self._close_for_good()
            return
        if self._editing is not None:
            recipients = self._recipients()
            if recipients is not None and any(recipients):
                self.save_scheduled()  # the window closes once the message is saved
            return

        def save_then_close() -> None:
            if self._stored is not None and not self.is_modified():
                self._close_for_good()
                return
            self.save_draft(quiet=True)
            if self._saving:
                self._waiting = self._close_when_saved
            elif not self._save_failed:
                self._close_for_good()

        self._after_the_save(save_then_close)

    _closing_for_update: bool = False  # closing after an update: nothing asks, what goes wrong is only written on the bottom line

    def _tell(self, box: Callable[[QWidget, str, str], object], title: str, text: str) -> None:
        """A message in a box, or, while the window closes after an update (no question at all), only on its bottom line."""
        if self._closing_for_update:
            self._say(text)
            return
        box(self, title, text)

    def close_for_quit(self) -> None:
        """The program ends now: the window goes without a question (what could be saved was saved)."""
        self.autosave_timer.stop()
        self._close_for_good()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        save_geometry(self, self._settings, "composer_geometry")
        if self._discarding:
            event.ignore()  # thrown away already: it closes by itself once the running save ended
            return
        if self._finished or not self.is_modified():
            self.autosave_timer.stop()
            event.accept()
            return
        if self._editing is not None:  # never a draft: save the changes (its time stays), or leave it as it was
            buttons = QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel
            answer = QMessageBox.question(self, "Save the changes?", "Save the changes to this scheduled message? Its time stays as it is.", buttons, QMessageBox.StandardButton.Save)
            event.ignore()
            if answer == QMessageBox.StandardButton.Save:
                self.save_scheduled()  # the window closes once the message is saved
            elif answer == QMessageBox.StandardButton.Discard:
                self._close_for_good()
            return
        buttons = QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel
        answer = QMessageBox.question(self, "Save this message?", "The message has not been sent. Save it as a draft?", buttons, QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            event.ignore()
            self._after_the_save(self._save_and_close)  # the window closes when the draft is safely stored
        elif answer == QMessageBox.StandardButton.Discard and self._saving and self._created_here:  # the running save would leave a draft behind
            event.ignore()
            self.autosave_timer.stop()
            self.hide()
            self._discarding = True
            self._waiting = self._discard  # the newest decision: a Save, Send or Save on close waiting no longer goes
        elif answer == QMessageBox.StandardButton.Discard:
            if self._created_here and self._stored is not None:  # a draft that only this window made (by autosave) goes with it
                folder, message = self._stored
                self._backend.ask(MESSAGE_DELETE, DeleteParameters(str(self.from_box.currentData()), folder, message, permanent=True), self._ignore, None)
            self.autosave_timer.stop()
            event.accept()
        else:
            event.ignore()

    def _discard(self) -> None:
        """Discard asked while a save ran: the draft only this window made goes, then the window."""
        if self._created_here and self._stored is not None:
            folder, message = self._stored
            self._backend.ask(MESSAGE_DELETE, DeleteParameters(str(self.from_box.currentData()), folder, message, permanent=True), self._ignore, None)
        self._discarding = False
        self._close_for_good()

    @staticmethod
    def _ignore(result: object) -> None:
        """The answer of a command whose result is of no interest."""
