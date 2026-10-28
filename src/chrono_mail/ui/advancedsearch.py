from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QWidget,
)

from chrono_mail.ui import forms
from chrono_mail.ui.forms import part

# --- Version ------------------------------------------------------------
__version__ = "advancedsearch.py 4.2.408"

# --- Version history ----------------------------------------------------
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


class SearchScope(StrEnum):
    """Where a search of a folder looks."""

    FOLDER = "folder"  # the folder only
    SUBFOLDERS = "subfolders"  # the folder and every folder inside it, at any depth
    ALL = "all"  # every folder of the account


# The choices in the search field's menu and in Advanced search, in this order (D-287)
SCOPE_CHOICES: tuple[tuple[str, SearchScope], ...] = (
    ("This folder only", SearchScope.FOLDER), ("This folder + subfolders", SearchScope.SUBFOLDERS), ("All folders", SearchScope.ALL),
)
DEFAULT_SCOPE: SearchScope = SearchScope.ALL  # where a search looks at every start, until the person chooses another (D-287)


@dataclass(frozen=True, slots=True)
class AdvancedSearch:
    """What the dialog asks for: words in the search box's own language, plus the filters the box cannot express."""

    query: str = ""
    scope: SearchScope = DEFAULT_SCOPE
    unread: bool = False
    flagged: bool = False
    attachments: bool = False
    date_from: str | None = None
    date_to: str | None = None  # Received: date_from and date_to
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


def _words(text: str) -> list[str]:
    """The words of a field. Quotes are dropped: they would change what is searched for."""
    return [word for word in text.replace('"', " ").split() if word not in ("OR", "NOT", "|")]


def build_query(
    all_words: str = "", phrase: str = "", any_words: str = "", none_words: str = "",
    sender: str = "", recipient: str = "", subject: str = "", body: str = "",
) -> str:
    """The search words for the dialog's fields. 'Any of' is spread over the other terms: x y (a or b) is (x y a) OR (x y b)."""
    base = _words(all_words)
    if _words(phrase):
        base.append('"' + " ".join(_words(phrase)) + '"')
    for prefix, text in (("from", sender), ("to", recipient), ("subject", subject), ("body", body)):
        base.extend(f"{prefix}:{word}" for word in _words(text))
    choices = _words(any_words)
    query = " OR ".join(" ".join([*base, choice]) for choice in choices) if choices else " ".join(base)
    exclusions = " ".join(f"-{word}" for word in _words(none_words))
    return f"{query} {exclusions}".strip()


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


class _Days:
    """A row of days: [ ] from [day] [ ] to [day]; a day counts only while its tick is on and the row is not greyed."""

    def __init__(self, form: QWidget, prefix: str, first: QDate, last: QDate) -> None:
        self.use_from: QCheckBox = part(form, QCheckBox, f"{prefix}UseFrom")
        self.day_from: QDateEdit = part(form, QDateEdit, f"{prefix}From")
        self.use_to: QCheckBox = part(form, QCheckBox, f"{prefix}UseTo")
        self.day_to: QDateEdit = part(form, QDateEdit, f"{prefix}To")
        for day, check, start in ((self.day_from, self.use_from, first), (self.day_to, self.use_to, last)):
            day.setDate(start)
            day.setCalendarPopup(True)
            day.setDisplayFormat("yyyy-MM-dd")
            day.setEnabled(False)
            check.toggled.connect(day.setEnabled)

    def set_usable(self, usable: bool, label: QLabel) -> None:
        """Greyed where the search cannot find such mail; the ticks are kept for when it can again."""
        label.setEnabled(usable)
        for check, day in ((self.use_from, self.day_from), (self.use_to, self.day_to)):
            check.setEnabled(usable)
            day.setEnabled(usable and check.isChecked())

    def chosen(self) -> tuple[str | None, str | None]:
        """The first and the last day asked (YYYY-MM-DD), each None when not asked."""
        usable = self.use_from.isEnabled()

        def day_of(check: QCheckBox, day: QDateEdit) -> str | None:
            return day.date().toString("yyyy-MM-dd") if usable and check.isChecked() else None

        return day_of(self.use_from, self.day_from), day_of(self.use_to, self.day_to)


SNOOZED_ROLES: tuple[str, ...] = ("snoozed",)  # where Snoozed until can find mail
SEND_AT_ROLES: tuple[str, ...] = ("scheduled", "outbox")  # where Send at can find mail


