from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend

# --- Version ------------------------------------------------------------
__version__ = "ruleedit.py 0.0.49"

# --- Version history ----------------------------------------------------
# v0.0.49: Rules without conditions for one account
# v0.0.33: Initial rule editor

FIELDS: tuple[tuple[str, str], ...] = (
    ("from", "From"), ("to", "To"), ("cc", "Cc"), ("subject", "Subject"), ("body", "Message text"),
    ("header", "Header"), ("size", "Size (KB)"), ("attachment", "Attachment"),
)
TEXT_OPERATORS: tuple[tuple[str, str], ...] = (
    ("contains", "contains"), ("not_contains", "does not contain"), ("equals", "is"), ("not_equals", "is not"),
    ("starts_with", "starts with"), ("ends_with", "ends with"),
)
SIZE_OPERATORS: tuple[tuple[str, str], ...] = (("greater_than", "is larger than"), ("less_than", "is smaller than"))
ATTACHMENT_OPERATORS: tuple[tuple[str, str], ...] = (("is", "is"),)
ACTIONS: tuple[tuple[str, str], ...] = (
    ("move_to", "Move to folder"), ("mark_read", "Mark as read"), ("mark_unread", "Mark as unread"), ("flag", "Flag"),
    ("unflag", "Remove the flag"), ("delete", "Move to Trash"), ("mark_junk", "Mark as junk"), ("stop", "Stop processing rules"),
)


