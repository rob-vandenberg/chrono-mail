from collections.abc import Sequence

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import QComboBox, QDateEdit, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QToolButton, QWidget

from chrono_mail.ui.searchform import KINDS, MAXIMUM_TEXT, PARTS, DateChoice, SearchLine, example

# --- Version ------------------------------------------------------------
__version__ = "searchlines.py 4.2.410"

# --- Version history ----------------------------------------------------
# v4.2.410: Initial: the search lines of Advanced search (Where / and / or, where in the message, contains, how the words count, the
#           words, [+] and [−]; the first line has no [−], its place kept so the buttons line up) and a date row (a span or fixed
#           days) (D-289)

class _Line:
    """The parts of one search line."""

    def __init__(self, parent: QWidget, line: SearchLine) -> None:
        self.joining: QLabel = QLabel(parent)
        self.part: QComboBox = QComboBox(parent)
        for value, label in PARTS:
            self.part.addItem(label, value)
        self.contains: QLabel = QLabel("contains", parent)
        self.kind: QComboBox = QComboBox(parent)
        for value, label in KINDS:
            self.kind.addItem(label, value)
        self.text: QLineEdit = QLineEdit(parent)
        self.text.setMaxLength(MAXIMUM_TEXT)  # a pasted book stays out: the search words are worked out at every key press
        self.add: QToolButton = QToolButton(parent)
        self.add.setText("+")
        self.add.setToolTip("Add a line below this one")
        self.remove: QToolButton = QToolButton(parent)
        self.remove.setText("−")
        self.remove.setToolTip("Remove this line")
        keeps_place = self.remove.sizePolicy()
        keeps_place.setRetainSizeWhenHidden(True)  # the first line's [−] is absent, its place kept: every [+] stays in one column
        self.remove.setSizePolicy(keeps_place)
        self.part.setCurrentIndex(max(0, self.part.findData(line.part)))
        self.kind.setCurrentIndex(max(0, self.kind.findData(line.kind)))
        self.text.setText(line.text)
        self.show_example()

    def widgets(self) -> tuple[QWidget, ...]:
        return (self.joining, self.part, self.contains, self.kind, self.text, self.add, self.remove)

    def value(self) -> SearchLine:
        return SearchLine(str(self.part.currentData()), str(self.kind.currentData()), self.text.text())

    def show_example(self) -> None:
        """The grey example text follows the line's choices."""
        self.text.setPlaceholderText(example(str(self.part.currentData()), str(self.kind.currentData())))