class AdvancedSearchDialog(QDialog):
    """Search with several conditions at once; the words go into the search box, so that they can be edited there. Where to search
    and the most used fields first; the rest below More... (D-288)."""

    def __init__(
        self, parent: QWidget | None = None, scope: SearchScope = DEFAULT_SCOPE, tags: Sequence[str] = (), folder_role: str = "",
        combined: str | None = None, marks: tuple[bool, bool, bool, str | None] = (False, False, False, None),
    ) -> None:
        """marks: the filters on now (Unread, Flagged, With attachments, the tag): the dialog opens with them, so Search keeps them."""
        super().__init__(parent)
        self._folder_role: str = folder_role  # the folder shown (or the Favorites entry, combined): where Snoozed until and Send at can find mail
        self._combined: str | None = combined
        form = forms.fill(self, "advanced_search", {"more": MoreBar})  # D-276
        self._form: QWidget = form
        self.scope: QComboBox = part(form, QComboBox, "scope")
        for label, choice in SCOPE_CHOICES:
            self.scope.addItem(label, choice)
        self.scope.setCurrentIndex(self.scope.findData(scope))  # where the search field looks now
        self.all_words: QLineEdit = part(form, QLineEdit, "allWords")
        self.from_words: QLineEdit = part(form, QLineEdit, "fromWords")
        self.to_words: QLineEdit = part(form, QLineEdit, "toWords")
        self.subject: QLineEdit = part(form, QLineEdit, "subject")
        self.body: QLineEdit = part(form, QLineEdit, "body")
        today = QDate.currentDate()
        self.received: _Days = _Days(form, "received", today.addMonths(-1), today)
        self.more: MoreBar = part(form, MoreBar, "more")
        self.more_fields: QWidget = part(form, QWidget, "moreFields")
        self.more_fields.hide()
        self.more.toggled.connect(self._show_more)
        self.phrase: QLineEdit = part(form, QLineEdit, "phrase")
        self.any_words: QLineEdit = part(form, QLineEdit, "anyWords")
        self.none_words: QLineEdit = part(form, QLineEdit, "noneWords")
        self.sent: _Days = _Days(form, "sent", today.addMonths(-1), today)
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
        unread, flagged, attachments, tag = marks
        self.unread.setChecked(unread)
        self.flagged.setChecked(flagged)
        self.attachments.setChecked(attachments)
        if tag is not None:  # tags are told apart without regard to case, as the list does
            listed = next((number for number in range(1, self.tag.count()) if str(self.tag.itemData(number)).casefold() == tag.casefold()), -1)
            if listed < 0:
                self.tag.addItem(tag, tag)  # a tag chosen in the Filter menu that the list of tags no longer offers
                listed = self.tag.count() - 1
            self.tag.setCurrentIndex(listed)
        self.snoozed: _Days = _Days(form, "snoozed", today, today.addMonths(1))
        self.send_at: _Days = _Days(form, "sendAt", today, today.addMonths(1))
        self._snoozed_label: QLabel = part(form, QLabel, "snoozedLabel")
        self._send_at_label: QLabel = part(form, QLabel, "sendAtLabel")
        self.scope.currentIndexChanged.connect(self._grey_timed)
        self._grey_timed()
        self._line_up(part(form, QFormLayout, "main"), part(form, QFormLayout, "extra"))
        self.preview: QLabel = part(form, QLabel, "preview")
        self.preview.setTextFormat(Qt.TextFormat.PlainText)  # the person's own words: never markup
        self.preview.setForegroundRole(self.preview.foregroundRole().PlaceholderText)
        buttons = part(form, QDialogButtonBox, "buttons")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Search")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        forms.smallest(self, 520)  # its texts are never cut off, also at the largest size
        for edit in (self.all_words, self.phrase, self.any_words, self.none_words, self.from_words, self.to_words, self.subject, self.body):
            edit.textChanged.connect(self._update_preview)
        self.all_words.setFocus()

    @staticmethod
    def _line_up(*layouts: QFormLayout) -> None:
        """The labels of the main fields and of those below More... are one column: as wide as the widest of them."""
        labels = [
            item.widget() for layout in layouts for row in range(layout.rowCount())
            if (item := layout.itemAt(row, QFormLayout.ItemRole.LabelRole)) is not None and item.widget() is not None
        ]
        widest = max((label.sizeHint().width() for label in labels if label is not None), default=0)
        for label in labels:
            if label is not None:
                label.setMinimumWidth(widest)

    def _show_more(self, opened: bool) -> None:
        self.more_fields.setVisible(opened)
        for layout in (self._form.layout(), self.layout()):  # the form's, then the window's: each knows its new size now, not later,
            if layout is not None:  # else closing left the window at its opened height
                layout.activate()
        self.resize(self.width(), self.sizeHint().height())  # its height follows what opened or closed; a width the person chose stays

    def _grey_timed(self) -> None:
        """Snoozed until and Send at are greyed unless the search looks where such mail can be: Snoozed, Scheduled or the Outbox
        shown, or all folders (a Favorites entry looks only in its own kind of folder)."""
        everywhere = self._combined is None and SearchScope(self.scope.currentData()) is SearchScope.ALL
        place = self._combined if self._combined is not None else self._folder_role
        self.snoozed.set_usable(everywhere or place in SNOOZED_ROLES, self._snoozed_label)
        self.send_at.set_usable(everywhere or place in SEND_AT_ROLES, self._send_at_label)

    def _query(self) -> str:
        return build_query(
            self.all_words.text(), self.phrase.text(), self.any_words.text(), self.none_words.text(),
            self.from_words.text(), self.to_words.text(), self.subject.text(), self.body.text(),
        )

    def _update_preview(self) -> None:
        query = self._query()
        self.preview.setText(f"Search words: {query}" if query else "")

    def search(self) -> AdvancedSearch:
        """What was asked for (valid after the dialog was accepted)."""
        received, sent, snoozed, send_at = self.received.chosen(), self.sent.chosen(), self.snoozed.chosen(), self.send_at.chosen()
        tag = self.tag.currentData()
        return AdvancedSearch(
            query=self._query(),
            scope=SearchScope(self.scope.currentData()),
            unread=self.unread.isChecked(),
            flagged=self.flagged.isChecked(),
            attachments=self.attachments.isChecked(),
            date_from=received[0],
            date_to=received[1],
            minimum_size=self.minimum_size.value() * KIBIBYTE if self.minimum_size.value() else None,
            maximum_size=self.maximum_size.value() * KIBIBYTE if self.maximum_size.value() else None,
            sent_from=sent[0], sent_to=sent[1], snoozed_from=snoozed[0], snoozed_to=snoozed[1], send_at_from=send_at[0], send_at_to=send_at[1],
            tag=tag if isinstance(tag, str) else None,
        )
