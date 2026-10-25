import html
from PySide6.QtGui import QPalette
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

from chrono_mail.engine.commands_pim import TagInfo
from chrono_mail.engine.commands_accounts import AccountSummary
from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_pim import TAG_LIST
from chrono_mail.engine.commands_rules import RULE_ADD, RULE_EDIT, RuleAddParameters, RuleEditParameters, RuleInfo
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import red
from chrono_mail.ui.icons import theme_icon

# --- Version ------------------------------------------------------------
__version__ = "ruleedit.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: an error text inside markup is escaped: a server's or a file's words are never drawn as markup (D-259)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.271: R-19: values gives RuleAddParameters; add and edit typed
# v1.1.174: Actions below Stop processing rules are shown greyed out (they are not done); they can still be changed and moved
# v1.0.171: Every action has Move up and Move down buttons (chevrons before its ✕); the order is the order the rule runs in
# v0.5.136: Tags: the condition Tag is / is not, the actions Add tag and Remove tag, each with the tags to choose from
# v0.0.69: Accounts are told apart by user name, incoming server and protocol
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.49: Rules without conditions for one account
# v0.0.33: Initial rule editor

FIELDS: tuple[tuple[str, str], ...] = (
    ("from", "From"), ("to", "To"), ("cc", "Cc"), ("subject", "Subject"), ("body", "Message text"),
    ("header", "Header"), ("size", "Size (KB)"), ("attachment", "Attachment"), ("tag", "Tag"),
)
TEXT_OPERATORS: tuple[tuple[str, str], ...] = (
    ("contains", "contains"), ("not_contains", "does not contain"), ("equals", "is"), ("not_equals", "is not"),
    ("starts_with", "starts with"), ("ends_with", "ends with"),
)
SIZE_OPERATORS: tuple[tuple[str, str], ...] = (("greater_than", "is larger than"), ("less_than", "is smaller than"))
ATTACHMENT_OPERATORS: tuple[tuple[str, str], ...] = (("is", "is"),)
TAG_OPERATORS: tuple[tuple[str, str], ...] = (("is", "is"), ("is_not", "is not"))
ACTIONS: tuple[tuple[str, str], ...] = (
    ("move_to", "Move to folder"), ("mark_read", "Mark as read"), ("mark_unread", "Mark as unread"), ("flag", "Flag"),
    ("unflag", "Remove the flag"), ("delete", "Move to Trash"), ("mark_junk", "Mark as junk"), ("add_tag", "Add tag"), ("remove_tag", "Remove tag"),
    ("stop", "Stop processing rules"),
)
TAG_ACTIONS: tuple[str, ...] = ("add_tag", "remove_tag")


def _tag_box(parent: QWidget) -> QComboBox:
    """The tags to choose from; a new name can be typed."""
    box = QComboBox(parent)
    box.setEditable(True)
    entry = box.lineEdit()
    if entry is not None:
        entry.setPlaceholderText("Tag")
    return box


