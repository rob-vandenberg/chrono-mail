from typing import override

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QToolBar, QVBoxLayout, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.collectionpanel import CollectionPanel
from chrono_mail.ui.icons import theme_icon
from chrono_mail.ui.sections import SectionPage

# --- Version ------------------------------------------------------------
__version__ = "pimpages.py 0.0.65"

# --- Version history ----------------------------------------------------
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
        self.toolbar = QToolBar("Section")
        self.toolbar.setMovable(False)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.act_new = QAction(self.new_label, self.toolbar)
        self.act_new.setIcon(theme_icon(self.new_icon))
        self.act_new.triggered.connect(self.new_item)
        self.act_refresh = QAction("Refresh", self.toolbar)
        self.act_refresh.setIcon(theme_icon("view-refresh"))
        self.act_refresh.triggered.connect(self.refresh)
        self.toolbar.addAction(self.act_new)
        self.toolbar.addAction(self.act_refresh)
        self.panel.changed.connect(self.selection_changed)

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

    def selection_changed(self) -> None:
        count = len(self.panel.visible_ids())
        self.message.setText(f"{count} selected" if count else "Nothing selected")
        self.status(self.message.text())
