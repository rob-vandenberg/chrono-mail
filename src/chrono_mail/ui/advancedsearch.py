from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QLabel, QLineEdit, QSpinBox, QWidget

from chrono_mail.ui import forms
from chrono_mail.ui.forms import part

# --- Version ------------------------------------------------------------
__version__ = "advancedsearch.py 4.2.406"

# --- Version history ----------------------------------------------------
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


@dataclass(frozen=True, slots=True)
class AdvancedSearch:
    """What the dialog asks for: words in the search box's own language, plus the filters the box cannot express."""

    query: str = ""
    scope: SearchScope = SearchScope.FOLDER
    unread: bool = False
    flagged: bool = False
    attachments: bool = False
    date_from: str | None = None
    date_to: str | None = None
    minimum_size: int | None = None
    maximum_size: int | None = None

    @property
    def extra_filters(self) -> bool:
        return any(value is not None for value in (self.date_from, self.date_to, self.minimum_size, self.maximum_size))


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


class AdvancedSearchDialog(QDialog):
    """Search with several conditions at once; the words go into the search box, so that they can be edited there."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = forms.fill(self, "advanced_search")  # D-276
        self.all_words: QLineEdit = part(form, QLineEdit, "allWords")
        self.phrase: QLineEdit = part(form, QLineEdit, "phrase")
        self.any_words: QLineEdit = part(form, QLineEdit, "anyWords")
        self.none_words: QLineEdit = part(form, QLineEdit, "noneWords")
        self.from_words: QLineEdit = part(form, QLineEdit, "fromWords")
        self.to_words: QLineEdit = part(form, QLineEdit, "toWords")
        self.subject: QLineEdit = part(form, QLineEdit, "subject")
        self.body: QLineEdit = part(form, QLineEdit, "body")
        self.use_from_date: QCheckBox = part(form, QCheckBox, "useFromDate")
        self.from_date: QDateEdit = part(form, QDateEdit, "fromDate")
        self.from_date.setDate(QDate.currentDate().addMonths(-1))
        self.use_to_date: QCheckBox = part(form, QCheckBox, "useToDate")
        self.to_date: QDateEdit = part(form, QDateEdit, "toDate")
        self.to_date.setDate(QDate.currentDate())
        for date_edit, check in ((self.from_date, self.use_from_date), (self.to_date, self.use_to_date)):
            date_edit.setCalendarPopup(True)
            date_edit.setDisplayFormat("yyyy-MM-dd")
            date_edit.setEnabled(False)
            check.toggled.connect(date_edit.setEnabled)
        self.minimum_size: QSpinBox = part(form, QSpinBox, "minimumSize")
        self.maximum_size: QSpinBox = part(form, QSpinBox, "maximumSize")
        for spin in (self.minimum_size, self.maximum_size):
            spin.setRange(0, 10_000_000)
        self.unread: QCheckBox = part(form, QCheckBox, "unread")
        self.flagged: QCheckBox = part(form, QCheckBox, "flagged")
        self.attachments: QCheckBox = part(form, QCheckBox, "attachments")
        self.scope: QComboBox = part(form, QComboBox, "scope")
        for label, scope in SCOPE_CHOICES:
            self.scope.addItem(label, scope)
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
        return AdvancedSearch(
            query=self._query(),
            scope=SearchScope(self.scope.currentData()),
            unread=self.unread.isChecked(),
            flagged=self.flagged.isChecked(),
            attachments=self.attachments.isChecked(),
            date_from=self.from_date.date().toString("yyyy-MM-dd") if self.use_from_date.isChecked() else None,
            date_to=self.to_date.date().toString("yyyy-MM-dd") if self.use_to_date.isChecked() else None,
            minimum_size=self.minimum_size.value() * KIBIBYTE if self.minimum_size.value() else None,
            maximum_size=self.maximum_size.value() * KIBIBYTE if self.maximum_size.value() else None,
        )
