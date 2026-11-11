import dataclasses
import html
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from datetime import UTC, datetime
from pathlib import Path
from typing import override

from PySide6.QtCore import QAbstractListModel, QDate, QLocale, QModelIndex, QPersistentModelIndex, QSettings, Qt, QTime, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QFont, QMoveEvent, QResizeEvent
from PySide6.QtWidgets import QAbstractItemView, QFileDialog, QLabel, QLineEdit, QListView, QMainWindow, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.changes import Change, MessageRow
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_addressbook import CONTACT_SHOW, ContactInfo
from chrono_mail.engine.commands_addressbook import ItemParameters as ContactItem
from chrono_mail.engine.commands_calendar import EVENT_SHOW, EventInfo
from chrono_mail.engine.commands_calendar import ItemParameters as EventItem
from chrono_mail.engine.commands_mail import MESSAGE_ATTACHMENT_SAVE, MESSAGE_SHOW, AttachmentSaveParameters, MessageParameters, ShowResult
from chrono_mail.engine.commands_pim import TAG_LIST, TagInfo
from chrono_mail.engine.commands_tagoverview import TAG_OVERVIEW, OverviewParameters, TaggedItem, TagOverview
from chrono_mail.engine.commands_tasklist import TASK_SHOW, TaskInfo
from chrono_mail.engine.commands_tasklist import ItemParameters as TaskItem
from chrono_mail.engine.commands_view import MESSAGE_COMBINED_ORDER, MESSAGE_ROWS, CombinedOrderParameters, RowsAnswer, RowsParameters, ViewOrder
from chrono_mail.pim.times import local_zone
from chrono_mail.ui import forms, scale, theme
from chrono_mail.ui.attachmentopening import open_attachment
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.contactdetail import ContactDetail
from chrono_mail.ui.icons import application_icon
from chrono_mail.ui.invitationwindow import CalendarSource, is_calendar_file
from chrono_mail.ui.linkhint import StatusLine
from chrono_mail.ui.reader import ReaderWidget
from chrono_mail.ui.splitter import PaneSplitter
from chrono_mail.ui.tagchips import TagColourBook
from chrono_mail.ui.windowstate import restore_geometry, save_geometry

# --- Version ------------------------------------------------------------
__version__ = "tagoverview.py 4.4.578"

# --- Version history ----------------------------------------------------
# v4.4.578: The size and place are kept at every move or resize, so quitting with the window open keeps them (the owner, D-328)
# v4.4.572: Initial Tag overview: everything with one tag in one list of four foldable groups, a reading pane, a search field; it follows every change (D-328)

CONTACTS, EVENTS, TASKS, MAIL = "contacts", "events", "tasks", "mail"
GROUPS: tuple[tuple[str, str], ...] = ((CONTACTS, "Contacts"), (EVENTS, "Appointments"), (TASKS, "Tasks"), (MAIL, "Mail"))
EARLIER, FINISHED = "events-earlier", "tasks-finished"  # the folded parts of Appointments and Tasks
PAGE: int = 100  # mail rows asked for at a time, as they come into view
SEARCH_DELAY: int = 300
HEADING, PART, ITEM = "heading", "part", "item"
KIND_ROLE: int = Qt.ItemDataRole.UserRole + 1  # heading, part or item


@dataclass(frozen=True, slots=True)
class Openers:
    """What the main window lends the overview: opening an item as its own section does, and reading a message as the reading pane does."""

    mail: Callable[[str, str, str], None]  # account, folder, message id: in a window of its own
    contact: Callable[[str], None]
    event: Callable[[str], None]
    task: Callable[[str], None]
    prepare_reader: Callable[[ReaderWidget], None]  # the privacy settings of a reader, before a message is shown
    wire_reader: Callable[[ReaderWidget, str, str, str], None]  # the calendar card's actions of a reader
    calendar_card: Callable[[CalendarSource, str, QWidget], None]  # a calendar attachment as a card
    open_link: Callable[[QUrl], None]


def mail_key(mailbox: str, message_id: str) -> str:
    return f"m\x1f{mailbox}\x1f{message_id}"


