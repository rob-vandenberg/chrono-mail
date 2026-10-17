from typing import override

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.collectionpanel import CollectionPanel
from chrono_mail.ui.icons import theme_icon
from chrono_mail.ui.sections import SectionPage

# --- Version ------------------------------------------------------------
__version__ = "pimpages.py 0.5.170"

# --- Version history ----------------------------------------------------
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
        self.act_refresh.triggered.connect(self.refresh)
        self.toolbar.add_left(self.act_new)
        self.toolbar.add_left(self.act_refresh)
        self.panel.changed.connect(self.selection_changed)
        self.pending: set[str] = set()  # items with an action under way: greyed, no second action (D-146)
        self._refetching: bool = False
        self._refetch_again: bool = False

    @override
    def on_show(self) -> None:
        """The first time the section is shown, the calendar, the task list and the address book of this computer are made if they are not there yet."""
        if not self._enabled:
            self._enabled = True

            def enabled(result: object) -> None:
                self.panel.reload()

            def failed(error: ChronoMailError) -> None:
                try:
                    self.message.setText(f"Calendars, tasks and contacts could not be opened: {error}")
                except RuntimeError:
                    pass  # the window was closed while the command ran

            self._backend.call("pim.enable", {}, enabled, failed, owner=self)
        else:
            self.panel.reload()

    @override
    def refresh(self) -> None:
        self.panel.reload()

    @override
    def new_item(self) -> None:
        self.panel.new_collection()  # until the items have their own editors

    # --- following the facts (D-146) ---------------------------------------------------------------------------

    def apply_fact(self, change: object) -> None:
        """A fact of the engine. Facts about this kind of collection change the panel and the section; a change from
        outside (a sync, an import) fetches again what this section shows of that account, once, one at a time."""
        if not self._enabled:
            return
        kind = str(getattr(change, "kind"))
        if kind == "collection_changed":
            if any(str(getattr(info, "account")) == str(getattr(change, "mailbox")) for info in self.panel.collections):
                self.refetch()
            return
        if str(getattr(change, "role")) != self.kind:
            return
        self.panel.apply(change)
        if kind.startswith("item_"):
            self.apply_item(change)

    def apply_item(self, change: object) -> None:
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
