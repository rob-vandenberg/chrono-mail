import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QDialog, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.ui.ruleedit import ActionRow, ConditionRow, RuleEditDialog
from chrono_mail.ui.rulespanel import RulesPanel
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_rules.py 2.4.333"

# --- Version history ----------------------------------------------------
# v2.4.333: the counts in the texts are written one way ("1 message", "3 messages"), never "message(s)"
# v1.9.271: The rule editor's values are typed (R-19)
# v1.9.265: the Menu items are checked in test_ui_theme.py only (this copy had fallen behind, R-44)
# v1.1.200: The Rules and Scheduled messages pages are found by name (their places moved when pages were added)
# v1.1.174: actions below Stop are greyed out and stay editable; the rules button is called Edit…
# v1.0.171: actions move up and down, in the order they are saved
# v0.5.136: the tag condition and the tag actions in the editor; the Menu has Tags…
# v0.0.76: The Menu has Settings, Accounts, Rules and Exit in its root
# v0.0.73: The menus are inside one Menu button
# v0.0.69: Accounts are told apart by user name, incoming server and protocol
# v0.0.50: Rules and scheduled messages are pages of the Settings window
# v0.0.49: Rules without conditions for one account
# v0.0.33: Initial rules window tests


def accounts(world: World) -> list[object]:
    return list(world.call("account.list"))


def editor(world: World, qtbot: QtBot, rule: object | None = None) -> RuleEditDialog:
    dialog = RuleEditDialog(world.backend, accounts(world), ["Inbox", "Archive", "Archive/2026"], rule)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def test_a_condition_row_follows_its_field(qtbot: QtBot) -> None:
    row = ConditionRow()
    qtbot.addWidget(row)
    row.show()
    assert row.operator.count() == 6 and not row.header_name.isHidden() is False and row.choice.isHidden()
    row.field.setCurrentIndex(row.field.findData("header"))
    assert not row.header_name.isHidden()
    row.header_name.setText("List-Id")
    row.value.setText("lists.example.org")
    assert row.condition() == "header.List-Id:contains:lists.example.org"
    row.field.setCurrentIndex(row.field.findData("size"))
    assert [row.operator.itemData(index) for index in range(row.operator.count())] == ["greater_than", "less_than"] and row.header_name.isHidden()
    row.value.setText("100")
    assert row.condition() == "size:greater_than:100"
    row.field.setCurrentIndex(row.field.findData("attachment"))
    assert row.value.isHidden() and not row.choice.isHidden()
    row.choice.setCurrentIndex(1)
    assert row.condition() == "attachment:is:no"


def test_rows_can_be_filled_from_the_written_form(qtbot: QtBot) -> None:
    row = ConditionRow()
    qtbot.addWidget(row)
    for text in ("from:starts_with:news@", "header.X-Mailer:not_equals:bulk", "size:less_than:5", "attachment:is:yes", "subject:contains:a:b"):
        row.set_condition(text)
        assert row.condition() == text
    action = ActionRow(["Inbox"])
    qtbot.addWidget(action)
    for text in ("move_to:Archive/2026", "mark_read", "stop", "delete"):
        action.set_action(text)
        assert action.action() == text
    action.set_action("mark_junk")
    assert action.folder.isHidden()


def test_a_new_rule_is_saved_through_the_command(world: World, qtbot: QtBot) -> None:
    dialog = editor(world, qtbot)
    dialog.name.setText("Newsletters")
    dialog.condition_rows[0].field.setCurrentIndex(dialog.condition_rows[0].field.findData("from"))
    dialog.condition_rows[0].value.setText("news@")
    dialog.add_condition("subject:contains:weekly")
    dialog.match.setCurrentIndex(dialog.match.findData("any"))
    dialog.action_rows[0].kind.setCurrentIndex(dialog.action_rows[0].kind.findData("move_to"))
    dialog.action_rows[0].folder.setCurrentText("Archive/2026")
    dialog.add_action("mark_read")
    dialog.account.setCurrentIndex(1)
    dialog.save_button.click()
    wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
    (rule,) = world.call("rule.list")
    assert (rule.name, rule.match, rule.conditions, rule.actions) == ("Newsletters", "any", ("from:contains:news@", "subject:contains:weekly"), ("move_to:Archive/2026", "mark_read"))
    assert rule.accounts == (world.account_id,)


