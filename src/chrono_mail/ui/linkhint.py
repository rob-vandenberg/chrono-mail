import html
import re

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QWidget

from chrono_mail.ui.icons import theme_icon

# --- Version ------------------------------------------------------------
__version__ = "linkhint.py 4.4.526"

# --- Version history ----------------------------------------------------
# v4.4.526: linkify, from reader.py: the invitation card uses it too
# v1.5.257: Initial: the left part of a status bar shows its own text, or, while the pointer is on a link in a message, where that
#           link leads, with the orange triangle when it leads to another main domain than the sender's (D-215)

_SENTENCE_END: str = ".,;:!?'"
# An address that opens with an apostrophe ends at the next one; any other keeps its apostrophes (O'Brien).
_URL_PATTERN: re.Pattern[str] = re.compile(r"(?<=')(?:https?://|mailto:)[^\s<>\"']+|(?<!')(?:https?://|mailto:)[^\s<>\"]+", re.IGNORECASE)  # a scheme in any case (RFC 3986)
WARNING_ICON: str = "emblem-warning"  # Breeze: the orange triangle with "!"


def _sentence_end_cut(found: str) -> int:
    """Where an address ends without the punctuation of the sentence after it, never inside its scheme; one pass, also over hostile text."""
    stop = len(found)
    scheme = len(found.split(":", 1)[0]) + 1 + (2 if found[:4].lower() == "http" else 0)  # "mailto:", "http://", "https://"
    unmatched = found.count(")") - found.count("(")
    while stop > scheme:
        last = found[stop - 1]
        if last in _SENTENCE_END:
            stop -= 1
        elif last == ")" and unmatched > 0:
            unmatched -= 1
            stop -= 1
        else:
            break
    return stop


def linkify(text: str) -> str:
    """Plain text as HTML with its web and mail addresses clickable.

    The addresses are found in the text as written, so one stops at its closing bracket or double quote; an
    apostrophe belongs to the address (O'Brien) unless the address opened with one. Punctuation that ends the
    sentence after an address (. , ; : ! ? ' and a ")" without its "(") is not part of it. The text around
    them and each address are made safe for HTML apart (CR-19).
    """
    pieces: list[str] = []
    end = 0
    for match in _URL_PATTERN.finditer(text):
        found = match.group(0)[:_sentence_end_cut(match.group(0))]  # what is cut is only punctuation: no other address in it
        if found.lower() in ("http://", "https://", "mailto:"):
            continue  # nothing but the scheme: plain text
        address = html.escape(found)
        pieces.append(f'{html.escape(text[end:match.start()])}<a href="{address}">{address}</a>')
        end = match.start() + len(found)
    pieces.append(html.escape(text[end:]))
    return "".join(pieces)


class StatusLine(QStackedWidget):
    """The left part of a status bar: its own text (a widget of the window, never changed here), or, while the pointer is on a
    link, the link's address. Leaving the link shows the window's text again: it never went away."""

    def __init__(self, normal: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.normal: QWidget = normal
        self.addWidget(normal)
        self.link = QWidget(self)
        layout = QHBoxLayout(self.link)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.warning = QLabel(self.link)
        self.warning.setToolTip("This link leads to another domain than the sender's")
        self.address = QLabel(self.link)
        self.address.setTextFormat(Qt.TextFormat.PlainText)  # an address from a message is hostile input: never markup
        self.address.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # a long address is cut off, it never widens the window
        layout.addWidget(self.warning)
        layout.addWidget(self.address, 1)
        self.addWidget(self.link)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def show_link(self, address: str, elsewhere: bool) -> None:
        """The pointer is on a link (its address, and whether it leads to another main domain than the sender's), or left it ("")."""
        if not address:
            self.setCurrentWidget(self.normal)
            return
        if elsewhere:
            side = self.address.fontMetrics().height()
            self.warning.setPixmap(theme_icon(WARNING_ICON).pixmap(QSize(side, side), self.devicePixelRatioF()))
        self.warning.setVisible(elsewhere)
        self.address.setText(address)
        self.setCurrentWidget(self.link)