class SearchLines(QWidget):
    """The search lines: each says where in the message it looks, how its words count and the words. The first starts with Where, the
    others with and or or, following Find messages that match all or any. [+] adds a line below its own, [−] removes its line; the
    first line cannot be removed and has no [−], but its place stays, so that every [+] is in one column (the owner, D-289)."""

    changed = Signal()  # a line, a choice or a word changed
    lines_changed = Signal()  # a line came or went: the Tab order is set again

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._grid: QGridLayout = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setColumnStretch(4, 1)  # the words take the room
        self._lines: list[_Line] = []
        self._match: str = "all"
        self.set_lines([SearchLine()])

    def lines(self) -> tuple[SearchLine, ...]:
        return tuple(line.value() for line in self._lines)

    def line_count(self) -> int:
        return len(self._lines)

    def line(self, number: int) -> _Line:
        return self._lines[number]

    def set_lines(self, lines: Sequence[SearchLine]) -> None:
        for line in self._lines:
            for widget in line.widgets():
                widget.deleteLater()
        self._lines = []
        for value in lines or [SearchLine()]:
            self._lines.append(self._made(value))
        self._lay_out()

    def set_match(self, match: str) -> None:
        """all: the lines after the first start with and; any: with or."""
        self._match = match
        self._show_joining()

    def add_after(self, line: _Line) -> None:
        """A new line right below this one, at Anywhere and All of these words, its words field taking the keyboard."""
        made = self._made(SearchLine())
        self._lines.insert(self._lines.index(line) + 1, made)
        self._lay_out()
        made.text.setFocus()
        self.changed.emit()

    def remove(self, line: _Line) -> None:
        if line not in self._lines[1:]:
            return  # the first line stays
        place = self._lines.index(line)
        self._lines.remove(line)
        for widget in line.widgets():
            widget.deleteLater()
        self._lay_out()
        self._lines[min(place, len(self._lines) - 1)].text.setFocus()
        self.changed.emit()

    def focus_chain(self) -> list[QWidget]:
        """The parts that take the keyboard, in the order of the screen."""
        return [widget for number, line in enumerate(self._lines) for widget in (line.part, line.kind, line.text, line.add, line.remove) if widget is not line.remove or number > 0]

    def _made(self, value: SearchLine) -> _Line:
        line = _Line(self, value)
        line.part.currentIndexChanged.connect(lambda _index=0, made=line: self._choice_changed(made))
        line.kind.currentIndexChanged.connect(lambda _index=0, made=line: self._choice_changed(made))
        line.text.textChanged.connect(self.changed)
        line.add.clicked.connect(lambda _checked=False, made=line: self.add_after(made))
        line.remove.clicked.connect(lambda _checked=False, made=line: self.remove(made))
        return line

    def _choice_changed(self, line: _Line) -> None:
        line.show_example()
        self.changed.emit()

    def _lay_out(self) -> None:
        """Every line at its row, in order (a line added in the middle moves those below it down)."""
        for line in self._lines:
            for widget in line.widgets():
                self._grid.removeWidget(widget)
        for row, line in enumerate(self._lines):
            for column, widget in enumerate(line.widgets()):
                self._grid.addWidget(widget, row, column)
            for widget in line.widgets():
                widget.setVisible(widget is not line.remove or row > 0)  # now, not later: the Tab order is set from it; the first line has no [−] (the owner's exception to OR-15)
        self._show_joining()
        self.lines_changed.emit()

    def _show_joining(self) -> None:
        for row, line in enumerate(self._lines):
            line.joining.setText("Where" if row == 0 else ("or" if self._match == "any" else "and"))


class DateRow(QWidget):
    """A date row: a drop-down of spans counted from today (Any time, Today, Last 7 days …) and Between, Before and After, which show
    their fixed days."""

    changed = Signal()

    def __init__(self, parent: QWidget, choices: Sequence[tuple[str, str]]) -> None:
        super().__init__(parent)
        self.choice: QComboBox = QComboBox(self)
        for value, label in choices:
            self.choice.addItem(label, value)
        self.first_day: QDateEdit = QDateEdit(self)
        self.joining: QLabel = QLabel("and", self)
        self.last_day: QDateEdit = QDateEdit(self)
        today = QDate.currentDate()
        for day in (self.first_day, self.last_day):
            day.setDateRange(QDate(2, 1, 2), QDate(9998, 12, 30))  # every choice of them a day the search can ask for (D-259)
            day.setCalendarPopup(True)
            day.setDisplayFormat("yyyy-MM-dd")
            day.setDate(today)
            day.dateChanged.connect(self.changed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for widget in (self.choice, self.first_day, self.joining, self.last_day):
            layout.addWidget(widget)
        layout.addStretch(1)
        self.choice.currentIndexChanged.connect(self._chosen)
        self._chosen()

    def value(self) -> DateChoice:
        choice = str(self.choice.currentData())
        first = self.first_day.date().toString("yyyy-MM-dd") if choice in ("between", "after") else None
        last = self.last_day.date().toString("yyyy-MM-dd") if choice in ("between", "before") else None
        return DateChoice(choice, first, last)

    def set_value(self, value: DateChoice) -> None:
        for text, day in ((value.first, self.first_day), (value.last, self.last_day)):
            if text:
                found = QDate.fromString(text, "yyyy-MM-dd")
                if found.isValid():
                    day.setDate(found)
        self.choice.setCurrentIndex(max(0, self.choice.findData(value.choice)))
        self._chosen()

    def _chosen(self) -> None:
        """Between shows both days, Before the last, After the first; a span none."""
        choice = str(self.choice.currentData())
        self.first_day.setVisible(choice in ("between", "after"))
        self.joining.setVisible(choice == "between")
        self.last_day.setVisible(choice in ("between", "before"))
        self.changed.emit()

