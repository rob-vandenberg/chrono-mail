from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QLabel, QRadioButton, QSpinBox, QWidget

from chrono_mail.pim.repeat import UNITS, WEEKDAYS, CustomRepeat, custom_of, rule_of
from chrono_mail.pim.times import local_zone
from chrono_mail.ui import forms
from chrono_mail.ui.forms import part

# --- Version ------------------------------------------------------------
__version__ = "eventrepeat.py 4.4.535"

# --- Version history ----------------------------------------------------
# v4.4.535: Initial version: the Custom repeat of an event (round 4)

MOST_TIMES: int = 9999  # the fields need an end; the rule itself has none


class RepeatDialog(QDialog):
    """Every so many days, weeks, months or years; the days of the week; ends never, on a date, or after so many occurrences."""

    def __init__(self, rule: str, day: date, all_day: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = forms.fill(self, "event_repeat")  # D-276
        self._rule: str = rule
        self._day: date = day
        self._all_day: bool = all_day
        custom = custom_of(rule, day, local_zone())
        self.every: QSpinBox = part(form, QSpinBox, "every")
        self.every.setRange(1, MOST_TIMES)
        self.every.setValue(custom.every)
        self.unit: QComboBox = part(form, QComboBox, "unit")
        for value, word in UNITS:
            self.unit.addItem(f"{word}s", value)
        self.unit.setCurrentIndex(self.unit.findData(custom.unit))
        self.days: dict[str, QCheckBox] = {name: part(form, QCheckBox, f"day{name}") for name in WEEKDAYS}
        for name, box in self.days.items():
            box.setChecked(name in custom.days)
        self.days_label: QLabel = part(form, QLabel, "daysLabel")
        self.ends_never: QRadioButton = part(form, QRadioButton, "endsNever")
        self.ends_on: QRadioButton = part(form, QRadioButton, "endsOn")
        self.ends_after: QRadioButton = part(form, QRadioButton, "endsAfter")
        self.end_date: QDateEdit = part(form, QDateEdit, "endDate")
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("yyyy-MM-dd")
        last = custom.until or day
        self.end_date.setDate(QDate(last.year, last.month, last.day))
        self.count: QSpinBox = part(form, QSpinBox, "count")
        self.count.setRange(1, MOST_TIMES)
        self.count.setValue(custom.count or 10)
        (self.ends_on if custom.until is not None else self.ends_after if custom.count is not None else self.ends_never).setChecked(True)
        buttons = part(form, QDialogButtonBox, "buttons")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.unit.currentIndexChanged.connect(self._follow)
        for choice in (self.ends_never, self.ends_on, self.ends_after):
            choice.toggled.connect(self._follow)
        self._follow()
        forms.smallest(self, 420)

    def _follow(self) -> None:
        """What does not apply is greyed: the days are for weeks, the date and the number for their own way of ending."""
        weekly = self.unit.currentData() == "WEEKLY"
        self.days_label.setEnabled(weekly)
        for box in self.days.values():
            box.setEnabled(weekly)
        self.end_date.setEnabled(self.ends_on.isChecked())
        self.count.setEnabled(self.ends_after.isChecked())

    def rule(self) -> str:
        qdate = self.end_date.date()
        custom = CustomRepeat(
            str(self.unit.currentData()), self.every.value(), tuple(name for name, box in self.days.items() if box.isChecked()),
            date(qdate.year(), qdate.month(), qdate.day()) if self.ends_on.isChecked() else None, self.count.value() if self.ends_after.isChecked() else None,
        )
        return rule_of(custom, self._rule, self._day, self._all_day, local_zone())
