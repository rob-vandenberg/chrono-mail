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
from chrono_mail.engine.commands_drafts import DRAFT_OPEN, DRAFT_SAVE, DraftKind, DraftOpenParameters, DraftOpenResult, DraftSaveParameters, DraftSaveResult
from chrono_mail.engine.commands_mail import MAIL_SEND, MESSAGE_DELETE, DeleteParameters, SendParameters, SendResult
from chrono_mail.engine.commands_schedule import MAIL_EDIT_SCHEDULED, ScheduledEdit, ScheduledInfo
from chrono_mail.ui.addresscompleter import AddressCompleter
from chrono_mail.ui import scale, theme
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import red_style
from chrono_mail.ui.icons import application_icon
from chrono_mail.ui.prefs import read_preferences
from chrono_mail.ui.richeditor import RichEditor
from chrono_mail.ui.windowstate import restore_geometry, save_geometry
from chrono_mail.ui.whendialog import WhenDialog

# --- Version ------------------------------------------------------------
__version__ = "composer.py 3.6.351"

# --- Version history ----------------------------------------------------
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
    quoted = angle = False
    for character in text:
        if character == '"':
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
SIGNATURE_MARK: str = "-- \n"  # the usual separator: two dashes and a space on a line of their own


def signature_block(signature: str) -> str:
    return SIGNATURE_MARK + signature.strip("\n") if signature.strip() else ""


@dataclass(frozen=True, slots=True)
class ComposeOptions:
    """What the settings say about writing: how often to save, whether to warn about a missing subject, the font size
    of the editor (0 is the system's) and whether replies are written under the quote."""

    autosave_seconds: int = AUTOSAVE_SECONDS
    warn_no_subject: bool = True
    font_size: float = 0.0  # points
    reply_below: bool = False


def with_signature(body: str, signature: str, below: bool = False) -> str:
    """The body of a new message, a reply or a forward with the signature before any quoted text
    (after the whole text when the answer is written under the quote)."""
    block = signature_block(signature)
    if not block:
        return body
    if not body.strip():
        return f"\n\n{block}\n"
    if below:
        return f"{body.rstrip(chr(10))}\n\n{block}\n"
    rest = body[2:] if body.startswith("\n\n") else body
    return f"\n\n{block}\n\n{rest}"

ROLE_PATH: int = 256  # an attachment that is a file on this computer
ROLE_KEPT: int = 257  # an attachment that is already inside the stored draft (its position there)


