import time
from pathlib import Path

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind
from chrono_mail.engine import Engine
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import open_pim
from tests.enginefactory import make_engine
from tests.test_engine_commands import call

# --- Version ------------------------------------------------------------
__version__ = "test_engine_pim_purge.py 4.4.615"

# --- Version history ----------------------------------------------------
# v4.4.615: also the deleted mail folders of every account and Local Folders: erased 30 days after their deletion, newer ones stay
# v4.4.615: Initial tests of pim.purge-deleted: what was deleted over 30 days ago is erased in every account, the rest stays (D-072)

DAY_MS = 86_400_000
CARD = "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ann Lee\r\nN:Lee;Ann;;;\r\nEND:VCARD\r\n"


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path)
    call(engine, "pim.enable")
    return engine


def second_account(engine: Engine) -> str:
    created = call(
        engine, "account.add", display_name="Me", email_address="bob@example.com", username="bob", password="secret", incoming_host="127.0.0.1",
        incoming_port=1, incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=1, outgoing_security="none",
    )
    with open_pim(engine, created.account_id, create=True) as store:
        store.create_collection(CollectionKind.CONTACTS, "Friends")
    return created.account_id


def deleted_days_ago(engine: Engine, account: str, days: int) -> Path:
    """A contact deleted this many days ago: its file in .deleted carries the moment it was deleted."""
    with open_pim(engine, account) as store:
        book = store.collections(CollectionKind.CONTACTS)[0].collection_id
        item = store.put(book, CARD)
        store.delete(book, item)
        directory = store.root / ".deleted" / book
    moved = next(directory.glob(f"{item}.*"))
    then = directory / f"{item}.{int(time.time() * 1000) - days * DAY_MS}"
    moved.rename(then)
    return then


def test_what_was_deleted_over_30_days_ago_goes_in_every_account_and_the_rest_stays(engine: Engine) -> None:
    other = second_account(engine)
    old_here, new_here = deleted_days_ago(engine, LOCAL_ACCOUNT_ID, 31), deleted_days_ago(engine, LOCAL_ACCOUNT_ID, 29)
    old_there, new_there = deleted_days_ago(engine, other, 45), deleted_days_ago(engine, other, 1)
    with open_pim(engine, other) as store:
        book = store.collections(CollectionKind.CONTACTS)[0].collection_id
        live = store.put(book, CARD)
        gone = store.create_collection(CollectionKind.EVENTS, "Old plans").collection_id
        store.delete_collection(gone)
        taken_away = next((store.root / ".deleted").glob(f"{gone}.*"))
    long_ago = taken_away.with_name(f"{gone}.{int(time.time()) - 40 * 86_400}")
    taken_away.rename(long_ago)
    result = call(engine, "pim.purge-deleted")
    assert (result.erased, result.failed) == (3, ())  # two contacts and one calendar
    assert not old_here.exists() and not old_there.exists() and not long_ago.exists()
    assert new_here.exists() and new_there.exists()  # deleted less than 30 days ago: still there
    with open_pim(engine, other) as store:
        assert "Ann Lee" in store.get(book, live)  # never anything that was not deleted
    assert call(engine, "pim.purge-deleted").erased == 0  # a second run finds nothing more


def test_an_account_whose_store_cannot_be_opened_leaves_the_others_alone(engine: Engine) -> None:
    other = second_account(engine)
    old_here = deleted_days_ago(engine, LOCAL_ACCOUNT_ID, 31)
    (engine.directories.pim_directory(other) / "pim.json").write_text("{damaged", "utf-8")
    result = call(engine, "pim.purge-deleted")
    assert result.erased == 1 and not old_here.exists()
    assert result.failed == ("Me",) or len(result.failed) == 1


def mail_account(engine: Engine, address: str) -> str:
    return call(
        engine, "account.add", display_name=address, email_address=address, username=address, password="secret", incoming_host="127.0.0.1",
        incoming_port=1, incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=1, outgoing_security="none",
    ).account_id


def folder_deleted_days_ago(engine: Engine, account: str, name: str, days: int) -> Path:
    """A mail folder deleted this many days ago: its directory in the store's .deleted carries the moment it was deleted."""
    call(engine, "folder.create", account=account, folder=name)
    with open_mailbox(engine, account) as mailbox:
        directory = mailbox.store._folder_directory((name,)).name  # noqa: SLF001
        deleted = mailbox.store.root / ".deleted"
    call(engine, "folder.delete", account=account, folder=name)
    [moved] = list(deleted.glob(f"{directory}.*"))
    return moved.rename(moved.with_name(f"{directory}.{int(time.time()) - days * 86_400}"))


def test_the_mail_folders_deleted_over_30_days_ago_go_in_every_account_and_local_folders(engine: Engine) -> None:
    first, second = mail_account(engine, "ann@example.com"), mail_account(engine, "bob@example.com")
    old = [folder_deleted_days_ago(engine, account, "Old", days) for account, days in ((first, 31), (second, 45), (LOCAL_ACCOUNT_ID, 100))]
    new = [folder_deleted_days_ago(engine, account, "New", days) for account, days in ((first, 29), (second, 1), (LOCAL_ACCOUNT_ID, 0))]
    call(engine, "folder.create", account=first, folder="Live")
    result = call(engine, "pim.purge-deleted")
    assert (result.folders, result.failed) == (3, ())
    assert not any(path.exists() for path in old) and all(path.exists() for path in new)
    assert "Live" in [folder.path for folder in call(engine, "folder.list", account=first)]  # never a folder that was not deleted
    assert call(engine, "pim.purge-deleted").folders == 0  # a second run finds nothing more
