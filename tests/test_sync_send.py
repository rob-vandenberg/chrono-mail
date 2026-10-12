from dataclasses import dataclass
from pathlib import Path

import pytest

from chrono_mail.core import ConnectionSecurity, FolderRole, MessageFlag, MessageFormatError, MessageRef, ServerSettings
from chrono_mail.crypto import generate_data_key
from chrono_mail.index import Index
from chrono_mail.mime import Address, OutgoingMessage, parse_message, without_bcc
from chrono_mail.protocols.smtp import SmtpLimits
from chrono_mail.store import Store
from chrono_mail.sync import reconcile_index, send_and_file, send_stored
from tests.fakesmtp import FakeSmtpServer

# --- Version ------------------------------------------------------------
__version__ = "test_sync_send.py 0.0.86"

# --- Version history ----------------------------------------------------
# v0.0.86: The stored copy keeps Bcc and what is sent does not; send_stored sends a message that waits in the Outbox, safely at every step
# v0.0.10: Initial send and file tests

OUTBOX = ("Outbox",)
SENT = ("Sent",)
FAST = SmtpLimits(timeout_seconds=2)
SENDER = Address("Alice", "alice@example.com")
RECIPIENT = Address("Bob", "bob@example.com")


class SimulatedCrash(BaseException):
    pass


@dataclass
class World:
    store: Store
    index: Index


def make_world(tmp_path: Path) -> World:
    store = Store.create(tmp_path / "store", "acc", generate_data_key(), durable=False)
    store.create_folder(OUTBOX, FolderRole.OTHER)
    store.create_folder(SENT, FolderRole.SENT)
    return World(store, Index(tmp_path / "index.sqlite"))


def outgoing(**changes: str) -> OutgoingMessage:
    return OutgoingMessage(
        from_address=SENDER,
        to_addresses=(RECIPIENT,),
        bcc_addresses=(Address("", "secret@example.com"),),
        subject=changes.get("subject", "Quarterly plan"),
        text_body=changes.get("body", "Please read the attached plan.\n"),
    )


def settings(server: FakeSmtpServer) -> ServerSettings:
    return ServerSettings("127.0.0.1", server.port, ConnectionSecurity.NONE, "user")


def contents(world: World, folder: tuple[str, ...]) -> list[bytes]:
    return [world.store.read(message.ref) for message in world.store.list_messages(folder)]


def test_sent_message_is_filed_in_sent_and_indexed(tmp_path: Path) -> None:
    world = make_world(tmp_path)
    with FakeSmtpServer() as server:
        outcome = send_and_file(world.store, world.index, settings(server), "secret", outgoing(), OUTBOX, SENT, FAST)
        assert outcome.sent and outcome.error is None and outcome.warning is None and outcome.reference.folder_path == SENT
        (received,) = server.received
        assert received.sender == "alice@example.com"
        assert received.recipients == ("bob@example.com", "secret@example.com")  # Bcc is in the envelope
        assert b"secret@example.com" not in received.data  # and nowhere in the message
        (stored,) = contents(world, SENT)
        assert without_bcc(stored) == received.data and b"secret@example.com" in stored  # our copy is what was sent, plus the Bcc header
        assert contents(world, OUTBOX) == []
        assert world.store.list_messages(SENT)[0].flags == {MessageFlag.SEEN}
        assert [m.subject for m in world.index.page(SENT)] == ["Quarterly plan"] and world.index.count(OUTBOX) == 0
        assert parse_message(received.data).text_body == "Please read the attached plan.\n"


def test_failed_send_keeps_the_message_in_the_outbox(tmp_path: Path) -> None:
    world = make_world(tmp_path)
    with FakeSmtpServer(password="right") as server:
        outcome = send_and_file(world.store, world.index, settings(server), "wrong", outgoing(), OUTBOX, SENT, FAST)
        assert not outcome.sent and outcome.error is not None and "wrong" not in outcome.error
        assert outcome.reference.folder_path == OUTBOX
        assert len(contents(world, OUTBOX)) == 1 and contents(world, SENT) == []
        assert world.index.count(OUTBOX) == 1 and server.received == []


def test_unreachable_server_keeps_the_message(tmp_path: Path) -> None:
    world = make_world(tmp_path)
    outcome = send_and_file(world.store, world.index, ServerSettings("127.0.0.1", 1, ConnectionSecurity.NONE, "u"), "x", outgoing(), OUTBOX, SENT, FAST)
    assert not outcome.sent and len(contents(world, OUTBOX)) == 1


def test_partly_refused_recipients_are_reported_and_the_message_is_filed(tmp_path: Path) -> None:
    world = make_world(tmp_path)
    with FakeSmtpServer(refuse=frozenset({"secret@example.com"})) as server:
        outcome = send_and_file(world.store, world.index, settings(server), "secret", outgoing(), OUTBOX, SENT, FAST)
        assert outcome.sent and [item.address for item in outcome.refused] == ["secret@example.com"]
        assert len(contents(world, SENT)) == 1


def test_every_recipient_refused_keeps_the_message(tmp_path: Path) -> None:
    world = make_world(tmp_path)
    with FakeSmtpServer(refuse=frozenset({"bob@example.com", "secret@example.com"})) as server:
        outcome = send_and_file(world.store, world.index, settings(server), "secret", outgoing(), OUTBOX, SENT, FAST)
        assert not outcome.sent and len(contents(world, OUTBOX)) == 1


