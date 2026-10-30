from typing import override

from PySide6.QtCore import QModelIndex, QObject, QPersistentModelIndex, QStringListModel, Qt, QTimer
from PySide6.QtWidgets import QCompleter, QLineEdit

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_contacts import CONTACT_SUGGEST, SuggestParameters, Suggestion
from chrono_mail.ui.backend import Backend

# --- Version ------------------------------------------------------------
__version__ = "addresscompleter.py 4.2.430"

# --- Version history ----------------------------------------------------
# v4.2.430: an escaped quote inside a quoted name never ends it
# v1.10.277: A contact group is suggested by its name; choosing it puts every member's address in the field (R-39)
# v1.9.273: R-19: the suggestions are a typed call
# v0.0.68: Address suggestion chosen with the arrow keys was entered twice
# v0.0.68: A suggestion chosen with the arrow keys was put in the field twice
# v0.0.23: Initial address completion

DELAY_MS: int = 150
MINIMUM_CHARACTERS: int = 2


def split_last_address(text: str) -> tuple[str, str]:
    """(everything before the address being typed, the address being typed). Separators are , and ;
    but not inside quotes ("Archer, Ann") or angle brackets."""
    cut = 0
    quoted = False
    angled = False
    escaped = False
    for position, character in enumerate(text):
        if escaped:
            escaped = False  # the character after a backslash inside quotes is part of the name
        elif character == "\\" and quoted:
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif character == "<" and not quoted:
            angled = True
        elif character == ">" and not quoted:
            angled = False
        elif character in ",;" and not quoted and not angled:
            cut = position + 1
    return text[:cut], text[cut:].lstrip()


class AddressCompleter(QCompleter):
    """Suggests addresses (from the mail already stored) while a person types in an address field with several addresses."""

    def __init__(self, backend: Backend, field: QLineEdit, parent: QObject | None = None) -> None:
        self._suggestions: QStringListModel = QStringListModel()
        super().__init__(self._suggestions, parent or field)
        self._backend: Backend = backend
        self._field: QLineEdit = field
        self._request: int = 0
        self._typed: str = field.text()  # what the person typed last, before any suggestion was put in the field
        self._fills: dict[str, str] = {}  # what a suggestion shown puts in the field, when that is not what it shows (a group: its addresses)
        self.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        field.setCompleter(self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._ask)
        field.textEdited.connect(self._edited)

    @override
    def splitPath(self, path: str) -> list[str]:
        return [split_last_address(path)[1]]

    @override
    def pathFromIndex(self, index: QModelIndex | QPersistentModelIndex, /) -> str:
        head, _ = split_last_address(self._typed)  # not the field: highlighting a suggestion with the arrow keys already puts it there
        shown = str(index.data(Qt.ItemDataRole.EditRole))
        chosen = self._fills.get(shown, shown)
        return f"{head.rstrip()} {chosen}, ".lstrip() if head.strip() else f"{chosen}, "

    def _edited(self, text: str) -> None:
        self._typed = text
        self._request += 1
        if len(split_last_address(text)[1]) < MINIMUM_CHARACTERS:
            self._suggestions.setStringList([])
            self._timer.stop()
            return
        self._timer.start(DELAY_MS)

    def _ask(self) -> None:
        fragment = split_last_address(self._field.text())[1]
        if len(fragment) < MINIMUM_CHARACTERS:
            return
        request = self._request

        def arrived(result: tuple[Suggestion, ...]) -> None:
            if request != self._request:  # the person has typed on since
                return
            self._fills = {item.label: item.display for item in result if item.label}
            self._suggestions.setStringList([item.label or item.display for item in result])
            if self._suggestions.rowCount() and self._field.hasFocus():
                self.complete()

        def failed(error: ChronoMailError) -> None:
            self._suggestions.setStringList([])

        self._backend.ask(CONTACT_SUGGEST, SuggestParameters(fragment), arrived, failed, owner=self._field)
