import json
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any  # command results are command-specific dataclasses; the tests check their fields

import pytest

from chrono_mail.config import ServerRole
from chrono_mail.core import AuthenticationError, ConfigurationError, CryptoError, MessageFormatError, ProtocolError, StorageError
from chrono_mail.engine import Engine, UsageError, build_registry
from chrono_mail.crypto import InMemoryKeyStore
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_engine_commands.py 0.0.12"

# --- Version history ----------------------------------------------------
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


def test_duplicate_email_address_is_refused(setup: Setup) -> None:
    with pytest.raises(ConfigurationError):
        add_account(setup.engine, setup.pop, setup.smtp)


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
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    with pytest.raises(UsageError):
        call(setup.engine, "key.export-file", path=str(target), passphrase="x")
    code = str(getattr(call(setup.engine, "key.recovery-code"), "code"))
    # the credential store is lost (new computer, wiped wallet)
    assert isinstance(setup.engine.keys, InMemoryKeyStore)
    setup.engine.keys.delete(bytes.fromhex(str(setup.engine.config().key_id)))
    assert getattr(call(setup.engine, "key.status"), "in_credential_store") is False
    with pytest.raises(CryptoError):
        call(setup.engine, "folder.list", account=setup.account_id)
    with pytest.raises(CryptoError):
        call(setup.engine, "key.restore-file", path=str(target), passphrase="wrong")
    call(setup.engine, "key.restore-file", path=str(target), passphrase="a good passphrase")
    assert len(getattr(call(setup.engine, "message.list", account=setup.account_id), "messages")) == 3  # the mail reads again
    setup.engine.keys.delete(bytes.fromhex(str(setup.engine.config().key_id)))
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
