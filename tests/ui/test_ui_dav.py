import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFormLayout, QLabel
from pytestqt.qtbot import QtBot

from chrono_mail.engine import commands_dav
from chrono_mail.engine.commands_dav import FoundFolder, FoundServer
from chrono_mail.protocols.tls import fingerprint
from chrono_mail.ui.accountedit import AccountEditDialog
from chrono_mail.ui.accountdialog import CALDAV, CARDDAV, AccountDialog
from chrono_mail.ui.settingsdialog import SettingsDialog
from chrono_mail.ui.foundfolders import FolderTicks, FoundFoldersDialog, folder_text
from tests.fakedav import FakeDav
from tests.test_sync_dav import card, event, task
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_dav.py 4.2.449"

# --- Version history ----------------------------------------------------
# v4.2.449: the automatic check is check_accounts(None) (check_mail is gone, D-300)
# v4.2.439: the calendar window is a part of the Add window (D-298): the sidebars open it on CalDAV or CardDAV
# v2.4.333: the counts in the texts are written one way ("1 message", "3 messages"), never "message(s)"
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.1.236: New calendar… on an account's heading is set to that account and made on its server; on the empty area to the local ones (R-29)
# v1.1.225: a change not yet on the server is made offline (a new task goes to the server first otherwise)
# v1.1.218: Refresh in Calendar, Contacts and Tasks synchronises with the servers and says so
# v1.1.217: the timer's check of mail brings the calendars in step, also of accounts of calendars only
# v1.1.214: the editor lists the folders with ticks, filled on open and applied on Save; the window says "Select the folders you would like to add:"
# v1.1.212: Add account… in the sections; accounts of calendars and contacts only stay out of everything about mail
# v1.1.210: Find calendars, contacts and tasks and the Additional folders found window; a new account looks for its calendars
# v0.4.117: A contact that came from the server is known to the message list at once
# v0.0.66: Initial tests of the calendar and contacts server in the account settings and the window


def untick_tasks_and_accept(dialog: FoundFoldersDialog) -> int:
    for folder, tick in dialog.ticks:
        if folder.kind == "tasks":
            tick.setChecked(False)
    return QDialog.DialogCode.Accepted.value


def untick(ticks: FolderTicks, kind: str) -> None:
    for folder, tick in ticks.ticks:
        if folder.kind == kind:
            tick.setChecked(False)


def test_the_server_is_found_and_saved_in_the_account_dialog(world: World, qtbot: QtBot) -> None:
    with FakeDav() as server:
        account = world.call("account.list")[0]
        dialog = AccountEditDialog(world.backend, account)
        qtbot.addWidget(dialog)
        dialog.show()
        titles = [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())]
        assert "Calendars and contacts" in titles
        assert dialog._dav_values() is None  # noqa: SLF001 - nothing changed
        dialog.dav_url.setText(server.url)
        dialog.dav_username.setText("ann")
        dialog.dav_password.setText("secret")
        assert dialog.dav_check_button.text() == "Find calendars, contacts and tasks"
        dialog.dav_check_button.click()
        wait(qtbot, lambda: "3 folders found. The ticks take effect with Save." in dialog.dav_result.text())
        assert len(dialog.dav_folders.ticks) == 3 and dialog.dav_folders.any_ticked() and not dialog.dav_folders.left_out()
        untick(dialog.dav_folders, "tasks")
        assert world.call("account.list")[0].dav_url == ""  # nothing saved before Save
        dialog.save_button.click()
        wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
        assert dialog.dav_changed
        stored = world.call("account.list")[0]
        assert stored.dav_url == server.url and stored.dav_username == "ann"
        assert [item.split(" ", 1)[0] for item in world.engine.config().find(stored.account_id).dav_left_out] == ["VTODO"]


def linked_editor(world: World, qtbot: QtBot, server: FakeDav) -> AccountEditDialog:
    """The editor of the world's account, already tied to the server and synchronised, with the folders loaded."""
    account_id = world.call("account.list")[0].account_id
    world.call("account.set-dav", account=account_id, url=server.url, username="ann", password="secret")
    world.call("dav.sync", account=account_id)
    dialog = AccountEditDialog(world.backend, world.call("account.list")[0])
    qtbot.addWidget(dialog)
    wait(qtbot, lambda: len(dialog.dav_folders.ticks) == 3)
    return dialog


