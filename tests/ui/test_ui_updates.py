from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QCheckBox
from pytestqt.qtbot import QtBot

from chrono_mail.engine import commands_updates
from chrono_mail.ui import updates
from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.ui.settingsdialog import SettingsContext, SettingsDialog
from chrono_mail.ui.updates import AboutWindow, UpdateAvailableWindow, UpdateDownloadedWindow
from tests.fakegithub import FakeGitHub
from tests.ui.world import Dialogs, World, build_world, record_opened_urls, reject_dialog, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_updates.py 4.2.423"

# --- Version history ----------------------------------------------------
# v4.2.423: About shows Check for updates only in a copy updated through an installer (the owner)
# v4.2.419: Initial: the check goes along with a Refresh that went online, once a day; Download, Skip, Cancel; Install; About (D-293)

DEB = "_amd64.deb"


@pytest.fixture
def github() -> Generator[FakeGitHub]:
    with FakeGitHub() as server:
        server.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": b"the new package"})
        yield server


def installed(world: World, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The copy of the world is one installed from a .deb of 4.2.418; GitHub is the stand-in; downloads go to a folder of the test."""
    monkeypatch.setattr(commands_updates, "install_kind", lambda: "deb")
    monkeypatch.setattr(commands_updates, "program_version", lambda: "4.2.418")
    monkeypatch.setattr(updates, "downloads_folder", lambda: str(tmp_path / "Downloads"))
    world.engine.releases_address = github.releases_address


def asked(github: FakeGitHub) -> int:
    return sum(1 for path in github.requests if path.startswith("/releases"))


def offered(world: World) -> UpdateAvailableWindow:
    window = world.window.updates.open_window
    assert isinstance(window, UpdateAvailableWindow)
    return window


def refresh(world: World, qtbot: QtBot, accounts: bool = True) -> None:
    """Refresh once the window has its accounts (they come in after the window opens)."""
    if not world.window.mail_state.all_accounts:  # the window has not started yet
        world.window.start()
    if accounts:
        settle(qtbot, world)
    else:
        wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001
    world.window.checking.send_and_receive()
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001


def test_a_refresh_that_goes_online_looks_for_an_update_once_a_day(world: World, qtbot: QtBot, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    installed(world, github, monkeypatch, tmp_path)
    refresh(world, qtbot)
    wait(qtbot, lambda: world.window.updates.open_window is not None)
    window = offered(world)
    assert window.check.version == "4.2.419" and asked(github) == 1
    window.reject()  # Cancel: nothing until the next check
    wait(qtbot, lambda: world.window.updates.open_window is None)
    refresh(world, qtbot)
    assert asked(github) == 1 and world.window.updates.open_window is None  # the next check is tomorrow


def test_a_refresh_that_failed_to_reach_its_server_still_counts(world: World, qtbot: QtBot, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    installed(world, github, monkeypatch, tmp_path)
    world.pop._fault = lambda verb, count: True  # noqa: SLF001 - the server drops every connection
    refresh(world, qtbot)
    wait(qtbot, lambda: world.window.updates.open_window is not None)
    assert asked(github) == 1


def test_no_check_with_the_tick_off(world: World, qtbot: QtBot, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    installed(world, github, monkeypatch, tmp_path)
    world.window.prefs.settings.setValue("update_check", False)
    refresh(world, qtbot)
    assert asked(github) == 0


def test_no_check_without_an_account_to_refresh(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> None:
    reject_dialog(monkeypatch, AccountDialog)  # the first start asks for an account: none is added
    for world in build_world(tmp_path, qtbot, dialogs, with_account=False):
        installed(world, github, monkeypatch, tmp_path)
        refresh(world, qtbot, accounts=False)
        assert asked(github) == 0


def test_skip_is_remembered_and_the_link_leaves_the_window_open(world: World, qtbot: QtBot, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    installed(world, github, monkeypatch, tmp_path)
    opened = record_opened_urls(monkeypatch)
    refresh(world, qtbot)
    wait(qtbot, lambda: world.window.updates.open_window is not None)
    window = offered(world)
    wait(qtbot, window.isVisible)
    text_area = window.page_link.contentsRect()
    qtbot.mouseClick(window.page_link, Qt.MouseButton.LeftButton, pos=QPoint(text_area.left() + 10, text_area.center().y()))  # on the link's first words
    assert opened == [f"{github.address}/release/4.2.419"] and window.isVisible()
    window.skip_button.click()
    assert not window.download_button.isEnabled()  # no Download while the Skip is answered
    wait(qtbot, lambda: world.window.updates.open_window is None)
    assert world.call("update.show").skipped == "4.2.419"


def test_download_then_install_on_linux(world: World, qtbot: QtBot, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    installed(world, github, monkeypatch, tmp_path)
    installs: list[Path] = []
    monkeypatch.setattr(commands_updates, "_install_package", installs.append)
    asked_restart: list[str] = []

    def later(parent: object, title: str, text: str, buttons: list[str]) -> int:
        asked_restart.append(text)
        return 1  # Later

    monkeypatch.setattr(updates, "ask_choice", later)
    world.window.updates.offer(world.call("update.check", by_hand=True))
    offered(world).download_button.click()
    wait(qtbot, lambda: isinstance(world.window.updates.open_window, UpdateDownloadedWindow))
    downloaded = world.window.updates.open_window
    assert isinstance(downloaded, UpdateDownloadedWindow)
    path = tmp_path / "Downloads" / f"chrono-mail_4.2.419{DEB}"
    assert path.read_bytes() == b"the new package"
    assert str(tmp_path / "Downloads") in downloaded.folder_link.text()
    downloaded.install_button.click()
    wait(qtbot, lambda: asked_restart != [])
    assert installs == [path] and "4.2.419" in asked_restart[0]
    assert world.window.isVisible()  # Later: Chrono Mail keeps running


def test_about_checks_by_hand_and_offers_a_skipped_version(world: World, qtbot: QtBot, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    installed(world, github, monkeypatch, tmp_path)
    world.call("update.skip", version="4.2.419")
    world.window.updates.about()
    about = world.window.findChild(AboutWindow)
    assert about is not None
    wait(qtbot, about.check_button.isEnabled)
    assert not about.check_button.isHidden()  # an installed copy shows it
    about.check_button.click()
    wait(qtbot, lambda: world.window.updates.open_window is not None)
    assert offered(world).check.version == "4.2.419"


def test_about_says_when_this_is_the_newest_version_and_when_github_is_out_of_reach(world: World, qtbot: QtBot, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    installed(world, github, monkeypatch, tmp_path)
    monkeypatch.setattr(commands_updates, "program_version", lambda: "4.2.419")
    world.window.updates.about()
    about = world.window.findChild(AboutWindow)
    assert about is not None
    wait(qtbot, about.check_button.isEnabled)
    about.check_button.click()
    wait(qtbot, lambda: about.update_note.text() == "You have the newest version, 4.2.419.")
    world.engine.releases_address = "http://127.0.0.1:9/releases"
    about.check_button.click()
    wait(qtbot, lambda: about.update_note.text() == updates.COULD_NOT_REACH)
    about.reject()


def test_a_copy_run_from_its_project_folder_hides_the_button_and_greys_the_tick(world: World, qtbot: QtBot) -> None:
    world.window.updates.about()
    about = world.window.findChild(AboutWindow)
    assert about is not None
    wait(qtbot, lambda: about.answered)
    assert about.check_button.isHidden()  # not there at all in such a copy (the owner)
    about.reject()
    window = world.window
    dialog = SettingsDialog(world.backend, window.settings, SettingsContext(tray_available=False, shortcut_actions=window._shortcut_registry()), window)  # noqa: SLF001
    qtbot.addWidget(dialog)
    tick = dialog.findChild(QCheckBox, "updateCheck")
    assert tick is not None and tick.isChecked()  # on by default
    wait(qtbot, lambda: tick.toolTip() == commands_updates.NOT_FROM_AN_INSTALLER)
    assert not tick.isEnabled()


def test_an_update_found_while_the_window_is_hidden_waits_for_it(world: World, qtbot: QtBot, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    installed(world, github, monkeypatch, tmp_path)
    found = world.call("update.check", by_hand=True)
    world.window.hide()
    world.window.updates.offer(found)
    assert world.window.updates.open_window is None
    world.window.show()
    wait(qtbot, lambda: world.window.updates.open_window is not None)
