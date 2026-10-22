from collections.abc import Callable

from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.engine.commands_accounts import AccountSummary
from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_rules import (
    RULE_ENABLE, RULE_LIST, RULE_MOVE, RULE_REMOVE, RULE_RUN, NoRuleParameters, RuleEnableParameters, RuleIdParameters, RuleInfo, RuleMoveParameters, RuleRunParameters,
    RuleRunResult,
)
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.ruleedit import RuleEditDialog
from chrono_mail.ui.colours import red

# --- Version ------------------------------------------------------------
__version__ = "rulespanel.py 1.9.275"

# --- Version history ----------------------------------------------------
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.271: R-19: every engine call typed; the rules are RuleInfo
# v1.1.174: The button Change… is called Edit…
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.50: Rules and scheduled messages are pages of the Settings window
# v0.0.33: Initial rules window

ROLE_RULE: int = 256


def rule_of(item: QListWidgetItem | None) -> RuleInfo | None:
    """The rule a line of the list stands for."""
    rule = None if item is None else item.data(ROLE_RULE)
    return rule if isinstance(rule, RuleInfo) else None


class RulesPanel(QWidget):
    """The rules, in the order they are applied: switch on and off, add, change, remove, reorder, and apply to a folder now. A page of the settings."""

    def __init__(self, backend: Backend, accounts: list[AccountSummary], folders: Callable[[], list[str]], current: Callable[[], tuple[str, str] | None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._accounts: list[AccountSummary] = accounts
        self._folders: Callable[[], list[str]] = folders
        self._current: Callable[[], tuple[str, str] | None] = current
        self.ran: bool = False  # the rules were applied to a folder: the message list is out of date
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Rules are applied in this order to new mail as it arrives in the Inbox. A rule with “Stop processing rules” ends the rules for that message.", self))
        body = QHBoxLayout()
        self.list = QListWidget(self)
        body.addWidget(self.list, 1)
        side = QVBoxLayout()
        self.new_button = QPushButton("New…", self)
        self.edit_button = QPushButton("Edit…", self)
        self.delete_button = QPushButton("Delete…", self)
        self.up_button = QPushButton("Move up", self)
        self.down_button = QPushButton("Move down", self)
        self.run_button = QPushButton("Apply to this folder", self)
        for button in (self.new_button, self.edit_button, self.delete_button, self.up_button, self.down_button, self.run_button):
            side.addWidget(button)
        side.addStretch(1)
        body.addLayout(side)
        layout.addLayout(body)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.new_button.clicked.connect(self.new_rule)
        self.edit_button.clicked.connect(self.edit_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.up_button.clicked.connect(lambda: self.move_selected(-1))
        self.down_button.clicked.connect(lambda: self.move_selected(1))
        self.run_button.clicked.connect(self.run_now)
        self.list.itemDoubleClicked.connect(self._double_clicked)
        self.list.itemChanged.connect(self._toggled)
        self.list.currentItemChanged.connect(self._selection_changed)
        self._loading = False
        self._selection_changed()  # nothing is selected until the rules have been listed
        self.reload()

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.reload()  # rules may have been changed elsewhere since the page was made

    # --- the list -----------------------------------------------------------------------------------------------

    def reload(self, keep: str | None = None) -> None:
        selected = keep or self._selected_id()

        def listed(result: tuple[RuleInfo, ...]) -> None:
            self._loading = True
            self.list.clear()
            for rule in result:
                item = QListWidgetItem(f"{rule.name}   —   {', '.join(rule.conditions)}  →  {', '.join(rule.actions)}")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked)
                item.setData(ROLE_RULE, rule)
                self.list.addItem(item)
                if rule.rule_id == selected:
                    self.list.setCurrentItem(item)
            self._loading = False
            if self.list.count() and self.list.currentRow() < 0:
                self.list.setCurrentRow(0)
            self._selection_changed()

        self._backend.ask(RULE_LIST, NoRuleParameters(), listed, self._failed, owner=self)

    def _failed(self, error: ChronoMailError) -> None:
        self.status.setText(f"<span style='color:{red()}'>{error}</span>")

    def _selected(self) -> RuleInfo | None:
        item = self.list.currentItem()
        return rule_of(item)

    def _selected_id(self) -> str | None:
        rule = self._selected()
        return None if rule is None else rule.rule_id

    def _selection_changed(self) -> None:
        has = self._selected() is not None
        row = self.list.currentRow()
        self.edit_button.setEnabled(has)
        self.delete_button.setEnabled(has)
        self.up_button.setEnabled(has and row > 0)
        self.down_button.setEnabled(has and 0 <= row < self.list.count() - 1)
        self.run_button.setEnabled(has and self._current() is not None)

    def _toggled(self, item: QListWidgetItem) -> None:
        if self._loading:
            return
        rule = rule_of(item)
        if rule is None:
            return
        enabled = item.checkState() == Qt.CheckState.Checked
        self._backend.ask(RULE_ENABLE, RuleEnableParameters(rule.rule_id, enabled), lambda result: self.reload(), self._failed, owner=self)

    # --- changes ----------------------------------------------------------------------------------------------------

    def _edit(self, rule: RuleInfo | None) -> None:
        dialog = RuleEditDialog(self._backend, self._accounts, self._folders(), rule, self)
        if dialog.exec() == RuleEditDialog.DialogCode.Accepted:
            self.reload()

    def new_rule(self) -> None:
        self._edit(None)

    def edit_selected(self) -> None:
        rule = self._selected()
        if rule is not None:
            self._edit(rule)

    def _double_clicked(self, item: QListWidgetItem) -> None:
        self.edit_selected()

    def delete_selected(self) -> None:
        rule = self._selected()
        if rule is None:
            return
        if QMessageBox.question(self, "Delete rule", f"Delete the rule “{rule.name}”? Mail that was already handled stays where it is.") != QMessageBox.StandardButton.Yes:
            return
        self._backend.ask(RULE_REMOVE, RuleIdParameters(rule.rule_id), lambda result: self.reload(), self._failed, owner=self)

    def move_selected(self, step: int) -> None:
        rule = self._selected()
        row = self.list.currentRow()
        if rule is None or not 0 <= row + step < self.list.count():
            return
        identifier = rule.rule_id
        self._backend.ask(RULE_MOVE, RuleMoveParameters(identifier, row + step), lambda result: self.reload(identifier), self._failed, owner=self)

    def run_now(self) -> None:
        rule, current = self._selected(), self._current()
        if rule is None or current is None:
            return
        account, folder = current
        self.status.setText("Applying…")

        def done(result: RuleRunResult) -> None:
            self.ran = True
            self.status.setText(f"{result.examined} message(s) looked at, {result.matched} matched, {result.actions} action(s) done in {folder}.")

        self._backend.ask(RULE_RUN, RuleRunParameters(account, folder, rule.rule_id), done, self._failed, owner=self)
