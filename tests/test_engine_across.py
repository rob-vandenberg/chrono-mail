from pathlib import Path

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, ChronoMailError, MessageFlag, MessageRef, ProtocolError
from chrono_mail.engine import commands_across
from chrono_mail.engine.local import ensure_local_folders
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from chrono_mail.protocols.imap import ImapClient
from tests.mailfactory import raw_message
from tests.test_engine_commands import call
from tests.test_engine_imap import World, world  # noqa: F401 - world is a fixture

__all__ = ["world"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_across.py 3.6.366"

# --- Version history ----------------------------------------------------
# v3.6.366: Initial: messages moved to another account, message by message: Local Folders, POP and IMAP each way, offline on either side,
#           stopped at every step (never in neither account), done again after a stop, Undo and Redo, a refused upload, what never
#           moves, the junk filter (D-272)


def local_mail(world: World, count: int, folder: str = "Inbox") -> list[str]:
    """Messages in Local Folders, the first read, flagged and tagged."""
    ids: list[str] = []
    ensure_local_folders(world.engine)
    with open_mailbox(world.engine, LOCAL_ACCOUNT_ID) as mailbox:
        for number in range(count):
            raw = raw_message(subject=f"Local {number}", body=f"body {number}\r\n", message_id=f"<local{number}@x>")
            flags = frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED}) if number == 0 else frozenset()
            ref = mailbox.deliver(mailbox.folder(folder), raw, flags)
            if number == 0:
                mailbox.set_tags(ref, ("Work",))
            ids.append(ref.store_id)
    return ids


def ids_in(world: World, account: str, folder: str) -> set[str]:
    return {row.message_id for row in call(world.engine, "message.list", account=account, folder=folder).messages}


def move(world: World, account: str, folder: str, target_account: str, target: str, ids: list[str], undo: int = 0) -> object:
    return call(world.engine, "message.move-across", account=account, folder=folder, target_account=target_account, target=target, messages=tuple(ids), undo=undo)


def unreachable(monkeypatch: pytest.MonkeyPatch, world: World) -> None:
    world.engine.close_imap_links()

    def refuse(self: ImapClient) -> None:
        raise ProtocolError("Connection failed: no route")

    monkeypatch.setattr(ImapClient, "connect", refuse)


def test_local_folders_to_imap_goes_to_the_server_first_with_marks_and_tags(world: World) -> None:
    ids = local_mail(world, 3)
    result = move(world, LOCAL_ACCOUNT_ID, "Inbox", world.account, "Inbox", ids)
    assert set(getattr(result, "done")) == set(ids) and getattr(result, "failed") == ()
    assert ids_in(world, LOCAL_ACCOUNT_ID, "Inbox") == set() and ids_in(world, world.account, "Inbox") == set(ids)  # the same ids
    assert len(world.imap.uids("INBOX")) == 3
    assert any({"\\Seen", "\\Flagged"} <= world.imap.flags_of("INBOX", uid) for uid in world.imap.uids("INBOX"))
    with open_mailbox(world.engine, world.account) as mailbox:
        assert mailbox.tags_of(ids[0]) == ("Work",)
    assert world.fetch().downloaded == 0  # nothing comes twice


def test_imap_to_local_folders_deletes_on_the_server_after_the_copy(world: World) -> None:
    for number in range(3):
        world.imap.add("INBOX", raw_message(subject=f"Server {number}", body="b\r\n", message_id=f"<server{number}@x>"))
    world.fetch()
    ensure_local_folders(world.engine)
    ids = sorted(ids_in(world, world.account, "Inbox"))
    move(world, world.account, "Inbox", LOCAL_ACCOUNT_ID, "Inbox", ids)
    assert world.imap.uids("INBOX") == [] and ids_in(world, LOCAL_ACCOUNT_ID, "Inbox") == set(ids)
    assert world.fetch().downloaded == 0


def test_a_pop_account_to_local_folders_needs_no_server(world: World) -> None:
    added = call(world.engine, "account.add", display_name="Pop", email_address="pop@example.com", username="u", password="p", incoming_host="127.0.0.1", outgoing_host="127.0.0.1")
    pop = str(added.account_id)
    ensure_local_folders(world.engine)
    with open_mailbox(world.engine, pop) as mailbox:
        ids = [mailbox.deliver(mailbox.folder("Inbox"), raw_message(subject=f"Pop {number}", body="b\r\n", message_id=f"<pop{number}@x>"), frozenset()).store_id for number in range(2)]
    if pop == LOCAL_ACCOUNT_ID or ids_in(world, LOCAL_ACCOUNT_ID, "Inbox") >= set(ids):
        pytest.skip("this POP account keeps its mail in Local Folders: an ordinary move")
    move(world, pop, "Inbox", LOCAL_ACCOUNT_ID, "Inbox", ids)
    assert ids_in(world, pop, "Inbox") == set() and ids_in(world, LOCAL_ACCOUNT_ID, "Inbox") == set(ids)