class ConditionRow(QWidget):
    """One condition: a field, an operator and a value."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.field = QComboBox(self)
        for value, label in FIELDS:
            self.field.addItem(label, value)
        self.header_name = QLineEdit(self)
        self.header_name.setPlaceholderText("Header name, e.g. List-Id")
        self.operator = QComboBox(self)
        self.value = QLineEdit(self)
        self.choice = QComboBox(self)  # yes or no, for attachments
        self.choice.addItem("yes", "yes")
        self.choice.addItem("no", "no")
        self.remove_button = QPushButton("✕", self)
        self.remove_button.setFixedWidth(30)
        for widget in (self.field, self.header_name, self.operator, self.value, self.choice, self.remove_button):
            layout.addWidget(widget)
        self.field.currentIndexChanged.connect(self._field_changed)
        self._field_changed()

    def _field_changed(self) -> None:
        kind = str(self.field.currentData())
        operators = SIZE_OPERATORS if kind == "size" else ATTACHMENT_OPERATORS if kind == "attachment" else TEXT_OPERATORS
        self.operator.clear()
        for value, label in operators:
            self.operator.addItem(label, value)
        self.header_name.setVisible(kind == "header")
        self.value.setVisible(kind != "attachment")
        self.choice.setVisible(kind == "attachment")
        self.value.setPlaceholderText("kilobytes" if kind == "size" else "text")

    def set_condition(self, text: str) -> None:
        """Fill the row from the written form field:operator:value (header.Name:operator:value)."""
        field_text, operator, value = (text.split(":", 2) + ["", ""])[:3]
        if field_text.lower().startswith("header."):
            self.field.setCurrentIndex(self.field.findData("header"))
            self.header_name.setText(field_text[len("header.") :])
        else:
            self.field.setCurrentIndex(max(0, self.field.findData(field_text)))
        self.operator.setCurrentIndex(max(0, self.operator.findData(operator)))
        if str(self.field.currentData()) == "attachment":
            self.choice.setCurrentIndex(max(0, self.choice.findData(value)))
        else:
            self.value.setText(value)

    def is_blank(self) -> bool:
        """A row in which nothing was typed is left out (a text, header or size condition needs a value)."""
        kind = str(self.field.currentData())
        return kind not in ("attachment",) and not self.value.text().strip() and (kind != "header" or not self.header_name.text().strip())

    def condition(self) -> str:
        kind = str(self.field.currentData())
        name = f"header.{self.header_name.text().strip()}" if kind == "header" else kind
        value = str(self.choice.currentData()) if kind == "attachment" else self.value.text().strip()
        return f"{name}:{self.operator.currentData()}:{value}"


class ActionRow(QWidget):
    """One action; moving to a folder also asks which."""

    def __init__(self, folders: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.kind = QComboBox(self)
        for value, label in ACTIONS:
            self.kind.addItem(label, value)
        self.folder = QComboBox(self)
        self.folder.setEditable(True)
        self.folder.addItems(folders)
        self.folder.setCurrentText("")
        entry = self.folder.lineEdit()
        if entry is not None:
            entry.setPlaceholderText("Folder, e.g. Archive/2026")
        self.remove_button = QPushButton("✕", self)
        self.remove_button.setFixedWidth(30)
        for widget in (self.kind, self.folder, self.remove_button):
            layout.addWidget(widget)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    def _kind_changed(self) -> None:
        self.folder.setVisible(str(self.kind.currentData()) == "move_to")

    def set_action(self, text: str) -> None:
        kind, _, value = text.partition(":")
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        self.folder.setCurrentText(value)

    def action(self) -> str:
        kind = str(self.kind.currentData())
        return f"move_to:{self.folder.currentText().strip()}" if kind == "move_to" else kind


class RuleEditDialog(QDialog):
    """Makes or changes a rule: a name, conditions, actions and the account(s) it is for."""

    def __init__(self, backend: Backend, accounts: list[object], folders: list[str], rule: object | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._folders: list[str] = folders
        self._rule_id: str | None = None if rule is None else str(getattr(rule, "rule_id"))
        self.setWindowTitle("Rule" if rule is None else f"Rule: {getattr(rule, 'name')}")
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(self)
        self.match = QComboBox(self)
        self.match.addItem("All of these conditions are true", "all")
        self.match.addItem("Any of these conditions is true", "any")
        self.account = QComboBox(self)
        self.account.addItem("All accounts", "")
        for item in accounts:
            self.account.addItem(str(getattr(item, "email_address")), str(getattr(item, "account_id")))
        form.addRow("Name", self.name)
        form.addRow("Conditions", self.match)
        form.addRow("For", self.account)
        layout.addLayout(form)
        self.conditions_layout = QVBoxLayout()
        layout.addLayout(self.conditions_layout)
        self.add_condition_button = QPushButton("Add a condition", self)
        layout.addWidget(self.add_condition_button)
        layout.addWidget(QLabel("Then:", self))
        self.actions_layout = QVBoxLayout()
        layout.addLayout(self.actions_layout)
        self.add_action_button = QPushButton("Add an action", self)
        layout.addWidget(self.add_action_button)
        self.result_label = QLabel("", self)
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.add_condition_button.clicked.connect(lambda: self.add_condition())
        self.add_action_button.clicked.connect(lambda: self.add_action())
        self.condition_rows: list[ConditionRow] = []
        self.action_rows: list[ActionRow] = []
        if rule is None:
            self.add_condition()
            self.add_action()
        else:
            self.name.setText(str(getattr(rule, "name")))
            self.match.setCurrentIndex(max(0, self.match.findData(str(getattr(rule, "match")))))
            accounts_of_rule = tuple(getattr(rule, "accounts"))
            if accounts_of_rule:
                self.account.setCurrentIndex(max(0, self.account.findData(accounts_of_rule[0])))
            for text in getattr(rule, "conditions"):
                self.add_condition(str(text))
            for text in getattr(rule, "actions"):
                self.add_action(str(text))

    def add_condition(self, text: str | None = None) -> ConditionRow:
        row = ConditionRow(self)
        if text is not None:
            row.set_condition(text)
        self.condition_rows.append(row)
        self.conditions_layout.addWidget(row)
        row.remove_button.clicked.connect(lambda: self._remove_condition(row))
        return row

    def add_action(self, text: str | None = None) -> ActionRow:
        row = ActionRow(self._folders, self)
        if text is not None:
            row.set_action(text)
        self.action_rows.append(row)
        self.actions_layout.addWidget(row)
        row.remove_button.clicked.connect(lambda: self._remove_action(row))
        return row

    def _remove_condition(self, row: ConditionRow) -> None:
        self.condition_rows.remove(row)  # a rule may have none: it then acts on all the mail of its account
        row.setParent(None)
        row.deleteLater()

    def _remove_action(self, row: ActionRow) -> None:
        if len(self.action_rows) > 1:
            self.action_rows.remove(row)
            row.setParent(None)
            row.deleteLater()

    def values(self) -> dict[str, object]:
        values: dict[str, object] = {
            "name": self.name.text().strip(),
            "condition": [row.condition() for row in self.condition_rows if not row.is_blank()],
            "action": [row.action() for row in self.action_rows],
            "match": str(self.match.currentData()),
        }
        if str(self.account.currentData()):
            values["account"] = [str(self.account.currentData())]
        return values

    def _save(self) -> None:
        values = self.values()
        self.save_button.setEnabled(False)
        self.result_label.setText("Saving…")

        def done(result: object) -> None:
            self.accept()

        def failed(error: ChronoMailError) -> None:
            self.save_button.setEnabled(True)
            self.result_label.setText(f"<span style='color:#c0392b'>{error}</span>")

        if self._rule_id is None:
            self._backend.call("rule.add", values, done, failed, owner=self)
        else:
            edit = {**values, "rule": self._rule_id}
            if not values["condition"]:
                edit["no_conditions"] = True
            if "account" not in edit:
                edit["every_account"] = True
            self._backend.call("rule.edit", edit, done, failed, owner=self)


def explain(parent: QWidget | None, title: str, error: ChronoMailError) -> None:
    QMessageBox.warning(parent, title, str(error))
