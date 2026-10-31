import pytest
from PySide6.QtWidgets import QDialog, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.engine.changes import Change
from chrono_mail.engine.commands_accounts import AccountSummary
from chrono_mail.engine.commands_rules import RuleInfo
from chrono_mail.ui.ruleedit import RuleEditDialog
from chrono_mail.ui.rulespanel import RulesPanel
from tests.ui.world import World, answer_input, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_rule_folders.py 4.2.453"

# --- Version history ----------------------------------------------------
# v4.2.453: Initial version: Rename folder and Delete folder… say what the rules did, a rule change found by a check is told in the status
#           line, and Settings > Rules on screen shows the rules as they are now; the rule editor keeps a rule of several accounts and follows
#           a rename made while it is open (D-302)


def started_with_projects(qtbot: QtBot, world: World) -> None:
    """The window on screen, the folder Projects selected, and a rule that moves mail to it."""
    world.window.start()
    settle(qtbot, world, 3)
    world.call("folder.create", account=world.account_id, folder="Projects")
    world.call("rule.add", name="Projects rule", condition=["subject:contains:x"], action=["move_to:Projects"])
    wait(qtbot, lambda: world.window.folders.find_item((world.account_id, "folder", "Projects")) is not None)  # noqa: SLF001
    projects = world.window.folders.find_item((world.account_id, "folder", "Projects"))  # noqa: SLF001
    assert projects is not None
    world.window.tree.setCurrentIndex(projects)
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001 - its (empty) list loaded: a load still on its way when the folder is renamed fails and says so in red


def test_rename_folder_says_where_the_rules_move_mail_now(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started_with_projects(qtbot, world)
    answer_input(monkeypatch, [("Work", True)])
    world.window.folders.rename_folder()
    wait(qtbot, lambda: world.window.status_label.text() == "Folder renamed. Rule “Projects rule” now moves mail to Work")


def test_delete_folder_says_which_rules_are_switched_off(world: World, qtbot: QtBot) -> None:
    started_with_projects(qtbot, world)
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    world.window.folders.delete_folder()
    wait(qtbot, lambda: world.window.status_label.text() == "Folder deleted. Rule “Projects rule” is switched off: its folder Projects was deleted")


def test_a_rule_change_found_by_a_check_is_told_in_the_status_line(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    world.backend.changed.emit(Change("rules_changed", world.account_id, "Projects", notes=("Rule “A” now moves mail to Work", "Rule “B” now moves mail to Work")))
    assert world.window.status_label.text() == "Rule “A” now moves mail to Work. Rule “B” now moves mail to Work"


def test_the_rules_page_on_screen_shows_the_rules_as_they_are_now(world: World, qtbot: QtBot) -> None:
    world.call("folder.create", account=world.account_id, folder="Projects")
    world.call("rule.add", name="Projects rule", condition=["subject:contains:x"], action=["move_to:Projects"])
    panel = RulesPanel(world.backend, [], lambda: ["Inbox", "Projects"], lambda: None)
    qtbot.addWidget(panel)
    panel.show()
    wait(qtbot, lambda: panel.list.count() == 1 and "move_to:Projects" in panel.list.item(0).text())
    world.call("folder.rename", account=world.account_id, folder="Projects", new_name="Work")
    wait(qtbot, lambda: panel.list.count() == 1 and "move_to:Work" in panel.list.item(0).text())


def mail_accounts(world: World) -> list[AccountSummary]:
    return [item for item in world.call("account.list") if isinstance(item, AccountSummary)]


def the_rule(world: World) -> RuleInfo:
    (rule,) = [rule for rule in world.call("rule.list") if isinstance(rule, RuleInfo)]
    return rule


def editor(world: World, qtbot: QtBot) -> RuleEditDialog:
    dialog = RuleEditDialog(world.backend, mail_accounts(world), ["Inbox", "Projects"], the_rule(world))
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def test_the_rule_editor_keeps_every_account_of_a_rule_of_several(world: World, qtbot: QtBot) -> None:
    world.call(
        "account.add", display_name="Me", email_address="second@example.com", username="user", password="secret", incoming_host="localhost",
        incoming_port=world.pop.port, incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=world.smtp.port, outgoing_security="none",
    )
    accounts = mail_accounts(world)
    both = tuple(item.account_id for item in accounts[:2])
    world.call("rule.add", name="Split one", condition=["subject:contains:x"], action=["move_to:Projects"], account=list(both))
    dialog = editor(world, qtbot)
    assert dialog.account.currentText() == ", ".join(item.label for item in accounts[:2])
    dialog.name.setText("Renamed rule")
    dialog.save_button.click()
    wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
    assert (the_rule(world).name, the_rule(world).accounts) == ("Renamed rule", both)


def test_the_rule_editor_follows_a_folder_renamed_while_it_is_open(world: World, qtbot: QtBot) -> None:
    world.call("folder.create", account=world.account_id, folder="Projects")
    world.call("rule.add", name="Projects rule", condition=["subject:contains:x"], action=["move_to:Projects", "mark_read"])
    dialog = editor(world, qtbot)
    world.call("folder.rename", account=world.account_id, folder="Projects", new_name="Work")  # a mail check or the main window
    wait(qtbot, lambda: dialog.action_rows[0].action() == "move_to:Work")
    assert dialog.action_rows[1].action() == "mark_read"
    dialog.save_button.click()
    wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
    assert the_rule(world).actions == ("move_to:Work", "mark_read")  # Save never puts the old folder back
