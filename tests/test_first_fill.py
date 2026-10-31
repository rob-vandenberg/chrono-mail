from collections.abc import Generator
from pathlib import Path

import pytest

from chrono_mail.config import Pop3DeletePolicy, Pop3Options
from chrono_mail.core import ConnectionSecurity, ServerSettings
from chrono_mail.engine import Engine
from chrono_mail.engine.commands_pim import CollectionSummary
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.protocols.pop3 import Pop3Client
from chrono_mail.sync import fetch_pop3
from tests.enginefactory import make_engine
from tests.fakedav import FakeDav
from tests.fakeimap import FakeImapServer
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message
from tests.test_engine_commands import call
from tests.test_engine_dav import linked
from tests.test_engine_junk import server_marked
from tests.test_sync_dav import card
from tests.test_sync_pop3 import CLIENT_LIMITS, INBOX, LIMIT, World, make_world, messages

# --- Version ------------------------------------------------------------
__version__ = "test_first_fill.py 4.2.449"

# --- Version history ----------------------------------------------------
# v4.2.449: the first fill of a new account only receives: POP3 deletes nothing on the server, IMAP sends no command that changes the server,
#           the calendar sync sends nothing, also no merge of an item kept here under the same UID (D-300)

WRITING_VERBS: frozenset[str] = frozenset({"SELECT", "STORE", "MOVE", "COPY", "EXPUNGE", "APPEND", "CREATE", "DELETE", "RENAME", "SUBSCRIBE", "UNSUBSCRIBE"})


def verbs(commands: list[str]) -> set[str]:
    """The verbs of the commands the fake server got; UID FETCH and UID MOVE count by their second word."""
    found: set[str] = set()
    for command in commands:
        words = command.upper().split()
        if words:
            found.add(words[1] if words[0] == "UID" and len(words) > 1 else words[0])
    return found


def calendar(engine: Engine, account: str) -> CollectionSummary:
    found: CollectionSummary = next(item for item in call(engine, "collection.list") if item.account == account and item.kind == "events")
    return found


@pytest.fixture
def pop3_world(tmp_path: Path) -> Generator[World]:
    created = make_world(tmp_path)
    yield created
    created.server.__exit__(None, None, None)


def first_fill_pop3(world: World, options: Pop3Options) -> int:
    settings = ServerSettings("127.0.0.1", world.server.port, ConnectionSecurity.NONE, "user")
    with Pop3Client(settings, "secret", CLIENT_LIMITS) as client:
        report = fetch_pop3(client, world.store, world.index, world.state, INBOX, options, LIMIT, lambda: world.clock[0], receive_only=True)
    return report.downloaded


@pytest.mark.parametrize("policy", [Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD, Pop3DeletePolicy.DELETE_WHEN_DELETED_LOCALLY])
def test_a_pop3_first_fill_downloads_and_deletes_nothing_on_the_server(pop3_world: World, policy: Pop3DeletePolicy) -> None:
    assert first_fill_pop3(pop3_world, Pop3Options(policy)) == 3
    assert set(pop3_world.server.messages) == {"uid1", "uid2", "uid3"}  # all still on the server, whatever the account says
    assert pop3_world.stored_bodies() == sorted(messages().values())
    report = pop3_world.fetch(Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD))  # the next normal check applies the account's policy
    assert (report.downloaded, report.deleted_on_server) == (0, 3) and pop3_world.server.messages == {}


def imap_account(engine: Engine, imap: FakeImapServer, smtp: FakeSmtpServer) -> str:
    created = call(
        engine, "account.add", kind="imap", display_name="Me", email_address="me@example.com", username="user", password="secret",
        incoming_host="127.0.0.1", incoming_port=imap.port, incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=smtp.port, outgoing_security="none",
    )
    return str(created.account_id)


def test_an_imap_first_fill_only_looks_at_the_server(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakeImapServer(plain=False) as imap, FakeSmtpServer() as smtp:
        account = imap_account(engine, imap, smtp)
        imap.add("INBOX", raw_message(subject="Hello", message_id="<a@x>"))
        imap.add("INBOX", server_marked(raw_message(subject="Cheap pills", message_id="<b@x>")))  # a normal check moves it to Junk on the server
        imap.add("Archive", raw_message(subject="Old", message_id="<c@x>"), {"\\Seen"})
        del imap.folders["Junk"]  # a standard folder the server lacks: a normal check makes it there
        imap.commands.clear()
        result = call(engine, "mail.first-fill", account=account)
        assert result.downloaded == 3 and result.junked == 0 and result.aborted is None
        assert verbs(imap.commands) & WRITING_VERBS == set()  # every folder opened with EXAMINE; nothing made, moved, marked or removed
        assert len(imap.uids("INBOX")) == 2 and "Junk" not in imap.folders
        inbox = call(engine, "message.list", account=account, folder="Inbox").messages
        assert sorted(row.subject for row in inbox) == ["Cheap pills", "Hello"]
        assert [row.subject for row in call(engine, "message.list", account=account, folder="Archive").messages] == ["Old"]


def test_after_an_imap_first_fill_the_next_check_downloads_nothing_again(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakeImapServer(plain=False) as imap, FakeSmtpServer() as smtp:
        account = imap_account(engine, imap, smtp)
        imap.add("INBOX", raw_message(subject="Hello", message_id="<a@x>"))
        assert call(engine, "mail.first-fill", account=account).downloaded == 1
        assert call(engine, "mail.fetch", account=account).downloaded == 0  # the first fill recorded what it took; nothing twice


def test_a_calendar_first_fill_sends_nothing_that_waits_here(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _told = linked(tmp_path, server)
        collection = calendar(engine, account).collection
        server.failing = True
        call(engine, "collection.edit", collection=collection, name="Home")  # waits here for the server
        server.failing = False
        server.add("contacts", "c9.vcf", card("c9", "Bea Moss"))
        server.requests.clear()
        (summary,) = call(engine, "dav.sync", account=account, receive_only=True)
        assert summary.errors == () and summary.pushed == 0 and summary.pulled == 1
        assert {method for method, _ in server.requests} & {"PUT", "DELETE", "PROPPATCH", "MKCOL", "MKCALENDAR"} == set()
        assert server.collections["events"].name == "Calendar" and calendar(engine, account).name == "Home"  # still waiting, kept here
        call(engine, "dav.sync", account=account)  # the next full sync sends it
        assert server.collections["events"].name == "Home"


def test_a_calendar_first_fill_sends_no_merge_of_an_item_kept_here_under_the_same_uid(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _told = linked(tmp_path, server)
        book = next(item for item in call(engine, "collection.list") if item.account == account and item.kind == "contacts")
        with open_pim(engine, account) as store:
            store.put(book.collection, "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c5\r\nFN:Ann Lee\r\nTEL:123\r\nEND:VCARD\r\n")  # here, not on the server yet
        server_text = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c5\r\nFN:Ann Lee\r\nEMAIL:ann@example.org\r\nEND:VCARD\r\n"
        server.add("contacts", "c5.vcf", server_text)  # the same person on the server, with other details: a full sync weighs the two (and may send a merge)
        server.requests.clear()
        (summary,) = call(engine, "dav.sync", account=account, receive_only=True)
        assert (summary.errors, summary.conflicts, summary.pulled) == ((), 0, 0)  # not weighed: a first fill sends no merge and decides nothing
        assert server.count("PUT") == 0 and server.collections["contacts"].items["c5.vcf"][1] == server_text
        (summary,) = call(engine, "dav.sync", account=account)  # the next full sync weighs it: the first fill left the listing for it
        assert summary.conflicts == 1  # these two clash as a whole: the person decides (D-194)
