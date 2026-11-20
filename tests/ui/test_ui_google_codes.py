import dataclasses

import pytest

from PySide6.QtWidgets import QDialog, QFormLayout, QLabel, QLineEdit
from pytestqt.qtbot import QtBot

from chrono_mail.config import ServerRole
from chrono_mail.engine.commands_accounts import AccountSummary, StoredSecrets
from chrono_mail.ui.accountedit import AccountEditDialog
from chrono_mail.ui.passwordfield import STORED
from tests.test_account_names_google import CALDAV, CARDDAV, google
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_google_codes.py 4.6.648"

# --- Version history ----------------------------------------------------
# v4.6.648: the stored client secret shows as 16 dots; Save without typing keeps it; dots typed back are ignored (D-365)
# v4.6.646: Initial tests: a Google account's window shows Client ID and Client secret on its General tab, below Google account; Save
#           changes them for both accounts of the Google address (B-34, D-364)


def google_pair(world: World) -> tuple[AccountSummary, AccountSummary]:
    """Google Calendar and Google Contacts of one sign-in, with the codes the person typed when adding them."""
    calendar, contacts = (dataclasses.replace(google(url), google_client_id="old-id") for url in (CALDAV, CARDDAV))
    world.engine.change_config(lambda config: config.with_account(calendar).with_account(contacts))
    for item in (calendar, contacts):
        world.engine.passwords.set(item.account.account_id, ServerRole.GOOGLE_SECRET, "old-secret")
    listed = {summary.account_id: summary for summary in world.call("account.list")}
    return listed[calendar.account.account_id], listed[contacts.account.account_id]


def label_of(form: QFormLayout, field: QLineEdit) -> str:
    label = form.labelForField(field)
    return label.text() if isinstance(label, QLabel) else ""


def row_of(form: QFormLayout, field: QLineEdit) -> int:
    return next(row for row in range(form.rowCount()) if (item := form.itemAt(row, QFormLayout.ItemRole.FieldRole)) is not None and item.widget() is field)


def test_a_google_accounts_general_tab_shows_its_client_id_and_a_secret_field_below_google_account(world: World, qtbot: QtBot) -> None:
    calendar, _ = google_pair(world)
    dialog = AccountEditDialog(world.backend, calendar)
    qtbot.addWidget(dialog)
    assert dialog.sign_in_name is not None and dialog.google_client_id is not None and dialog.google_client_secret is not None
    general = next(form for form in dialog.findChildren(QFormLayout) if form.indexOf(dialog.sign_in_name) >= 0)
    rows = [row_of(general, field) for field in (dialog.sign_in_name, dialog.google_client_id, dialog.google_client_secret)]
    assert rows == [rows[0], rows[0] + 1, rows[0] + 2]
    assert label_of(general, dialog.google_client_id) == "Client ID" and label_of(general, dialog.google_client_secret) == "Client secret"
    assert dialog.google_client_id.text() == "old-id"
    wait(qtbot, lambda: dialog.google_client_secret is not None and dialog.google_client_secret.text() == STORED)  # stored: 16 dots, never the secret (D-365)
    assert dialog.google_client_secret.echoMode() == QLineEdit.EchoMode.Password


def test_a_mail_account_has_no_google_codes(world: World, qtbot: QtBot) -> None:
    dialog = AccountEditDialog(world.backend, world.call("account.list")[0])
    qtbot.addWidget(dialog)
    assert dialog.google_client_id is None and dialog.google_client_secret is None


def test_save_changes_the_codes_of_both_accounts_of_the_google_address(world: World, qtbot: QtBot) -> None:
    calendar, contacts = google_pair(world)
    dialog = AccountEditDialog(world.backend, contacts)
    qtbot.addWidget(dialog)
    assert dialog.google_client_id is not None and dialog.google_client_secret is not None
    dialog.google_client_id.setText(" new-id ")
    dialog.google_client_secret.setText("new-secret")
    dialog.save_button.click()
    wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
    for summary in (calendar, contacts):
        assert world.engine.config().find(summary.account_id).google_client_id == "new-id"
        assert world.engine.passwords.get(summary.account_id, ServerRole.GOOGLE_SECRET) == "new-secret"


def test_save_with_the_secret_left_empty_keeps_the_stored_one(world: World, qtbot: QtBot) -> None:
    calendar, contacts = google_pair(world)
    dialog = AccountEditDialog(world.backend, calendar)
    qtbot.addWidget(dialog)
    assert dialog.google_client_id is not None
    dialog.google_client_id.setText("new-id")
    dialog.save_button.click()
    wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
    for summary in (calendar, contacts):
        assert world.engine.config().find(summary.account_id).google_client_id == "new-id"
        assert world.engine.passwords.get(summary.account_id, ServerRole.GOOGLE_SECRET) == "old-secret"


def test_an_emptied_client_id_is_refused_and_the_window_stays(world: World, qtbot: QtBot) -> None:
    calendar, _ = google_pair(world)
    dialog = AccountEditDialog(world.backend, calendar)
    qtbot.addWidget(dialog)
    assert dialog.google_client_id is not None
    dialog.google_client_id.setText("")
    dialog.save_button.click()
    wait(qtbot, lambda: "must not be empty" in dialog.result_label.text())
    assert dialog.result() != QDialog.DialogCode.Accepted and world.engine.config().find(calendar.account_id).google_client_id == "old-id"


def test_the_dots_of_the_stored_secret_are_never_sent_and_an_account_without_one_shows_an_empty_field(
    world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar, contacts = google_pair(world)
    world.engine.passwords.delete(contacts.account_id, ServerRole.GOOGLE_SECRET)  # as on a computer where it was never entered
    answered: list[StoredSecrets] = []
    shown = AccountEditDialog._show_stored  # pyright: ignore[reportPrivateUsage]

    def noted(self: AccountEditDialog, stored: StoredSecrets) -> None:
        answered.append(stored)
        shown(self, stored)

    monkeypatch.setattr(AccountEditDialog, "_show_stored", noted)
    empty = AccountEditDialog(world.backend, contacts)
    qtbot.addWidget(empty)
    assert empty.google_client_secret is not None
    wait(qtbot, lambda: bool(answered))
    assert not answered[0].google_secret and empty.google_client_secret.text() == ""
    dialog = AccountEditDialog(world.backend, calendar)
    qtbot.addWidget(dialog)
    assert dialog.google_client_id is not None and dialog.google_client_secret is not None
    wait(qtbot, lambda: dialog.google_client_secret is not None and dialog.google_client_secret.text() == STORED)
    dialog.google_client_id.setText("new-id")
    dialog.google_client_secret.setText(STORED + STORED)  # dots of any number are never a secret
    dialog.save_button.click()
    wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
    assert world.engine.passwords.get(calendar.account_id, ServerRole.GOOGLE_SECRET) == "old-secret"