def test_a_mistake_is_shown_and_the_dialog_stays_open(world: World, qtbot: QtBot) -> None:
    dialog = editor(world, qtbot)
    dialog.name.setText("")
    dialog.condition_rows[0].value.setText("x")
    dialog.action_rows[0].kind.setCurrentIndex(dialog.action_rows[0].kind.findData("mark_read"))
    dialog.save_button.click()
    wait(qtbot, lambda: "name" in dialog.result_label.text())
    assert dialog.isVisible() and dialog.save_button.isEnabled() and world.call("rule.list") == ()
    dialog.name.setText("Fine")
    dialog.condition_rows[0].value.setText("")  # a row without a value is left out: no conditions and no account would act on all mail
    dialog.save_button.click()
    wait(qtbot, lambda: "must be for one account" in dialog.result_label.text())
    dialog.condition_rows[0].value.setText("x")
    dialog.action_rows[0].kind.setCurrentIndex(dialog.action_rows[0].kind.findData("move_to"))  # and a move needs a folder
    dialog.save_button.click()
    wait(qtbot, lambda: "needs a folder" in dialog.result_label.text())


def test_rows_can_be_removed_but_one_action_is_kept(world: World, qtbot: QtBot) -> None:
    dialog = editor(world, qtbot)
    second = dialog.add_condition()
    dialog.add_action()
    assert len(dialog.condition_rows) == 2 and len(dialog.action_rows) == 2
    second.remove_button.click()
    dialog.action_rows[1].remove_button.click()
    assert len(dialog.condition_rows) == 1 and len(dialog.action_rows) == 1
    dialog.condition_rows[0].remove_button.click()
    dialog.action_rows[0].remove_button.click()
    assert len(dialog.condition_rows) == 0 and len(dialog.action_rows) == 1  # a rule may have no conditions, never no action


def test_actions_move_up_and_down_and_are_saved_in_that_order(world: World, qtbot: QtBot) -> None:
    dialog = editor(world, qtbot)
    dialog.action_rows[0].set_action("move_to:Archive")
    dialog.add_action("stop")
    dialog.add_action("mark_read")
    first, stop, last = dialog.action_rows
    assert not first.up_button.isEnabled() and first.down_button.isEnabled()  # the first cannot go up
    assert stop.up_button.isEnabled() and stop.down_button.isEnabled()
    assert last.up_button.isEnabled() and not last.down_button.isEnabled()  # the last cannot go down
    assert not first.up_button.icon().isNull() and first.up_button.toolTip() == "Move up" and first.down_button.toolTip() == "Move down"
    last.up_button.click()  # Mark as read before Stop: it is done again
    assert dialog.values().action == ("move_to:Archive", "mark_read", "stop")
    assert dialog.actions_layout.indexOf(last) == 1 and not stop.down_button.isEnabled() and last.down_button.isEnabled()
    first.down_button.click()
    first.down_button.click()
    assert dialog.values().action == ("mark_read", "stop", "move_to:Archive") and not first.down_button.isEnabled()
    first.down_button.click()  # the last one stays last
    assert dialog.values().action == ("mark_read", "stop", "move_to:Archive")
    stop.remove_button.click()
    assert not last.up_button.isEnabled() and last.down_button.isEnabled() and first.up_button.isEnabled() and not first.down_button.isEnabled()  # set again after a remove


def greyed(row: ActionRow) -> bool:
    """The action's text has the colour of greyed-out text, in both the choice of action and its folder."""
    kind, folder = row.kind.palette(), row.folder.palette()
    return (
        kind.color(QPalette.ColorGroup.Active, QPalette.ColorRole.ButtonText) == kind.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText)
        and folder.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text) == folder.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
    )


