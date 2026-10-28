from dataclasses import replace as changed

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QKeyEvent
from PySide6.QtWidgets import QMenu, QToolButton, QWidget

from chrono_mail.ui.advancedsearch import AdvancedSearch, AdvancedSearchDialog
from chrono_mail.ui.savedsearches import SavedSearches
from chrono_mail.ui.icons import section_icon, theme_icon
from chrono_mail.ui.mailstate import MailState
from chrono_mail.ui.messagelistpane import MessageListPane
from chrono_mail.ui.prefs import Remembered
from chrono_mail.ui.searchfield import SearchField
from chrono_mail.ui.searchhelp import HelpLine, SearchHelp, send_key
from chrono_mail.ui.searchhistory import SearchHistory, history_text, matching, read_history, with_search
from chrono_mail.ui.tagchips import TagColourBook
from chrono_mail.ui.tagging import offered_for_mail, tag_icon

# --- Version ------------------------------------------------------------
__version__ = "searchfilters.py 4.2.410"

# --- Version history ----------------------------------------------------
# v4.2.410: Advanced search is given the saved searches and the form last left (D-289)
# v4.2.408: Advanced search is given the tags and where the list looks (Snoozed until and Send at greyed elsewhere); its Tag is
#           Filter > Tagged with; it opens with the filters that are on (D-288)
# v4.2.407: Advanced search opens on where the search field looks (D-287)
# v4.2.406: where the search looks is one of three: This folder only, This folder + subfolders, All folders (D-287)
# v1.9.274: R-19 round 4: the tags are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v1.9.264: R-17: the filters and the search of the mail as parts of their own (out of ui/mainwindow.py): the Filter menu and its chip
#           over the list; the search field with its history and help, This folder or All folders, Advanced search; they change the
#           current view through the message list and only show it (R-14)


class ListFilters(QObject):
    """The Filter button over the message list and the chip of Advanced search's days and sizes."""

    def __init__(self, state: MailState, messages: MessageListPane, tag_book: TagColourBook, parent: QWidget) -> None:
        super().__init__(parent)
        self._state: MailState = state
        self._messages: MessageListPane = messages
        self._tag_book: TagColourBook = tag_book
        self.button: QToolButton = QToolButton(parent)
        self.button.setObjectName("filterButton")
        self.button.setText("Filter")
        self.button.setIcon(section_icon("view-filter"))  # blue while a filter is on
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.button.setAutoRaise(True)
        self.button.setCheckable(True)  # checked = at least one filter is on; set by show_state only
        self.menu: QMenu = QMenu(self.button)
        heading = self.menu.addAction("Filter by")
        heading.setEnabled(False)
        self.flagged: QAction = self._entry("Flagged", "flag")
        self.unread: QAction = self._entry("Unread", "mail-unread")
        self.attachments: QAction = self._entry("With attachments", "mail-attachment")
        self.tag_menu: QMenu = self.menu.addMenu("Tagged with")
        self.tag_menu.aboutToShow.connect(self.fill_tags)
        self.menu.addSeparator()
        self.clear: QAction = self.menu.addAction("Clear filter")
        self.clear.setEnabled(False)
        self.clear.triggered.connect(self.clear_filters)
        self.button.setMenu(self.menu)
        self.chip: QToolButton = QToolButton(parent)
        self.chip.setAutoRaise(True)
        self.chip.setText("Advanced filters ✕")
        self.chip.setToolTip("Click to remove the days and sizes of the advanced search")
        self.chip.hide()
        self.chip.clicked.connect(lambda: self._messages.change_view(changed(self._state.view, advanced=None)))
        for entry in (self.unread, self.flagged, self.attachments):
            entry.triggered.connect(self._clicked)  # the person's click only: the window setting the tick starts nothing

    def _entry(self, text: str, icon: str) -> QAction:
        action = self.menu.addAction(theme_icon(icon), text)
        action.setCheckable(True)
        return action

    def show_state(self) -> None:
        """The Filter button is blue and bold while at least one filter is on; in the menu the filters that are on are bold; the chip
        while Advanced search's days or sizes narrow the list."""
        view = self._state.view
        for entry, on in ((self.flagged, view.flagged), (self.unread, view.unread), (self.attachments, view.attachments)):
            entry.setChecked(on)  # only the person's click is heard (triggered): showing the value starts nothing
            font = entry.font()
            font.setBold(on)
            entry.setFont(font)
        on = view.filtered
        font = self.tag_menu.menuAction().font()
        font.setBold(view.tag is not None)
        self.tag_menu.menuAction().setFont(font)
        self.button.setChecked(on)
        self.clear.setEnabled(on)
        self.chip.setVisible(view.advanced is not None)

    def clear_filters(self) -> None:
        self._messages.change_view(changed(self._state.view, unread=False, flagged=False, attachments=False, tag=None))

    def _clicked(self) -> None:
        """The person ticked or unticked Flagged, Unread or With attachments in the Filter menu."""
        self._messages.change_view(changed(self._state.view, unread=self.unread.isChecked(), flagged=self.flagged.isChecked(), attachments=self.attachments.isChecked()))

    def fill_tags(self) -> None:
        self.tag_menu.clear()
        for tag in self._state.tags:
            if not offered_for_mail(tag):
                continue
            name = tag.name
            chosen = self._state.view.tag is not None and self._state.view.tag.casefold() == name.casefold()
            action = self.tag_menu.addAction(tag_icon(self._tag_book.colour(name), chosen), name)
            action.triggered.connect(lambda checked=False, picked=name, off=chosen: self.set_tag(None if off else picked))
        if not self.tag_menu.actions():
            self.tag_menu.addAction("No tags yet").setEnabled(False)

    def set_tag(self, name: str | None) -> None:
        self._messages.change_view(changed(self._state.view, tag=name))