def test_the_folders_are_listed_when_the_editor_opens_and_cancel_changes_nothing(world: World, qtbot: QtBot) -> None:
    with FakeDav() as server:
        dialog = linked_editor(world, qtbot, server)
        form = next(item for item in dialog.findChildren(QFormLayout) if item.indexOf(dialog.dav_folders) >= 0)
        row, _ = form.getWidgetPosition(dialog.dav_folders)
        label = form.itemAt(row, QFormLayout.ItemRole.LabelRole).widget()
        assert isinstance(label, QLabel) and label.text() == "Folders" and form.getWidgetPosition(dialog.dav_password)[0] == row - 1
        assert all(tick.isChecked() for _, tick in dialog.dav_folders.ticks)
        untick(dialog.dav_folders, "events")
        dialog.reject()
        assert world.engine.config().find(world.call("account.list")[0].account_id).dav_left_out == ()
        assert len([item for item in world.call("collection.list") if item.account == world.call("account.list")[0].account_id]) == 3


def test_unticking_a_folder_and_saving_disconnects_it(world: World, qtbot: QtBot) -> None:
    with FakeDav() as server:
        dialog = linked_editor(world, qtbot, server)
        account_id = world.call("account.list")[0].account_id
        untick(dialog.dav_folders, "tasks")
        dialog.save_button.click()
        wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
        assert {item.kind for item in world.call("collection.list") if item.account == account_id} == {"events", "contacts"}
        assert "tasks" in server.collections  # the server is not touched
        again = AccountEditDialog(world.backend, world.call("account.list")[0])
        qtbot.addWidget(again)
        wait(qtbot, lambda: len(again.dav_folders.ticks) == 3)
        assert {folder.kind: tick.isChecked() for folder, tick in again.dav_folders.ticks}["tasks"] is False


def test_a_folder_with_changes_not_yet_sent_is_not_disconnected(world: World, qtbot: QtBot) -> None:
    with FakeDav() as server:
        dialog = linked_editor(world, qtbot, server)
        account_id = world.call("account.list")[0].account_id
        tasks = next(item.collection for item in world.call("collection.list") if item.account == account_id and item.kind == "tasks")
        server.writes_failing = True  # offline for a moment: the new task waits here (on the server first otherwise, D-194)
        world.call("task.add", list=tasks, summary="Made here")
        server.writes_failing = False
        untick(dialog.dav_folders, "tasks")
        dialog.save_button.click()
        wait(qtbot, lambda: "not on the server yet" in dialog.result_label.text())
        assert dialog.result() != QDialog.DialogCode.Accepted and dialog.save_button.isEnabled()
        assert "tasks" in {item.kind for item in world.call("collection.list") if item.account == account_id}


def test_the_find_button_says_when_nothing_answers(world: World, qtbot: QtBot) -> None:
    account = world.call("account.list")[0]
    dialog = AccountEditDialog(world.backend, account)
    qtbot.addWidget(dialog)
    with FakeDav(password="other") as server:
        dialog.dav_url.setText(server.url)
        dialog.dav_check_button.click()
        wait(qtbot, lambda: "refused" in dialog.dav_result.text())
    assert world.call("account.list")[0].dav_url == ""


def test_the_found_folders_window_names_each_folder_and_starts_as_told(qtbot: QtBot) -> None:
    folders = (
        FoundFolder("VEVENT /a/", "Calendar", "events", False, False, True), FoundFolder("VEVENT /p/", "Calendar", "events", True, False, True),
        FoundFolder("VCARD /b/", "Public Contacts", "contacts", True, True, False), FoundFolder("VTODO /t/", "Tasks", "tasks", False, False, True),
    )
    dialog = FoundFoldersDialog(FoundServer("https://x/", "", folders, ""))
    qtbot.addWidget(dialog)
    assert "Select the folders you would like to add:" in [label.text() for label in dialog.findChildren(QLabel)]
    assert [tick.text() for _, tick in dialog.ticks] == ["Calendar: Calendar", "Calendar: Calendar (public)", "Contacts: Public Contacts (public) (read only)", "Tasks: Tasks"]
    assert folder_text(folders[3]) == "Tasks: Tasks"
    assert dialog.left_out() == ("VCARD /b/",) and dialog.any_ticked()
    for _, tick in dialog.ticks:
        tick.setChecked(False)
    assert len(dialog.left_out()) == 4 and not dialog.any_ticked()


