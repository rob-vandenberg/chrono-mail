from collections.abc import Callable
from typing import override

from PySide6.QtCore import QSettings, Qt, Signal, SignalInstance
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from chrono_mail.engine.changes import Change
from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_pim import PIM_ENABLE, CollectionSummary
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.syncmarks import SyncMarks
from chrono_mail.ui.collectionpanel import CollectionPanel
from chrono_mail.ui.icons import theme_icon
from chrono_mail.ui.sections import SectionPage

# --- Version ------------------------------------------------------------
__version__ = "pimpages.py 1.9.275"

# --- Version history ----------------------------------------------------
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.273: R-19: pim.enable typed
# v1.1.245: section_name and _failed: the one error line of Calendar, Tasks and Contacts (R-07)
# v1.1.229: conflict_requested: a click on an item's triangle, for the window to open that conflict (the owner)
# v1.1.226: use_marks: the Sync conflicts entry and the items' sync marks follow the window's SyncMarks (D-194)
# v1.1.218: Refresh brings the calendars, task lists and address books in step with their servers (servers), not only the list here (D-190)
# v0.5.170: Its buttons are a row of the window's one toolbar (D-156)
# v0.5.167: The toolbar folds like Mail's: New and Refresh at the left (D-154)
# v0.5.159: apply_fact: the facts of this kind of collection reach the panel and the section (apply_item); a change from outside fetches again, once, one at a time (D-146 step 5)
# v0.0.65: The CalDAV and CardDAV client; first-time setup of the local calendars is serialised
# v0.0.64: The calendar section
# v0.0.63: The tasks section
# v0.0.62: The contacts section
# v0.0.61: Initial pages for the calendar, task and contact sections (the sidebar, a toolbar and a main area to be filled)


class PimPage(SectionPage):
    """A section with calendars, task lists or address books on the left; what is shown on the right is made by the subclasses."""

    kind: str = "events"
    multiple: bool = True
    new_label: str = "New"
    new_icon: str = "list-add"
    section_name: str = ""  # "Calendar", "Tasks", "Contacts": how the status line names the section in an error
    conflict_requested = Signal(str)  # an item's triangle was clicked: its item (D-194)

    def __init__(self, backend: Backend, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._settings: QSettings = settings
        self._enabled: bool = False
        self.panel = CollectionPanel(backend, settings, self.kind, self.multiple)
        self.side = self.panel
        self.content = QWidget()
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(0, 0, 0, 0)
        self.message = QLabel("", self.content)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.toolbar.setObjectName(f"{self.kind}Toolbar")
        self.act_new = QAction(self.new_label, self.toolbar)
        self.act_new.setIcon(theme_icon(self.new_icon))
        self.act_new.triggered.connect(self.new_item)
        self.act_refresh = QAction("Refresh", self.toolbar)
        self.act_refresh.setIcon(theme_icon("view-refresh"))
        self.act_refresh.triggered.connect(self.refresh_from_servers)
        self.servers: Callable[[], None] | None = None  # set by the window: synchronise with the servers (Refresh)
        self.toolbar.add_left(self.act_new)
        self.toolbar.add_left(self.act_refresh)
        self.panel.changed.connect(self.selection_changed)
        self.pending: set[str] = set()  # items with an action under way: greyed, no second action (D-146)
        self._refetching: bool = False
        self._refetch_again: bool = False
        self.marks: SyncMarks | None = None  # set by the window (use_marks)

    @override
    def on_show(self) -> None:
        """The first time the section is shown, the calendar, the task list and the address book of this computer are made if they are not there yet."""
        if not self._enabled:
            self._enabled = True

            def enabled(result: tuple[CollectionSummary, ...]) -> None:
                self.panel.reload()

            def failed(error: ChronoMailError) -> None:
                try:
                    self.message.setText(f"Calendars, tasks and contacts could not be opened: {error}")
                except RuntimeError:
                    pass  # the window was closed while the command ran

            self._backend.ask(PIM_ENABLE, NoParameters(), enabled, failed, owner=self)
        else:
            self.panel.reload()

    @override
    def refresh(self) -> None:
        self.panel.reload()

    def refresh_from_servers(self) -> None:
        """Refresh: ask the servers what changed (the window shows that it runs and what came of it); without the window, the list here only."""
        if self.servers is not None:
            self.servers()
        else:
            self.refresh()

    @override
    def new_item(self) -> None:
        self.panel.new_collection()  # until the items have their own editors

    # --- following the facts (D-146) ---------------------------------------------------------------------------

    def apply_fact(self, change: Change) -> None:
        """A fact of the engine. Facts about this kind of collection change the panel and the section; a change from
        outside (a sync, an import) fetches again what this section shows of that account, once, one at a time."""
        if not self._enabled:
            return
        kind = str(change.kind)
        if kind == "collection_changed":
            if any(info.account == change.mailbox for info in self.panel.collections):
                self.refetch()
            return
        if change.role != self.kind:
            return
        self.panel.apply(change)
        if kind.startswith("item_"):
            self.apply_item(change)

    def use_marks(self, marks: SyncMarks) -> None:
        """The window's sync marks: the sidebar's Sync conflicts entry and the marks on the items follow them (D-194); a click on a triangle asks
        the window for that conflict (conflict_requested)."""
        self.marks = marks
        marks.changed.connect(self._marks_changed)
        for clicked in self.conflict_sources():
            clicked.connect(self.conflict_requested.emit)
        self._marks_changed()

    def _failed(self, error: ChronoMailError) -> None:
        self.status(f"{self.section_name}: {error}")

    def conflict_sources(self) -> list[SignalInstance]:
        """The signals of what draws this section's items that tell a click on a triangle (subclasses)."""
        return []

    def _marks_changed(self) -> None:
        marks = self.marks
        if marks is None:
            return
        self.panel.show_conflicts(len(marks.conflicts))
        self.marks_changed(marks)

    def marks_changed(self, marks: SyncMarks) -> None:
        """Which items carry a sync mark changed: the section draws them again (subclasses)."""

    def apply_item(self, change: Change) -> None:
        """An item of this kind was added, changed, removed or moved: the section changes that one item (subclasses)."""

    def refetch(self) -> None:
        """A change from outside: the collections and what the section shows are fetched again; one at a time."""
        if self._refetching:
            self._refetch_again = True
            return
        self._refetching = True
        self.panel.reloaded.connect(self._refetched)
        self.panel.reload(self.panel.current_id())

    def _refetched(self) -> None:
        self.panel.reloaded.disconnect(self._refetched)
        self._refetching = False
        if self._refetch_again:
            self._refetch_again = False
            self.refetch()

    def mark_pending(self, items: list[str]) -> None:
        self.pending.update(items)

    def clear_pending(self, item: str) -> None:
        self.pending.discard(item)

    def selection_changed(self) -> None:
        count = len(self.panel.visible_ids())
        self.message.setText(f"{count} selected" if count else "Nothing selected")
        self.status(self.message.text())
