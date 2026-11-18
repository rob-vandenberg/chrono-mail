"""Settings > Templates and Signatures: the library of named texts with, below it, what each account puts in by itself; and the window
of one text (B-24, D-358).

A template and a signature are the same thing, a named text; which one it is shows only in how an account (Accounts, Edit) or the
person uses it. The page lists the texts in the person's order, the first seven with their key, Ctrl+1 to Ctrl+7; New, Edit, Delete,
↑ and ↓ act on the list, Open…, Save As…, Import… and Export… on files, and the preview shows the text selected. As everything in
Settings, a change is kept only at OK (snippet.save-all) and Cancel leaves the library as it was. Everything is kept on this computer
only (D-201)."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import override

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_signatures import (
    SNIPPET_ALL,
    SNIPPET_EXPORT,
    SNIPPET_IMPORT,
    SNIPPET_OPEN,
    SNIPPET_SAVE_ALL,
    SNIPPET_SAVE_AS,
    SNIPPET_SET_ACCOUNT_TEXTS,
    AccountTextsAnswer,
    ExportParameters,
    ImportParameters,
    NoParameters,
    OpenParameters,
    SaveAllParameters,
    SaveAsParameters,
    SetAccountParameters,
    SnippetSummary,
    SnippetText,
    WrittenFile,
    free_name,
)
from chrono_mail.engine.commands_accounts import ACCOUNT_LIST, AccountSummary, NoParameters as AccountListParameters
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import red_style
from chrono_mail.ui.forms import part
from chrono_mail.ui.richeditor import RichEditor
from chrono_mail.ui.scale import wpx
from chrono_mail.ui.windowstate import restore_geometry, save_geometry

# --- Version ------------------------------------------------------------
__version__ = "signaturesdialog.py 4.5.626"

# --- Version history ----------------------------------------------------
# v4.5.626: Initial version: the library of templates and signatures on Settings > Templates and Signatures, kept at OK, and the
#           window of one text (B-24, D-358)

HTML_FILTER: str = "Web pages (*.html *.htm)"
EXPORT_FILTER: str = "Templates and signatures (*.json)"
EDITOR_WIDTH: int = 640
KEYED: int = 7  # the first seven texts have Ctrl+1 to Ctrl+7
ARROW_WIDTH: int = 36  # the ↑ and ↓ buttons, at the standard size
ACCOUNTS_GAP: int = 24  # between the account list and its fields, at the standard size
LIBRARY_SIZES: tuple[int, int] = (340, 457)  # the list and the preview, measured on the owner's screen (2026-11-18): their proportion
ACCOUNTS_SIZES: tuple[int, int] = (336, 437)  # the account list and its fields, measured the same way
HANDLE_WIDTH: int = 10  # a splitter's handle, at the standard size
CHOICE_FIELDS: tuple[str, ...] = ("templateNew", "templateReply", "templateForward", "signatureNew", "signatureReply", "signatureForward")


@dataclass(frozen=True, slots=True)
class AccountChoice:
    """An account's texts on this page: template new, reply, forward, signature new, reply, forward (each a key of the page, "" none), and
    whether its signature stands after the quoted text."""

    ids: tuple[str, ...]
    below: bool


def key_of(place: int) -> str:
    """The key of the text at this place of the library ("" after the seventh)."""
    return f"Ctrl+{place + 1}" if 0 <= place < KEYED else ""


def _preview(parent: QWidget) -> RichEditor:
    """A text as it looks: the editor of the New message window without its bar, read only."""
    shown = RichEditor(parent)
    shown.toolbar.hide()
    shown.edit.setReadOnly(True)
    shown.edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return shown


class SignatureEditor(QDialog):
    """New, Edit or Open…: the text's name and the text, with the formatting bar of the New message window. Save closes the window with
    the result (kept at the OK of Settings); a name another text has is said in the window, which stays open."""

    def __init__(self, settings: QSettings | None, existing: SnippetText | None, taken: Callable[[str], bool], parent: QWidget | None = None, title: str = "") -> None:
        super().__init__(parent)
        self._settings: QSettings | None = settings
        self._existing: SnippetText | None = existing
        self._taken: Callable[[str], bool] = taken
        self.result_text: SnippetText | None = None
        self.setWindowTitle(title or ("New template or signature" if existing is None else "Edit template or signature"))
        self.form: QWidget = forms.fill(self, "signature_editor", {"text": RichEditor})
        self.name = part(self.form, QLineEdit, "name")
        self.text = part(self.form, RichEditor, "text")
        self.status = part(self.form, QLabel, "status")
        self.status.setStyleSheet(red_style())
        self.status.hide()
        buttons = part(self.form, QDialogButtonBox, "buttons")
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        assert save is not None
        self.save_button: QPushButton = save
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        if existing is not None:
            self.name.setText(existing.name)
            self.text.set_html(existing.html)
        self.name.textChanged.connect(self._changed)
        self._changed()
        forms.open_at(self, EDITOR_WIDTH, EDITOR_WIDTH)
        restore_geometry(self, settings, "signature_editor_geometry")

    def _changed(self) -> None:
        """Save is greyed while the name is empty; a refusal said before goes."""
        named = bool(self.name.text().strip())
        self.save_button.setEnabled(named)
        self.save_button.setToolTip("" if named else "Give it a name first")
        self.status.hide()

    def _save(self) -> None:
        name = " ".join(self.name.text().split())
        if not name:
            return
        if self._taken(name):
            self.status.setText(f"A template or signature called {name} already exists")
            self.status.show()
            return
        self.result_text = SnippetText(self._existing.item_id if self._existing is not None else "", name, self.text.outgoing_html())
        self.accept()

    @override
    def done(self, result: int) -> None:
        save_geometry(self, self._settings, "signature_editor_geometry")
        super().done(result)


class SignaturesPanel(QWidget):
    """Settings > Templates and Signatures: the library at the top, below it per account the texts it puts in by itself."""

    def __init__(self, backend: Backend, settings: QSettings | None, sent_from_line: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._settings: QSettings | None = settings
        self._loaded: bool = False  # the library arrived (a damaged one leaves the page greyed and OK changes nothing)
        self._kept: tuple[SnippetText, ...] = ()  # as kept now
        self.entries: list[SnippetText] = []  # as shown, kept at OK
        self.keys: list[str] = []  # each entry's key on this page: its id, or "new-n" for one not kept yet
        self._made: int = 0
        self._kept_choices: dict[str, AccountChoice] = {}  # account id -> as kept now
        self.choices: dict[str, AccountChoice] = {}  # account id -> as shown, kept at OK (texts by their key on this page)
        self.accounts_saved: bool = False  # OK changed an account's choice: the main window reads the accounts again
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.form: QWidget = forms.load("settings_signatures", self, {"preview": _preview})
        layout.addWidget(self.form)
        self.splitter = part(self.form, QSplitter, "librarySplitter")
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setSizes([LIBRARY_SIZES[0], LIBRARY_SIZES[1]])  # the owner's measured layout, taken in proportion
        for column in ("listColumn", "previewColumn"):  # the library lines up with the accounts block below it, left and right
            part(self.form, QVBoxLayout, column).setContentsMargins(0, 0, 0, 0)
        part(self.form, QVBoxLayout, "accountsColumn").setContentsMargins(0, 0, 0, 0)
        part(self.form, QFormLayout, "choices").setContentsMargins(wpx(ACCOUNTS_GAP), 0, 0, 0)  # the fields a little away from the account list
        self.accounts_splitter = part(self.form, QSplitter, "accountsSplitter")
        self.accounts_splitter.setSizes([ACCOUNTS_SIZES[0], ACCOUNTS_SIZES[1]])  # the owner's measured layout, taken in proportion
        for splitter in (self.splitter, self.accounts_splitter):
            splitter.setHandleWidth(wpx(HANDLE_WIDTH))  # wide enough to take hold of
        self.items = part(self.form, QTreeWidget, "items")
        self.items.header().setStretchLastSection(False)
        self.items.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.items.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.preview = part(self.form, RichEditor, "preview")
        self.status = part(self.form, QLabel, "status")
        self.status.setStyleSheet(red_style())
        self.status.hide()
        self.sent_from = part(self.form, QCheckBox, "sentFrom")
        self.sent_from.setChecked(sent_from_line)
        self.sent_from.setToolTip("Every message you send ends with this line")
        self.new_button = part(self.form, QPushButton, "newButton")
        self.edit_button = part(self.form, QPushButton, "editButton")
        self.delete_button = part(self.form, QPushButton, "deleteButton")
        self.up_button = part(self.form, QPushButton, "upButton")
        self.down_button = part(self.form, QPushButton, "downButton")
        self.open_button = part(self.form, QPushButton, "openButton")
        self.save_as_button = part(self.form, QPushButton, "saveAsButton")
        self.import_button = part(self.form, QPushButton, "importButton")
        self.export_button = part(self.form, QPushButton, "exportButton")
        self.accounts = part(self.form, QListWidget, "accounts")
        self.boxes: list[QComboBox] = [part(self.form, QComboBox, name) for name in CHOICE_FIELDS]
        self.position = part(self.form, QComboBox, "signaturePosition")
        self.position.addItem("Before the quoted text", False)
        self.position.addItem("After the quoted text", True)
        for arrow in (self.up_button, self.down_button):  # an arrow needs no more room than its sign: the list side can be made narrow
            arrow.setMaximumWidth(wpx(ARROW_WIDTH))
        self.new_button.clicked.connect(self.new_item)
        self.edit_button.clicked.connect(self.edit_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.open_button.clicked.connect(self.open_file)
        self.save_as_button.clicked.connect(self.save_as)
        self.import_button.clicked.connect(self.import_file)
        self.export_button.clicked.connect(self.export_file)
        self.items.currentItemChanged.connect(self._selection_changed)
        self.items.itemDoubleClicked.connect(self._double_clicked)
        self.accounts.currentRowChanged.connect(self._account_chosen)
        for box in [*self.boxes, self.position]:
            box.activated.connect(self._choice_changed)
        self._show_list(-1)
        self._show_choices()
        self._backend.ask(SNIPPET_ALL, NoParameters(), self._arrived, self._not_read, owner=self)

    def _arrived(self, found: tuple[SnippetText, ...]) -> None:
        self._loaded = True
        self._kept = found
        self.entries = list(found)
        self.keys = [entry.item_id for entry in found]
        self._show_list(0 if self.entries else -1)
        self._backend.ask(ACCOUNT_LIST, AccountListParameters(), self._accounts_arrived, self._not_read, owner=self)

    def _accounts_arrived(self, found: tuple[AccountSummary, ...]) -> None:
        """The mail accounts, each with what it puts in by itself; a choice of a text no longer in the library counts as none."""
        known = set(self.keys)
        self.accounts.clear()
        for account in found:
            if account.kind in ("local", "dav"):
                continue
            texts = account.texts
            ids = tuple(item if item in known else "" for item in (texts.template_new, texts.template_reply, texts.template_forward, texts.signature_new, texts.signature_reply, texts.signature_forward))
            self._kept_choices[account.account_id] = self.choices[account.account_id] = AccountChoice(ids, texts.signature_below)
            row = QListWidgetItem(account.label)
            row.setData(Qt.ItemDataRole.UserRole, account.account_id)
            self.accounts.addItem(row)
        self.accounts.setCurrentRow(0 if self.accounts.count() else -1)
        self._show_choices()

    def _not_read(self, error: ChronoMailError) -> None:
        self._say(f"The templates and signatures could not be read: {error}")
        self._buttons_follow()

    def _say(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    # --- the library ------------------------------------------------------------------------------------------

    def _show_list(self, place: int) -> None:
        """The texts with their keys; the one at place is selected (-1 none). The accounts' drop-downs follow."""
        self.items.blockSignals(True)
        self.items.clear()
        for index, entry in enumerate(self.entries):
            self.items.addTopLevelItem(QTreeWidgetItem([entry.name, key_of(index)]))
        chosen = self.items.topLevelItem(place) if 0 <= place < len(self.entries) else None
        if chosen is not None:
            self.items.setCurrentItem(chosen)
        self.items.blockSignals(False)
        self._selection_changed()
        self._show_choices()

    def selected_index(self) -> int:
        row = self.items.currentItem()
        return self.items.indexOfTopLevelItem(row) if row is not None else -1

    def _selection_changed(self, *ignored: object) -> None:
        self._buttons_follow()
        index = self.selected_index()
        self.preview.set_html(self.entries[index].html if 0 <= index < len(self.entries) else "")

    def _double_clicked(self, row: QTreeWidgetItem, column: int) -> None:
        self.edit_selected()

    def _buttons_follow(self) -> None:
        """A button that cannot act now is greyed (OR-15): the library not read, nothing selected, the top or the bottom one."""
        index, count = self.selected_index(), len(self.entries)
        selected = self._loaded and 0 <= index < count
        for button in (self.new_button, self.open_button, self.import_button):
            button.setEnabled(self._loaded)
        self.export_button.setEnabled(self._loaded and count > 0)
        for button in (self.edit_button, self.delete_button, self.save_as_button):
            button.setEnabled(selected)
        self.up_button.setEnabled(selected and index > 0)
        self.down_button.setEnabled(selected and index < count - 1)

    def _taken(self, name: str, besides: int = -1) -> bool:
        folded = name.casefold()
        return any(entry.name.casefold() == folded for place, entry in enumerate(self.entries) if place != besides)

    def _add(self, entry: SnippetText) -> None:
        """A new text at the end of the list, selected."""
        self._made += 1
        self.entries.append(replace(entry, item_id=""))
        self.keys.append(f"new-{self._made}")
        self._show_list(len(self.entries) - 1)

    # --- the actions on the library ---------------------------------------------------------------------------

    def new_item(self) -> None:
        editor = SignatureEditor(self._settings, None, self._taken, self)
        if editor.exec() == QDialog.DialogCode.Accepted and editor.result_text is not None:
            self._add(editor.result_text)

    def edit_selected(self) -> None:
        index = self.selected_index()
        if index < 0:
            return
        editor = SignatureEditor(self._settings, self.entries[index], lambda name: self._taken(name, index), self)
        if editor.exec() == QDialog.DialogCode.Accepted and editor.result_text is not None:
            self.entries[index] = replace(editor.result_text, item_id=self.entries[index].item_id)
            self._show_list(index)

    def delete_selected(self) -> None:
        index = self.selected_index()
        if index < 0:
            return
        if QMessageBox.question(self, "Delete", f"Delete “{self.entries[index].name}”?\n\nAn account that uses it puts in nothing there any more.") != QMessageBox.StandardButton.Yes:
            return
        gone = self.keys[index]
        del self.entries[index]
        del self.keys[index]
        for account_id, choice in self.choices.items():
            self.choices[account_id] = AccountChoice(tuple("" if key == gone else key for key in choice.ids), choice.below)
        self._show_list(min(index, len(self.entries) - 1))  # the one below, at the end the one above

    def _move(self, step: int) -> None:
        index = self.selected_index()
        target = index + step
        if 0 <= index < len(self.entries) and 0 <= target < len(self.entries):
            self.entries[index], self.entries[target] = self.entries[target], self.entries[index]
            self.keys[index], self.keys[target] = self.keys[target], self.keys[index]
            self._show_list(target)

    def open_file(self) -> None:
        """An HTML file as a new text: the window of New opens with its name and text, to check and save it."""
        path, _ = QFileDialog.getOpenFileName(self, "Open a template or signature", "", HTML_FILTER)
        if not path:
            return
        self._say("")

        def opened(found: SnippetText) -> None:
            start = replace(found, name=free_name(found.name, self._taken))
            editor = SignatureEditor(self._settings, start, self._taken, self, title="Open a template or signature")
            if editor.exec() == QDialog.DialogCode.Accepted and editor.result_text is not None:
                self._add(editor.result_text)

        self._backend.ask(SNIPPET_OPEN, OpenParameters(path), opened, lambda error: self._say(str(error)), owner=self)

    def save_as(self) -> None:
        index = self.selected_index()
        if index < 0:
            return
        entry = self.entries[index]
        path, _ = QFileDialog.getSaveFileName(self, "Save as", f"{entry.name}.html", HTML_FILTER)
        if not path:
            return
        if not Path(path).suffix:
            path += ".html"
        self._say("")
        self._backend.ask(SNIPPET_SAVE_AS, SaveAsParameters(entry.name, entry.html, path), self._written, lambda error: self._say(str(error)), owner=self)

    def _written(self, answer: WrittenFile) -> None:
        pass

    def import_file(self) -> None:
        """The texts of a file Export made join the end of the list; a name already used gets " (2)"."""
        path, _ = QFileDialog.getOpenFileName(self, "Import templates and signatures", "", EXPORT_FILTER)
        if not path:
            return
        self._say("")

        def read(found: tuple[SnippetText, ...]) -> None:
            place = self.selected_index()
            for entry in found:
                self._add(replace(entry, name=free_name(entry.name, self._taken)))
            self._show_list(place if place >= 0 else (0 if self.entries else -1))

        self._backend.ask(SNIPPET_IMPORT, ImportParameters(path), read, lambda error: self._say(str(error)), owner=self)

    def export_file(self) -> None:
        """Every text as the page shows it, in one file of Chrono Mail's own."""
        path, _ = QFileDialog.getSaveFileName(self, "Export the templates and signatures", "Templates and signatures.json", EXPORT_FILTER)
        if not path:
            return
        if not Path(path).suffix:
            path += ".json"
        self._say("")
        shown = tuple(self.entries)
        parameters = ExportParameters(path, tuple(entry.name for entry in shown), tuple(entry.html for entry in shown))
        self._backend.ask(SNIPPET_EXPORT, parameters, self._written, lambda error: self._say(str(error)), owner=self)

    # --- the accounts -----------------------------------------------------------------------------------------

    def selected_account(self) -> str:
        row = self.accounts.currentItem()
        return str(row.data(Qt.ItemDataRole.UserRole)) if row is not None else ""

    def _account_chosen(self, row: int) -> None:
        self._show_choices()

    def _show_choices(self) -> None:
        """The drop-downs of the account selected: every text of the library, "Not selected" first; greyed without an account."""
        choice = self.choices.get(self.selected_account())
        for place, box in enumerate(self.boxes):
            box.blockSignals(True)
            box.clear()
            box.addItem("Not selected", "")
            for key, entry in zip(self.keys, self.entries, strict=True):
                box.addItem(entry.name, key)
            box.setCurrentIndex(max(0, box.findData(choice.ids[place])) if choice is not None else 0)
            box.setEnabled(choice is not None)
            box.blockSignals(False)
        self.position.setCurrentIndex(1 if choice is not None and choice.below else 0)
        self.position.setEnabled(choice is not None)

    def _choice_changed(self, *ignored: object) -> None:
        account_id = self.selected_account()
        if account_id in self.choices:
            self.choices[account_id] = AccountChoice(tuple(str(box.currentData() or "") for box in self.boxes), bool(self.position.currentData()))

    # --- OK -----------------------------------------------------------------------------------------------------

    def changed(self) -> bool:
        return self._loaded and (tuple(self.entries) != self._kept or self.choices != self._kept_choices)

    def save(self, saved: Callable[[], None], failed: Callable[[], None]) -> None:
        """Keep the library and the accounts' choices as shown (Settings' OK): the library first, then each account whose choice
        changed. A refusal is said on the page; what was kept before it stays kept."""
        shown, keys = tuple(self.entries), tuple(self.keys)

        def refused(error: ChronoMailError) -> None:
            self._say(f"The templates and signatures were not saved: {error}")
            failed()

        def library_kept(found: tuple[SnippetSummary, ...]) -> None:
            real = {key: summary.item_id for key, summary in zip(keys, found, strict=True)}
            self.accounts_saved = True  # a deleted text was taken off the accounts that used it
            self._kept = tuple(replace(entry, item_id=summary.item_id) for summary, entry in zip(found, shown, strict=True))
            self.entries, self.keys = list(self._kept), [entry.item_id for entry in self._kept]
            self.choices = {account_id: AccountChoice(tuple(real.get(key, "") for key in choice.ids), choice.below) for account_id, choice in self.choices.items()}
            self._kept_choices = {account_id: AccountChoice(tuple(real.get(key, "") for key in choice.ids), choice.below) for account_id, choice in self._kept_choices.items()}
            next_account()

        def next_account() -> None:
            waiting = [account_id for account_id, choice in self.choices.items() if self._kept_choices.get(account_id) != choice]
            if not waiting:
                saved()
                return
            account_id = waiting[0]
            choice = self.choices[account_id]

            def kept(answer: AccountTextsAnswer) -> None:
                self._kept_choices[account_id] = choice
                self.accounts_saved = True
                next_account()

            self._backend.ask(SNIPPET_SET_ACCOUNT_TEXTS, SetAccountParameters(account_id, *choice.ids, signature_below=choice.below), kept, refused, owner=self)

        if tuple(self.entries) == self._kept:
            next_account()
            return
        parameters = SaveAllParameters(tuple(entry.item_id for entry in shown), tuple(entry.name for entry in shown), tuple(entry.html for entry in shown))
        self._backend.ask(SNIPPET_SAVE_ALL, parameters, library_kept, refused, owner=self)