def test_a_new_account_looks_for_its_calendars_and_keeps_the_ticked_ones(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FoundFoldersDialog, "exec", untick_tasks_and_accept)
    with FakeDav(user="user", tls=True) as server:
        server.add("events", "e1.ics", event("e1", "Meeting"))
        monkeypatch.setattr(commands_dav, "WEB_PORT", server.port)
        account = world.call("account.list")[0]
        port = int(str(account.incoming).split(":")[1].split(" ")[0])
        world.call("account.trust-certificate", account=account.account_id, host="127.0.0.1", port=port, fingerprint=fingerprint(server.certificate))
        world.window._find_calendars(account.account_id)  # noqa: SLF001 - what Add account does after OK
        wait(qtbot, lambda: bool(world.call("account.list")[0].dav_url))
        wait(qtbot, lambda: {item.kind for item in world.call("collection.list") if item.account == account.account_id} == {"events", "contacts"})
        wait(qtbot, lambda: not world.window.checking.dav_syncing)  # noqa: SLF001


def test_the_window_synchronises_the_calendars_when_mail_is_checked(world: World, qtbot: QtBot) -> None:
    with FakeDav() as server:
        server.add("events", "e1.ics", event("e1", "Meeting"))
        server.add("tasks", "t1.ics", task("t1", "Write"))
        server.add("contacts", "c1.vcf", card("c1", "Ann Lee"))
        account = world.call("account.list")[0].account_id
        world.call("account.set-dav", account=account, url=server.url, username="ann", password="secret")
        world.window.reload_accounts(select_saved=False)
        wait(qtbot, lambda: bool(world.window.mail_state.accounts) and bool(getattr(world.window.mail_state.accounts[0], "dav_url", "")))  # noqa: SLF001
        world.window.checking.check_accounts(None)
        wait(qtbot, lambda: len([item for item in world.call("collection.list") if item.account == account]) == 3)
        wait(qtbot, lambda: not world.window.checking.dav_syncing)  # noqa: SLF001
        assert [item.name for item in world.call("contact.list").contacts] == ["Ann Lee"]


def test_a_contact_from_the_server_is_known_to_the_message_list_at_once(world: World, qtbot: QtBot) -> None:
    with FakeDav() as server:
        server.add("contacts", "c1.vcf", "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c1\r\nFN:Ann Lee\r\nEMAIL:ann@example.org\r\nEND:VCARD\r\n")
        account = world.call("account.list")[0].account_id
        world.call("account.set-dav", account=account, url=server.url, username="ann", password="secret")
        world.window.reload_accounts(select_saved=False)
        wait(qtbot, lambda: bool(world.window.mail_state.accounts) and bool(getattr(world.window.mail_state.accounts[0], "dav_url", "")))  # noqa: SLF001
        world.window.checking.sync_calendars()  # noqa: SLF001 - only the calendar and contacts sync, no mail check (that loads the addresses too)
        wait(qtbot, lambda: "ann@example.org" in world.window.list_model.contacts)


def accept_all(dialog: FoundFoldersDialog) -> int:
    return QDialog.DialogCode.Accepted.value


