from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.core import AuthenticationError, ProtocolError
from chrono_mail.engine import Engine
from chrono_mail.engine.mailbox import open_mailbox
from tests.enginefactory import make_engine
from tests.fakeimap import FakeImapServer
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message
from tests.test_engine_commands import call

# --- Version ------------------------------------------------------------
__version__ = "test_engine_imap.py 0.4.120"

# --- Version history ----------------------------------------------------
# v0.4.120: Actions reach the server at once (no longer at the next fetch); the held-back test loses files instead of deleting
# v0.4.119: mail.fetch reports a folder held back by a safety rule; mail.settle answers
# v0.0.31: Initial tests of IMAP accounts in the engine


@dataclass
class World:
    engine: Engine
    imap: FakeImapServer
    smtp: FakeSmtpServer
    account: str

    def fetch(self) -> Any:  # noqa: ANN401 - a FetchResult
        return call(self.engine, "mail.fetch", account=self.account)

    def rows(self, folder: str) -> list[Any]:
        return list(call(self.engine, "message.list", account=self.account, folder=folder).messages)


def mail(number: int) -> bytes:
    return raw_message(subject=f"Message {number}", body=f"body {number}\r\n", message_id=f"<m{number}@x>")


@pytest.fixture
def world(tmp_path: Path) -> Generator[World]:
    engine = make_engine(tmp_path)
    with FakeImapServer(plain=False) as imap, FakeSmtpServer() as smtp:
        result = call(
            engine, "account.add", kind="imap", display_name="Me", email_address="me@example.com", username="user", password="secret",
            incoming_host="127.0.0.1", incoming_port=imap.port, incoming_security="none",
            outgoing_host="127.0.0.1", outgoing_port=smtp.port, outgoing_security="none",
        )
        yield World(engine, imap, smtp, str(result.account_id))


def test_an_imap_account_is_added_and_listed_as_imap(world: World) -> None:
    (item,) = call(world.engine, "account.list")
    assert item.kind == "imap" and item.account_id == world.account
    with pytest.raises(Exception, match="kind|pop3|imap"):
        call(world.engine, "account.add", kind="exchange", display_name="x", email_address="x@example.com", username="u", password="p", incoming_host="h", outgoing_host="h")


def test_fetch_downloads_the_server_folders_and_their_flags(world: World) -> None:
    world.imap.add("INBOX", mail(1), {"\\Seen"})
    world.imap.add("INBOX", mail(2))
    world.imap.add("Sent", mail(3), {"\\Seen"})
    result = world.fetch()
    assert result.downloaded == 3 and result.aborted is None
    inbox = {row.subject: row for row in world.rows("Inbox")}
    assert set(inbox) == {"Message 1", "Message 2"} and inbox["Message 1"].flags == ("seen",) and inbox["Message 2"].flags == ()
    assert [row.subject for row in world.rows("Sent")] == ["Message 3"]
    folders = {info.path: info for info in call(world.engine, "folder.list", account=world.account)}
    assert folders["Inbox"].unread == 1 and folders["Sent"].unread == 0
    assert world.fetch().downloaded == 0


def test_commands_on_an_imap_account_reach_the_server_at_once(world: World) -> None:
    uid = world.imap.add("INBOX", mail(1))
    world.imap.add("INBOX", mail(2))
    world.fetch()
    first = next(row for row in world.rows("Inbox") if row.subject == "Message 1")
    call(world.engine, "message.flag", account=world.account, folder="Inbox", message=first.message_id, seen=True, flagged=True)
    assert world.imap.flags_of("INBOX", uid) == {"\\Seen", "\\Flagged"}  # on the server at once, before any sync
    call(world.engine, "message.delete", account=world.account, folder="Inbox", message=first.message_id)  # to Trash
    assert world.imap.uids("INBOX") == [2] and len(world.imap.uids("Trash")) == 1
    trash = world.rows("Trash")
    assert [row.subject for row in trash] == ["Message 1"]
    call(world.engine, "message.delete", account=world.account, folder="Trash", message=trash[0].message_id)  # for good
    assert world.imap.uids("Trash") == [] and world.rows("Trash") == []
    result = world.fetch()  # nothing left to do
    assert result.downloaded == 0 and result.failed == () and world.imap.uids("INBOX") == [2] and len(world.rows("Inbox")) == 1


