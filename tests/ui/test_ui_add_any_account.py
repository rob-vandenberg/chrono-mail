import pytest
import shiboken6
from PySide6.QtWidgets import QApplication, QDialog, QLabel
from pytestqt.qtbot import QtBot

from chrono_mail.ui.accountdialog import CALDAV, CARDDAV, AccountDialog
from chrono_mail.ui.foundfolders import FoundFoldersDialog
from tests.fakedav import FakeDav
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_add_any_account.py 4.2.440"

# --- Version history ----------------------------------------------------
# v4.2.440: a long error text is shown whole; the smallest height follows the text and the width
# v4.2.439: Initial tests: every kind of account is added in the one Add window (UR-04, D-298); its size and the type waiting for a test

CHOICES = [
    "POP3: the mail is downloaded to this computer",
    "IMAP: the mail stays on the server and is kept in step",
    "CalDAV: calendars and tasks from a server",
    "CardDAV: contacts from a server",
]


def test_settings_add_offers_pop3_imap_caldav_and_carddav(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[AccountDialog] = []

    def shown(dialog: AccountDialog) -> int:
        opened.append(dialog)
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(AccountDialog, "exec", shown)
    world.window.add_account()  # Settings > Accounts > Add, and Menu's Add an account…
    (dialog,) = opened
    assert [dialog.kind.itemText(index) for index in range(dialog.kind.count())] == CHOICES
    assert dialog.kind.currentIndex() == 0 and dialog.windowTitle() == "Add an account"


def test_the_part_below_the_choice_follows_it_and_keeps_what_was_typed(world: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(world.backend, mail_accounts=world.window.mail_state.mail_accounts())
    qtbot.addWidget(dialog)
    mail_parts = (dialog.servers.widget, dialog.test_button)
    assert all(part.isVisibleTo(dialog) for part in mail_parts) and not dialog.calendar.isVisibleTo(dialog)
    dialog.email.setText("ann@example.com")
    dialog.kind.setCurrentIndex(dialog.kind.findData(CALDAV))
    assert dialog.calendar.isVisibleTo(dialog) and not any(part.isVisibleTo(dialog) for part in mail_parts)
    assert not dialog.add_button.isEnabled()  # Add needs what Find found
    dialog.calendar.url.setText("https://dav.example.com/")
    dialog.kind.setCurrentIndex(dialog.kind.findData(CARDDAV))
    assert dialog.calendar.url.text() == "https://dav.example.com/"
    dialog.kind.setCurrentIndex(0)
    assert dialog.email.text() == "ann@example.com" and dialog.add_button.isEnabled() and not dialog.calendar.isVisibleTo(dialog)
    dialog.kind.setCurrentIndex(dialog.kind.findData(CALDAV))
    assert dialog.calendar.url.text() == "https://dav.example.com/"


@pytest.mark.parametrize(("kind", "ticked"), [(CALDAV, {"events", "tasks"}), (CARDDAV, {"contacts"})])
def test_caldav_and_carddav_start_with_their_own_folders_ticked(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, kind: str, ticked: set[str]) -> None:
    offered: list[dict[str, bool]] = []

    def look(dialog: FoundFoldersDialog) -> int:
        offered.append({folder.kind: tick.isChecked() for folder, tick in dialog.ticks})
        return QDialog.DialogCode.Rejected.value  # Cancel: nothing is added

    monkeypatch.setattr(FoundFoldersDialog, "exec", look)
    with FakeDav() as server:
        dialog = AccountDialog(world.backend, start_kind=kind)
        qtbot.addWidget(dialog)
        fields = dialog.calendar
        fields.url.setText(server.url)
        fields.username.setText("ann")
        fields.password.setText("secret")
        fields.find_button.click()
        wait(qtbot, lambda: "3 folders found" in fields.result_label.text())
        dialog.add_button.click()
    assert offered == [{name: name in ticked for name in ("events", "tasks", "contacts")}]  # every folder listed, the kind's own ticked
    assert dialog.result() != QDialog.DialogCode.Accepted and not any(item.kind == "dav" for item in world.call("account.list"))


def test_the_first_account_window_offers_every_kind(world: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(world.backend, first_account=True, restore=lambda: None)
    qtbot.addWidget(dialog)
    welcome = dialog.findChild(QLabel, "welcome")
    assert isinstance(welcome, QLabel) and "Add your first account to get started" in welcome.text()
    assert [dialog.kind.itemText(index) for index in range(dialog.kind.count())] == CHOICES


def test_the_window_opens_and_switches_at_the_size_of_the_kind_shown(world: World, qtbot: QtBot) -> None:
    """The reviews of 4.2.439: measured with both parts it opened far too tall, also on CalDAV; switched back to POP3 it was too tall again; opened
    on CalDAV it stayed too narrow for the mail part."""
    heights: dict[str, int] = {}
    for kind in ("pop3", CALDAV):
        dialog = AccountDialog(world.backend, start_kind=kind)
        qtbot.addWidget(dialog)
        dialog.show()
        qtbot.waitExposed(dialog)
        assert dialog.height() <= dialog.sizeHint().height() + 2, (kind, dialog.height(), dialog.sizeHint().height())
        heights[kind] = dialog.height()
        for other in (CARDDAV, "imap", CALDAV, "pop3"):
            dialog.kind.setCurrentIndex(dialog.kind.findData(other))
            wait(qtbot, lambda: dialog.height() <= dialog.sizeHint().height() + 2)
            assert dialog.width() >= dialog.minimumSizeHint().width() and dialog.minimumWidth() >= dialog.minimumSizeHint().width(), (kind, other)
    assert heights[CALDAV] < heights["pop3"] / 2


def test_an_answer_after_a_change_of_kind_stays_with_its_own_part(world: World, qtbot: QtBot) -> None:
    """The reviews of 4.2.439: a test still running when the kind changed switched the shared Add on, wrote under the other kind, or showed the
    mail certificate button there; a Find did the same the other way round."""
    dialog = AccountDialog(world.backend)
    qtbot.addWidget(dialog)
    dialog.email.setText("ann@example.com")
    dialog.username.setText("ann")
    dialog.password.setText("secret")
    dialog.incoming_host.setText("127.0.0.1")
    dialog.incoming_port.setValue(1)  # nothing listens there: the test fails quickly
    dialog.outgoing_host.setText("127.0.0.1")
    dialog.outgoing_port.setValue(1)
    dialog.test_button.click()
    assert not dialog.add_button.isEnabled() and dialog.result_label.text() == "Testing…"
    dialog.kind.setCurrentIndex(dialog.kind.findData(CALDAV))
    fields = dialog.calendar
    wait(qtbot, lambda: dialog.result_label.text() != "Testing…")  # the test's answer, in the mail part's own line
    assert not dialog.add_button.isEnabled() and fields.result_label.text() == "" and not dialog.certificate_button.isVisibleTo(dialog)
    fields.url.setText("https://127.0.0.1:1/")
    fields.username.setText("ann")
    fields.password.setText("secret")
    fields.find_button.click()
    assert fields.result_label.text() == "Looking…" and not fields.certificate_button.isEnabled()
    dialog.kind.setCurrentIndex(0)
    assert dialog.add_button.isEnabled()  # the mail part's Add: no test runs
    wait(qtbot, lambda: fields.result_label.text() != "Looking…")
    assert dialog.add_button.isEnabled() and "found" not in dialog.result_label.text()


def test_a_certificate_look_never_starts_a_second_test_or_find(world: World, qtbot: QtBot) -> None:
    """The third review of 4.2.439: accepting a certificate while a test or Find ran started a second one; the first answer then enabled
    Add and the type while the second still ran. A second one would write its "Testing…" or "Looking…" again."""
    dialog = AccountDialog(world.backend)
    qtbot.addWidget(dialog)
    for field, text in ((dialog.email, "ann@example.com"), (dialog.username, "ann"), (dialog.password, "secret"), (dialog.incoming_host, "127.0.0.1"),
                        (dialog.outgoing_host, "127.0.0.1")):
        field.setText(text)
    dialog.incoming_port.setValue(1)
    dialog.outgoing_port.setValue(1)
    dialog._test()  # noqa: SLF001 - the test button
    dialog.result_label.setText("first test runs")
    dialog._test()  # noqa: SLF001 - what an accepted certificate does while it runs
    assert dialog.result_label.text() == "first test runs"
    wait(qtbot, lambda: dialog.result_label.text() != "first test runs")
    dialog.kind.setCurrentIndex(dialog.kind.findData(CALDAV))
    fields = dialog.calendar
    for field, text in ((fields.url, "https://127.0.0.1:1/"), (fields.username, "ann"), (fields.password, "secret")):
        field.setText(text)
    fields.find_button.click()
    fields.result_label.setText("first Find runs")
    fields._find()  # noqa: SLF001 - what an accepted certificate does while it runs
    assert fields.result_label.text() == "first Find runs"
    wait(qtbot, lambda: fields.result_label.text() != "first Find runs")


def test_the_window_follows_its_part_after_a_switch_and_its_timer_never_outlives_it(world: World, qtbot: QtBot) -> None:
    """The third review of 4.2.439: after a switch the window could not grow when the certificate button appeared; the size step ran after
    the window was deleted."""
    dialog = AccountDialog(world.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    dialog.kind.setCurrentIndex(dialog.kind.findData("imap"))
    wait(qtbot, lambda: dialog.height() <= dialog.sizeHint().height() + 2)
    dialog._certificate_trouble(("incoming", "certificate verify failed"))  # noqa: SLF001 - what a failed test does
    assert dialog.certificate_button.isVisibleTo(dialog)
    wait(qtbot, lambda: dialog.height() >= dialog.minimumSizeHint().height())
    gone = AccountDialog(world.backend)
    gone.show()
    gone.kind.setCurrentIndex(gone.kind.findData(CALDAV))  # its size step waits for the next pass of the event loop
    shiboken6.delete(gone)  # gone at once, before that pass
    QApplication.processEvents()  # the pass in which its size step was due: it never runs on the deleted window (it raised there)


def test_a_failed_add_never_frees_a_test_or_find_still_running(world: World, qtbot: QtBot) -> None:
    """The third review of 4.2.439: a failed add switched Test or Find back on while one still ran, so a second could start."""
    dialog = AccountDialog(world.backend)
    qtbot.addWidget(dialog)
    for field, text in ((dialog.email, "ann@example.com"), (dialog.username, "ann"), (dialog.password, "secret"), (dialog.incoming_host, "127.0.0.1"),
                        (dialog.outgoing_host, "127.0.0.1")):
        field.setText(text)
    dialog.incoming_port.setValue(1)
    dialog.outgoing_port.setValue(1)
    dialog.test_button.click()
    dialog._set_adding(True)  # noqa: SLF001 - an add of the other part, which then fails
    dialog._set_adding(False)  # noqa: SLF001
    assert not dialog.test_button.isEnabled() and not dialog.certificate_button.isEnabled()
    wait(qtbot, dialog.test_button.isEnabled)  # the test's answer frees them
    dialog.kind.setCurrentIndex(dialog.kind.findData(CALDAV))
    fields = dialog.calendar
    for field, text in ((fields.url, "https://127.0.0.1:1/"), (fields.username, "ann"), (fields.password, "secret")):
        field.setText(text)
    fields.find_button.click()
    dialog._set_adding(True)  # noqa: SLF001
    dialog._set_adding(False)  # noqa: SLF001
    assert not fields.find_button.isEnabled() and not fields.certificate_button.isEnabled()
    wait(qtbot, fields.find_button.isEnabled)


@pytest.mark.parametrize("kind", ["pop3", CALDAV])
def test_a_long_error_text_is_shown_whole(world: World, qtbot: QtBot, kind: str) -> None:
    """The review of 4.2.439 (older fault): a result text wrapping over several lines was not counted in the window's height, so its last
    lines were cut off."""
    dialog = AccountDialog(world.backend, start_kind=kind)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    line = dialog.calendar.result_label if kind == CALDAV else dialog.result_label
    line.setText("The server could not be reached: " + "the connection was refused by the other side, " * 12)

    def whole() -> bool:
        return line.height() >= line.heightForWidth(line.width()) and dialog.height() >= dialog.minimumHeight()

    wait(qtbot, whole)


def test_the_smallest_height_follows_the_text_and_the_width_and_never_passes_the_screen(world: World, qtbot: QtBot) -> None:
    """The review of the long-text fix: the smallest height only went up (a short text after a long one, a wider window, the certificate
    button gone), a narrower window was not measured again, and a huge server text made the window far taller than its screen."""
    dialog = AccountDialog(world.backend, start_kind=CALDAV)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    line = dialog.calendar.result_label
    line.setText("Looking…")  # the short text measured with the font of this screen (an empty line is one pixel lower on some)
    wait(qtbot, lambda: dialog.minimumHeight() >= dialog.minimumSizeHint().height() and line.height() >= line.heightForWidth(line.width()))
    short_need = dialog.minimumHeight()
    line.setText("The server could not be reached: " + "the connection was refused by the other side, " * 12)
    wait(qtbot, lambda: dialog.minimumHeight() > short_need)
    long_need = dialog.minimumHeight()
    dialog.resize(dialog.width() + 400, dialog.height())  # wider: the text needs fewer lines
    wait(qtbot, lambda: dialog.minimumHeight() < long_need)
    dialog.resize(dialog.minimumWidth(), dialog.height())  # narrower by hand: measured again, shown whole
    wait(qtbot, lambda: line.height() >= line.heightForWidth(line.width()))
    line.setText("Looking…")
    wait(qtbot, lambda: dialog.minimumHeight() == short_need)  # it can be made small again
    line.setText("err " * 20000)  # a huge text from a server
    screen = dialog.screen()
    assert screen is not None
    wait(qtbot, lambda: dialog.minimumHeight() > short_need)
    assert dialog.minimumHeight() <= screen.availableGeometry().height() and dialog.height() <= screen.availableGeometry().height()