class MailSearch(QObject):
    """The search field of the mail in the toolbar: its history, its help and where it looks; Advanced search."""

    def __init__(self, state: MailState, messages: MessageListPane, memory: Remembered, parent: QWidget, saved: SavedSearches | None = None) -> None:
        super().__init__(parent)
        self._saved: SavedSearches = saved if saved is not None else SavedSearches(None)  # Advanced search's saved searches and the form last left
        self._state: MailState = state
        self._messages: MessageListPane = messages
        self._memory: Remembered = memory
        self._parent: QWidget = parent
        self.pill: SearchField = SearchField(parent)
        self.box = self.pill.line_edit
        self.scope = self.pill.scope
        self._history: list[str] = read_history(memory.search_history)
        self.history: SearchHistory = SearchHistory(parent, self.box)  # it never takes the mouse or the keyboard from the field
        self.help: SearchHelp = SearchHelp(parent)
        self.pill.clicked.connect(self._show_history)
        self.pill.cleared.connect(self._clear)
        self.history.picked.connect(self._history_picked)
        self.history.removed.connect(self._history_removed)
        self.help.picked.connect(self._help_picked)
        self.help.typed.connect(self._help_typed)
        self.box.returnPressed.connect(self.run)
        self.box.textChanged.connect(self._text_changed)
        self.scope.changed.connect(self._scope_changed)

    def show_state(self) -> None:
        """Where it looks and the field's look show the current view."""
        view = self._state.view
        self.scope.show_scope(view.scope)
        self.pill.set_active(bool(view.query))

    def ended(self) -> None:
        """Another entry of the folder list was opened: the search ended with the new view; the field's answer to an empty text changes nothing."""
        self.box.clear()

    def _text_changed(self, text: str) -> None:
        if text == "?":
            self.history.hide()
            self.help.open_under(self.pill)
            return
        if self.history.isVisible():
            self.history.show_entries(matching(self._history, text), self.pill)
        if not text:  # the field emptied: the search ends (nothing changes when it had ended already)
            self._messages.change_view(changed(self._state.view, query="", advanced=None))

    def run(self) -> None:
        """Enter in the search box: the search is kept in the history and the field shows that a search is active."""
        self.history.hide()
        text = self.box.text().strip()
        if text:
            self._history = with_search(self._history, text)
            self._memory.search_history = history_text(self._history)
        self._messages.change_view(changed(self._state.view, query=text))

    def _clear(self) -> None:
        """The red ✕: the search ends and the whole folder is shown again."""
        self.box.clear()
        self.pill.set_active(False)
        self.box.setFocus()

    def _show_history(self) -> None:
        if self.box.text() != "?":
            self.history.show_entries(matching(self._history, self.box.text()), self.pill)

    def _history_picked(self, text: str) -> None:
        self.box.setText(text)
        self.box.setFocus()
        self.run()

    def _history_removed(self, text: str) -> None:
        self._history = [entry for entry in self._history if entry != text]
        self._memory.search_history = history_text(self._history)
        self.history.show_entries(matching(self._history, self.box.text()), self.pill)

    def _help_picked(self, line: HelpLine) -> None:
        """Puts what the picked help line stands for in the search box, in place of the "?" (typed, not run yet)."""
        self.box.setText(line.inserted)
        self.box.setCursorPosition(len(line.inserted) - line.cursor_back)
        self.box.setFocus()

    def _help_typed(self, event: QKeyEvent) -> None:
        send_key(self.box, event)

    def _scope_changed(self) -> None:
        """The person chose where the search looks (it matters only while a search is shown)."""
        self._messages.change_view(changed(self._state.view, scope=self.scope.current_data()))

    def advanced(self) -> None:
        """Ask for several conditions at once and show the result in the message list."""
        if self._state.view.account is None:
            return
        view = self._state.view
        tags = [tag.name for tag in self._state.tags if offered_for_mail(tag)]
        marks = (view.unread, view.flagged, view.attachments, view.tag)  # the filters on now: Search keeps them unless the person changes them
        dialog = AdvancedSearchDialog(self._parent, view.scope, tags, view.folder_role, view.combined, marks, self._saved)  # it opens on where the search field looks
        accepted = dialog.exec() == AdvancedSearchDialog.DialogCode.Accepted
        wanted: AdvancedSearch = dialog.search()
        dialog.deleteLater()  # one window per opening, gone when done (each opening made a new one that stayed)
        if not accepted:
            return
        self._messages.change_view(changed(
            self._state.view, query=wanted.query.strip(), scope=wanted.scope, advanced=wanted if wanted.extra_filters else None,
            unread=wanted.unread, flagged=wanted.flagged, attachments=wanted.attachments, tag=wanted.tag,
        ))
        self.box.setText(wanted.query)  # the words of the search that was run

    def focus(self) -> None:
        self.box.setFocus()
        self.box.selectAll()
