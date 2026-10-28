from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import override

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton, QSpinBox,
    QWidget,
)

from chrono_mail.ui import forms
from chrono_mail.ui.forms import part
from chrono_mail.ui.savedsearches import SavedSearches
from chrono_mail.ui.searchform import (
    DEFAULT_SCOPE, FUTURE_CHOICES, MATCHES, PAST_CHOICES, SCOPE_CHOICES, SearchForm, SearchScope, build_query, days, too_big,
)
from chrono_mail.ui.searchlines import DateRow, SearchLines

__all__ = ["DEFAULT_SCOPE", "SCOPE_CHOICES", "AdvancedSearch", "AdvancedSearchDialog", "MoreBar", "SearchScope"]

# --- Version ------------------------------------------------------------
__version__ = "advancedsearch.py 4.2.410"

# --- Version history ----------------------------------------------------
# v4.2.410: a search builder (D-289): Saved searches (Save, Save as, Rename, Delete), Where to search, Find messages that match all or
#           any of the lines, the search lines, Received; below More... Sent, Size, Only, Tag, Snoozed until and Send at; the dates a
#           span counted from today or fixed days; Reset; Search and Close; it opens as it was last left (the lines and choices in
#           searchlines.py, the value and the search words in searchform.py, the file in savedsearches.py)
# v4.2.408: Where to search first, then Words, From, To, Subject, Body and Received; the rest below More..., a bar that opens and closes
#           wherever it is clicked: the phrase, any and none of the words, Sent, Size, Unread, Flagged, With attachments, Tag, Snoozed
#           until and Send at (greyed unless the search looks where such mail can be); it opens with the filters that are on (D-288)
# v4.2.407: All folders is where a search looks unless another is chosen (DEFAULT_SCOPE); the dialog opens on the choice it is given
#           (the search field's, D-287)
# v4.2.406: where a search looks has three choices, the same as the search field's: This folder only, This folder + subfolders,
#           All folders (SearchScope, SCOPE_CHOICES, D-287)
# v4.0.377: built from its Qt Designer form (ui/forms/advanced_search.ui, D-276); the preview of the search words is plain text; its smallest
#           width follows the size factor
# v0.0.19: Initial advanced search dialog

KIBIBYTE: int = 1024
NO_SAVED_SEARCH: str = "No saved search chosen"
TOO_BIG: str = "Fewer words in the \"Any of these words\" lines, please: together the lines make more combinations than a search can take."


@dataclass(frozen=True, slots=True)
class AdvancedSearch:
    """What the dialog asks for: words in the search box's own language, plus the filters the box cannot express."""

    query: str = ""
    scope: SearchScope = DEFAULT_SCOPE
    unread: bool = False
    flagged: bool = False
    attachments: bool = False
    date_from: str | None = None  # Received: date_from and date_to
    date_to: str | None = None
    minimum_size: int | None = None
    maximum_size: int | None = None
    sent_from: str | None = None  # the message's own Date (D-288)
    sent_to: str | None = None
    snoozed_from: str | None = None  # when a snoozed message comes back
    snoozed_to: str | None = None
    send_at_from: str | None = None  # when a message waiting in Scheduled or the Outbox is sent
    send_at_to: str | None = None
    tag: str | None = None  # the same as Filter > Tagged with

    @property
    def extra_filters(self) -> bool:
        """Days or sizes: what the search box cannot hold (the chip over the list shows them)."""
        return any(value is not None for value in (
            self.date_from, self.date_to, self.minimum_size, self.maximum_size, self.sent_from, self.sent_to, self.snoozed_from, self.snoozed_to,
            self.send_at_from, self.send_at_to,
        ))


