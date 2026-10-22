from dataclasses import dataclass

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# --- Version ------------------------------------------------------------
__version__ = "advancedsearch.py 0.0.19"

# --- Version history ----------------------------------------------------
# v0.0.19: Initial advanced search dialog

KIBIBYTE: int = 1024


@dataclass(frozen=True, slots=True)
class AdvancedSearch:
    """What the dialog asks for: words in the search box's own language, plus the filters the box cannot express."""

    query: str = ""
    all_folders: bool = False
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
        self.setWindowTitle("Advanced search")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.all_words = QLineEdit(self)
        self.phrase = QLineEdit(self)
        self.any_words = QLineEdit(self)
        self.none_words = QLineEdit(self)
        self.from_words = QLineEdit(self)
        self.to_words = QLineEdit(self)
        self.subject = QLineEdit(self)
        self.body = QLineEdit(self)
        form.addRow("All of these words", self.all_words)
        form.addRow("This exact phrase", self.phrase)
        form.addRow("Any of these words", self.any_words)
        form.addRow("None of these words", self.none_words)
        form.addRow("From", self.from_words)
        form.addRow("To", self.to_words)
        form.addRow("Subject contains", self.subject)
        form.addRow("Message text contains", self.body)
        layout.addLayout(form)

        self.use_from_date = QCheckBox("Sent on or after", self)
        self.from_date = QDateEdit(QDate.currentDate().addMonths(-1), self)
        self.use_to_date = QCheckBox("Sent on or before", self)
        self.to_date = QDateEdit(QDate.currentDate(), self)
        for date_edit, check in ((self.from_date, self.use_from_date), (self.to_date, self.use_to_date)):
            date_edit.setCalendarPopup(True)
            date_edit.setDisplayFormat("yyyy-MM-dd")
            date_edit.setEnabled(False)
            check.toggled.connect(date_edit.setEnabled)
        dates = QHBoxLayout()
        for widget in (self.use_from_date, self.from_date, self.use_to_date, self.to_date):
            dates.addWidget(widget)
        layout.addLayout(dates)

        self.minimum_size = QSpinBox(self)
        self.maximum_size = QSpinBox(self)
        for spin in (self.minimum_size, self.maximum_size):
            spin.setRange(0, 10_000_000)
            spin.setSuffix(" KB")
            spin.setSpecialValueText("no limit")
        sizes = QHBoxLayout()
        for widget in (QLabel("Size at least", self), self.minimum_size, QLabel("at most", self), self.maximum_size):
            sizes.addWidget(widget)
        layout.addLayout(sizes)

        checks = QHBoxLayout()
        self.unread = QCheckBox("Unread", self)
        self.flagged = QCheckBox("Flagged", self)
        self.attachments = QCheckBox("With attachments", self)
        for widget in (self.unread, self.flagged, self.attachments):
            checks.addWidget(widget)
        checks.addStretch(1)
        layout.addLayout(checks)

        self.scope = QComboBox(self)
        self.scope.addItem("Search in this folder", False)
        self.scope.addItem("Search in all folders", True)
        layout.addWidget(self.scope)
        self.preview = QLabel("", self)
        self.preview.setWordWrap(True)
        self.preview.setForegroundRole(self.preview.foregroundRole().PlaceholderText)
        layout.addWidget(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Search")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
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
            all_folders=bool(self.scope.currentData()),
            unread=self.unread.isChecked(),
            flagged=self.flagged.isChecked(),
            attachments=self.attachments.isChecked(),
            date_from=self.from_date.date().toString("yyyy-MM-dd") if self.use_from_date.isChecked() else None,
            date_to=self.to_date.date().toString("yyyy-MM-dd") if self.use_to_date.isChecked() else None,
            minimum_size=self.minimum_size.value() * KIBIBYTE if self.minimum_size.value() else None,
            maximum_size=self.maximum_size.value() * KIBIBYTE if self.maximum_size.value() else None,
        )