@pytest.mark.parametrize("step", ["store", "remove"])
def test_stopped_at_a_step_a_message_is_in_one_account_or_both_never_neither(world: World, monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    ids = local_mail(world, 3)
    if step == "store":  # the second cannot be stored in the other account: it stays where it was
        deliver = Mailbox.deliver_moved

        def refuse_second(self: Mailbox, folder: tuple[str, ...], raw: bytes, flags: frozenset[MessageFlag], store_id: str) -> MessageRef:
            if store_id == ids[1]:
                raise ChronoMailError("The disk is full")
            return deliver(self, folder, raw, flags, store_id)

        monkeypatch.setattr(Mailbox, "deliver_moved", refuse_second)
    else:  # the second cannot be removed where it was: it is in both
        delete = Mailbox.delete

        def refuse_second(self: Mailbox, ref: MessageRef, permanent: bool) -> str:  # type: ignore[misc]
            if ref.store_id == ids[1]:
                raise ChronoMailError("The disk is full")
            return delete(self, ref, permanent)

        monkeypatch.setattr(Mailbox, "delete", refuse_second)
    result = move(world, LOCAL_ACCOUNT_ID, "Inbox", world.account, "Inbox", ids)
    assert set(getattr(result, "done")) == {ids[0], ids[2]} and [item.message for item in getattr(result, "failed")] == [ids[1]]
    here, there = ids_in(world, LOCAL_ACCOUNT_ID, "Inbox"), ids_in(world, world.account, "Inbox")
    assert all(message in here or message in there for message in ids)
    assert (ids[1] in here) and (ids[1] in there) == (step == "remove")
    if step == "remove":
        assert "both accounts" in getattr(result, "failed")[0].reason


def test_stopped_between_storing_and_removing_and_done_again_nothing_is_stored_twice(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = local_mail(world, 3)

    def crash(*_: object) -> list[object]:
        raise RuntimeError("the program stopped")

    monkeypatch.setattr(commands_across, "_remove", crash)
    with pytest.raises(RuntimeError):
        move(world, LOCAL_ACCOUNT_ID, "Inbox", world.account, "Inbox", ids)
    assert ids_in(world, LOCAL_ACCOUNT_ID, "Inbox") == set(ids) and ids_in(world, world.account, "Inbox") == set(ids)  # in both, never in neither
    monkeypatch.undo()
    move(world, LOCAL_ACCOUNT_ID, "Inbox", world.account, "Inbox", ids)
    assert ids_in(world, LOCAL_ACCOUNT_ID, "Inbox") == set() and ids_in(world, world.account, "Inbox") == set(ids)
    assert len(world.imap.uids("INBOX")) == 3


def test_offline_the_imap_side_follows_at_the_next_check(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    for number in range(2):
        world.imap.add("INBOX", raw_message(subject=f"Server {number}", body="b\r\n", message_id=f"<server{number}@x>"))
    world.fetch()
    from_server = sorted(ids_in(world, world.account, "Inbox"))
    to_server = local_mail(world, 2, "Drafts")
    unreachable(monkeypatch, world)
    move(world, world.account, "Inbox", LOCAL_ACCOUNT_ID, "Inbox", from_server)  # out of the IMAP account: its server delete waits
    move(world, LOCAL_ACCOUNT_ID, "Drafts", world.account, "Inbox", to_server)  # into it: the upload waits
    assert ids_in(world, LOCAL_ACCOUNT_ID, "Inbox") == set(from_server) and ids_in(world, world.account, "Inbox") == set(to_server)
    assert len(world.imap.uids("INBOX")) == 2  # the server unchanged meanwhile
    monkeypatch.undo()
    world.fetch()
    assert len(world.imap.uids("INBOX")) == 2 and ids_in(world, world.account, "Inbox") == set(to_server)  # the two old gone, the two new up


def test_undo_moves_them_back_and_redo_again(world: World) -> None:
    ids = local_mail(world, 2)
    move(world, LOCAL_ACCOUNT_ID, "Inbox", world.account, "Inbox", ids, undo=7)
    undone = call(world.engine, "mail.undo")
    assert set(undone.messages) == set(ids) and ids_in(world, LOCAL_ACCOUNT_ID, "Inbox") == set(ids) and world.imap.uids("INBOX") == []
    call(world.engine, "mail.redo")
    assert ids_in(world, world.account, "Inbox") == set(ids) and len(world.imap.uids("INBOX")) == 2


def test_the_outbox_and_scheduled_never_move_to_another_account(world: World) -> None:
    ids = local_mail(world, 1)
    with pytest.raises(ChronoMailError):
        move(world, LOCAL_ACCOUNT_ID, "Inbox", world.account, "Outbox", ids)
    assert ids_in(world, LOCAL_ACCOUNT_ID, "Inbox") == set(ids)


def test_into_junk_of_the_other_account_teaches_the_junk_filter(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    taught: list[tuple[list[str], bool]] = []

    def learned(mailbox: Mailbox, references: list[MessageRef], junk: bool) -> dict[str, object]:
        taught.append(([reference.store_id for reference in references], junk))
        return {}

    monkeypatch.setattr(commands_across, "learn", learned)
    ids = local_mail(world, 1)
    move(world, LOCAL_ACCOUNT_ID, "Inbox", world.account, "Junk", ids)
    move(world, world.account, "Junk", LOCAL_ACCOUNT_ID, "Inbox", ids)
    assert taught == [(ids, True), (ids, False)]  # into Junk: junk; out of Junk (not to Trash): good


def test_a_server_that_refuses_the_upload_leaves_each_message_where_it_was(world: World) -> None:
    """A lasting no (a full mailbox): nothing moves, each message says why; offline the same refusal later is a sync conflict (D-195)."""
    ids = local_mail(world, 2)
    world.imap.refuse_appends = True
    result = move(world, LOCAL_ACCOUNT_ID, "Inbox", world.account, "Inbox", ids)
    assert getattr(result, "done") == () and len(getattr(result, "failed")) == 2
    assert ids_in(world, LOCAL_ACCOUNT_ID, "Inbox") == set(ids) and ids_in(world, world.account, "Inbox") == set() and world.imap.uids("INBOX") == []
