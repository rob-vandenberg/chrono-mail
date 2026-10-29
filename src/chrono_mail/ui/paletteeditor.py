import html
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.engine.commands_pim import CollectionSummary
from chrono_mail.core import OWN_COLOUR_ID, ChronoMailError, PaletteColour
from chrono_mail.core.colours import COLLECTION_WITHOUT_COLOUR
from chrono_mail.engine.commands_pim import COLLECTION_SET_PALETTE, CollectionSummary, PaletteParameters
from chrono_mail.ui.infomark import InfoMark, info_marks
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import red
from chrono_mail.ui.forms import part
from chrono_mail.ui.icons import theme_icon
from chrono_mail.ui.scale import px_for, wpx

# --- Version ------------------------------------------------------------
__version__ = "paletteeditor.py 4.2.424"

# --- Version history ----------------------------------------------------
# v4.2.424: an (i) after the greyed Delete of the calendar's own colour, and after + of a read-only calendar, saying why; a read-only calendar greys each row's parts, never the (i) (D-294)
# v4.0.378: the editor and a colour's row are built from their Qt Designer forms (ui/forms/palette_editor.ui, palette_colour.ui, D-276); the
#           rows are made at their place, and a colour added comes after the others in the Tab order (it came after Save and Cancel)
# v3.6.350: an error text inside markup is escaped: a server's or a file's words are never drawn as markup (D-259)
# v2.0.291: A small margin at the right of the list, so the delete buttons do not touch the scrollbar (the owner)
# v2.0.290: No outline around the list of colours (the owner)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.1.222: Title "Event colours"; the Calendar row of the event editor at the top (the owner)
# v1.1.221: The colours of the calendar's account, named in the title (D-191)
# v1.1.219: Initial: the colours of one calendar (its own first, which cannot be deleted), each with a name; and how an appointment's colour is found (D-191)

OWN_COLOUR_STAYS: str = "The calendar's own colour cannot be deleted"
READ_ONLY_COLOURS: str = "This calendar is read only: its colours are shown, but cannot be changed."
LIST_RIGHT_MARGIN: int = 8  # pixels between the delete buttons and the scrollbar, at the windows' size

OTHER_COLOUR_NAME: str = "Other colour"  # an appointment coloured by another program, with no colour of the palette