def test_the_sidebar_menu_starts_with_add_account_and_it_opens_the_window(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[AccountDialog] = []

    def shown(dialog: AccountDialog) -> int:
        opened.append(dialog)
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(AccountDialog, "exec", shown)
    for page in world.window.pages.values():
        menu = page.panel.menu_at(page.panel.tree.rect().center())
        assert [action.text() for action in menu.actions()][:2] == ["Add account…", ""]
        page.panel.add_account_requested.emit()
    assert len(opened) == 3  # the one Add window (D-298), on CalDAV from Calendar and Tasks, on CardDAV from Contacts
    kinds = {name: dialog.kind.currentData() for name, dialog in zip(world.window.pages, opened, strict=True)}
    assert kinds == {"calendar": CALDAV, "tasks": CALDAV, "contacts": CARDDAV}, kinds
    assert opened[0].calendar.target.itemText(0) == "A new account (calendars, contacts and tasks only)" and opened[0].calendar.isVisibleTo(opened[0])


def test_a_new_account_of_calendars_only_is_added_in_the_window(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FoundFoldersDialog, "exec", accept_all)
    with FakeDav() as server:
        dialog = AccountDialog(world.backend, mail_accounts=world.window.mail_state.mail_accounts(), start_kind=CALDAV)  # noqa: SLF001
        qtbot.addWidget(dialog)
        fields = dialog.calendar
        assert not dialog.add_button.isEnabled() and fields.account_name.isEnabled()
        fields.find_button.click()
        wait(qtbot, lambda: "Type the server address" in fields.result_label.text())
        fields.url.setText(server.url)
        fields.username.setText("ann")
        fields.password.setText("secret")
        fields.account_name.setText("Family calendar")
        fields.find_button.click()
        wait(qtbot, lambda: "3 folders found" in fields.result_label.text())
        assert dialog.add_button.isEnabled()
        dialog.add_button.click()
        wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
        assert dialog.calendars_added
        added = next(item for item in world.call("account.list") if item.kind == "dav")
        assert added.shown_name == "Family calendar" and added.dav_url == server.url


def test_the_window_can_give_a_mail_account_its_calendar_server(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FoundFoldersDialog, "exec", accept_all)
    with FakeDav(user="user") as server:
        dialog = AccountDialog(world.backend, mail_accounts=list(world.call("account.list")), start_kind=CALDAV)
        qtbot.addWidget(dialog)
        fields = dialog.calendar
        fields.target.setCurrentIndex(1)
        assert not fields.account_name.isEnabled() and fields.username.placeholderText() == "(the same as for mail)"
        fields.url.setText(server.url)
        fields.find_button.click()
        wait(qtbot, lambda: " found" in fields.result_label.text() or "span" in fields.result_label.text())
        assert " found" in fields.result_label.text(), fields.result_label.text()
        dialog.add_button.click()
        wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
        assert world.call("account.list")[0].dav_url == server.url and not any(item.kind == "dav" for item in world.call("account.list"))


def test_an_account_of_calendars_only_stays_out_of_everything_about_mail(world: World, qtbot: QtBot) -> None:
    with FakeDav() as server:
        added = world.call("account.add-dav", url=server.url, username="ann", password="secret", account_name="Family calendar")
        world.window.reload_accounts(select_saved=False)
        wait(qtbot, lambda: any(str(getattr(item, "account_id")) == added.account_id for item in world.window.mail_state.all_accounts))  # noqa: SLF001
        assert added.account_id not in {str(getattr(item, "account_id")) for item in world.window.mail_state.mail_accounts()}  # noqa: SLF001
        wait(qtbot, lambda: bool(world.window.tree_model.findItems("me@example.com", Qt.MatchFlag.MatchRecursive)))
        assert not world.window.tree_model.findItems("Family calendar", Qt.MatchFlag.MatchRecursive)
        world.window._fill_refresh_menu()  # noqa: SLF001
        assert not any("Family calendar" in action.text() for action in world.window.refresh_menu.actions())
        settings = SettingsDialog(world.backend, world.window.settings)
        qtbot.addWidget(settings)
        settings.show()
        wait(qtbot, lambda: settings.accounts.count() == 2)
        texts = [settings.accounts.item(row).text() for row in range(settings.accounts.count())]
        assert "Family calendar  - 127.0.0.1 (Calendars and contacts)" in texts
        assert not any("Family calendar" in settings.default_account.itemText(index) for index in range(settings.default_account.count()))
        account = next(item for item in world.call("account.list") if item.account_id == added.account_id)
        edit = AccountEditDialog(world.backend, account)
        qtbot.addWidget(edit)
        assert [edit.tabs.tabText(index) for index in range(edit.tabs.count())] == ["General", "Calendars and contacts"]
        edit.account_name.setText("Club calendar")
        edit.save_button.click()
        wait(qtbot, lambda: edit.result() == QDialog.DialogCode.Accepted)
        assert next(item for item in world.call("account.list") if item.account_id == added.account_id).shown_name == "Club calendar"


def events_of(world: World, account: str) -> int:
    calendars = [item.collection for item in world.call("collection.list") if item.account == account and item.kind == "events"]
    return sum(len(world.call("event.list", from_date="2026-10-01", to_date="2026-11-01", calendar=calendar).events) for calendar in calendars)


def test_the_timers_check_brings_the_calendars_in_step(world: World, qtbot: QtBot) -> None:
    with FakeDav() as server, FakeDav(user="cal", password="cal-secret") as other:
        mail = world.call("account.list")[0].account_id
        world.call("account.set-dav", account=mail, url=server.url, username="ann", password="secret")
        only = world.call("account.add-dav", url=other.url, username="cal", password="cal-secret").account_id
        world.call("dav.sync")
        world.window.reload_accounts(select_saved=False)
        wait(qtbot, lambda: len(world.window.mail_state.all_accounts) >= 3)  # noqa: SLF001
        server.add("events", "e1.ics", event("e1", "Dentist"))
        other.add("events", "e2.ics", event("e2", "Club meeting"))
        world.window.checking.check_on_general_interval()  # noqa: SLF001 - what the 5-minute timer does
        wait(qtbot, lambda: events_of(world, mail) == 1 and events_of(world, only) == 1)
        wait(qtbot, lambda: not world.window.checking.dav_syncing)  # noqa: SLF001


def test_refresh_in_the_sections_brings_the_server_in_and_says_so(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.sections import CALENDAR, CONTACTS, TASKS
    window = world.window
    window.reload_accounts(select_saved=False)
    wait(qtbot, lambda: bool(window.mail_state.all_accounts))  # noqa: SLF001
    page = window.pages[CALENDAR]
    page.act_refresh.trigger()
    assert "No account has a calendar and contacts server" in window.status_label.text()
    with FakeDav() as server:
        mail = world.call("account.list")[0].account_id
        world.call("account.set-dav", account=mail, url=server.url, username="ann", password="secret")
        window.reload_accounts(select_saved=False)
        wait(qtbot, lambda: any(str(getattr(item, "dav_url", "")) for item in window.mail_state.all_accounts))  # noqa: SLF001
        for number, section in enumerate((CALENDAR, TASKS, CONTACTS)):
            server.add("events", f"r{number}.ics", event(f"r{number}", f"From the server {number}"))
            window.switch_section(section)
            window.pages[section].act_refresh.trigger()
            assert not window.pages[section].act_refresh.isEnabled() and "synchronising" in window.status_label.text()
            wait(qtbot, lambda: not window.checking.dav_syncing)  # noqa: SLF001
            assert all(item.act_refresh.isEnabled() for item in window.pages.values())
            assert events_of(world, mail) == number + 1
            wait(qtbot, lambda: "Calendars, contacts and tasks: 1 added or changed here. " in window.status_label.text())  # with the section's count
        window.pages[CONTACTS].act_refresh.trigger()
        wait(qtbot, lambda: "are up to date" in window.status_label.text())
        qtbot.wait(500)  # the hidden sections re-read nothing that could cover it
        assert "are up to date" in window.status_label.text()


def test_new_calendar_is_made_in_the_account_right_clicked(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.core import LOCAL_ACCOUNT_ID
    from chrono_mail.ui.collectionpanel import ROLE_ACCOUNT
    from chrono_mail.ui.newcollection import NewCollectionDialog

    with FakeDav() as server:
        account_id = world.call("account.list")[0].account_id
        world.call("account.set-dav", account=account_id, url=server.url, username="ann", password="secret")
        world.call("dav.sync", account=account_id)
        panel = world.window.pages["calendar"].panel
        panel.reload()
        wait(qtbot, lambda: any(str(panel.tree.topLevelItem(n).data(0, ROLE_ACCOUNT)) == account_id for n in range(panel.tree.topLevelItemCount())))
        heading = next(panel.tree.topLevelItem(n) for n in range(panel.tree.topLevelItemCount()) if str(panel.tree.topLevelItem(n).data(0, ROLE_ACCOUNT)) == account_id)
        shown: list[str] = []

        def fill(dialog: NewCollectionDialog) -> int:
            wait(qtbot, lambda: dialog.account_field.isEnabled())
            shown.append(dialog.account)
            assert [dialog.account_field.itemData(n) for n in range(dialog.account_field.count())] == [LOCAL_ACCOUNT_ID, account_id]
            dialog.name_field.setText("Work")
            return QDialog.DialogCode.Accepted.value

        monkeypatch.setattr(NewCollectionDialog, "exec", fill)
        menu = panel.menu_at(panel.tree.visualItemRect(heading).center())
        next(action for action in menu.actions() if action.text() == "New calendar…").trigger()
        wait(qtbot, lambda: any(c.name == "Work" for c in server.collections.values()))
        assert shown == [account_id]
        wait(qtbot, lambda: any(item.text(0) == "Work" and item.parent() is heading for item in [heading.child(n) for n in range(heading.childCount())]))
        monkeypatch.setattr(NewCollectionDialog, "exec", lambda dialog: (wait(qtbot, lambda: dialog.account_field.isEnabled()), shown.append(dialog.account), QDialog.DialogCode.Rejected.value)[-1])
        panel.new_collection()  # opened from no account (the empty area, the New button of the section)
        assert shown[-1] == LOCAL_ACCOUNT_ID
