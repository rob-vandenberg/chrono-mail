import json
import sqlite3
import sys
import threading
from collections.abc import Generator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, override  # command results are command-specific dataclasses; the tests check their fields

import pytest

from chrono_mail.config import ServerRole
from chrono_mail.core import LOCAL_ACCOUNT_ID, AuthenticationError, ConfigurationError, CryptoError, MessageFormatError, ProtocolError, StorageError
from chrono_mail.engine import Engine, UsageError, build_registry
from chrono_mail.engine.changes import Change
from chrono_mail.index import entry_from_raw
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.crypto import DataKey, InMemoryKeyStore
from chrono_mail.store import Store
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_engine_commands.py 0.5.164"

# --- Version history ----------------------------------------------------
# v0.5.164: a question waits while a change is written to the index but not yet told
# v0.5.163: a POP3 check tells each new message as a fact
# v0.5.162: a message read while shown stays in a search of unread mail when it is named in stay
# v0.5.161: an action on one message (open, mark, move) never lists its whole folder; the test that makes an index old closes its connection (on Windows the open file kept the rebuild from replacing it)
# v0.5.160: message.places gives the places of several messages, as message.list and message.search show them
# v0.5.154: The index is opened only inside its lock; the permission check of the key export only where the system has those bits
# v0.5.153: message.position gives the place message.list shows a message at
# v0.4.118: Commands that open an index of an old format at the same moment rebuild it once, without errors
# v0.3.112: A delete or a move to Junk of a message whose file is gone answers "gone"
# v0.3.111: Two rebuilds of the shared Local Folders index at once both succeed
# v0.3.110: Deleting a message whose file is gone removes it from the list
# v0.0.69: The same e-mail address may be used by several accounts
# v0.0.14: Changes after the first real use (see DEVLOG.md)
# v0.0.13: Changes for the user interface (see DEVLOG.md)
# v0.0.12: Initial engine command tests

REGISTRY = build_registry()


def call(engine: Engine, command: str, **values: object) -> Any:
    return REGISTRY.run(engine, command, values)


@dataclass
class Setup:
    engine: Engine
    pop: FakePop3Server
    smtp: FakeSmtpServer
    account_id: str


def mail(number: int) -> bytes:
    return raw_message(subject=f"Message {number}", body=f"body number {number}\r\n", message_id=f"<m{number}@x>")


def add_account(engine: Engine, pop: FakePop3Server, smtp: FakeSmtpServer, email: str = "me@example.com", **extra: object) -> str:
    values: dict[str, object] = {
        "display_name": "Me", "email_address": email, "username": "user", "password": "secret",
        "incoming_host": "127.0.0.1", "incoming_port": pop.port, "incoming_security": "none",
        "outgoing_host": "127.0.0.1", "outgoing_port": smtp.port, "outgoing_security": "none",
    }
    taken = {item.incoming_host for item in call(engine, "account.list") if item.incoming_username == values["username"]}
    values["incoming_host"] = next(host for host in ("127.0.0.1", "localhost", "127.1", "127.0.1") if host not in taken)  # one login on one server is one account
    values.update(extra)
    result = call(engine, "account.add", **values)
    return str(getattr(result, "account_id"))


@pytest.fixture
def setup(tmp_path: Path) -> Generator[Setup]:
    engine = make_engine(tmp_path)
    with FakePop3Server({f"uid{n}": mail(n) for n in (1, 2, 3)}) as pop, FakeSmtpServer() as smtp:
        yield Setup(engine, pop, smtp, add_account(engine, pop, smtp))


# --- accounts and keys ------------------------------------------------------------------------------


def test_adding_an_account_creates_key_store_folders_and_settings(setup: Setup) -> None:
    config = setup.engine.config()
    (item,) = config.accounts
    assert item.account.account_id == setup.account_id and item.encrypted and config.key_id is not None
    store = setup.engine.open_store(item)
    assert [folder.path for folder in store.list_folders()] == [("Drafts",), ("Inbox",), ("Junk",), ("Outbox",), ("Sent",), ("Trash",)]
    assert setup.engine.passwords.get(setup.account_id, ServerRole.INCOMING) == "secret"
    assert setup.engine.passwords.get(setup.account_id, ServerRole.OUTGOING) == "secret"
    text = setup.engine.directories.config_file.read_text("utf-8")
    assert "secret" not in text and "password" not in text.lower().replace('"policy"', "")


def test_second_account_shares_the_key(setup: Setup) -> None:
    result = call(setup.engine, "account.add", display_name="Two", email_address="two@example.com", username="u", password="p", incoming_host="h", outgoing_host="h")
    assert getattr(result, "key_created") is False
    assert len(setup.engine.config().accounts) == 2


@pytest.mark.parametrize("email", ["nobody", "@example.com", "me@", "a@b@c"])
def test_invalid_email_address_is_refused(setup: Setup, email: str) -> None:
    with pytest.raises(UsageError):
        call(setup.engine, "account.add", display_name="X", email_address=email, username="u", password="p", incoming_host="h", outgoing_host="h")


def test_the_same_email_address_may_be_used_by_more_than_one_account(setup: Setup) -> None:
    """Only the same user name on the same incoming server is the same account."""
    other = FakePop3Server({})
    with other:
        second = add_account(setup.engine, other, setup.smtp, incoming_host="localhost")
    accounts = call(setup.engine, "account.list")
    assert len(accounts) == 2 and accounts[0].email_address == accounts[1].email_address and second != setup.account_id
    with pytest.raises(ConfigurationError, match="More than one"):
        setup.engine.resolve_account("me@example.com")
    assert setup.engine.resolve_account(second).account.account_id == second  # the id tells them apart