def _fill_tags(box: QComboBox, names: list[str]) -> None:
    current = box.currentText()
    box.clear()
    box.addItems(names)
    box.setCurrentText(current)


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
        self.tag = _tag_box(self)
        self.remove_button = QPushButton("✕", self)
        self.remove_button.setFixedWidth(30)
        for widget in (self.field, self.header_name, self.operator, self.value, self.choice, self.tag, self.remove_button):
            layout.addWidget(widget)
        self.field.currentIndexChanged.connect(self._field_changed)
        self._field_changed()

    def _field_changed(self) -> None:
        kind = str(self.field.currentData())
        operators = SIZE_OPERATORS if kind == "size" else ATTACHMENT_OPERATORS if kind == "attachment" else TAG_OPERATORS if kind == "tag" else TEXT_OPERATORS
        self.operator.clear()
        for value, label in operators:
            self.operator.addItem(label, value)
        self.header_name.setVisible(kind == "header")
        self.value.setVisible(kind not in ("attachment", "tag"))
        self.choice.setVisible(kind == "attachment")
        self.tag.setVisible(kind == "tag")
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
        elif str(self.field.currentData()) == "tag":
            self.tag.setCurrentText(value)
        else:
            self.value.setText(value)

    def set_tags(self, names: list[str]) -> None:
        _fill_tags(self.tag, names)

    def is_blank(self) -> bool:
        """A row in which nothing was typed is left out (a text, header or size condition needs a value)."""
        kind = str(self.field.currentData())
        if kind == "tag":
            return not self.tag.currentText().strip()
        return kind not in ("attachment",) and not self.value.text().strip() and (kind != "header" or not self.header_name.text().strip())

    def condition(self) -> str:
        kind = str(self.field.currentData())
        name = f"header.{self.header_name.text().strip()}" if kind == "header" else kind
        value = str(self.choice.currentData()) if kind == "attachment" else self.tag.currentText().strip() if kind == "tag" else self.value.text().strip()
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
        self.tag = _tag_box(self)
        self.up_button = QPushButton(theme_icon("arrow-up"), "", self)
        self.up_button.setToolTip("Move up")
        self.down_button = QPushButton(theme_icon("arrow-down"), "", self)
        self.down_button.setToolTip("Move down")
        self.remove_button = QPushButton("✕", self)
        for button in (self.up_button, self.down_button, self.remove_button):
            button.setFixedWidth(30)
        for widget in (self.kind, self.folder, self.tag, self.up_button, self.down_button, self.remove_button):
            layout.addWidget(widget)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    def _kind_changed(self) -> None:
        self.folder.setVisible(str(self.kind.currentData()) == "move_to")
        self.tag.setVisible(str(self.kind.currentData()) in TAG_ACTIONS)

    def set_action(self, text: str) -> None:
        kind, _, value = text.partition(":")
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        if kind in TAG_ACTIONS:
            self.tag.setCurrentText(value)
        else:
            self.folder.setCurrentText(value)

    def set_tags(self, names: list[str]) -> None:
        _fill_tags(self.tag, names)

    def set_skipped(self, skipped: bool) -> None:
        """Grey out the action when a Stop above it means it is never done; it can still be changed and moved."""
        for widget in (self.kind, self.folder, self.tag):
            if not skipped:
                widget.setPalette(QPalette())  # back to the window's own colours
                continue
            palette = QPalette(self.palette())
            for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
                for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
                    palette.setColor(group, role, palette.color(QPalette.ColorGroup.Disabled, role))
            widget.setPalette(palette)

    def action(self) -> str:
        kind = str(self.kind.currentData())
        if kind in TAG_ACTIONS:
            return f"{kind}:{self.tag.currentText().strip()}"
        return f"move_to:{self.folder.currentText().strip()}" if kind == "move_to" else kind


