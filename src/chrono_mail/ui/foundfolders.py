from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QScrollArea, QVBoxLayout, QWidget

from chrono_mail.engine.commands_dav import FoundFolder, FoundServer
from chrono_mail.ui import forms
from chrono_mail.ui.forms import part
from chrono_mail.ui.scale import wpx

# --- Version ------------------------------------------------------------
__version__ = "foundfolders.py 4.0.376"

# --- Version history ----------------------------------------------------
# v4.0.376: the window is built from its Qt Designer form (ui/forms/found_folders.ui, D-276), the ticks at its place; its sizes follow the size factor
# v1.1.214: "Select the folders you would like to add:"; the ticks are a widget of their own, also on the editor's Calendars and contacts tab (D-187)
# v1.1.210: Initial window: the calendars, task lists and address books found for an account, each with a tick (D-181)

KIND_WORDS: dict[str, str] = {"events": "Calendar", "tasks": "Tasks", "contacts": "Contacts"}
MAXIMUM_LIST_HEIGHT: int = 400


def folder_text(folder: FoundFolder) -> str:
    """How a found folder is named in the list: its kind, its name, and whether it is public or read-only."""
    text = f"{KIND_WORDS.get(folder.kind, folder.kind)}: {folder.name}"
    if folder.public:
        text += " (public)"
    if folder.read_only:
        text += " (read only)"
    return text


class FolderTicks(QWidget):
    """The folders of a calendar server, one tick each, as the search says they start (kept in step now, or all for a server not used yet)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows = QVBoxLayout(self)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self.ticks: list[tuple[FoundFolder, QCheckBox]] = []

    def show_folders(self, folders: tuple[FoundFolder, ...]) -> None:
        for _, tick in self.ticks:
            self._rows.removeWidget(tick)
            tick.deleteLater()
        self.ticks = []
        for folder in folders:
            tick = QCheckBox(folder_text(folder), self)
            tick.setChecked(folder.ticked)
            self._rows.addWidget(tick)
            self.ticks.append((folder, tick))

    def left_out(self) -> tuple[str, ...]:
        """The folders that are not ticked (their keys)."""
        return tuple(folder.key for folder, tick in self.ticks if not tick.isChecked())

    def any_ticked(self) -> bool:
        return any(tick.isChecked() for _, tick in self.ticks)

    def changed(self) -> bool:
        """Whether a tick differs from how it started."""
        return any(tick.isChecked() != folder.ticked for folder, tick in self.ticks)


class FoundFoldersDialog(QDialog):
    """Additional folders found: one tick per folder, as the search says it starts (all ticked for a server the account does not use yet). OK keeps the
    ticked ones in step; Cancel changes nothing."""

    def __init__(self, found: FoundServer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.found: FoundServer = found
        form = forms.fill(self, "found_folders", {"folders": self._ticks})  # D-276
        self.folders: FolderTicks = part(form, FolderTicks, "folders")
        self.ticks: list[tuple[FoundFolder, QCheckBox]] = self.folders.ticks
        scroll = part(form, QScrollArea, "scroll")
        scroll.setMaximumHeight(min(wpx(MAXIMUM_LIST_HEIGHT), self.folders.sizeHint().height() + 4))
        box = part(form, QDialogButtonBox, "buttons")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        forms.smallest(self, 420)  # its texts are never cut off, also at the largest size

    def _ticks(self, parent: QWidget) -> FolderTicks:
        """The list with its ticks, made at its place in the form, so Tab reaches the ticks before the buttons below them."""
        ticks = FolderTicks(parent)
        ticks.show_folders(self.found.folders)
        return ticks

    def left_out(self) -> tuple[str, ...]:
        """The folders that are not ticked (their keys)."""
        return self.folders.left_out()

    def any_ticked(self) -> bool:
        return self.folders.any_ticked()
