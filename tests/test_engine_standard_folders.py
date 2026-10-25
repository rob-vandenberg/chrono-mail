"""CR-36: a standard folder is known by its role, never by an English name, and is never deleted; Chrono Mail's own folders
(Outbox, Scheduled, Snoozed, Recovered) carry their role too, also those made before they had one."""

import json
from collections.abc import Generator
from pathlib import Path

import pytest

from chrono_mail.core import FolderRole
from chrono_mail.crypto import generate_data_key
from chrono_mail.engine import UsageError
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.store import Store
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.test_engine_commands import Setup, add_account, call

# --- Version ------------------------------------------------------------
__version__ = "test_engine_standard_folders.py 3.6.354"

# --- Version history ----------------------------------------------------
# v3.6.354: Initial: delete refused by role (CR-36), the own folders' roles given once, never renamed by the person, Recovered a folder


@pytest.fixture
def setup(tmp_path: Path) -> Generator[Setup]:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        yield Setup(engine, pop, smtp, add_account(engine, pop, smtp))


def _paths(setup: Setup) -> set[str]:
    return {str(getattr(item, "path")) for item in call(setup.engine, "folder.list", account=setup.account_id)}


def test_a_standard_folder_with_another_name_is_never_deleted(setup: Setup) -> None:
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        mailbox.ensure_folder(("Archiv",), FolderRole.ARCHIVE)  # a server's Archive under another language's name
        mailbox.ensure_folder(("Work",))
        mailbox.ensure_folder(("Work", "Gesendet"), FolderRole.SENT)  # a standard folder inside one of the person's
    for folder in ("Archiv", "Work"):
        with pytest.raises(UsageError, match="standard folder"):
            call(setup.engine, "folder.delete", account=setup.account_id, folder=folder, with_messages=True, include_subfolders=True)
    assert {"Archiv", "Work/Gesendet"} <= _paths(setup)


def test_the_own_folders_are_never_deleted_but_a_folder_inside_recovered_is(setup: Setup) -> None:
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        for path in (("Snoozed",), ("Recovered",), ("Recovered", "Old")):
            mailbox.ensure_folder(path)
    for folder in ("Snoozed", "Recovered", "Outbox"):
        with pytest.raises(UsageError, match="standard folder"):
            call(setup.engine, "folder.delete", account=setup.account_id, folder=folder, with_messages=True)
    call(setup.engine, "folder.delete", account=setup.account_id, folder="Recovered/Old", with_messages=True)
    assert "Recovered/Old" not in _paths(setup) and "Recovered" in _paths(setup)


def test_an_own_folder_made_before_it_had_a_role_gets_it_once(tmp_path: Path) -> None:
    store = Store(tmp_path / "store", "a" * 32, generate_data_key())
    store.create_folder(("Outbox",))
    description = next((tmp_path / "store" / "folders").glob("*/folder.json"))
    description.write_text(json.dumps({"version": 1, "path": ["Outbox"], "role": "other"}))  # as every version before 3.6.354 wrote it
    store.refresh()
    assert [folder.role for folder in store.list_folders()] == [FolderRole.OUTBOX]
    assert json.loads(description.read_text())["role"] == "outbox"


def test_the_person_never_renames_an_own_folder_but_a_rename_the_server_made_is_followed(setup: Setup, tmp_path: Path) -> None:
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        mailbox.ensure_folder(("Snoozed",))
        mailbox.ensure_folder(("Work",))
    for folder, new_name in (("Snoozed", "Later"), ("Work", "Scheduled")):
        with pytest.raises(UsageError):
            call(setup.engine, "folder.rename", account=setup.account_id, folder=folder, new_name=new_name)
    store = Store(tmp_path / "store", "a" * 32, generate_data_key())
    store.create_folder(("Projects",))
    store.rename_folder(("Projects",), ("Snoozed",))  # as an IMAP check follows another program's rename (D-193): never refused
    assert [(folder.path, folder.role) for folder in store.list_folders()] == [(("Snoozed",), FolderRole.SNOOZED)]


def test_recovered_is_a_folder_of_its_own_with_its_role(tmp_path: Path) -> None:
    store = Store(tmp_path / "store", "a" * 32, generate_data_key())
    store.create_folder(("Recovered", "Inbox"))
    assert {(folder.path, folder.role) for folder in store.list_folders()} == {(("Recovered",), FolderRole.RECOVERED), (("Recovered", "Inbox"), FolderRole.OTHER)}