class MoreBar(QPushButton):
    """A bar the whole width of the window: "More..." at its start, ▾ at its end (▴ while open); a click anywhere on it opens or closes
    what lies below it (D-288)."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setAccessibleName("More...")
        self.text_label: QLabel = QLabel("More...", self)
        self.arrow: QLabel = QLabel("▾", self)
        layout = QHBoxLayout(self)
        layout.addWidget(self.text_label)
        layout.addStretch(1)
        layout.addWidget(self.arrow)
        for label in (self.text_label, self.arrow):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # the bar takes every click
        self.toggled.connect(self._turn_arrow)
        self.setMinimumHeight(layout.sizeHint().height())

    def _turn_arrow(self, opened: bool) -> None:
        self.arrow.setText("▴" if opened else "▾")


SNOOZED_ROLES: tuple[str, ...] = ("snoozed",)  # where Snoozed until can find mail
SEND_AT_ROLES: tuple[str, ...] = ("scheduled", "outbox")  # where Send at can find mail


def _row_maker(choices: Sequence[tuple[str, str]]) -> Callable[[QWidget], QWidget]:
    return lambda parent: DateRow(parent, choices)


class AdvancedSearchDialog(QDialog):
    """A search builder (D-289): saved searches; where to search; the search lines, all or any of which must match; Received; below
    More... the rest. The words go into the search box, so that they can be edited there. It opens as it was last left, on the search
    field's choice of where to search and with the filters that are on."""

    def __init__(
        self, parent: QWidget | None = None, scope: SearchScope = DEFAULT_SCOPE, tags: Sequence[str] = (), folder_role: str = "",
        combined: str | None = None, marks: tuple[bool, bool, bool, str | None] = (False, False, False, None),
        saved: SavedSearches | None = None, today: date | None = None,
    ) -> None:
        """marks: the filters on now (Unread, Flagged, With attachments, the tag); saved: the saved searches and the form last left
        (None: kept in memory only); today: the day the spans are counted from (None: today)."""
        super().__init__(parent)
        self._folder_role: str = folder_role  # the folder shown (or the Favorites entry, combined): where Snoozed until and Send at can find mail
        self._combined: str | None = combined
        self._saved: SavedSearches = saved if saved is not None else SavedSearches(None)
        self._today: date | None = today
        form = forms.fill(self, "advanced_search", {  # D-276
            "more": MoreBar, "lines": SearchLines, "received": _row_maker(PAST_CHOICES), "sent": _row_maker(PAST_CHOICES),
            "snoozed": _row_maker(FUTURE_CHOICES), "sendAt": _row_maker(FUTURE_CHOICES),
        })
        self._form: QWidget = form
        self.saved_choice: QComboBox = part(form, QComboBox, "saved")
        self.saved_choice.setPlaceholderText(NO_SAVED_SEARCH)
        self.scope: QComboBox = part(form, QComboBox, "scope")
        for label, choice in SCOPE_CHOICES:
            self.scope.addItem(label, choice)
        self.match: QComboBox = part(form, QComboBox, "match")
        for value, label in MATCHES:
            self.match.addItem(label, value)
        self.lines: SearchLines = part(form, SearchLines, "lines")
        self.received: DateRow = part(form, DateRow, "received")
        self.more: MoreBar = part(form, MoreBar, "more")
        self.more_fields: QWidget = part(form, QWidget, "moreFields")
        self.more_fields.hide()
        self.more.toggled.connect(self._show_more)
        self.sent: DateRow = part(form, DateRow, "sent")
        self.minimum_size: QSpinBox = part(form, QSpinBox, "minimumSize")
        self.maximum_size: QSpinBox = part(form, QSpinBox, "maximumSize")
        for spin in (self.minimum_size, self.maximum_size):
            spin.setRange(0, 10_000_000)
        self.unread: QCheckBox = part(form, QCheckBox, "unread")
        self.flagged: QCheckBox = part(form, QCheckBox, "flagged")
        self.attachments: QCheckBox = part(form, QCheckBox, "attachments")
        self.tag: QComboBox = part(form, QComboBox, "tag")
        self.tag.addItem("No tag chosen", None)
        for name in tags:
            self.tag.addItem(name, name)
        self.snoozed: DateRow = part(form, DateRow, "snoozed")
        self.send_at: DateRow = part(form, DateRow, "sendAt")
        self._snoozed_label: QLabel = part(form, QLabel, "snoozedLabel")
        self._send_at_label: QLabel = part(form, QLabel, "sendAtLabel")
        self.preview: QLabel = part(form, QLabel, "preview")
        self.preview.setTextFormat(Qt.TextFormat.PlainText)  # the person's own words: never markup
        self.preview.setForegroundRole(self.preview.foregroundRole().PlaceholderText)
        self.save_button: QPushButton = part(form, QPushButton, "save")
        self.save_as_button: QPushButton = part(form, QPushButton, "saveAs")
        self.rename_button: QPushButton = part(form, QPushButton, "rename")
        self.delete_button: QPushButton = part(form, QPushButton, "delete")
        self.reset_button: QPushButton = part(form, QPushButton, "reset")
        buttons = part(form, QDialogButtonBox, "buttons")
        self.search_button: QPushButton = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.search_button.setText("Search")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._line_up(part(form, QFormLayout, "main"), part(form, QFormLayout, "dates"), part(form, QFormLayout, "extra"))
        start = self._saved.last or SearchForm()
        unread, flagged, attachments, tag = marks
        self.set_form(replace(start, scope=scope, unread=unread, flagged=flagged, attachments=attachments, tag=tag))
        self._fill_saved(None)
        self.saved_choice.activated.connect(self._saved_chosen)
        self.save_button.clicked.connect(self.save)
        self.save_as_button.clicked.connect(self.save_as)
        self.rename_button.clicked.connect(self.rename)
        self.delete_button.clicked.connect(self.delete)
        self.reset_button.clicked.connect(self.reset)
        self.match.currentIndexChanged.connect(self._match_changed)
        self.lines.changed.connect(self._update_preview)
        self.lines.lines_changed.connect(self._order_tabs)
        self.scope.currentIndexChanged.connect(self._grey_timed)
        forms.smallest(self, 640)  # its texts are never cut off, also at the largest size
        self._order_tabs()
        self.lines.line(0).text.setFocus()

    # --- the form as one value ---------------------------------------------------------------------------------------------

    def current_form(self) -> SearchForm:
        tag = self.tag.currentData()
        return SearchForm(
            scope=SearchScope(self.scope.currentData()), match=str(self.match.currentData()), lines=self.lines.lines(),
            received=self.received.value(), sent=self.sent.value(), minimum_kb=self.minimum_size.value(), maximum_kb=self.maximum_size.value(),
            unread=self.unread.isChecked(), flagged=self.flagged.isChecked(), attachments=self.attachments.isChecked(),
            tag=tag if isinstance(tag, str) else None, snoozed=self.snoozed.value(), send_at=self.send_at.value(),
        )

    def set_form(self, form: SearchForm) -> None:
        self.scope.setCurrentIndex(max(0, self.scope.findData(form.scope)))
        self.match.setCurrentIndex(max(0, self.match.findData(form.match)))
        self.lines.set_lines(form.lines)
        self.lines.set_match(form.match)
        for row, value in ((self.received, form.received), (self.sent, form.sent), (self.snoozed, form.snoozed), (self.send_at, form.send_at)):
            row.set_value(value)
        self.minimum_size.setValue(form.minimum_kb)
        self.maximum_size.setValue(form.maximum_kb)
        self.unread.setChecked(form.unread)
        self.flagged.setChecked(form.flagged)
        self.attachments.setChecked(form.attachments)
        self._choose_tag(form.tag)
        self._grey_timed()
        self._update_preview()

    def _choose_tag(self, tag: str | None) -> None:
        """Tags are told apart without regard to case, as the list does; one no longer offered is added."""
        if tag is None:
            self.tag.setCurrentIndex(0)
            return
        listed = next((number for number in range(1, self.tag.count()) if str(self.tag.itemData(number)).casefold() == tag.casefold()), -1)
        if listed < 0:
            self.tag.addItem(tag, tag)
            listed = self.tag.count() - 1
        self.tag.setCurrentIndex(listed)

    def search(self) -> AdvancedSearch:
        """What was asked for (valid after the dialog was accepted); a span is counted from today, now."""
        form = self.current_form()
        today = self._today or date.today()
        received, sent = days(form.received, today), days(form.sent, today)
        snoozed = days(form.snoozed, today) if self.snoozed.isEnabled() else (None, None)
        send_at = days(form.send_at, today) if self.send_at.isEnabled() else (None, None)
        return AdvancedSearch(
            query=build_query(form.lines, form.match), scope=form.scope, unread=form.unread, flagged=form.flagged, attachments=form.attachments,
            date_from=received[0], date_to=received[1],
            minimum_size=form.minimum_kb * KIBIBYTE if form.minimum_kb else None, maximum_size=form.maximum_kb * KIBIBYTE if form.maximum_kb else None,
            sent_from=sent[0], sent_to=sent[1], snoozed_from=snoozed[0], snoozed_to=snoozed[1], send_at_from=send_at[0], send_at_to=send_at[1],
            tag=form.tag,
        )

    @override
    def done(self, result: int) -> None:
        """Search and Close alike: the form is kept as it was left, for the next time."""
        self._keep(lambda: self._saved.keep_last(self.current_form()))
        super().done(result)

    # --- saved searches ----------------------------------------------------------------------------------------------------

    def chosen_name(self) -> str | None:
        return self.saved_choice.currentText() if self.saved_choice.currentIndex() >= 0 else None

    def _fill_saved(self, chosen: str | None) -> None:
        self.saved_choice.clear()
        self.saved_choice.addItems(self._saved.names())
        self.saved_choice.setCurrentIndex(self.saved_choice.findText(chosen) if chosen is not None else -1)
        self._grey_buttons()

    def _grey_buttons(self) -> None:
        chosen = self.chosen_name() is not None
        for button in (self.save_button, self.rename_button, self.delete_button):
            button.setEnabled(chosen)

    def _saved_chosen(self, _index: int) -> None:
        name = self.chosen_name()
        found = self._saved.form(name) if name is not None else None
        if found is not None:
            self.set_form(found)
        self._grey_buttons()

    def save(self) -> None:
        """Save over the saved search chosen."""
        name = self.chosen_name()
        if name is not None:
            self._keep(lambda: self._saved.put(name, self.current_form()))

    def save_as(self) -> None:
        name = self._ask_name("Save search as", "Name:", "")
        if name is None or not self._read_again() or not self._may_replace(name, None):  # the names asked about are the file's
            return
        if self._keep(lambda: self._saved.put(name, self.current_form())):
            self._fill_saved(self._saved.named(name))

    def rename(self) -> None:
        old = self.chosen_name()
        if old is None:
            return
        new = self._ask_name("Rename saved search", "New name:", old)
        if new is None or new == old or not self._read_again() or not self._may_replace(new, old):
            return
        if self._keep(lambda: self._saved.rename(old, new)):
            self._fill_saved(self._saved.named(new))

    def delete(self) -> None:
        name = self.chosen_name()
        if name is None:
            return
        asked = QMessageBox.question(self, "Delete saved search", f"Delete the saved search “{name}”?")
        if asked == QMessageBox.StandardButton.Yes and self._keep(lambda: self._saved.delete(name)):
            self._fill_saved(None)

    def reset(self) -> None:
        """One empty line and the first choices everywhere; the saved searches stay."""
        self.set_form(SearchForm(scope=SearchScope(self.scope.currentData())))
        self.saved_choice.setCurrentIndex(-1)
        self._grey_buttons()

    def _read_again(self) -> bool:
        """A file that could not be opened before is read now; the list shows what it holds, whatever is answered next."""
        if not self._keep(self._saved.ready):
            return False
        self._fill_saved(self.chosen_name())
        return True

    def _ask_name(self, title: str, label: str, text: str) -> str | None:
        name, given = QInputDialog.getText(self, title, label, text=text)
        name = " ".join(name.split())
        return name if given and name else None

    def _may_replace(self, name: str, own: str | None) -> bool:
        """Whether a name may be used: free, the search's own, or the person agrees to replace the one that has it."""
        known = self._saved.named(name)
        if known is None or known == own:
            return True
        asked = QMessageBox.question(self, "Saved searches", f"A saved search named “{known}” exists already. Replace it?")
        return asked == QMessageBox.StandardButton.Yes

    def _keep(self, write: Callable[[], None]) -> bool:
        """Write the saved searches; a failure is said, never a crash."""
        try:
            write()
        except OSError as error:
            QMessageBox.warning(self, "Saved searches", f"The saved searches could not be kept: {error.strerror or error}")
            return False
        return True

    # --- the look ----------------------------------------------------------------------------------------------------------

    @staticmethod
    def _line_up(*layouts: QFormLayout) -> None:
        """The labels of every part of the form are one column: as wide as the widest of them."""
        labels = [
            item.widget() for layout in layouts for row in range(layout.rowCount())
            if (item := layout.itemAt(row, QFormLayout.ItemRole.LabelRole)) is not None and item.widget() is not None
        ]
        widest = max((label.sizeHint().width() for label in labels if label is not None), default=0)
        for label in labels:
            if label is not None:
                label.setMinimumWidth(widest)

    def _order_tabs(self) -> None:
        """Tab goes through the parts in the order of the screen, also after a line was added in the middle."""
        chain: list[QWidget] = [self.saved_choice, self.scope, self.match, *self.lines.focus_chain(), self.received.choice]
        for before, after in zip(chain, chain[1:], strict=False):
            QWidget.setTabOrder(before, after)

    def _show_more(self, opened: bool) -> None:
        self.more_fields.setVisible(opened)
        for layout in (self._form.layout(), self.layout()):  # the form's, then the window's: each knows its new size now, not later,
            if layout is not None:  # else closing left the window at its opened height
                layout.activate()
        self.resize(self.width(), self.sizeHint().height())  # its height follows what opened or closed; a width the person chose stays

    def _match_changed(self) -> None:
        self.lines.set_match(str(self.match.currentData()))
        self._update_preview()

    def _grey_timed(self) -> None:
        """Snoozed until and Send at are greyed unless the search looks where such mail can be: Snoozed, Scheduled or the Outbox
        shown, or all folders (a Favorites entry looks only in its own kind of folder)."""
        everywhere = self._combined is None and SearchScope(self.scope.currentData()) is SearchScope.ALL
        place = self._combined if self._combined is not None else self._folder_role
        for row, label, roles in ((self.snoozed, self._snoozed_label, SNOOZED_ROLES), (self.send_at, self._send_at_label, SEND_AT_ROLES)):
            usable = everywhere or place in roles
            row.setEnabled(usable)
            label.setEnabled(usable)

    def _update_preview(self) -> None:
        """The search words the lines make; when they make more than a search can take, it says so and Search is greyed."""
        lines, match = self.lines.lines(), str(self.match.currentData())
        big = too_big(lines, match)
        self.search_button.setEnabled(not big)
        query = "" if big else build_query(lines, match)
        self.preview.setText(TOO_BIG if big else (f"Search words: {query}" if query else ""))
