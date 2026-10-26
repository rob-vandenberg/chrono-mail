from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from chrono_mail.engine.commands_sidebar import (
    FAVORITE_SHOW, FAVORITE_UNPIN, SIDEBAR_SHOW_LOCAL_FOLDER, SIDEBAR_SHOW_TREE, LocalFolderParameters, PinnedParameters, SidebarInfo, TreeParameters, VisibleParameters,
)
from chrono_mail.ui import forms
from chrono_mail.ui.backend import AnyStep, Step
from chrono_mail.ui.forms import part
from chrono_mail.ui.prefs import StartupView
from chrono_mail.ui.scale import wpx

# --- Version ------------------------------------------------------------
__version__ = "sidebarpage.py 3.6.373"

# --- Version history ----------------------------------------------------
# v3.6.373: built from the form settings_sidebar.ui: the heading and the place of the ticks (D-276); nothing visible changed
# v1.9.270: R-19: commands gives typed steps
# v1.2.253: startup_forces takes Show on startup as read once (StartupView), never split here (R-12)
# v1.1.204: A tick for each standard folder of Local Folders that exists (greyed while Local Folders is off; the one
#           Show on startup chose, or holds the chosen folder, is ticked and greyed)
# v1.1.191: Initial: Settings > Sidebar (replaces the Favorites page of 1.1.182): Favorites with its entries and pinned
#           folders, Tags, Local Folders and every account with a folder tree of its own; what Show on startup chose is
#           ticked and greyed (always shown); the last thing still shown cannot be switched off


@dataclass(frozen=True, slots=True)
class StartupForces:
    """What the choice of Show on startup keeps visible in the folder list, whatever is switched off."""

    views: frozenset[str] = field(default_factory=frozenset)  # a standard favorite
    favorites: bool = False  # the Favorites heading (a favorite or a pinned folder was chosen)
    tags: bool = False  # the Tags heading (a tag was chosen)
    trees: frozenset[str] = field(default_factory=frozenset)  # the account (or Local Folders) of a chosen folder
    folders: frozenset[tuple[str, str]] = field(default_factory=frozenset)  # (account, folder) of a chosen folder


def startup_forces(startup: StartupView) -> StartupForces:
    """What Show on startup chose (Settings > General): the last selected folder, an entry of the folder list or
    a section. The last selected folder and the sections keep nothing visible."""
    if startup.kind != "mail":
        return StartupForces()
    account, kind, path = startup.account, startup.entry_kind, startup.path
    if kind == "favorite":
        return StartupForces(views=frozenset({path}), favorites=True)
    if kind == "pinlink":
        return StartupForces(favorites=True)
    if kind == "tagview":
        return StartupForces(tags=True)
    if account and kind == "folder":
        return StartupForces(trees=frozenset({account}), folders=frozenset({(account, path)}))
    if account and kind in ("account", "unread", "flagged"):
        return StartupForces(trees=frozenset({account}))
    return StartupForces()