def test_messages_removed_on_the_server_disappear_here(world: World) -> None:
    uid = world.imap.add("INBOX", mail(1))
    world.imap.add("INBOX", mail(2))
    world.fetch()
    world.imap.remove("INBOX", uid)
    world.fetch()
    assert [row.subject for row in world.rows("Inbox")] == ["Message 2"]


def test_a_sent_message_is_copied_to_the_sent_folder_on_the_server(world: World) -> None:
    result = call(world.engine, "mail.send", account=world.account, to="you@example.com", subject="Outgoing", body="hello")
    assert result.sent and len(world.smtp.received) == 1
    (uid,) = world.imap.uids("Sent")  # in Sent on the server at once
    world.fetch()
    assert b"Subject: Outgoing" in world.imap.raw_of("Sent", uid)
    world.fetch()
    assert len(world.imap.uids("Sent")) == 1 and len(world.rows("Sent")) == 1


def test_a_draft_is_kept_on_the_server_and_replaced_when_saved_again(world: World) -> None:
    first = call(world.engine, "draft.save", account=world.account, subject="v1", body="one")
    world.fetch()
    assert len(world.imap.uids("Drafts")) == 1
    call(world.engine, "draft.save", account=world.account, subject="v2", body="two", replace_message=first.message_id)
    world.fetch()
    (uid,) = world.imap.uids("Drafts")
    assert b"Subject: v2" in world.imap.raw_of("Drafts", uid)


def test_a_folder_made_here_appears_on_the_server(world: World) -> None:
    world.fetch()
    call(world.engine, "folder.create", account=world.account, folder="Taxes")
    world.fetch()
    assert "Taxes" in world.imap.folders


def test_a_wrong_password_and_a_dead_server_are_reported(world: World) -> None:
    call(world.engine, "account.edit", account=world.account, password="wrong")
    with pytest.raises(AuthenticationError):
        world.fetch()
    call(world.engine, "account.edit", account=world.account, password="secret", incoming_port=1)
    with pytest.raises(ProtocolError):
        world.fetch()


def test_the_connection_check_knows_about_imap(world: World) -> None:
    good = call(
        world.engine, "account.check", kind="imap", username="user", password="secret", incoming_host="127.0.0.1", incoming_port=world.imap.port,
        incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=world.smtp.port, outgoing_security="none",
    )
    assert good.incoming_ok and good.outgoing_ok
    bad = call(
        world.engine, "account.check", kind="imap", username="user", password="nope", incoming_host="127.0.0.1", incoming_port=world.imap.port,
        incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=world.smtp.port, outgoing_security="none",
    )
    assert not bad.incoming_ok and "refused" in bad.incoming_message
    pop3_against_imap = call(
        world.engine, "account.check", username="user", password="secret", incoming_host="127.0.0.1", incoming_port=world.imap.port,
        incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=world.smtp.port, outgoing_security="none",
    )
    assert not pop3_against_imap.incoming_ok  # an IMAP server is not a POP3 server


def test_the_account_state_file_is_the_imap_one(world: World) -> None:
    world.imap.add("INBOX", mail(1))
    world.fetch()
    assert world.engine.directories.imap_state_file(world.account).exists() and not world.engine.directories.pop3_state_file(world.account).exists()


def test_a_folder_held_back_is_reported_without_an_error_and_settled_by_the_person(world: World) -> None:
    for number in range(1, 13):
        world.imap.add("INBOX", mail(number))
    world.fetch()
    with open_mailbox(world.engine, world.account) as mailbox:  # 9 files are lost (not deleted by the person)
        for row in world.rows("Inbox")[:9]:
            mailbox.store.delete(mailbox.reference(("Inbox",), row.message_id))
    result = world.fetch()
    assert [(item.folder, item.kind, item.count) for item in result.blocked] == [("Inbox", "missing_here", 9)]
    assert not any("missing from the store" in text for text in result.failed)  # a question, not a red error line
    settled = call(world.engine, "mail.settle", account=world.account, folder="Inbox", choice="delete_on_server")
    assert settled.blocked == () and len(world.imap.uids("INBOX")) == 3 and world.fetch().blocked == ()