class RuleEditDialog(QDialog):
    """Makes or changes a rule: a name, conditions, actions and the account(s) it is for."""

    def __init__(self, backend: Backend, accounts: list[AccountSummary], folders: list[str], rule: RuleInfo | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._folders: list[str] = folders
        self._rule_id: str | None = None if rule is None else rule.rule_id
        self.setWindowTitle("Rule" if rule is None else f"Rule: {rule.name}")
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
            self.account.addItem(item.label, item.account_id)
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
        self._tag_names: list[str] = []
        backend.ask(TAG_LIST, NoParameters(), self._tags_loaded, lambda error: None, owner=self)  # without them a tag is simply typed
        if rule is None:
            self.add_condition()
            self.add_action()
        else:
            self.name.setText(rule.name)
            self.match.setCurrentIndex(max(0, self.match.findData(rule.match)))
            accounts_of_rule = tuple(rule.accounts)
            if accounts_of_rule:
                self.account.setCurrentIndex(max(0, self.account.findData(accounts_of_rule[0])))
            for text in rule.conditions:
                self.add_condition(text)
            for text in rule.actions:
                self.add_action(text)

    def _tags_loaded(self, result: tuple[TagInfo, ...]) -> None:
        tags = tuple(result) if isinstance(result, (tuple, list)) else ()
        self._tag_names = [tag.name for tag in tags if "mail" in tuple(tag.kinds)]
        for row in (*self.condition_rows, *self.action_rows):
            row.set_tags(self._tag_names)

    def add_condition(self, text: str | None = None) -> ConditionRow:
        row = ConditionRow(self)
        row.set_tags(self._tag_names)
        if text is not None:
            row.set_condition(text)
        self.condition_rows.append(row)
        self.conditions_layout.addWidget(row)
        row.remove_button.clicked.connect(lambda: self._remove_condition(row))
        return row

    def add_action(self, text: str | None = None) -> ActionRow:
        row = ActionRow(self._folders, self)
        row.set_tags(self._tag_names)
        if text is not None:
            row.set_action(text)
        self.action_rows.append(row)
        self.actions_layout.addWidget(row)
        row.remove_button.clicked.connect(lambda: self._remove_action(row))
        row.up_button.clicked.connect(lambda: self.move_action(row, -1))
        row.down_button.clicked.connect(lambda: self.move_action(row, 1))
        row.kind.currentIndexChanged.connect(self._update_skipped)
        self._update_moves()
        return row

    def move_action(self, row: ActionRow, step: int) -> None:
        """One place up (-1) or down (1): the order is the order the rule saves and runs its actions in."""
        place = self.action_rows.index(row) + step
        if not 0 <= place < len(self.action_rows):
            return
        self.action_rows.remove(row)
        self.action_rows.insert(place, row)
        self.actions_layout.removeWidget(row)
        self.actions_layout.insertWidget(place, row)
        self._update_moves()

    def _update_moves(self) -> None:
        """The first action cannot go up, the last cannot go down."""
        for place, row in enumerate(self.action_rows):
            row.up_button.setEnabled(place > 0)
            row.down_button.setEnabled(place < len(self.action_rows) - 1)
        self._update_skipped()

    def _update_skipped(self) -> None:
        """The actions below the first Stop are not done, so they are shown greyed out."""
        stopped = False
        for row in self.action_rows:
            row.set_skipped(stopped)
            stopped = stopped or str(row.kind.currentData()) == "stop"

    def _remove_condition(self, row: ConditionRow) -> None:
        self.condition_rows.remove(row)  # a rule may have none: it then acts on all the mail of its account
        row.setParent(None)
        row.deleteLater()

    def _remove_action(self, row: ActionRow) -> None:
        if len(self.action_rows) > 1:
            self.action_rows.remove(row)
            row.setParent(None)
            row.deleteLater()
            self._update_moves()

    def values(self) -> RuleAddParameters:
        account = str(self.account.currentData())
        return RuleAddParameters(
            name=self.name.text().strip(), condition=tuple(row.condition() for row in self.condition_rows if not row.is_blank()),
            action=tuple(row.action() for row in self.action_rows), match=str(self.match.currentData()), account=(account,) if account else (),
        )

    def _save(self) -> None:
        values = self.values()
        self.save_button.setEnabled(False)
        self.result_label.setText("Saving…")

        def done(result: RuleInfo) -> None:
            self.accept()

        def failed(error: ChronoMailError) -> None:
            self.save_button.setEnabled(True)
            self.result_label.setText(f"<span style='color:{red()}'>{html.escape(str(error))}</span>")

        if self._rule_id is None:
            self._backend.ask(RULE_ADD, values, done, failed, owner=self)
        else:
            edit = RuleEditParameters(
                self._rule_id, name=values.name, condition=values.condition, action=values.action, match=values.match, account=values.account,
                every_account=not values.account, no_conditions=not values.condition,
            )
            self._backend.ask(RULE_EDIT, edit, done, failed, owner=self)


def explain(parent: QWidget | None, title: str, error: ChronoMailError) -> None:
    QMessageBox.warning(parent, title, str(error))
