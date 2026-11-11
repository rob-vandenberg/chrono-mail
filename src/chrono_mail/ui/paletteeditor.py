from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from chrono_mail.core import PaletteColour
from chrono_mail.core.colours import COLLECTION_WITHOUT_COLOUR
from chrono_mail.engine.commands_pim import CollectionSummary

# --- Version ------------------------------------------------------------
__version__ = "paletteeditor.py 4.4.573"

# --- Version history ----------------------------------------------------
# v4.4.573: The Event colours window is gone, appointments take their colour from a tag (D-329); left: the circle of a colour, and an appointment's colour when it has no tag
# v4.3.503: opens at 550 pixels on the owner's screen until the person gives it another width; never below 500 (the owner)
# v4.3.502: the Calendar field is read-only text in the normal colour, with its full name as hover text: a long name no longer keeps the
#           window from being made narrower (a drop-down asks for its whole text's width); it opens at the width last given it (the owner)
# v4.2.445: its 500 pixels are the owner's screen's, in Qt's units at the size slider (forms.owner_width), so it follows every screen's scale
# v4.2.442: opens at, and never goes below, 500 pixels on the screen (the owner); it was 440 at the size factor
# v4.2.438: the tick "Show background colour for timed events in Month view", kept with the account's colours; its text wraps and a click on it
#           works the tick (UR-02, D-297)
# v4.2.431: the + of a read-only calendar and the Delete of its own colour have no hover text while their (i) shows
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