def circle_icon(colour: str, size: int = 14) -> QIcon:
    """A filled circle in a colour, for the colour lists."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour if QColor(colour).isValid() else COLLECTION_WITHOUT_COLOUR))
    painter.drawEllipse(0, 0, size - 1, size - 1)
    painter.end()
    return QIcon(pixmap)


def palette_of_summary(calendar: CollectionSummary | None) -> tuple[PaletteColour, ...]:
    return tuple(calendar.palette) if calendar is not None else ()


def colour_from(calendar: CollectionSummary | None, colour_id: str, other: str) -> str:
    """The colour an appointment is drawn in, from what the window knows of its calendar now: its colour of the palette, else a colour another
    program gave it, else the calendar's own (also when its colour was deleted). The same rule as the engine's (commands_calendar.colour_of)."""
    palette = palette_of_summary(calendar)
    if colour_id:
        chosen = next((entry.colour for entry in palette if entry.colour_id == colour_id), "")
        if chosen:
            return chosen
    if other:
        return other
    return palette[0].colour if palette else ((calendar.colour if calendar is not None else "") or COLLECTION_WITHOUT_COLOUR)


class ColourRow(QWidget):
    """One colour in the palette editor: its colour (a click picks another), its name, and a bin (greyed for the calendar's own colour)."""

    def __init__(self, entry: PaletteColour, editor: "PaletteEditor", parent: QWidget | None = None) -> None:
        super().__init__(parent or editor)
        self.colour_id: str = entry.colour_id
        self.colour: str = entry.colour
        form = forms.fill(self, "palette_colour", info_marks("deleteInfo", keep_place=True))  # one colour of the editor (D-276); the rows stay lined up
        self.colour_button: QPushButton = part(form, QPushButton, "colour")
        self.name: QLineEdit = part(form, QLineEdit, "name")
        self.name.setText(entry.name)
        self.name.setClearButtonEnabled(True)
        self.delete_button: QToolButton = part(form, QToolButton, "delete")
        self.delete_button.setIcon(theme_icon("edit-delete", "user-trash"))
        own = entry.colour_id == OWN_COLOUR_ID
        self.delete_button.setEnabled(not own)  # the calendar's own colour can be changed, never deleted
        if own:
            self.delete_button.setToolTip(OWN_COLOUR_STAYS)
        self.delete_info: InfoMark = part(form, InfoMark, "deleteInfo")
        self.delete_info.explain("Delete this colour", OWN_COLOUR_STAYS if own else "")  # D-294
        self._show_colour()
        self.colour_button.clicked.connect(self._pick)
        self.delete_button.clicked.connect(lambda: editor.remove_row(self))

    def _show_colour(self) -> None:
        self.colour_button.setIcon(circle_icon(self.colour, 18))

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(QColor(self.colour), self, "Colour")
        if chosen.isValid():
            self.colour = chosen.name()
            self._show_colour()


class PaletteEditor(QDialog):
    """The appointment colours of a calendar's account: a row per colour (the calendar's own first, then the account's, shared by all its calendars),
    + to add one, Cancel and Save (D-191). Save keeps them with the account; the summary that comes back is in `result_summary`."""

    def __init__(self, backend: Backend, calendar: CollectionSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._calendar: CollectionSummary = calendar
        self.result_summary: CollectionSummary | None = None
        self.rows: list[ColourRow] = []
        form = forms.fill(self, "palette_editor", {"rows": self._holder, **info_marks("addInfo")})  # D-276
        self.calendar: QComboBox = part(form, QComboBox, "calendar")  # the event editor's Calendar row: which calendar's own colour comes first, and whose account's colours follow
        self.calendar.addItem(f"{calendar.account_name} — {calendar.name}")
        self.calendar.setEnabled(False)
        part(form, QScrollArea, "scroll").setMinimumHeight(wpx(320))
        self.result_label: QLabel = part(form, QLabel, "result")
        self.add_button: QToolButton = part(form, QToolButton, "add")
        self.add_button.setIcon(theme_icon("list-add"))
        self.add_button.clicked.connect(self._new_row)
        box = part(form, QDialogButtonBox, "buttons")
        self.save_button = box.button(QDialogButtonBox.StandardButton.Save)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        forms.smallest(self, 440)  # its texts are never cut off, also at the largest size
        if self.rows:
            self.rows[0].colour_button.setFocus()  # it opens on the first colour, as before (the list's scroll area comes first in the Tab order)
        if calendar.read_only:  # a read-only calendar's colours are shown, not changed; the (i) of a row stays usable (D-294)
            for widget in (self.add_button, self.save_button, *(control for row in self.rows for control in (row.colour_button, row.name, row.delete_button))):
                widget.setEnabled(False)
            part(form, InfoMark, "addInfo").explain("Event colours", READ_ONLY_COLOURS)

    def _holder(self, parent: QWidget) -> QWidget:
        """The list of colours with a row each, made at its place in the form (so Tab reaches the rows before the buttons below them)."""
        self.holder = QWidget(parent)
        self.rows_layout = QVBoxLayout(self.holder)
        self.rows_layout.setContentsMargins(0, 0, px_for(self, LIST_RIGHT_MARGIN), 0)  # room between the delete buttons and the scrollbar (the owner)
        self.rows_layout.addStretch(1)
        for entry in palette_of_summary(self._calendar):
            self._add_row(entry)
        return self.holder

    def _add_row(self, entry: PaletteColour) -> ColourRow:
        row = ColourRow(entry, self, self.holder)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        self.rows.append(row)
        return row

    def _new_row(self) -> None:
        last = self.rows[-1].delete_button if self.rows else self.calendar
        row = self._add_row(PaletteColour("", "", COLLECTION_WITHOUT_COLOUR))
        for widget in (row.colour_button, row.name, row.delete_button):  # Tab reaches it after the colours above it, not after Save and Cancel
            QWidget.setTabOrder(last, widget)
            last = widget
        row.name.setFocus()

    def remove_row(self, row: ColourRow) -> None:
        if row.colour_id == OWN_COLOUR_ID:
            return
        self.rows.remove(row)
        self.rows_layout.removeWidget(row)
        row.deleteLater()

    def parameters(self) -> PaletteParameters:
        own = next(row for row in self.rows if row.colour_id == OWN_COLOUR_ID)
        others = tuple(f"{row.colour_id}\t{row.name.text().strip()}\t{row.colour}" for row in self.rows if row.colour_id != OWN_COLOUR_ID)
        return PaletteParameters(self._calendar.collection, own.name.text().strip(), own.colour, others)

    def _save(self) -> None:
        if any(not row.name.text().strip() for row in self.rows):
            self.result_label.setText(f"<span style='color:{red()}'>Every colour needs a name.</span>")
            return
        self.save_button.setEnabled(False)

        def saved(summary: CollectionSummary) -> None:
            self.result_summary = summary
            self.accept()

        def failed(error: ChronoMailError) -> None:
            self.save_button.setEnabled(True)
            self.result_label.setText(f"<span style='color:{red()}'>{html.escape(str(error))}</span>")

        self._backend.ask(COLLECTION_SET_PALETTE, self.parameters(), saved, failed, owner=self)
