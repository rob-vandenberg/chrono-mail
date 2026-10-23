from collections.abc import Callable, Sequence

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QInputDialog, QMenu, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.changes import MessageRow
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_pim import TAG_LIST, TagInfo
from chrono_mail.engine.commands_tags import MESSAGE_TAG, MailTagParameters, MessageTagsResult
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.mailstate import MailState, Placed
from chrono_mail.ui.messagelistpane import MessageListPane
from chrono_mail.ui.statusreport import StatusReport
from chrono_mail.ui.tagchips import TagColourBook
from chrono_mail.ui.tagging import fill_tag_menu, offered_for_mail
from chrono_mail.ui.undoing import Undoing

# --- Version ------------------------------------------------------------
__version__ = "mailtags.py 2.4.329"

# --- Version history ----------------------------------------------------
# v2.4.329: a tag goes to every selected message, also those not loaded yet; the shortcut and the menu's ticks count them all (W12)
# v2.2.313: a tag given or taken away is one action of Undo (B-18)
# v1.9.274: R-19 round 4: the tags and rows are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v1.9.270: R-19: tag.list and message.tag typed
# v1.9.264: R-17: the tags of the mail as a part of their own (out of ui/mainwindow.py): the tags with their colours and keys, the Tag
#           menu, giving and taking a tag; the one owner of the tags the parts share


class MailTags(QObject):
    """The tags of the mail: loaded with tag.list, one set of colours for the list, the cards and the reading pane."""

    arrived = Signal()  # tag.list answered (or failed): the folder list takes the Tags entries in
    message_tags_changed = Signal(str, object)  # a message got other tags (message id, tags): the reading pane follows

    def __init__(
        self, backend: Backend, state: MailState, status: StatusReport, messages: MessageListPane, book: TagColourBook, open_tags: Callable[[], None], undoing: Undoing, parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._undoing: Undoing = undoing
        self._backend: Backend = backend
        self._state: MailState = state
        self._status: StatusReport = status
        self._messages: MessageListPane = messages
        self.book: TagColourBook = book
        self._open_tags: Callable[[], None] = open_tags
        self._parent: QWidget = parent  # the tag keys are the window's
        self.ready: bool = False  # tag.list has answered since the accounts were last loaded
        self._shortcuts: list[QAction] = []

    def reload(self) -> None:
        """The accounts are loaded again: the tags too, and the folder list waits for them."""
        self.ready = False
        self.load()

    def load(self) -> None:
        """The tags with their colours, order and shortcuts: for the menus, the list, the reading pane and the folder list."""

        def loaded(result: tuple[TagInfo, ...]) -> None:
            self._state.tags = result
            self.book.update(self._state.tags)
            self._make_shortcuts()
            self._arrived()
            self._messages.view.viewport().update()

        def failed(error: ChronoMailError) -> None:
            self._status.report("Tags", error)
            self._arrived()

        self._backend.ask(TAG_LIST, NoParameters(), loaded, failed, owner=self)

    def _arrived(self) -> None:
        self.ready = True
        self.arrived.emit()

    def tags_of_message(self, message_id: str) -> tuple[str, ...]:
        """The tags of a message of the list (a message of the conversation bar that is not in the list: none shown)."""
        model = self._messages.model
        position = model.find(message_id)
        row = model.row_at(position) if position >= 0 else None
        return tuple(row.tags) if row is not None else ()

    def _make_shortcuts(self) -> None:
        for action in self._shortcuts:
            self._parent.removeAction(action)
            action.deleteLater()
        self._shortcuts = []
        for tag in self._state.tags:
            shortcut = tag.shortcut
            if not shortcut or not offered_for_mail(tag):
                continue
            action = QAction(self._parent)
            action.setShortcut(QKeySequence(shortcut))
            name = tag.name
            action.triggered.connect(lambda checked=False, chosen=name: self.toggle_tag_by_key(chosen))
            self._parent.addAction(action)
            self._shortcuts.append(action)

    def toggle_tag_by_key(self, name: str) -> None:
        """A tag's shortcut: give the tag to the selected messages, or take it away when all of them have it (all of them counted, also
        those not loaded yet, W12)."""
        chosen = self._messages.selected_rows()

        def decide(rows: list[MessageRow]) -> None:
            everyone = all(any(have.casefold() == name.casefold() for have in tuple(row.tags)) for row in rows)
            self.toggle_tag(name, not everyone, chosen)

        if chosen:
            self._messages.with_rows(chosen, decide)

    def fill_tag_menu(self, menu: QMenu) -> None:
        """The ticks say which tags the selected messages have; rows not loaded yet are asked first and the menu is filled again with them."""
        chosen = self._messages.selected_rows()
        loaded = [item.row for item in chosen if item.row is not None]
        fill_tag_menu(menu, self._state.tags, self.book, [tuple(row.tags) for row in loaded], self.toggle_tag, self._new_tag, self._open_tags)
        if len(loaded) < len(chosen):

            def refill(rows: list[MessageRow]) -> None:
                if menu.isVisible():
                    fill_tag_menu(menu, self._state.tags, self.book, [tuple(row.tags) for row in rows], self.toggle_tag, self._new_tag, self._open_tags)

            self._messages.with_rows(chosen, refill)

    def toggle_tag(self, name: str, add: bool, chosen: Sequence[Placed] | None = None) -> None:
        """Give a tag to the selected messages (add) or take it away; the rows show the new tags at once."""
        rows = self._messages.selected_rows() if chosen is None else chosen
        groups: dict[tuple[str, str], list[str]] = {}
        for row in rows:
            groups.setdefault((self._state.account_of(row), row.folder), []).append(row.message_id)
        new = not any(tag.name.casefold() == name.casefold() for tag in self._state.tags)

        def done(result: tuple[MessageTagsResult, ...]) -> None:
            for item in result:
                message_id, tags = item.message, item.tags
                self._messages.model.replace_tags(message_id, tags)
                self.message_tags_changed.emit(message_id, tags)
            if new and add:
                self.load()  # a message that loses the tag stays in a tag's list until it is left, as a read message in Unread (D-146)
            self._undoing.ended()

        def failed(error: ChronoMailError) -> None:
            self._status.report("Could not change the tags", error)
            self._undoing.ended()

        number = self._undoing.new_action()  # one key press, however many folders: one step of Undo
        for (account, folder), ids in groups.items():
            parameters = MailTagParameters(account, folder, tuple(ids), add=(name,) if add else (), remove=() if add else (name,), undo=number)
            self._undoing.started()
            self._backend.ask(MESSAGE_TAG, parameters, done, failed, owner=self)

    def _new_tag(self) -> None:
        name, ok = QInputDialog.getText(self._parent, "New tag", "Name of the new tag (the selected messages get it):")
        if ok and " ".join(name.split()):
            self.toggle_tag(" ".join(name.split()), True)