def when_text(seconds: int | None, floating: bool, with_time: bool) -> str:
    """A moment as this computer's language writes it (a floating time as its wall clock)."""
    if seconds is None:
        return ""
    moment = datetime.fromtimestamp(seconds, UTC) if floating else datetime.fromtimestamp(seconds, local_zone())
    locale = QLocale()
    day = locale.toString(QDate(moment.year, moment.month, moment.day), QLocale.FormatType.ShortFormat)
    return f"{day} {locale.toString(QTime(moment.hour, moment.minute), QLocale.FormatType.ShortFormat)}" if with_time else day


class OverviewModel(QAbstractListModel):
    """The rows of the overview: a heading per group, the folded parts (earlier appointments, finished tasks) and the items. A row is known by
    its key, and the list follows a new state in place (rows removed, inserted or moved), so the selection and the scroll stay (D-146)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.keys: list[str] = []
        self.pim: dict[str, TaggedItem] = {}
        self.mail_order: list[tuple[str, str, str]] = []  # mailbox, folder, message id, in order
        self.mail_total: int = 0
        self.mail_rows: dict[str, MessageRow] = {}
        self.folded: set[str] = {EARLIER, FINISHED}
        self.counts: dict[str, int] = {}
        self.groups: dict[str, tuple[TaggedItem, ...]] = {CONTACTS: (), EVENTS: (), TASKS: ()}
        self.group_of: dict[str, str] = {}  # an item's key: contacts, events or tasks
        self.fetch: Callable[[list[tuple[str, str]]], None] = lambda wanted: None
        self.asked: set[str] = set()

    # --- what should be there ---------------------------------------------------------------------------------------

    def wanted(self, overview: TagOverview | None) -> list[str]:
        """The keys of every row as the overview and the mail order are now."""
        if overview is not None:
            self.pim = {f"p\x1f{item.item}": item for item in (*overview.contacts, *overview.events, *overview.tasks)}
            self.counts.update({CONTACTS: len(overview.contacts), EVENTS: len(overview.events), TASKS: len(overview.tasks)})
            self.groups = {CONTACTS: overview.contacts, EVENTS: overview.events, TASKS: overview.tasks}
            self.group_of = {f"p\x1f{item.item}": group for group, items in self.groups.items() for item in items}
        self.counts[MAIL] = len(self.mail_order)
        keys: list[str] = []
        for group, _ in GROUPS:
            keys.append(f"h\x1f{group}")
            if group in self.folded:
                continue
            if group == MAIL:
                keys.extend(mail_key(mailbox, message_id) for mailbox, _, message_id in self.mail_order)
                continue
            items = self.groups[group]
            if group == CONTACTS:
                keys.extend(f"p\x1f{item.item}" for item in items)
                continue
            keys.extend(f"p\x1f{item.item}" for item in items if item.later)
            rest = [item for item in items if not item.later]
            if rest:
                part = EARLIER if group == EVENTS else FINISHED
                keys.append(f"s\x1f{part}")
                if part not in self.folded:
                    keys.extend(f"p\x1f{item.item}" for item in rest)
        return keys

    def follow(self, new: list[str]) -> None:
        """The rows changed in place to these keys: the ones that went are removed, the ones that came inserted, a moved one moved."""
        wanted = set(new)
        for row in range(len(self.keys) - 1, -1, -1):
            if self.keys[row] not in wanted:
                self.beginRemoveRows(QModelIndex(), row, row)
                del self.keys[row]
                self.endRemoveRows()
        present = set(self.keys)
        for row, key in enumerate(new):
            if row < len(self.keys) and self.keys[row] == key:
                continue
            if key in present:
                old = self.keys.index(key, row)
                self.beginMoveRows(QModelIndex(), old, old, QModelIndex(), row)
                self.keys.insert(row, self.keys.pop(old))
                self.endMoveRows()
            else:
                self.beginInsertRows(QModelIndex(), row, row)
                self.keys.insert(row, key)
                self.endInsertRows()
                present.add(key)
        if self.keys:
            self.dataChanged.emit(self.index(0), self.index(len(self.keys) - 1))  # the headings' numbers, a changed item's text

    # --- Qt ---------------------------------------------------------------------------------------------------------

    @override
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.keys)

    @override
    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or index.row() >= len(self.keys):
            return None
        key = self.keys[index.row()]
        kind = HEADING if key.startswith("h\x1f") else PART if key.startswith("s\x1f") else ITEM
        if role == KIND_ROLE:
            return kind
        if role == Qt.ItemDataRole.FontRole and kind != ITEM:
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ItemDataRole.ToolTipRole and kind != ITEM:
            return "Click to fold or unfold"
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if kind == HEADING:
            group = key[2:]
            arrow = "▸" if group in self.folded else "▾"
            return f"{arrow} {dict(GROUPS)[group]} ({self.counts.get(group, 0):,})"
        if kind == PART:
            part = key[2:]
            items = self.groups[EVENTS if part == EARLIER else TASKS]
            arrow = "▸" if part in self.folded else "▾"
            return f"      {arrow} {'Earlier' if part == EARLIER else 'Finished'} ({sum(1 for item in items if not item.later):,})"
        if key.startswith("m\x1f"):
            return self._mail_text(key, index.row())
        return self._item_text(self.pim[key], self.group_of.get(key, "")) if key in self.pim else ""

    def _item_text(self, item: TaggedItem, group: str) -> str:
        place = f"  ·  {item.subtitle}" if item.subtitle else ""
        if group == EVENTS:
            when = when_text(item.start, item.floating, not item.all_day)
            return f"    {when}   {item.title}{place}  —  {item.collection_name}{'  (repeats)' if item.repeats else ''}"
        if group == TASKS:
            box = "☐" if item.later else "☑"
            due = f"  ·  due {when_text(item.due, item.floating, False)}" if item.due is not None else ""
            return f"    {box} {item.title}{due}  —  {item.collection_name}"
        return f"    {item.title}{place}"

    def _mail_text(self, key: str, row: int) -> str:
        found = self.mail_rows.get(key)
        if found is None:
            self._want(row)
            return "    …"
        date = when_text(int(found.date.timestamp()), False, True) if found.date is not None else ""
        unread = "● " if "seen" not in found.flags else ""
        return f"    {unread}{found.sender}   {found.subject or '(no subject)'}   {date}"

    def _want(self, row: int) -> None:
        """The rows of a page of mail from this row on, asked once (by their mailbox), as they come into view."""
        wanted: list[tuple[str, str]] = []
        for key in self.keys[row:]:
            if len(wanted) >= PAGE:
                break
            if key.startswith("m\x1f") and key not in self.mail_rows and key not in self.asked:
                _, mailbox, message_id = key.split("\x1f")
                wanted.append((mailbox, message_id))
                self.asked.add(key)
        if wanted:
            self.fetch(wanted)

    def kind_of(self, key: str) -> str:
        """contacts, events, tasks or mail: the group of an item's key."""
        return MAIL if key.startswith("m\x1f") else self.group_of.get(key, "")