def test_actions_below_stop_are_greyed_out_and_follow_every_change(world: World, qtbot: QtBot) -> None:
    dialog = editor(world, qtbot)
    dialog.action_rows[0].set_action("mark_read")
    dialog.add_action("stop")
    dialog.add_action("move_to:Archive")
    first, stop, last = dialog.action_rows
    assert not greyed(first) and not greyed(stop) and greyed(last)  # Stop itself is done, what follows it is not
    assert last.isEnabled() and last.kind.isEnabled() and last.folder.isEnabled()  # still editable
    dialog.add_action("flag")
    assert greyed(dialog.action_rows[3])  # a new action lands below Stop
    stop.down_button.click()
    assert not greyed(last) and greyed(dialog.action_rows[3])  # follows a move at once
    first.kind.setCurrentIndex(first.kind.findData("stop"))
    assert greyed(last) and greyed(stop) and not greyed(first)  # the first Stop counts
    first.kind.setCurrentIndex(first.kind.findData("mark_read"))
    stop.remove_button.click()
    assert not any(greyed(row) for row in dialog.action_rows)  # no Stop: everything is done
    assert dialog.values().action == ("mark_read", "move_to:Archive", "flag")


def test_a_rule_for_one_account_needs_no_conditions(world: World, qtbot: QtBot) -> None:
    dialog = editor(world, qtbot)
    dialog.name.setText("Everything of this account")
    dialog.account.setCurrentIndex(dialog.account.findData(world.account_id))
    dialog.action_rows[0].kind.setCurrentIndex(dialog.action_rows[0].kind.findData("mark_read"))
    dialog.save_button.click()  # the empty condition row is ignored
    wait(qtbot, lambda: len(world.call("rule.list")) == 1)
    rule = world.call("rule.list")[0]
    assert rule.conditions == () and rule.accounts == (world.account_id,)
    again = editor(world, qtbot, rule)
    assert again.condition_rows == []
    added = again.add_condition()
    added.value.setText("x")
    written = added.condition()
    again.save_button.click()
    wait(qtbot, lambda: world.call("rule.list")[0].conditions == (written,))
    third = editor(world, qtbot, world.call("rule.list")[0])
    third.condition_rows[0].remove_button.click()
    third.save_button.click()
    wait(qtbot, lambda: world.call("rule.list")[0].conditions == ())


def test_a_rule_is_changed_in_the_editor(world: World, qtbot: QtBot) -> None:
    created = world.call("rule.add", name="Old", condition=["subject:contains:a"], action=["flag"], account=[world.account_id])
    dialog = editor(world, qtbot, created)
    assert dialog.name.text() == "Old" and dialog.account.currentData() == world.account_id and dialog.condition_rows[0].condition() == "subject:contains:a"
    dialog.name.setText("New")
    dialog.account.setCurrentIndex(0)  # every account
    dialog.save_button.click()
    wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
    (rule,) = world.call("rule.list")
    assert rule.name == "New" and rule.accounts == () and rule.rule_id == created.rule_id


def open_rules(world: World, qtbot: QtBot, count: int) -> RulesPanel:
    dialog = RulesPanel(world.backend, accounts(world), lambda: ["Inbox", "Archive"], lambda: (world.account_id, "Inbox"))
    qtbot.addWidget(dialog)
    dialog.show()
    wait(qtbot, lambda: dialog.list.count() == count)
    return dialog


def test_the_rules_list_switches_reorders_and_deletes(world: World, qtbot: QtBot) -> None:
    first = world.call("rule.add", name="First", condition=["subject:contains:a"], action=["flag"])
    world.call("rule.add", name="Second", condition=["subject:contains:b"], action=["mark_read"])
    dialog = open_rules(world, qtbot, 2)
    assert dialog.list.item(0).text().startswith("First") and not dialog.up_button.isEnabled() and dialog.down_button.isEnabled()
    dialog.list.item(0).setCheckState(dialog.list.item(0).checkState().Unchecked)
    wait(qtbot, lambda: not world.call("rule.list")[0].enabled)
    dialog.list.setCurrentRow(1)
    assert dialog.up_button.isEnabled() and not dialog.down_button.isEnabled()
    dialog.up_button.click()
    wait(qtbot, lambda: [rule.name for rule in world.call("rule.list")] == ["Second", "First"])
    wait(qtbot, lambda: dialog.list.currentItem() is not None and dialog.list.currentItem().text().startswith("Second"))
    world.dialogs.answer = QMessageBox.StandardButton.No
    dialog.delete_button.click()
    qtbot.wait(100)
    assert len(world.call("rule.list")) == 2
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    dialog.delete_button.click()
    wait(qtbot, lambda: dialog.list.count() == 1)
    assert world.call("rule.list")[0].rule_id == first.rule_id