def test_the_same_user_name_on_the_same_incoming_server_is_refused(setup: Setup) -> None:
    with pytest.raises(ConfigurationError, match="exists already"):
        add_account(setup.engine, setup.pop, setup.smtp, email="other@example.com", incoming_host="127.0.0.1")
    assert len(call(setup.engine, "account.list")) == 1


def test_failed_account_creation_leaves_nothing_behind(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.store import Store

    def failing(*args: object, **kwargs: object) -> Store:
        raise StorageError("disk full")

    before = setup.engine.config()
    monkeypatch.setattr(Store, "create", failing)
    with pytest.raises(StorageError):
        add_account(setup.engine, setup.pop, setup.smtp, email="other@example.com")
    monkeypatch.undo()
    assert setup.engine.config() == before
    assert len(list((setup.engine.directories.data_directory / "accounts").iterdir())) == 1


def test_unencrypted_account(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({"u": mail(1)}) as pop, FakeSmtpServer() as smtp:
        account_id = add_account(engine, pop, smtp, encrypted=False)
        assert engine.config().key_id is None
        call(engine, "mail.fetch", account=account_id)
        assert any(b"body number 1" in path.read_bytes() for path in (engine.directories.data_directory).rglob("*") if path.is_file() and path.parent.name == "new")


def test_account_is_found_by_id_prefix_or_email(setup: Setup) -> None:
    for identifier in (setup.account_id, setup.account_id[:8], "ME@example.com"):
        assert getattr(call(setup.engine, "folder.list", account=identifier)[0], "path") == "Drafts"
    with pytest.raises(ConfigurationError):
        call(setup.engine, "folder.list", account="nobody@example.com")
    with pytest.raises(ConfigurationError):
        call(setup.engine, "folder.list", account=setup.account_id[:3])


def test_removing_an_account_keeps_the_mail(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    result = call(setup.engine, "account.remove", account=setup.account_id)
    assert setup.engine.config().accounts == ()
    assert setup.engine.passwords.get(setup.account_id, ServerRole.INCOMING) is None
    assert Path(str(getattr(result, "data_kept_in"))).is_dir() and any(Path(str(getattr(result, "data_kept_in"))).rglob("new/*"))


def test_policy_change(setup: Setup) -> None:
    call(setup.engine, "account.policy", account=setup.account_id, policy="delete_after_days", days=7)
    assert setup.engine.config().accounts[0].pop3.days == 7
    with pytest.raises(ConfigurationError):
        call(setup.engine, "account.policy", account=setup.account_id, policy="delete_after_days", days=0)


def test_key_status_export_and_restore_cycle(setup: Setup, tmp_path: Path) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    status = call(setup.engine, "key.status")
    assert getattr(status, "in_credential_store") is True
    target = tmp_path / "backup" / "recovery.key"
    call(setup.engine, "key.export-file", path=str(target), passphrase="a good passphrase")
    if sys.platform != "win32":  # Windows has no such permission bits; there the folder's access rules protect the file
        assert oct(target.stat().st_mode & 0o777) == "0o600"
    with pytest.raises(UsageError):
        call(setup.engine, "key.export-file", path=str(target), passphrase="x")
    code = str(getattr(call(setup.engine, "key.recovery-code"), "code"))
    # the credential store is lost (new computer, wiped wallet)
    assert isinstance(setup.engine.keys, InMemoryKeyStore)
    setup.engine.keys.delete(bytes.fromhex(str(setup.engine.config().key_id)))
    assert getattr(call(setup.engine, "key.status"), "in_credential_store") is False
    assert len(getattr(call(setup.engine, "message.list", account=setup.account_id), "messages")) == 3  # a running session keeps its key
    setup.engine.forget_keys()  # a restart
    with pytest.raises(CryptoError):
        call(setup.engine, "folder.list", account=setup.account_id)
    with pytest.raises(CryptoError):
        call(setup.engine, "key.restore-file", path=str(target), passphrase="wrong")
    call(setup.engine, "key.restore-file", path=str(target), passphrase="a good passphrase")
    assert len(getattr(call(setup.engine, "message.list", account=setup.account_id), "messages")) == 3  # the mail reads again
    setup.engine.keys.delete(bytes.fromhex(str(setup.engine.config().key_id)))
    setup.engine.forget_keys()
    with pytest.raises(CryptoError):
        call(setup.engine, "key.restore-code", code="AAAA-BBBB")
    call(setup.engine, "key.restore-code", code=code)
    assert getattr(call(setup.engine, "key.status"), "in_credential_store") is True


def test_a_key_of_another_installation_is_refused(setup: Setup, tmp_path: Path) -> None:
    other = make_engine(tmp_path / "other")
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        add_account(other, pop, smtp)
    target = tmp_path / "other.key"
    call(other, "key.export-file", path=str(target), passphrase="pw")
    with pytest.raises(CryptoError, match="not the key"):
        call(setup.engine, "key.restore-file", path=str(target), passphrase="pw")


def test_missing_recovery_file(setup: Setup, tmp_path: Path) -> None:
    with pytest.raises(UsageError):
        call(setup.engine, "key.restore-file", path=str(tmp_path / "nothing.key"), passphrase="x")


# --- receiving and reading ---------------------------------------------------------------------------


def test_fetch_list_show(setup: Setup) -> None:
    result = call(setup.engine, "mail.fetch", account=setup.account_id)
    assert (getattr(result, "downloaded"), getattr(result, "failed"), getattr(result, "aborted")) == (3, (), None)
    listing = call(setup.engine, "message.list", account=setup.account_id)
    assert (getattr(listing, "total"), getattr(listing, "unread")) == (3, 3)
    rows = getattr(listing, "messages")
    assert {row.subject for row in rows} == {"Message 1", "Message 2", "Message 3"}
    shown = call(setup.engine, "message.show", account=setup.account_id, folder="inbox", message=rows[0].message_id)
    assert getattr(shown, "text_body").startswith("body number") and getattr(shown, "sender") == ("Alice <alice@example.com>",)
    assert getattr(shown, "flags") == ()
    assert getattr(call(setup.engine, "mail.fetch", account=setup.account_id), "downloaded") == 0


def test_fetch_reports_progress(setup: Setup) -> None:
    seen: list[tuple[str, int, int | None]] = []
    setup.engine.progress = lambda what, done, total: seen.append((what, done, total))
    call(setup.engine, "mail.fetch", account=setup.account_id)
    assert ("Downloading", 3, 3) in seen


def test_fetch_with_a_wrong_password_is_an_authentication_error(setup: Setup) -> None:
    setup.engine.passwords.set(setup.account_id, ServerRole.INCOMING, "wrong")
    with pytest.raises(AuthenticationError):
        call(setup.engine, "mail.fetch", account=setup.account_id)


def test_fetch_without_a_stored_password(setup: Setup) -> None:
    setup.engine.passwords.delete(setup.account_id, ServerRole.INCOMING)
    with pytest.raises(ConfigurationError, match="password"):
        call(setup.engine, "mail.fetch", account=setup.account_id)


def test_fetch_with_the_server_down(setup: Setup) -> None:
    setup.pop.__exit__(None, None, None)
    with pytest.raises(ProtocolError):
        call(setup.engine, "mail.fetch", account=setup.account_id)


def test_delete_after_download_policy_through_the_engine(setup: Setup) -> None:
    call(setup.engine, "account.policy", account=setup.account_id, policy="delete_after_download")
    result = call(setup.engine, "mail.fetch", account=setup.account_id)
    assert getattr(result, "deleted_on_server") == 3 and setup.pop.messages == {}


def test_list_sorting_paging_and_unread(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    page = call(setup.engine, "message.list", account=setup.account_id, sort="subject", oldest_first=True, limit=2)
    assert [row.subject for row in getattr(page, "messages")] == ["Message 1", "Message 2"]
    rest = call(setup.engine, "message.list", account=setup.account_id, sort="subject", oldest_first=True, offset=2, limit=2)
    assert [row.subject for row in getattr(rest, "messages")] == ["Message 3"]
    first = getattr(page, "messages")[0]
    call(setup.engine, "message.flag", account=setup.account_id, folder="Inbox", message=first.message_id, seen=True)
    unread = call(setup.engine, "message.list", account=setup.account_id, unread_only=True)
    assert getattr(unread, "unread") == 2 and len(getattr(unread, "messages")) == 2
    with pytest.raises(UsageError):
        call(setup.engine, "message.list", account=setup.account_id, folder="Nowhere")


def test_search(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    found = call(setup.engine, "message.search", account=setup.account_id, query="number 2")
    assert [row.subject for row in found] == ["Message 2"]
    assert len(call(setup.engine, "message.search", account=setup.account_id, query="from:alice")) == 3
    assert call(setup.engine, "message.search", account=setup.account_id, query="zzzz") == ()


# --- changing messages and folders ---------------------------------------------------------------------


def first_id(setup: Setup) -> str:
    return str(getattr(call(setup.engine, "message.list", account=setup.account_id), "messages")[0].message_id)


def test_flags(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    message = first_id(setup)
    result = call(setup.engine, "message.flag", account=setup.account_id, folder="Inbox", message=message, seen=True, flagged=True)
    assert getattr(result, "flags") == ("flagged", "seen")
    result = call(setup.engine, "message.flag", account=setup.account_id, folder="Inbox", message=message, flagged=False, replied=True)
    assert getattr(result, "flags") == ("replied", "seen")
    shown = call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=message)
    assert getattr(shown, "flags") == ("replied", "seen")


@pytest.mark.parametrize("bad", ["", "xyz", "0" * 31, "G" * 32, "../../../etc/passwd"])
def test_malformed_message_ids_are_usage_errors(setup: Setup, bad: str) -> None:
    with pytest.raises(UsageError):
        call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=bad)


def test_unknown_message(setup: Setup) -> None:
    with pytest.raises(StorageError):
        call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message="0" * 32)


def test_move_and_copy(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    call(setup.engine, "folder.create", account=setup.account_id, folder="Archive/2026")
    message = first_id(setup)
    copied = call(setup.engine, "message.copy", account=setup.account_id, folder="Inbox", message=message, target="Archive/2026")
    assert getattr(copied, "message_id") != message
    moved = call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=message, target="Archive/2026")
    assert getattr(moved, "folder") == "Archive/2026"
    folders = {item.path: item for item in call(setup.engine, "folder.list", account=setup.account_id)}
    assert folders["Archive/2026"].messages == 2 and folders["Inbox"].messages == 2
    assert [row.subject for row in call(setup.engine, "message.search", account=setup.account_id, query="number", folder="Archive/2026")] != []


def test_delete_moves_to_trash_and_then_deletes_for_good(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    message = first_id(setup)
    first = call(setup.engine, "message.delete", account=setup.account_id, folder="Inbox", message=message)
    assert getattr(first, "outcome") == "moved to Trash"
    assert getattr(call(setup.engine, "message.list", account=setup.account_id, folder="Trash"), "total") == 1
    second = call(setup.engine, "message.delete", account=setup.account_id, folder="Trash", message=message)
    assert getattr(second, "outcome") == "deleted"
    assert getattr(call(setup.engine, "message.list", account=setup.account_id, folder="Trash"), "total") == 0
    other = [row.message_id for row in getattr(call(setup.engine, "message.list", account=setup.account_id), "messages")][0]
    assert getattr(call(setup.engine, "message.delete", account=setup.account_id, folder="Inbox", message=other, permanent=True), "outcome") == "deleted"


def test_deleting_a_message_whose_file_is_gone_removes_it_from_the_list(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    before = getattr(call(setup.engine, "message.list", account=setup.account_id), "messages")
    gone = [row.message_id for row in before][:2]
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        for message_id in gone:
            files = list(mailbox.store.root.rglob(f"{message_id}*"))
            assert len(files) == 1
            files[0].unlink()  # the file is lost; the index still lists the message
    to_trash = call(setup.engine, "message.delete", account=setup.account_id, folder="Inbox", message=gone[0])
    for_good = call(setup.engine, "message.delete", account=setup.account_id, folder="Inbox", message=gone[1], permanent=True)
    assert getattr(to_trash, "outcome") == getattr(for_good, "outcome") == "gone"
    after = getattr(call(setup.engine, "message.list", account=setup.account_id), "messages")
    assert len(after) == len(before) - 2 and not {row.message_id for row in after} & set(gone)
    assert getattr(call(setup.engine, "message.list", account=setup.account_id, folder="Trash"), "total") == 0
    third = [row.message_id for row in after][0]
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        next(mailbox.store.root.rglob(f"{third}*")).unlink()
    junk = call(setup.engine, "message.junk", account=setup.account_id, folder="Inbox", message=third)
    assert getattr(junk, "gone") and len(getattr(call(setup.engine, "message.list", account=setup.account_id), "messages")) == len(after) - 1
    assert getattr(call(setup.engine, "message.list", account=setup.account_id, folder="Junk"), "total") == 0


def test_two_rebuilds_of_the_same_index_at_once_both_succeed(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    call(setup.engine, "account.set-storage", account=setup.account_id, storage="local")  # the account now shares the Local Folders index
    results: list[object] = []

    def rebuild(account: str) -> None:
        try:
            results.append(call(setup.engine, "index.rebuild", account=account))
        except Exception as error:  # noqa: BLE001 - any failure is the finding
            results.append(error)

    workers = [threading.Thread(target=rebuild, args=(account,)) for account in (setup.account_id, LOCAL_ACCOUNT_ID, setup.account_id)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert [getattr(result, "messages", result) for result in results] == [3, 3, 3]


def test_commands_that_open_an_old_index_at_once_all_succeed(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    call(setup.engine, "account.set-storage", account=setup.account_id, storage="local")  # the account shares the Local Folders index
    path = setup.engine.directories.index_file(LOCAL_ACCOUNT_ID)
    with closing(sqlite3.connect(path)) as connection, connection:  # closed too, not only committed: on Windows an open file cannot be replaced
        connection.execute("UPDATE meta SET value = '1' WHERE key = 'schema'")  # an old format: the next command rebuilds it
    results: list[object] = []

    def listed(account: str) -> None:
        try:
            results.append(call(setup.engine, "message.list", account=account, folder="Inbox"))
        except Exception as error:  # noqa: BLE001 - any failure is the finding
            results.append(error)

    workers = [threading.Thread(target=listed, args=(account,)) for account in (setup.account_id, LOCAL_ACCOUNT_ID) * 3]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert [getattr(result, "total", result) for result in results] == [3] * 6


def test_the_index_is_opened_only_inside_its_lock(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.engine import session

    held: list[bool] = []
    real_index = session.Index

    def watched(path: Path) -> object:
        held.append(session.index_lock(Path(path)).locked())  # no command may have the files open while another replaces them
        return real_index(path)

    monkeypatch.setattr(session, "Index", watched)
    call(setup.engine, "mail.fetch", account=setup.account_id)
    call(setup.engine, "message.list", account=setup.account_id, folder="Inbox")
    assert held and all(held)


def test_folder_commands(setup: Setup) -> None:
    call(setup.engine, "folder.create", account=setup.account_id, folder="Projects")
    call(setup.engine, "folder.create", account=setup.account_id, folder="Projects/Alpha")
    call(setup.engine, "mail.fetch", account=setup.account_id)
    message = first_id(setup)
    call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=message, target="Projects/Alpha")
    call(setup.engine, "folder.rename", account=setup.account_id, folder="Projects", new_name="Work")
    paths = [item.path for item in call(setup.engine, "folder.list", account=setup.account_id)]
    assert "Work/Alpha" in paths and "Projects/Alpha" not in paths
    assert getattr(call(setup.engine, "message.list", account=setup.account_id, folder="Work/Alpha"), "total") == 1
    with pytest.raises(StorageError):
        call(setup.engine, "folder.delete", account=setup.account_id, folder="Work/Alpha")
    result = call(setup.engine, "folder.delete", account=setup.account_id, folder="Work/Alpha", with_messages=True)
    assert getattr(result, "messages_removed") == 1 and any(Path(str(getattr(result, "kept_in"))).iterdir())
    with pytest.raises(UsageError):
        call(setup.engine, "folder.delete", account=setup.account_id, folder="Inbox")
    with pytest.raises(StorageError):
        call(setup.engine, "folder.create", account=setup.account_id, folder="Work")
    with pytest.raises(UsageError):
        call(setup.engine, "folder.create", account=setup.account_id, folder="a//b")


# --- sending -----------------------------------------------------------------------------------------------


def test_send_files_the_message_in_sent(setup: Setup, tmp_path: Path) -> None:
    attachment = tmp_path / "notes.txt"
    attachment.write_text("attached notes")
    result = call(setup.engine, "mail.send", account=setup.account_id, to="You <you@example.com>", cc="cc@example.com", bcc="hidden@example.com", subject="Hello", body="Hi there", attach=str(attachment))
    assert getattr(result, "sent") is True and getattr(result, "folder") == "Sent" and getattr(result, "error") is None
    (received,) = setup.smtp.received
    assert received.recipients == ("you@example.com", "cc@example.com", "hidden@example.com")
    assert b"hidden@example.com" not in received.data and b"notes.txt" in received.data
    sent = call(setup.engine, "message.show", account=setup.account_id, folder="Sent", message=getattr(result, "message_id"))
    assert getattr(sent, "subject") == "Hello" and getattr(sent, "attachments")[0].filename == "notes.txt"
    assert getattr(call(setup.engine, "message.list", account=setup.account_id, folder="Outbox"), "total") == 0


def test_failed_send_stays_in_the_outbox(setup: Setup) -> None:
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "wrong")
    result = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Stuck", body="x")
    assert getattr(result, "sent") is False and getattr(result, "folder") == "Outbox" and getattr(result, "error") is not None
    assert getattr(call(setup.engine, "message.list", account=setup.account_id, folder="Outbox"), "total") == 1
    assert setup.smtp.received == []


def test_send_input_errors_store_nothing(setup: Setup) -> None:
    with pytest.raises(UsageError):
        call(setup.engine, "mail.send", account=setup.account_id, to="not an address")
    with pytest.raises(UsageError):
        call(setup.engine, "mail.send", account=setup.account_id, to="a@example.com", attach="/no/such/file")
    with pytest.raises(MessageFormatError):
        call(setup.engine, "mail.send", account=setup.account_id, to="a@example.com", subject="x\r\nBcc: evil@example.com")
    assert getattr(call(setup.engine, "message.list", account=setup.account_id, folder="Outbox"), "total") == 0


# --- maintenance ----------------------------------------------------------------------------------------------


def test_the_index_is_rebuilt_automatically_when_it_is_gone_or_damaged(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    index_file = setup.engine.directories.index_file(setup.account_id)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{index_file}{suffix}").unlink(missing_ok=True)
    assert getattr(call(setup.engine, "message.list", account=setup.account_id), "total") == 3
    index_file.write_bytes(b"garbage" * 1000)
    for suffix in ("-wal", "-shm"):
        Path(f"{index_file}{suffix}").unlink(missing_ok=True)
    assert getattr(call(setup.engine, "message.list", account=setup.account_id), "total") == 3


def test_rebuild_reconcile_verify_recover(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    rebuilt = call(setup.engine, "index.rebuild", account=setup.account_id)
    assert (getattr(rebuilt, "messages"), getattr(rebuilt, "unreadable")) == (3, 0)
    reconciled = call(setup.engine, "index.reconcile", account=setup.account_id)
    assert (getattr(reconciled, "added"), getattr(reconciled, "removed"), getattr(reconciled, "corrected")) == (0, 0, 0)
    assert getattr(call(setup.engine, "store.verify", account=setup.account_id), "damaged") == ()
    victim = next(setup.engine.directories.data_directory.rglob("new/*"))
    data = bytearray(victim.read_bytes())
    data[-1] ^= 1
    victim.write_bytes(bytes(data))
    verified = call(setup.engine, "store.verify", account=setup.account_id)
    assert getattr(verified, "checked") == 3 and len(getattr(verified, "damaged")) == 1
    recovered = call(setup.engine, "store.recover", account=setup.account_id)
    assert getattr(recovered, "damaged_folders") == ()


def test_the_whole_set_of_results_can_be_turned_into_json(setup: Setup) -> None:
    from chrono_mail.engine import to_data

    call(setup.engine, "mail.fetch", account=setup.account_id)
    cases: list[tuple[str, dict[str, object]]] = [
        ("account.list", {}), ("key.status", {}), ("folder.list", {"account": setup.account_id}),
        ("message.list", {"account": setup.account_id}), ("message.search", {"account": setup.account_id, "query": "message"}),
    ]
    for command, values in cases:
        json.dumps(to_data(call(setup.engine, command, **values)))


# --- what the interface needs (v0.0.13) --------------------------------------------------------------------------


def with_attachments(count: int = 1) -> bytes:
    import base64

    parts = [b"From: Alice <alice@example.com>\r\nTo: me@example.com\r\nCc: bob@example.com, me@example.com\r\nSubject: Photos\r\nMessage-ID: <photos@x>\r\nReferences: <root@x>\r\nDate: Thu, 08 Oct 2026 10:30:00 +0000\r\nMIME-Version: 1.0\r\nContent-Type: multipart/related; boundary=B\r\n\r\n",
             b"--B\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>Look: <img src=\"cid:logo@x\"></p>\r\n",
             b"--B\r\nContent-Type: image/png\r\nContent-ID: <logo@x>\r\nContent-Disposition: inline\r\nContent-Transfer-Encoding: base64\r\n\r\n" + base64.encodebytes(b"\x89PNG-data") + b"\r\n"]
    for number in range(count):
        parts.append(b"--B\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=\"../../etc/re:port?.pdf\"\r\nContent-Transfer-Encoding: base64\r\n\r\n" + base64.encodebytes(b"%PDF-" + str(number).encode()) + b"\r\n")
    parts.append(b"--B--\r\n")
    return b"".join(parts)


@pytest.fixture
def rich(tmp_path: Path) -> Generator[Setup]:
    engine = make_engine(tmp_path)
    with FakePop3Server({"rich": with_attachments(2)}) as pop, FakeSmtpServer() as smtp:
        account = add_account(engine, pop, smtp)
        call(engine, "mail.fetch", account=account)
        yield Setup(engine, pop, smtp, account)


def rich_id(setup: Setup) -> str:
    return first_id(setup)


def test_list_rows_have_a_preview(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    rows = getattr(call(setup.engine, "message.list", account=setup.account_id), "messages")
    assert all(row.preview.startswith("body number") for row in rows)
    assert all(row.preview == "body number 1" for row in call(setup.engine, "message.search", account=setup.account_id, query="number 1"))


def test_show_gives_inline_images_and_safe_names(rich: Setup) -> None:
    shown = call(rich.engine, "message.show", account=rich.account_id, folder="Inbox", message=rich_id(rich))
    (image,) = getattr(shown, "inline_images")
    assert image.content_id == "logo@x" and image.content_type == "image/png"
    import base64
    assert base64.b64decode(image.data_base64) == b"\x89PNG-data"
    attachments = getattr(shown, "attachments")
    assert [a.inline for a in attachments] == [True, False, False]
    assert attachments[1].suggested_name == "re_port_.pdf"  # no path, no reserved characters
    assert getattr(shown, "reply_to") == ()


@pytest.mark.parametrize(("name", "content_type", "expected"), [("", "application/pdf", "attachment-3.pdf"), (None, "image/png", "attachment-3.png"), ("a/b\\c.txt", "text/plain", "c.txt"), ("..", "x/y", "attachment-3.bin"), ("x" * 400, "text/plain", "x" * 150), ("CON:.txt\x00", "text/plain", "CON_.txt_"), ("  .hidden", "text/plain", "hidden")])
def test_suggested_file_names(name: str | None, content_type: str, expected: str) -> None:
    from chrono_mail.engine.compose import suggest_filename

    assert suggest_filename(name, content_type, 2) == expected


def test_save_an_attachment(rich: Setup, tmp_path: Path) -> None:
    message = rich_id(rich)
    target = tmp_path / "saved.pdf"
    result = call(rich.engine, "message.attachment.save", account=rich.account_id, folder="Inbox", message=message, index=2, path=str(target))
    assert target.read_bytes() == b"%PDF-1" and getattr(result, "size") == 6 and getattr(result, "content_type") == "application/pdf"
    with pytest.raises(UsageError, match="exists"):
        call(rich.engine, "message.attachment.save", account=rich.account_id, folder="Inbox", message=message, index=1, path=str(target))
    call(rich.engine, "message.attachment.save", account=rich.account_id, folder="Inbox", message=message, index=1, path=str(target), overwrite=True)
    assert target.read_bytes() == b"%PDF-0"
    for bad in (-1, 3, 99):
        with pytest.raises(UsageError, match="no such attachment"):
            call(rich.engine, "message.attachment.save", account=rich.account_id, folder="Inbox", message=message, index=bad, path=str(tmp_path / "x"))
    with pytest.raises(UsageError, match="cannot be written"):
        call(rich.engine, "message.attachment.save", account=rich.account_id, folder="Inbox", message=message, index=1, path=str(tmp_path / "no" / "such" / "dir" / "x"))
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []  # no half-written file left behind


def test_compose_reply_reply_all_and_forward(rich: Setup) -> None:
    message = rich_id(rich)
    reply = call(rich.engine, "message.compose", account=rich.account_id, folder="Inbox", message=message)
    assert getattr(reply, "to") == ("Alice <alice@example.com>",) and getattr(reply, "cc") == ()
    assert getattr(reply, "subject") == "Re: Photos" and getattr(reply, "in_reply_to") == "<photos@x>"
    assert getattr(reply, "references") == ("<root@x>", "<photos@x>")
    assert "wrote:" in getattr(reply, "body") and "> Look:" in getattr(reply, "body")
    everyone = call(rich.engine, "message.compose", account=rich.account_id, folder="Inbox", message=message, kind="reply_all")
    assert getattr(everyone, "to") == ("Alice <alice@example.com>",) and getattr(everyone, "cc") == ("bob@example.com",)  # not myself
    forward = call(rich.engine, "message.compose", account=rich.account_id, folder="Inbox", message=message, kind="forward")
    assert getattr(forward, "subject") == "Fwd: Photos" and getattr(forward, "to") == ()
    assert "Forwarded message" in getattr(forward, "body")
    assert getattr(forward, "attachment_names") == ("re_port_.pdf", "re_port_.pdf")
    with pytest.raises(UsageError):
        call(rich.engine, "message.compose", account=rich.account_id, folder="Inbox", message=message, kind="shout")


def test_forward_sends_the_attachments_and_a_reply_marks_the_original(rich: Setup) -> None:
    message = rich_id(rich)
    call(rich.engine, "mail.send", account=rich.account_id, to="you@example.com", subject="Fwd: Photos", body="see below", source_folder="Inbox", source_message=message, include_source_attachments=True)
    (received,) = rich.smtp.received
    assert received.data.count(b"Content-Disposition: attachment") == 2
    call(rich.engine, "mail.send", account=rich.account_id, to="alice@example.com", subject="Re: Photos", body="thanks", in_reply_to="<photos@x>", references=("<root@x>", "<photos@x>"), source_folder="Inbox", source_message=message, mark_replied=True)
    assert b"References: <root@x> <photos@x>" in rich.smtp.received[1].data
    shown = call(rich.engine, "message.show", account=rich.account_id, folder="Inbox", message=message)
    assert "replied" in getattr(shown, "flags")
    with pytest.raises(UsageError, match="source"):
        call(rich.engine, "mail.send", account=rich.account_id, to="a@example.com", include_source_attachments=True)


def test_a_failed_reply_is_not_marked_as_replied(rich: Setup) -> None:
    from chrono_mail.config import ServerRole

    rich.engine.passwords.set(rich.account_id, ServerRole.OUTGOING, "wrong")
    message = rich_id(rich)
    result = call(rich.engine, "mail.send", account=rich.account_id, to="alice@example.com", subject="Re: Photos", source_folder="Inbox", source_message=message, mark_replied=True)
    assert getattr(result, "sent") is False
    assert "replied" not in getattr(call(rich.engine, "message.show", account=rich.account_id, folder="Inbox", message=message), "flags")


def test_account_check(setup: Setup) -> None:
    good = dict(username="user", password="secret", incoming_host="127.0.0.1", incoming_port=setup.pop.port, incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=setup.smtp.port, outgoing_security="none")
    result = call(setup.engine, "account.check", **good)
    assert (getattr(result, "incoming_ok"), getattr(result, "outgoing_ok")) == (True, True)
    assert not any(command.startswith(("MAIL", "DELE")) for command in setup.smtp.commands + setup.pop.commands)
    bad = call(setup.engine, "account.check", **{**good, "password": "wrong-password-xyz"})
    assert (getattr(bad, "incoming_ok"), getattr(bad, "outgoing_ok")) == (False, False)
    assert "wrong-password-xyz" not in getattr(bad, "incoming_message") + getattr(bad, "outgoing_message")
    unreachable = call(setup.engine, "account.check", **{**good, "incoming_port": 1, "outgoing_host": "no-such-host.invalid"})
    assert getattr(unreachable, "incoming_ok") is False and getattr(unreachable, "outgoing_ok") is False
    invalid = call(setup.engine, "account.check", **{**good, "incoming_port": 70000, "outgoing_port": 0})
    assert getattr(invalid, "incoming_ok") is False and "port" in getattr(invalid, "incoming_message")
    assert len(setup.engine.config().accounts) == 1  # nothing was created


def test_the_key_is_fetched_from_the_credential_store_once(setup: Setup) -> None:
    class Counting(InMemoryKeyStore):
        loads = 0

        @override
        def load(self, key_id: bytes) -> DataKey:
            Counting.loads += 1
            return super().load(key_id)

    source = setup.engine.keys
    assert isinstance(source, InMemoryKeyStore)
    counting = Counting()
    counting.save(setup.engine.data_key(setup.engine.config()))
    setup.engine.keys = counting
    setup.engine.forget_keys()
    for _ in range(5):
        call(setup.engine, "message.list", account=setup.account_id)
    assert Counting.loads == 1


def test_a_message_with_only_a_blind_copy_recipient(setup: Setup) -> None:
    result = call(setup.engine, "mail.send", account=setup.account_id, bcc="hidden@example.com", subject="Undisclosed", body="x")
    assert getattr(result, "sent") is True
    (received,) = setup.smtp.received
    assert received.recipients == ("hidden@example.com",) and b"hidden@example.com" not in received.data
    with pytest.raises(UsageError, match="no recipient"):
        call(setup.engine, "mail.send", account=setup.account_id, subject="Nobody")


def test_deleting_a_folder_with_its_subfolders(setup: Setup) -> None:
    for path in ("Work", "Work/Alpha", "Work/Alpha/Deep", "Workshop"):
        call(setup.engine, "folder.create", account=setup.account_id, folder=path)
    call(setup.engine, "mail.fetch", account=setup.account_id)
    message = first_id(setup)
    call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=message, target="Work/Alpha/Deep")
    with pytest.raises(StorageError):
        call(setup.engine, "folder.delete", account=setup.account_id, folder="Work", include_subfolders=True)  # not empty below
    plain = call(setup.engine, "folder.delete", account=setup.account_id, folder="Work/Alpha/Deep", with_messages=True)
    assert getattr(plain, "folders_removed") == 1 and getattr(plain, "messages_removed") == 1
    call(setup.engine, "folder.create", account=setup.account_id, folder="Work/Alpha/Deep")
    call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=first_id(setup), target="Work/Alpha/Deep")
    result = call(setup.engine, "folder.delete", account=setup.account_id, folder="Work", with_messages=True, include_subfolders=True)
    assert getattr(result, "folders_removed") == 3 and getattr(result, "messages_removed") == 1
    paths = [item.path for item in call(setup.engine, "folder.list", account=setup.account_id)]
    assert "Workshop" in paths and not any(path == "Work" or path.startswith("Work/") for path in paths)  # a folder with a similar name stays


def test_an_index_with_an_older_schema_is_rebuilt_automatically(tmp_path: Path) -> None:
    import sqlite3

    engine = make_engine(tmp_path)
    messages = {"u": raw_message(subject="Newsletter", body="[<https://tracker.example/x>\n------ HELLO Rob\n", message_id="<n@x>")}
    with FakePop3Server(messages) as pop, FakeSmtpServer() as smtp:
        account_id = add_account(engine, pop, smtp)
        call(engine, "mail.fetch", account=account_id)
        index_file = engine.directories.index_file(account_id)
        connection = sqlite3.connect(index_file)
        connection.execute("UPDATE meta SET value = '2' WHERE key = 'schema'")
        connection.execute("UPDATE messages SET preview = '[<https://tracker.example/x> ------ HELLO Rob'")
        connection.commit()
        connection.close()
        listing = call(engine, "message.list", account=account_id)  # opens the account: the old index is rebuilt
        assert getattr(listing, "total") == 1 and getattr(listing, "messages")[0].preview == "HELLO Rob"


def test_the_same_login_on_the_same_server_through_another_protocol_is_another_account(setup: Setup) -> None:
    imap = add_account(setup.engine, setup.pop, setup.smtp, incoming_host="127.0.0.1", kind="imap")  # the same mailbox through IMAP
    accounts = call(setup.engine, "account.list")
    assert len(accounts) == 2 and {item.kind for item in accounts} == {"pop3", "imap"} and imap != setup.account_id
    first, second = accounts
    assert first.label == "me@example.com - 127.0.0.1 (POP3)" and second.label == "me@example.com - 127.0.0.1 (IMAP)"
    assert first.shown_name == first.label and second.shown_name == second.label  # the same address twice: each is told apart by its server
    with pytest.raises(ConfigurationError, match="exists already"):
        add_account(setup.engine, setup.pop, setup.smtp, email="x@example.com", incoming_host="127.0.0.1", kind="imap")  # not twice through the same protocol
    other = add_account(setup.engine, setup.pop, setup.smtp, email="other@example.com", incoming_host="127.0.0.1", username="someone")
    assert next(item for item in call(setup.engine, "account.list") if item.account_id == other).shown_name == "other@example.com"  # a unique address stays short


def test_the_position_of_a_message_is_where_message_list_shows_it(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    for sort in ("date", "subject", "size", "contact", "unread"):
        for oldest_first in (False, True):
            listed = [row.message_id for row in call(setup.engine, "message.list", account=setup.account_id, sort=sort, oldest_first=oldest_first).messages]
            for place, message_id in enumerate(listed):
                found = call(setup.engine, "message.position", account=setup.account_id, folder="Inbox", message=message_id, sort=sort, oldest_first=oldest_first)
                assert found.position == place, (sort, oldest_first)
    assert call(setup.engine, "message.position", account=setup.account_id, folder="Sent", message=listed[0]).position is None  # not in that folder


def test_a_question_waits_while_a_change_is_written_but_not_yet_told(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    told: list[Change] = []
    setup.engine.changed = told.append
    written, release = threading.Event(), threading.Event()
    answered: list[int] = []
    raw = mail(9)

    def change() -> None:
        with open_mailbox(setup.engine, setup.account_id) as mailbox, mailbox.changing():
            reference = mailbox.store.deliver(mailbox.folder("Inbox"), raw, verify=True)
            mailbox.index.add(entry_from_raw(reference, mailbox.store.locate(reference), frozenset(), len(raw), raw))
            written.set()
            release.wait(5)
            mailbox.report_added(reference)

    writer = threading.Thread(target=change)
    writer.start()
    assert written.wait(5)
    asker = threading.Thread(target=lambda: answered.append(call(setup.engine, "message.list", account=setup.account_id, folder="Inbox").total))
    asker.start()
    asker.join(0.3)
    assert answered == []  # it waits: the index has the message, but its fact is not told yet
    release.set()
    writer.join(5)
    asker.join(5)
    assert answered == [4] and told[-1].kind == "added"  # the fact first, then an answer that counts it


def test_a_pop3_check_tells_each_new_message_as_a_fact(setup: Setup) -> None:
    told: list[Change] = []
    setup.engine.changed = told.append
    result = call(setup.engine, "mail.fetch", account=setup.account_id)
    added = [change for change in told if change.kind == "added"]
    assert len(added) == result.downloaded > 0 and all(change.row is not None and change.folder == "Inbox" for change in added)
    assert not [change for change in told if change.kind == "mailbox_changed"]


def test_an_action_on_one_message_never_lists_its_folder(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    first, second = (row.message_id for row in call(setup.engine, "message.list", account=setup.account_id).messages[:2])

    def listed(store: Store, folder_path: tuple[str, ...]) -> list[object]:
        raise AssertionError("an action on one message listed its whole folder")

    monkeypatch.setattr(Store, "list_messages", listed)
    call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=first)  # opening reads the flags
    call(setup.engine, "message.flag", account=setup.account_id, folder="Inbox", message=first, flagged=True)
    moved = call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=second, target="Trash")
    assert moved is not None
    monkeypatch.undo()
    flags = {row.message_id: row.flags for row in call(setup.engine, "message.list", account=setup.account_id).messages}
    assert "flagged" in flags[first] and second not in flags


def test_a_message_read_while_shown_stays_in_a_search_of_unread_mail(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    unread = [row.message_id for row in call(setup.engine, "message.search", account=setup.account_id, folder="Inbox", unread=True)]
    read = unread[0]
    call(setup.engine, "message.flag", account=setup.account_id, folder="Inbox", message=read, seen=True)
    values = {"account": setup.account_id, "folder": "Inbox", "unread": True}
    assert read not in [row.message_id for row in call(setup.engine, "message.search", **values)]
    kept = [row.message_id for row in call(setup.engine, "message.search", stay=(read,), **values)]
    assert kept == unread  # the same messages in the same order: the read one stays where it was
    assert sum(group.count for group in call(setup.engine, "message.groups", stay=(read,), **values)) == len(unread)
    assert call(setup.engine, "message.places", stay=(read,), messages=(read,), **values).positions == (0,)
    assert [row.message_id for row in call(setup.engine, "message.search", account=setup.account_id, folder="Inbox", stay=(read,))] == [
        row.message_id for row in call(setup.engine, "message.search", account=setup.account_id, folder="Inbox")
    ]  # without a filter of marks, stay changes nothing


def test_the_places_of_several_messages_are_where_message_list_and_message_search_show_them(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    for sort in ("date", "subject", "size", "contact", "unread", "snoozed"):
        for oldest_first in (False, True):
            listed = [row.message_id for row in call(setup.engine, "message.list", account=setup.account_id, sort=sort, oldest_first=oldest_first).messages]
            found = call(setup.engine, "message.places", account=setup.account_id, folder="Inbox", messages=tuple(reversed(listed)), sort=sort, oldest_first=oldest_first)
            assert found.positions == tuple(reversed(range(len(listed)))), (sort, oldest_first)  # each in the order asked
    searched = [row.message_id for row in call(setup.engine, "message.search", account=setup.account_id, folder="Inbox", unread=True, sort="subject")]
    found = call(setup.engine, "message.places", account=setup.account_id, folder="Inbox", unread=True, sort="subject", messages=tuple(searched))
    assert found.positions == tuple(range(len(searched))) and searched
    missing = call(setup.engine, "message.places", account=setup.account_id, folder="Sent", messages=(listed[0], "none"))
    assert missing.positions == (None, None)  # not in that folder, or not at all
    assert call(setup.engine, "message.places", account=setup.account_id, folder="Inbox").positions == ()