class TagOverviewWindow(QMainWindow):
    """Everything with one tag (D-328): its contacts, appointments, tasks and mail in one list of four foldable groups, the selected item in the
    reading pane, a double click opens it as its own section does. It only shows: nothing is changed here or on the server. It follows every
    change as a fact (D-146): an item that gets or loses the tag comes or goes in place."""

    def __init__(self, backend: Backend, tag: str, openers: Openers, settings: QSettings | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._backend = backend
        self.tag = tag
        self._openers = openers
        self._settings = settings
        self._overview: TagOverview | None = None
        self._generation: int = 0
        self._shown: str = ""  # the key of the item in the reading pane
        self.setWindowIcon(application_icon())
        self.setStyleSheet(theme.stylesheet(scale.windows()))  # a window of its own: its sizes follow Other windows and dialogs
        host = QWidget(self)
        self.setCentralWidget(host)
        self.splitter = PaneSplitter(Qt.Orientation.Horizontal)
        form = forms.fill(host, "tag_overview", {"panes": lambda parent: self.splitter})  # D-276
        self.setWindowTitle(f"Everything tagged {tag}")
        self.search: QLineEdit = forms.part(form, QLineEdit, "search")
        self.note: QLabel = forms.part(form, QLabel, "note")
        self.note.hide()
        self.model = OverviewModel(self)
        self.model.fetch = self._fetch_mail
        self.list = QListView(self.splitter)
        self.list.setModel(self.model)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setFrameShape(QListView.Shape.NoFrame)
        self.colours = TagColourBook()
        self.reading = QStackedWidget(self.splitter)
        self.nothing = QLabel("Select an item", self.reading)
        self.nothing.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.contact = ContactDetail(backend, self.colours, self.reading)
        self.text = QTextBrowser(self.reading)
        self.text.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.mail_holder = QWidget(self.reading)
        self.reader: ReaderWidget | None = None
        for page in (self.nothing, self.contact, self.text, self.mail_holder):
            self.reading.addWidget(page)
        self.splitter.addWidget(self.list)
        self.splitter.addWidget(self.reading)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setChildrenCollapsible(False)
        self.status_line = StatusLine(QLabel(self), self)  # where the link under the pointer leads (D-215)
        self.statusBar().addWidget(self.status_line, 1)
        self.resize(1000, 680)
        restore_geometry(self, settings, "tag_overview_geometry")
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY)
        self._search_timer.timeout.connect(self.reload)
        self._again = QTimer(self)  # facts that come together ask once
        self._again.setSingleShot(True)
        self._again.setInterval(0)
        self._again.timeout.connect(self._refetch)
        self._again_pim = False
        self._again_mail = False
        self._shown_changed = False  # a fact named the item in the reading pane: shown again once the overview has come
        self._moved_to: str = ""  # the key of the shown item after a move to another collection
        self.search.textChanged.connect(self._search_changed)
        self.list.clicked.connect(self._clicked)
        self.list.doubleClicked.connect(self._double_clicked)
        self.list.selectionModel().currentChanged.connect(self._current_changed)
        backend.changed.connect(self._fact)
        self._backend.ask(TAG_LIST, NoParameters(), self._tags_arrived, lambda error: None, owner=self)
        self.reload()

    def _search_changed(self, text: str) -> None:
        self._search_timer.start()

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._keep_geometry()

    @override
    def moveEvent(self, event: QMoveEvent) -> None:
        super().moveEvent(event)
        self._keep_geometry()

    def _keep_geometry(self) -> None:
        """Kept at once, never only at closing: Quit leaves an open overview without closing it (the owner). Not before it is on screen: the
        sizes of its making are not the person's."""
        if self.isVisible():
            save_geometry(self, self._settings, "tag_overview_geometry")

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        save_geometry(self, self._settings, "tag_overview_geometry")
        try:
            self._backend.changed.disconnect(self._fact)
        except RuntimeError:
            pass  # never connected, or gone already
        super().closeEvent(event)

    # --- loading ----------------------------------------------------------------------------------------------------

    def reload(self) -> None:
        """Both halves asked again: the contacts, appointments and tasks, and the order of the mail (a new search)."""
        self._generation += 1
        self._ask_pim()
        self._ask_mail()

    def _ask_pim(self) -> None:
        generation = self._generation

        def arrived(result: TagOverview) -> None:
            if generation != self._generation:
                return
            self._overview = result
            self._show_unreadable(result.unreadable)
            moved, self._moved_to = self._moved_to, ""
            self.model.follow(self.model.wanted(result))
            if moved and moved in self.model.keys:
                self._shown = ""
                self.list.setCurrentIndex(self.model.index(self.model.keys.index(moved)))  # the moved item, selected and shown again
                self._shown_changed = False
                return
            self._after_follow()
            if self._shown_changed and self._shown in self.model.pim and self._shown in self.model.keys:
                self._show_pim(self._shown)  # the item on the right changed: shown as it is now
            self._shown_changed = False

        def failed(error: ChronoMailError) -> None:
            self._shown_changed, self._moved_to = False, ""
            self.statusBar().showMessage(f"The contacts, appointments and tasks with this tag could not be read: {error}", 8000)

        self._backend.ask(TAG_OVERVIEW, OverviewParameters(self.tag, self.search.text().strip()), arrived, failed, owner=self)

    def _ask_mail(self) -> None:
        generation = self._generation

        def arrived(result: ViewOrder) -> None:
            if generation != self._generation:
                return
            mailboxes = result.mailboxes or ("",) * len(result.messages)
            self.model.mail_order = list(zip(mailboxes, result.folders, result.messages, strict=True))
            self.model.mail_total = result.total
            wanted = {mail_key(mailbox, message_id) for mailbox, _, message_id in self.model.mail_order}
            for row in result.rows:  # the first rows come with the order
                self.model.mail_rows[mail_key(row.account, row.message_id)] = row
            self.model.mail_rows = {key: row for key, row in self.model.mail_rows.items() if key in wanted}
            self.model.asked &= wanted
            self.model.follow(self.model.wanted(None))
            self._after_follow()

        def failed(error: ChronoMailError) -> None:
            self.statusBar().showMessage(f"The mail with this tag could not be read: {error}", 8000)

        parameters = CombinedOrderParameters(view="tagged", tag=self.tag, query=self.search.text().strip())
        self._backend.ask(MESSAGE_COMBINED_ORDER, parameters, arrived, failed, owner=self)

    def _fetch_mail(self, wanted: list[tuple[str, str]]) -> None:
        by_mailbox: dict[str, list[str]] = {}
        for mailbox, message_id in wanted:
            by_mailbox.setdefault(mailbox, []).append(message_id)
        for mailbox, ids in by_mailbox.items():

            def arrived(result: RowsAnswer, mailbox: str = mailbox, ids: tuple[str, ...] = tuple(ids)) -> None:
                for row in result.rows:
                    self.model.mail_rows[mail_key(mailbox, row.message_id)] = row
                for message_id in ids:
                    self.model.asked.discard(mail_key(mailbox, message_id))
                self._redraw_mail()

            self._backend.ask(MESSAGE_ROWS, RowsParameters(mailbox, tuple(ids), combined=True), arrived, lambda error: None, owner=self)

    def _redraw_mail(self) -> None:
        if self.model.keys:
            self.model.dataChanged.emit(self.model.index(0), self.model.index(len(self.model.keys) - 1))

    def _show_unreadable(self, accounts: tuple[str, ...]) -> None:
        self.note.setVisible(bool(accounts))
        if accounts:
            self.note.setText("Some calendars, task lists or address books could not be read just now, so their items are not shown here.")

    def _tags_arrived(self, result: tuple[TagInfo, ...]) -> None:
        self.colours.update(list(result))

    def _after_follow(self) -> None:
        """The item shown on the right went: the right side says so."""
        if self._shown and self._shown not in self.model.keys:
            self._shown = ""
            self.reading.setCurrentWidget(self.nothing)

    # --- facts --------------------------------------------------------------------------------------------------------

    def _fact(self, change: object) -> None:
        if not isinstance(change, Change):
            return
        kind = str(change.kind)
        if kind.startswith("item_") or kind in ("collection_changed", "collection_removed"):
            shown = self.model.pim.get(self._shown)
            if shown is not None and (change.message_id == shown.item or (kind == "collection_changed" and change.mailbox == shown.account)):
                self._shown_changed = True  # only then the pane is drawn again: its scroll stays through other changes
                if kind == "item_moved" and change.new_message_id:
                    self._moved_to = f"p\x1f{change.new_message_id}"  # selected again under its new id once the overview has come
            self._again_pim = True
            self._again.start()
        elif kind == "removed":
            key = mail_key(change.mailbox, change.message_id)
            if any(mail_key(mailbox, message_id) == key for mailbox, _, message_id in self.model.mail_order):
                self.model.mail_order = [entry for entry in self.model.mail_order if mail_key(entry[0], entry[2]) != key]
                self.model.follow(self.model.wanted(None))
                self._after_follow()
        elif kind == "flags":
            key = mail_key(change.mailbox, change.message_id)
            row = self.model.mail_rows.get(key)
            if row is not None:
                self.model.mail_rows[key] = dataclasses.replace(row, flags=tuple(change.flags))  # read, flagged, replied (the fact carries no row)
                self._redraw_mail()
        elif kind == "folder_read":
            read = False
            for mailbox, folder, message_id in self.model.mail_order:
                key = mail_key(mailbox, message_id)
                row = self.model.mail_rows.get(key)
                if mailbox == change.mailbox and folder == change.folder and row is not None and "seen" not in row.flags:
                    self.model.mail_rows[key] = dataclasses.replace(row, flags=(*row.flags, "seen"))
                    read = True
            if read:
                self._redraw_mail()
        elif kind == "changed" and change.row is not None:
            key = mail_key(change.mailbox, change.message_id)
            if key in self.model.mail_rows:
                self.model.mail_rows[key] = change.row
                self._redraw_mail()
        elif kind in ("added", "moved", "tags", "mailbox_changed", "folder_renamed", "folder_deleted", "folder_emptied"):
            self._again_mail = True
            self._again.start()

    def _refetch(self) -> None:
        pim, mail = self._again_pim, self._again_mail
        self._again_pim = self._again_mail = False
        if pim:
            self._ask_pim()
        if mail:
            self._ask_mail()

    # --- the list ---------------------------------------------------------------------------------------------------

    def _clicked(self, index: QModelIndex) -> None:
        """A click on a heading or a folded part folds or unfolds it."""
        if str(index.data(KIND_ROLE)) == ITEM:
            return
        key = self.model.keys[index.row()]
        part = key[2:]
        self.model.folded ^= {part}
        self.model.follow(self.model.wanted(None))

    def _current_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        if not current.isValid() or str(current.data(KIND_ROLE)) != ITEM:
            self._shown = ""
            self.reading.setCurrentWidget(self.nothing)
            return
        key = self.model.keys[current.row()]
        self._shown = key
        self.reading.setCurrentWidget(self.nothing)  # never the item before while this one comes (or cannot be shown)
        if self.model.kind_of(key) == MAIL:
            self._show_mail(key)
            return
        self._show_pim(key)

    def _show_pim(self, key: str) -> None:
        """A contact, appointment or task in the reading pane, as it is now."""
        group = self.model.kind_of(key)
        item = self.model.pim[key]
        if group == CONTACTS:
            self._backend.ask(CONTACT_SHOW, ContactItem(item.item), lambda result: self._show_contact(key, result), self._not_shown, owner=self)
        elif group == EVENTS:
            self._backend.ask(EVENT_SHOW, EventItem(item.item), lambda result: self._show_event(key, result), self._not_shown, owner=self)
        else:
            self._backend.ask(TASK_SHOW, TaskItem(item.item), lambda result: self._show_task(key, result), self._not_shown, owner=self)

    def _double_clicked(self, index: QModelIndex) -> None:
        if str(index.data(KIND_ROLE)) != ITEM:
            return
        key = self.model.keys[index.row()]
        group = self.model.kind_of(key)
        if group == MAIL:
            _, mailbox, message_id = key.split("\x1f")
            folder = next((folder for box, folder, found in self.model.mail_order if box == mailbox and found == message_id), "")
            self._openers.mail(mailbox, folder, message_id)
            return
        item = self.model.pim[key].item
        {CONTACTS: self._openers.contact, EVENTS: self._openers.event, TASKS: self._openers.task}[group](item)

    # --- the reading pane ---------------------------------------------------------------------------------------------

    def _not_shown(self, error: ChronoMailError) -> None:
        self.statusBar().showMessage(f"Could not show it: {error}", 8000)

    def _show_contact(self, key: str, result: ContactInfo) -> None:
        if key == self._shown:
            self.contact.set_contact(result)
            self.reading.setCurrentWidget(self.contact)

    def _show_event(self, key: str, result: EventInfo) -> None:
        if key != self._shown:
            return
        item = self.model.pim[key]

        def row(label: str, value: str) -> str:
            return f"<tr><td style='color:gray' align=right>{html.escape(label)}</td><td>{value}</td></tr>" if value else ""

        when = when_text(item.start, item.floating, not item.all_day)
        details = "".join((
            row("Next" if item.repeats and item.later else "When", html.escape(when)), row("Repeats", "Yes" if result.rrule else ""),
            row("Where", html.escape(result.location)), row("Calendar", html.escape(item.collection_name)),
            row("Status", "Cancelled" if result.status == "CANCELLED" else ""),
        ))
        tags = self.colours.html(result.tags)
        note = result.description
        self.text.setHtml(
            f"<h2>{html.escape(result.summary or '(no title)')}</h2>{('<p>' + tags + '</p>') if tags else ''}<table cellspacing=4>{details}</table>"
            + (f"<h4 style='margin-bottom:2px'>Description</h4><p>{html.escape(note).replace(chr(10), '<br>')}</p>" if note else "")
        )
        self.reading.setCurrentWidget(self.text)

    def _show_task(self, key: str, result: TaskInfo) -> None:
        if key != self._shown:
            return
        item = self.model.pim[key]
        status = "Completed" if result.done else {"NEEDS-ACTION": "Not started", "IN-PROCESS": "In progress", "CANCELLED": "Cancelled"}.get(result.status, result.status)

        def row(label: str, value: str) -> str:
            return f"<tr><td style='color:gray' align=right>{html.escape(label)}</td><td>{value}</td></tr>" if value else ""

        details = "".join((
            row("Status", html.escape(status)), row("Due", html.escape(when_text(item.due, item.floating, False))),
            row("List", html.escape(item.collection_name)), row("Location", html.escape(result.location)),
            row("Progress", f"{result.percent} %" if result.percent else ""),
        ))
        tags = self.colours.html(result.tags)
        note = result.description
        self.text.setHtml(
            f"<h2>{html.escape(result.summary or '(no title)')}</h2>{('<p>' + tags + '</p>') if tags else ''}<table cellspacing=4>{details}</table>"
            + (f"<h4 style='margin-bottom:2px'>Note</h4><p>{html.escape(note).replace(chr(10), '<br>')}</p>" if note else "")
        )
        self.reading.setCurrentWidget(self.text)

    def _show_mail(self, key: str) -> None:
        _, mailbox, message_id = key.split("\x1f")
        folder = next((folder for box, folder, found in self.model.mail_order if box == mailbox and found == message_id), "")

        def shown(result: ShowResult) -> None:
            if key != self._shown:
                return
            if self.reader is not None:  # a reader is made for one message: its card's actions name that message
                self.reader.deleteLater()
            layout = self.mail_holder.layout()
            if layout is None:
                layout = QVBoxLayout(self.mail_holder)
                layout.setContentsMargins(0, 0, 0, 0)
            reader = ReaderWidget(self.mail_holder)
            layout.addWidget(reader)
            self.reader = reader
            self._openers.prepare_reader(reader)  # the privacy settings come before the message
            self._openers.wire_reader(reader, mailbox, folder, message_id)
            reader.link_activated.connect(self._openers.open_link)
            reader.link_hovered.connect(self.status_line.show_link)
            reader.attachment_open_requested.connect(partial(self._open_attachment, mailbox, folder, message_id))
            reader.attachment_save_requested.connect(partial(self._save_attachment, mailbox, folder, message_id))
            reader.show_message(result)
            self.reading.setCurrentWidget(self.mail_holder)

        self._backend.ask(MESSAGE_SHOW, MessageParameters(mailbox, folder, message_id), shown, self._not_shown, owner=self)

    def _open_attachment(self, mailbox: str, folder: str, message_id: str, position: int, name: str) -> None:
        if is_calendar_file(name) or (self.reader is not None and position in self.reader.calendar_parts):
            self._openers.calendar_card(CalendarSource(mailbox, folder, message_id, part=position), name, self)
            return
        open_attachment(self, self._backend, (mailbox, folder, message_id), position, name, self._open_failed, self)

    def _open_failed(self, text: str, error: ChronoMailError | None) -> None:
        self.statusBar().showMessage(text if error is None else f"{text}: {error}", 8000)

    def _save_attachment(self, mailbox: str, folder: str, message_id: str, position: int, suggested: str) -> None:
        downloads = Path.home() / "Downloads"
        start = str((downloads if downloads.is_dir() else Path.home()) / suggested)
        path, _ = QFileDialog.getSaveFileName(self, "Save attachment", start)
        if not path:
            return
        self._backend.ask(
            MESSAGE_ATTACHMENT_SAVE, AttachmentSaveParameters(mailbox, folder, message_id, position, path, overwrite=True),
            lambda result: self.statusBar().showMessage(f"Saved {result.path}", 8000), lambda error: self.statusBar().showMessage(f"Could not save the attachment: {error}", 8000), owner=self,
        )