class SidebarPage(QWidget):
    """Settings > Sidebar: what the folder list shows. Favorites (the heading, each entry and each pinned folder), Tags,
    Local Folders with each of its standard folders, and every account that has a folder tree of its own. What Show on startup chose is always shown: its
    ticks are ticked and greyed, and the person's own tick comes back when the startup choice changes. The last thing that
    still shows something cannot be switched off (greyed). Nothing changes before OK."""

    def __init__(
        self, standard: tuple[tuple[str, str], ...], hidden: set[str], pins: list[tuple[str, str]], show_favorites: bool, show_tags: bool,
        show_local_folders: bool, has_tags: bool, local_id: str, accounts: list[tuple[str, str]], hidden_trees: set[str], startup_view: str,
        parent: QWidget | None = None, local_folders: list[tuple[str, str]] | None = None, hidden_local: set[str] | None = None,
    ) -> None:
        """standard: (view id, label) of the standard favorites in use; pins: (pin id, label); accounts: (account id, label)
        of the accounts with a folder tree of their own; local_folders: (kind, folder) of the standard folders Local Folders
        has (LOCAL_FOLDER_KINDS), in the order of the folder list; hidden_local: the kinds switched off."""
        super().__init__(parent)
        self._hidden, self._hidden_trees, self._local_id = set(hidden), set(hidden_trees), local_id
        self._show = (show_favorites, show_tags, show_local_folders)
        self._hidden_local = set(hidden_local or set())
        self._local_paths = {kind: path for kind, path in local_folders or []}
        form = forms.fill(self, "settings_sidebar")  # the heading and the place of the ticks, which are made here from what the folder list holds (D-276)
        part(form, QScrollArea, "scroller").setFrameShape(QFrame.Shape.NoFrame)
        inner = part(form, QWidget, "ticks")
        self._layout = part(form, QVBoxLayout, "ticksLayout")
        self.favorites_box = self._box("Favorites", show_favorites, inner)
        self.standard_boxes: dict[str, QCheckBox] = {view: self._box(label, view not in hidden, inner, indent=True) for view, label in standard}
        self.pin_boxes: dict[str, QCheckBox] = {pin_id: self._box(label, True, inner, indent=True) for pin_id, label in pins}
        self.tags_box: QCheckBox | None = self._box("Tags", show_tags, inner) if has_tags else None
        self.local_box = self._box("Local Folders", show_local_folders, inner)
        self.local_folder_boxes: dict[str, QCheckBox] = {
            kind: self._box(path, kind not in self._hidden_local, inner, indent=True) for kind, path in local_folders or []
        }
        self.account_boxes: dict[str, QCheckBox] = {account: self._box(label, account not in hidden_trees, inner) for account, label in accounts}
        self._chosen: dict[QCheckBox, bool] = {box: box.isChecked() for box in self._boxes()}  # the person's own ticks
        self._applying = False
        self._forces = StartupForces()
        for box in self._boxes():
            box.toggled.connect(self._toggle_of(box))
        self.set_startup(startup_view)

    def _box(self, text: str, checked: bool, parent: QWidget, indent: bool = False) -> QCheckBox:
        box = QCheckBox(text, parent)
        box.setChecked(checked)
        if indent:
            row = QHBoxLayout()
            row.addSpacing(wpx(24))
            row.addWidget(box)
            row.addStretch(1)
            self._layout.addLayout(row)
        else:
            font = box.font()
            font.setBold(True)
            box.setFont(font)
            self._layout.addWidget(box)
        return box

    def _boxes(self) -> list[QCheckBox]:
        tops = [self.favorites_box, *([self.tags_box] if self.tags_box is not None else []), self.local_box, *self.account_boxes.values()]
        return [*tops, *self.standard_boxes.values(), *self.pin_boxes.values(), *self.local_folder_boxes.values()]

    def _toggle_of(self, box: QCheckBox) -> Callable[[bool], None]:
        def toggled(checked: bool) -> None:
            self._toggled(box, checked)

        return toggled

    def _toggled(self, box: QCheckBox, checked: bool) -> None:
        if not self._applying:
            self._chosen[box] = checked
            self._refresh()

    def set_startup(self, startup_view: str) -> None:
        """Show on startup changed (or the page opened): grey and tick what it keeps visible."""
        self._forces = startup_forces(StartupView.parse(startup_view))
        self._refresh()

    def _forced(self) -> set[QCheckBox]:
        forces, forced = self._forces, set[QCheckBox]()
        if forces.favorites:
            forced.add(self.favorites_box)
        forced.update(self.standard_boxes[view] for view in forces.views if view in self.standard_boxes)
        if forces.tags and self.tags_box is not None:
            forced.add(self.tags_box)
        for account in forces.trees:
            forced.add(self.local_box if account == self._local_id else self.account_boxes.get(account, self.local_box))
        for account, folder in forces.folders:  # a folder of Local Folders: its standard folder (or the one it is inside)
            if account == self._local_id:
                forced.update(box for kind, box in self.local_folder_boxes.items() if folder == self._local_paths[kind] or folder.startswith(self._local_paths[kind] + "/"))
        return forced

    def _refresh(self) -> None:
        """Every tick as the person set it, except what the startup choice keeps visible (ticked, greyed); the entries of
        Favorites are greyed while Favorites is off, those of Local Folders while Local Folders is off; the last thing that still shows something is greyed too."""
        self._applying = True
        try:
            forced = self._forced()
            for box in self._boxes():
                box.setChecked(True if box in forced else self._chosen[box])
                box.setEnabled(box not in forced)
            entries = [*self.standard_boxes.values(), *self.pin_boxes.values()]
            for box in entries:
                box.setEnabled(box.isEnabled() and self.favorites_box.isChecked())
            for box in self.local_folder_boxes.values():
                box.setEnabled(box.isEnabled() and self.local_box.isChecked())
            shown_entries = [box for box in entries if box.isChecked()]
            favorites_show = self.favorites_box.isChecked() and bool(shown_entries)
            tops = [box for box in (self.tags_box, self.local_box, *self.account_boxes.values()) if box is not None and box.isChecked()]
            if len(tops) + (1 if favorites_show else 0) == 1:  # the last thing shown: it cannot be switched off
                last = tops[0] if tops else self.favorites_box
                last.setEnabled(False)
                if last is self.favorites_box and len(shown_entries) == 1:
                    shown_entries[0].setEnabled(False)
        finally:
            self._applying = False

    # --- after OK -----------------------------------------------------------------------------

    def values(self) -> dict[str, object]:
        """The switches kept in the settings: as the person ticked them (what the startup choice forces is not saved)."""
        tags = self._chosen[self.tags_box] if self.tags_box is not None else self._show[1]
        return {"show_favorites": self._chosen[self.favorites_box], "show_tags": tags, "show_local_folders": self._chosen[self.local_box]}

    def commands(self) -> list[AnyStep[SidebarInfo]]:
        """The changes that make the folder list as the person ticked it: only the ticks that changed."""
        steps: list[AnyStep[SidebarInfo]] = []
        for view, box in self.standard_boxes.items():
            if self._chosen[box] == (view in self._hidden):
                steps.append(Step(FAVORITE_SHOW, VisibleParameters(view, self._chosen[box])))
        steps.extend(Step(FAVORITE_UNPIN, PinnedParameters(pin_id)) for pin_id, box in self.pin_boxes.items() if not self._chosen[box])
        for account, box in self.account_boxes.items():
            if self._chosen[box] == (account in self._hidden_trees):
                steps.append(Step(SIDEBAR_SHOW_TREE, TreeParameters(account, self._chosen[box])))
        for kind, box in self.local_folder_boxes.items():
            if self._chosen[box] == (kind in self._hidden_local):
                steps.append(Step(SIDEBAR_SHOW_LOCAL_FOLDER, LocalFolderParameters(kind, self._chosen[box])))
        return steps