def test_message_that_cannot_be_built_stores_nothing(tmp_path: Path) -> None:
    world = make_world(tmp_path)
    with FakeSmtpServer() as server:
        with pytest.raises(MessageFormatError):
            send_and_file(world.store, world.index, settings(server), "secret", outgoing(subject="a\r\nBcc: evil@example.com"), OUTBOX, SENT, FAST)
        assert contents(world, OUTBOX) == [] and server.connections == 0


def test_move_failure_after_sending_is_a_warning_not_an_error(tmp_path: Path) -> None:
    world = make_world(tmp_path)
    world.store.delete_folder(SENT)
    with FakeSmtpServer() as server:
        outcome = send_and_file(world.store, world.index, settings(server), "secret", outgoing(), OUTBOX, SENT, FAST)
        assert outcome.sent and outcome.warning is not None and "Sent" in outcome.warning
        assert len(contents(world, OUTBOX)) == 1 and len(server.received) == 1


@pytest.mark.parametrize("point", ["send:after-outbox", "send:after-smtp", "send:after-move"])
def test_crash_at_every_step_never_loses_the_message(tmp_path: Path, point: str) -> None:
    world = make_world(tmp_path)

    def crash(name: str) -> None:
        if name == point:
            raise SimulatedCrash(name)

    with FakeSmtpServer() as server:
        with pytest.raises(SimulatedCrash):
            send_and_file(world.store, world.index, settings(server), "secret", outgoing(), OUTBOX, SENT, FAST, None, crash)
        restarted = Store.open(tmp_path / "store", world.store._key, durable=False)  # noqa: SLF001
        stored = [restarted.read(m.ref) for folder in (OUTBOX, SENT) for m in restarted.list_messages(folder)]
        assert len(stored) == 1, "exactly one copy of the message exists, never zero, never two"
        if point == "send:after-outbox":
            assert server.received == []
        else:
            assert len(server.received) == 1 and without_bcc(stored[0]) == server.received[0].data
        # the index can be repaired from the store after the crash
        reconcile_index(restarted, world.index)
        assert world.index.message_count() == 1


def test_a_message_left_in_the_outbox_is_never_sent_by_this_code_again(tmp_path: Path) -> None:
    world = make_world(tmp_path)
    with FakeSmtpServer(password="right") as server:
        send_and_file(world.store, world.index, settings(server), "wrong", outgoing(), OUTBOX, SENT, FAST)
        send_and_file(world.store, world.index, settings(server), "wrong", outgoing(subject="Another"), OUTBOX, SENT, FAST)
        assert len(contents(world, OUTBOX)) == 2 and server.received == []  # each call sends only its own message


def test_a_failed_message_keeps_its_bcc_in_the_outbox(tmp_path: Path) -> None:
    world = make_world(tmp_path)
    with FakeSmtpServer(password="right") as server:
        send_and_file(world.store, world.index, settings(server), "wrong", outgoing(), OUTBOX, SENT, FAST)
    (waiting,) = contents(world, OUTBOX)
    assert [address.address for address in parse_message(waiting).bcc_addresses] == ["secret@example.com"]


def _waiting(world: World, server: FakeSmtpServer) -> tuple[MessageRef, bytes]:
    send_and_file(world.store, world.index, settings(server), "wrong", outgoing(), OUTBOX, SENT, FAST)
    (message,) = world.store.list_messages(OUTBOX)
    return message.ref, world.store.read(message.ref)


def test_a_waiting_message_is_sent_as_stored_without_its_bcc_header_and_filed_in_sent(tmp_path: Path) -> None:
    world = make_world(tmp_path)
    with FakeSmtpServer(password="right") as server:
        reference, raw = _waiting(world, server)
        outcome = send_stored(world.store, world.index, settings(server), "right", reference, None, raw, "alice@example.com", ("bob@example.com", "secret@example.com"), SENT, FAST)
        assert outcome.sent and outcome.reference.folder_path == SENT
        (received,) = server.received
        assert received.recipients == ("bob@example.com", "secret@example.com") and b"secret@example.com" not in received.data
        assert received.data == without_bcc(raw) and contents(world, SENT) == [raw] and contents(world, OUTBOX) == []
        assert world.index.count(SENT) == 1 and world.index.count(OUTBOX) == 0


def test_a_waiting_message_that_fails_again_stays_in_the_outbox(tmp_path: Path) -> None:
    world = make_world(tmp_path)
    with FakeSmtpServer(password="right") as server:
        reference, raw = _waiting(world, server)
        outcome = send_stored(world.store, world.index, settings(server), "wrong", reference, None, raw, "alice@example.com", ("bob@example.com",), SENT, FAST)
        assert not outcome.sent and contents(world, OUTBOX) == [raw] and server.received == []


@pytest.mark.parametrize("point", ["send:after-smtp", "send:after-move"])
def test_a_crash_while_sending_a_waiting_message_never_loses_it(tmp_path: Path, point: str) -> None:
    world = make_world(tmp_path)

    def crash(name: str) -> None:
        if name == point:
            raise SimulatedCrash(name)

    with FakeSmtpServer(password="right") as server:
        reference, raw = _waiting(world, server)
        with pytest.raises(SimulatedCrash):
            send_stored(world.store, world.index, settings(server), "right", reference, None, raw, "alice@example.com", ("bob@example.com",), SENT, FAST, None, crash)
        restarted = Store.open(tmp_path / "store", world.store._key, durable=False)  # noqa: SLF001
        stored = [restarted.read(m.ref) for folder in (OUTBOX, SENT) for m in restarted.list_messages(folder)]
        assert stored == [raw], "exactly one copy of the message exists, never zero, never two"
