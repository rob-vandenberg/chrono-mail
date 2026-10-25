from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QScrollArea, QVBoxLayout, QWidget

from chrono_mail.engine.commands_dav import FoundFolder, FoundServer

# --- Version ------------------------------------------------------------
__version__ = "foundfolders.py 1.1.214"

# --- Version history ----------------------------------------------------
# v1.1.214: "Select the folders you would like to add:"; the ticks are a widget of their own, also on the editor's Calendars and contacts tab (D-187)
# v1.1.210: Initial window: the calendars, task lists and address books found for an account, each with a tick (D-181)

KIND_WORDS: dict[str, str] = {"events": "Calendar", "tasks": "Tasks", "contacts": "Contacts"}
MAXIMUM_LIST_HEIGHT: int = 400
CHOOSE_TEXT: str = "Select the folders you would like to add:"


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
        self.setWindowTitle("Additional folders found")
        self.setMinimumWidth(420)
        self.found: FoundServer = found
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(CHOOSE_TEXT, self))
        self.folders = FolderTicks(self)
        self.folders.show_folders(found.folders)
        self.ticks: list[tuple[FoundFolder, QCheckBox]] = self.folders.ticks
        scroll = QScrollArea(self)
        scroll.setWidget(self.folders)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMaximumHeight(min(MAXIMUM_LIST_HEIGHT, self.folders.sizeHint().height() + 4))
        layout.addWidget(scroll)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def left_out(self) -> tuple[str, ...]:
        """The folders that are not ticked (their keys)."""
        return self.folders.left_out()

    def any_ticked(self) -> bool:
        return self.folders.any_ticked()