def test_a_rule_can_be_applied_to_the_current_folder(world: World, qtbot: QtBot) -> None:
    world.call("rule.add", name="Read all", condition=["subject:contains:message"], action=["mark_read"])
    dialog = open_rules(world, qtbot, 1)
    dialog.run_button.click()
    wait(qtbot, lambda: "3 messages looked at, 3 matched" in dialog.status.text())
    assert dialog.ran and all(row.flags == ("seen",) for row in world.call("message.list", account=world.account_id, folder="Inbox").messages)


def test_with_no_rules_the_buttons_that_need_one_are_off(world: World, qtbot: QtBot) -> None:
    dialog = open_rules(world, qtbot, 0)
    assert not any(button.isEnabled() for button in (dialog.edit_button, dialog.delete_button, dialog.up_button, dialog.down_button, dialog.run_button)) and dialog.new_button.isEnabled()
    assert dialog.edit_button.text() == "Edit…"


def test_the_rules_are_a_page_of_the_settings(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.ui.settingsdialog import SettingsDialog

    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    seen: list[SettingsDialog] = []

    def fake_exec(self: SettingsDialog) -> QDialog.DialogCode:
        seen.append(self)
        assert self.pages.currentWidget() is self.rules_panel  # the key for the rules opens the settings at that page
        assert self.rules_panel._current() == (world.account_id, "Inbox") and "Inbox" in self.rules_panel._folders()  # noqa: SLF001
        self.rules_panel.ran = True
        return QDialog.DialogCode.Rejected  # cancelled: what the rules did stays

    monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
    world.window.act_rules.trigger()
    pages = [seen[0].sidebar.item(row).text() for row in range(seen[0].sidebar.count())] if seen else []
    assert len(seen) == 1 and "Rules" in pages and "Scheduled messages" in pages and pages.index("Rules") < pages.index("Scheduled messages")
    wait(qtbot, lambda: "Rules applied" in world.window.status_label.text())


def test_accounts_with_the_same_address_are_told_apart_in_the_rule_editor(world: World, qtbot: QtBot) -> None:
    world.call("account.add", display_name="Me", email_address="me@example.com", username="other", password="secret", incoming_host="127.0.0.1", incoming_port=world.pop.port, incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=world.smtp.port, outgoing_security="none", kind="imap")
    accounts = list(world.call("account.list"))
    dialog = RuleEditDialog(world.backend, accounts, [])
    qtbot.addWidget(dialog)
    labels = [dialog.account.itemText(index) for index in range(1, dialog.account.count())]
    assert len(labels) == 2 and len(set(labels)) == 2 and all(" - " in label for label in labels) and labels[1].endswith("(IMAP)")


def test_tags_can_be_chosen_in_conditions_and_actions(world: World, qtbot: QtBot) -> None:
    world.call("tag.create", name="Work")
    dialog = editor(world, qtbot)
    wait(qtbot, lambda: dialog.condition_rows[0].tag.count() == 1)
    condition, action = dialog.condition_rows[0], dialog.action_rows[0]
    condition.field.setCurrentIndex(condition.field.findData("tag"))
    assert condition.tag.isVisibleTo(dialog) and not condition.value.isVisibleTo(dialog)
    assert [condition.operator.itemData(index) for index in range(condition.operator.count())] == ["is", "is_not"]
    assert condition.is_blank()
    condition.tag.setCurrentText("Work")
    assert condition.condition() == "tag:is:Work" and not condition.is_blank()
    action.kind.setCurrentIndex(action.kind.findData("remove_tag"))
    assert action.tag.isVisibleTo(dialog) and not action.folder.isVisibleTo(dialog)
    action.tag.setCurrentText("New one")
    assert action.action() == "remove_tag:New one"
    other = dialog.add_action("add_tag:Work")
    assert other.action() == "add_tag:Work" and other.tag.count() == 1