DRAFT_KINDS: dict[str, DraftKind] = {"new": "new", "reply": "reply", "reply_all": "reply_all", "forward": "forward", "forward_attachment": "forward_attachment"}


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
            names: tuple[str, ...] = draft.attachment_names if isinstance(draft, ComposeResult) else ()  # what a forward takes along
            if names:
                wording = "Attach the original message" if self._kind == "forward_attachment" else "Include the attachments of the original message"
                self.include_original.setText(f"{wording} ({', '.join(names)})")
                self.include_original.setChecked(True)
                self.include_original.show()
            for stored in draft.attachments if isinstance(draft, DraftOpenResult) else ():  # a draft's own attachments
                self._add_kept(stored.position, stored.name, stored.size)
            self.setWindowTitle(draft.subject or "New message")
            self.body.moveCursor(self.body.textCursor().MoveOperation.Start)
        if self._kind == "forward_attachment" and self.include_original.isHidden():  # a draft taken up again: the original still goes along
            self.include_original.setText("Attach the original message")
            self.include_original.setChecked(True)
            self.include_original.show()
        self._accounts: list[AccountSummary] = accounts
        self._signature: str = self._signature_of(account_id)
        if resume is None and self._signature:
            self.body.setPlainText(with_signature(self.body.toPlainText(), self._signature, below=options.reply_below and self._kind in ("reply", "reply_all")))
        self.body.moveCursor(self.body.textCursor().MoveOperation.End if options.reply_below and self._kind in ("reply", "reply_all") and resume is None else self.body.textCursor().MoveOperation.Start)
        self.body.document().setModified(False)
        if options.font_size > 0:
            self.set_text_size(options.font_size)
        stored_html = draft.html if isinstance(draft, DraftOpenResult) else None
        if stored_html:  # a rich text draft comes back as rich text
            self.set_rich(True, stored_html)
        elif rich:
            self.set_rich(True)
        if editing is not None:  # it stays scheduled: Save keeps its time, Schedule… changes it; sending now and drafts are on the page
            self.keep_button.show()
            self.keep_button.setDefault(True)
            self.send_button.setDefault(False)
            self.send_button.setObjectName("flatButton")
            self.send_button.setToolTip("Send now is on Settings > Scheduled messages")
            self.save_button.setToolTip("Move back to drafts is on Settings > Scheduled messages")
            self._grey_edit_buttons()
            self.setWindowTitle("Edit scheduled message")
        self._saved_snapshot: tuple[object, ...] = self._snapshot()
        if taken_back:  # a send cancelled (B-21): as a message not saved yet, closing asks, and Discard removes its draft
            self._created_here = True
            self._saved_snapshot = ()

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

    def _signature_of(self, account_id: str) -> str:
        for account in self._accounts:
            if account.account_id == account_id:
                return account.signature
        return ""

    def _account_changed(self) -> None:
        """Another sender was chosen: the signature of the first one is replaced by the signature of the new one."""
        new = self._signature_of(str(self.from_box.currentData()))
        if self.is_rich():  # swapping would flatten the formatting: in rich text the signature stays as it is
            self._signature = new
            return
        old, body = signature_block(self._signature), self.body.toPlainText()
        if new != self._signature:
            if old and old in body:
                self.body.setPlainText(body.replace(old, signature_block(new), 1) if new else body.replace(old, "", 1).replace("\n\n\n\n", "\n\n"))
            elif new and not old and not body.strip():
                self.body.setPlainText(with_signature(body, new))
        self._signature = new

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
        if on:
            if html is not None:
                self.rich.set_html(html)
            else:
                self.rich.setPlainText(self.body.toPlainText())
            self.editor_stack.setCurrentWidget(self.rich)
        else:
            self.body.setPlainText(self.rich.toPlainText())
            self.editor_stack.setCurrentWidget(self.body)
        self._show_mode()
        (self.rich if on else self.body).setFocus()

    def _show_mode(self) -> None:
        rich = self.is_rich()
        self.rich_button.setText("Rich text" if rich else "Plain text")
        (self.rich_action if rich else self.plain_action).setChecked(True)

    def _mode_chosen(self, rich: bool) -> None:
        """Rich text or Plain text was chosen in the menu of the button."""
        if not rich and self.is_rich() and self.rich.has_formatting():
            answer = QMessageBox.question(self, "Plain text", "Changing to plain text removes the formatting (bold, lists, links). Continue?")
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

    def save_draft(self, then: Callable[[], None] | None = None, quiet: bool = False) -> None:
        """Save the message as a draft (replacing the version saved before)."""
        if self._sending or self._saving:
            return
        recipients = self._recipients()
        if recipients is None:
            if not quiet:
                QMessageBox.warning(self, "Save draft", f"The {self._bad_field} field contains something that is not an e-mail address.")
            else:
                self._say("Not saved yet: check the addresses")
            return
        values = self._draft_values(*recipients)
        snapshot = self._snapshot()
        self._saving = True
        self._say("Saving the draft…")

        def saved(result: DraftSaveResult) -> None:
            self._saving = False
            self._stored = (result.folder, result.message_id)
            self._say(f"Draft saved at {datetime.now().strftime('%H:%M')}")
            self._refresh_kept_attachments(snapshot, then)

        def failed(error: ChronoMailError) -> None:
            self._saving = False
            self._say(f"The draft could not be saved: {error}", error=True)
            if not quiet:  # Save asked for it; an automatic save never interrupts the writing (it tries again by itself)
                QMessageBox.warning(self, "Save draft", f"The draft could not be saved: {error}")

        self._backend.ask(DRAFT_SAVE, values, saved, failed, owner=self)

    def _refresh_kept_attachments(self, snapshot: tuple[object, ...], then: Callable[[], None] | None) -> None:
        """After a save every attachment is inside the stored draft: list them as such, so that the next save keeps them."""
        stored = self._stored
        account = str(self.from_box.currentData())

        def opened(result: DraftOpenResult) -> None:
            if self._fields() == snapshot[0]:  # nothing was typed in the meantime
                self.attachments.clear()
                for attachment in result.attachments:
                    self._add_kept(attachment.position, attachment.name, attachment.size)
                self._saved_snapshot = self._snapshot()
            self.draft_saved.emit(account)
            if then is not None:
                then()

        def failed(error: ChronoMailError) -> None:
            self._saved_snapshot = snapshot
            self.draft_saved.emit(account)
            if then is not None:
                then()

        if stored is None:
            return
        self._backend.ask(DRAFT_OPEN, DraftOpenParameters(account, stored[1], stored[0]), opened, failed, owner=self)

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
            account=str(self.from_box.currentData()), to=tuple(to), cc=tuple(cc), bcc=tuple(bcc), subject=self.subject.text(), body=self._text(),
            html=self.rich.outgoing_html() if self.is_rich() else None, attach=tuple(self.attached_paths()), in_reply_to=self._in_reply_to,
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
        QMessageBox.warning(self, "Not saved", f"{error}\n\nThe scheduled message is as it was. You can correct this and try again.")

    def send(self, send_at: str | None = None, repeat: dict[str, object] | None = None) -> None:
        if self._sending or self._saving:
            return
        recipients = self._recipients()
        if recipients is None:
            QMessageBox.warning(self, "Send", f"The {self._bad_field} field contains something that is not an e-mail address.")
            return
        to, cc, bcc = recipients
        if not (to or cc or bcc):
            QMessageBox.warning(self, "Send", "Add at least one recipient.")
            return
        if not self.subject.text().strip() and self._options.warn_no_subject:
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
            account=str(self.from_box.currentData()), to=tuple(to), subject=self.subject.text(), body=self._text(), cc=tuple(cc), bcc=tuple(bcc),
            html=self.rich.outgoing_html() if self.is_rich() else None, attach=tuple(self.attached_paths()), in_reply_to=self._in_reply_to or None,
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
            QMessageBox.information(self, "Scheduled", f"The message will be sent at {result.scheduled}. It waits in your Scheduled folder until then; the program has to be running at that time.")
            self.close()
            return
        if result.waiting:  # a delayed send: it waits in the Outbox (B-21)
            self._handed_over()
            self.sent.emit(str(self.from_box.currentData()))
            self.waiting.emit(str(self.from_box.currentData()), result.message_id, self.subject.text(), self._delay)
            self.close()
            return
        if not result.sent:  # the message is in the Outbox now, sent from there: this window no longer holds it (a second Send sent it twice, RV-155)
            self._handed_over()
            self.sent.emit(str(self.from_box.currentData()))  # the Outbox changed
            QMessageBox.warning(
                self,
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
            QMessageBox.information(self, "Sent", "The message was sent, with remarks:\n\n" + "\n".join(problems))
        self.close()

    def _failed(self, error: ChronoMailError) -> None:
        self._set_sending(False)
        if isinstance(error, SentAlready):  # what it replaces was sent meanwhile: nothing more is sent
            QMessageBox.warning(self, "The message was not sent again", str(error))
        elif isinstance(error, GoneMeanwhile):  # what it replaces left the Outbox meanwhile: nothing was done, never "try again"
            QMessageBox.warning(self, "The message was not sent", str(error))
        elif isinstance(error, (UsageError, ConfigurationError, MessageFormatError)):  # refused before anything was stored (or built)
            QMessageBox.warning(self, "The message was not sent", f"{error}\n\nNothing was stored or sent. You can correct this and try again.")
        else:  # it may have got further: never invite a second Send without a look
            QMessageBox.warning(self, "The message may not have been sent", f"{error}\n\nLook in the Outbox and in Sent before you send it again.")

    # --- closing ------------------------------------------------------------------------------------

    def _close_for_good(self) -> None:
        self._finished = True
        self.close()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        save_geometry(self, self._settings, "composer_geometry")
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
            self.save_draft(then=self._close_for_good)  # the window closes when the draft is safely stored
        elif answer == QMessageBox.StandardButton.Discard:
            if self._created_here and self._stored is not None:  # a draft that only this window made (by autosave) goes with it
                folder, message = self._stored
                self._backend.ask(MESSAGE_DELETE, DeleteParameters(str(self.from_box.currentData()), folder, message, permanent=True), self._ignore, None)
            self.autosave_timer.stop()
            event.accept()
        else:
            event.ignore()

    @staticmethod
    def _ignore(result: object) -> None:
        """The answer of a command whose result is of no interest."""
