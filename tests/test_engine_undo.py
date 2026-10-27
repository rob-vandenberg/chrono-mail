from datetime import datetime, timedelta

import pytest

from chrono_mail.core import MessageFlag, ProtocolError
from chrono_mail.engine import Engine
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_actions import FolderActionParameters, folder_mark_read
from chrono_mail.engine.commands_many import (
    ManyDeleteParameters, ManyFlagParameters, ManyParameters, ManySnoozeParameters, ManyTargetParameters, message_archive_many, message_copy_many,
    message_delete_many, message_flag_many, message_junk_many, message_move_many, message_not_junk_many, message_snooze_many, message_unarchive_many,
)
from chrono_mail.engine.commands_schedule import WakeParameters, message_wake
from chrono_mail.engine.commands_tags import MailTagParameters, message_tag
from chrono_mail.engine.junkwords import JunkWords
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.undo import CHANGED_SINCE, UndoResult, mail_redo, mail_undo, mail_undo_labels
from chrono_mail.engine.undohistory import MAXIMUM_ACTIONS, UndoHistory, UndoStep
from chrono_mail.protocols.imap import ImapClient
from tests.test_engine_commands import call
from tests.test_engine_imap import World, mail, world  # noqa: F401 - world is a fixture

__all__ = ["world"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_undo.py 4.1.382"

# --- Version history ----------------------------------------------------
# v4.1.382: an Undo or Redo that runs longer than a second counts its messages for the status line; a quick one says nothing (D-266)
# v2.2.313: Initial (B-18): every action on mail undone and redone on the IMAP server first; changed since, offline, refused; the junk filter as before


def fill(world: World, count: int) -> list[str]:
    """count messages in the Inbox, here and on the server; their ids in the order of their subjects."""
    for number in range(1, count + 1):
        world.imap.add("INBOX", mail(number))
    world.fetch()
    return [store_id for _, store_id in sorted(held(world, "Inbox").items())]


def held(world: World, folder: str) -> dict[str, str]:
    """subject -> store id of the messages in a folder here."""
    with open_mailbox(world.engine, world.account) as mailbox:
        return {row.subject: row.store_id for row in mailbox.index.page((folder,), limit=1000)}


def flags(world: World, folder: str, store_id: str) -> frozenset[MessageFlag]:
    with open_mailbox(world.engine, world.account) as mailbox:
        return mailbox.current_flags(mailbox.reference((folder,), store_id))


def undo(engine: Engine) -> UndoResult:
    return mail_undo(engine, NoParameters())


def redo(engine: Engine) -> UndoResult:
    return mail_redo(engine, NoParameters())


def in_step(world: World, *folders: tuple[str, str]) -> None:
    """Here and on the server the same messages per folder, and a check finds nothing to download."""
    world.fetch()
    for local, server in folders:
        assert len(held(world, local)) == len(world.imap.uids(server)), local


def test_undo_of_move_to_trash_brings_the_messages_back_on_the_server_first_and_redo_trashes_them_again(world: World) -> None:
    first, second, third = fill(world, 3)
    message_delete_many(world.engine, ManyDeleteParameters(world.account, "Inbox", (first, second), undo=1))
    assert mail_undo_labels(world.engine, NoParameters()).undo == "Move to Trash (2 messages)"
    result = undo(world.engine)
    assert result.action == "Move to Trash (2 messages)" and set(result.messages) == {first, second} and result.not_done == 0
    assert set(held(world, "Inbox").values()) == {first, second, third} and len(world.imap.uids("INBOX")) == 3 and world.imap.uids("Trash") == []
    labels = mail_undo_labels(world.engine, NoParameters())
    assert (labels.undo, labels.redo) == ("", "Move to Trash (2 messages)")
    redo(world.engine)
    assert set(held(world, "Trash").values()) == {first, second} and len(world.imap.uids("Trash")) == 2
    in_step(world, ("Inbox", "INBOX"), ("Trash", "Trash"))


def test_one_action_over_two_folders_is_one_undo(world: World) -> None:
    first, second, third = fill(world, 3)
    call(world.engine, "folder.create", account=world.account, folder="Projects")
    message_move_many(world.engine, ManyTargetParameters(world.account, "Inbox", "Projects", (first,), undo=7))
    message_move_many(world.engine, ManyTargetParameters(world.account, "Inbox", "Trash", (second,), undo=8))
    message_delete_many(world.engine, ManyDeleteParameters(world.account, "Projects", (first,), undo=9))
    message_delete_many(world.engine, ManyDeleteParameters(world.account, "Inbox", (third,), undo=9))  # the same key press in another folder
    assert undo(world.engine).action == "Move to Trash (2 messages)"
    assert set(held(world, "Projects").values()) == {first} and set(held(world, "Inbox").values()) == {third}
    assert undo(world.engine).action == "Move to Trash (1 message)"
    assert undo(world.engine).action == "Move to Projects (1 message)"
    assert set(held(world, "Inbox").values()) == {first, second, third}
    in_step(world, ("Inbox", "INBOX"), ("Projects", "Projects"), ("Trash", "Trash"))


def test_an_action_without_a_number_is_never_undone(world: World) -> None:
    first, _, _ = fill(world, 3)
    message_flag_many(world.engine, ManyFlagParameters(world.account, "Inbox", (first,), seen=True))  # a message opened in the reading pane
    with pytest.raises(UsageError, match="Nothing to undo"):
        undo(world.engine)


def test_marks_and_mark_all_as_read_come_back_as_they_were(world: World) -> None:
    first, second, third = fill(world, 3)
    message_flag_many(world.engine, ManyFlagParameters(world.account, "Inbox", (first,), seen=True, undo=1))
    message_flag_many(world.engine, ManyFlagParameters(world.account, "Inbox", (second,), flagged=True, undo=2))
    folder_mark_read(world.engine, FolderActionParameters(world.account, "Inbox", undo=3))
    assert all(MessageFlag.SEEN in flags(world, "Inbox", store_id) for store_id in (first, second, third))
    assert undo(world.engine).action == "Mark all as read (2 messages)"  # the two it marked, not the one read before
    assert MessageFlag.SEEN in flags(world, "Inbox", first) and MessageFlag.SEEN not in flags(world, "Inbox", second)
    assert undo(world.engine).action == "Flag (1 message)"
    assert MessageFlag.FLAGGED not in flags(world, "Inbox", second)
    assert undo(world.engine).action == "Mark as read (1 message)"
    assert MessageFlag.SEEN not in flags(world, "Inbox", first)
    assert not any("\\Seen" in world.imap.flags_of("INBOX", uid) or "\\Flagged" in world.imap.flags_of("INBOX", uid) for uid in world.imap.uids("INBOX"))


def test_archive_and_unarchive_come_back_with_where_they_came_from(world: World) -> None:
    first, _, _ = fill(world, 3)
    message_archive_many(world.engine, ManyParameters(world.account, "Inbox", (first,), undo=1))
    undo(world.engine)
    with open_mailbox(world.engine, world.account) as mailbox:
        assert mailbox.store.properties.of(first).archived_from is None
    redo(world.engine)
    with open_mailbox(world.engine, world.account) as mailbox:
        assert mailbox.store.properties.of(first).archived_from == "Inbox"
    message_unarchive_many(world.engine, ManyParameters(world.account, "Archive", (first,), undo=2))
    assert undo(world.engine).action == "Unarchive (1 message)"
    assert set(held(world, "Archive").values()) == {first}
    with open_mailbox(world.engine, world.account) as mailbox:
        assert mailbox.store.properties.of(first).archived_from == "Inbox"
    in_step(world, ("Inbox", "INBOX"), ("Archive", "Archive"))


def test_snooze_and_cancel_snooze_come_back_with_their_record_and_marks(world: World) -> None:
    first, _, _ = fill(world, 3)
    until = (datetime.fromtimestamp(world.engine.clock()) + timedelta(days=2)).strftime("%Y-%m-%dT09:00")
    message_flag_many(world.engine, ManyFlagParameters(world.account, "Inbox", (first,), seen=True))
    message_snooze_many(world.engine, ManySnoozeParameters(world.account, "Inbox", until, (first,), undo=1))
    undo(world.engine)
    with open_mailbox(world.engine, world.account) as mailbox:
        assert mailbox.store.properties.of(first).snoozed_until is None
    assert set(held(world, "Inbox").values()) >= {first}
    redo(world.engine)
    message_wake(world.engine, WakeParameters(world.account, first, undo=2))  # Cancel snooze: back, unread
    assert MessageFlag.SEEN not in flags(world, "Inbox", first)
    assert undo(world.engine).action == "Cancel snooze (1 message)"
    assert set(held(world, "Snoozed").values()) == {first} and MessageFlag.SEEN in flags(world, "Snoozed", first)
    with open_mailbox(world.engine, world.account) as mailbox:
        record = mailbox.store.properties.of(first)
    assert record.snoozed_until is not None and record.snoozed_from == "Inbox"


def learned(engine: Engine) -> tuple[int, int, int]:
    with engine.junk.lock:
        words = engine.junk.words or JunkWords()
        return words.junk_messages, words.good_messages, len(words.verdicts)


def test_undo_of_junk_puts_the_junk_filter_back_as_it_was(world: World) -> None:
    first, second, _ = fill(world, 3)
    before = learned(world.engine)
    message_junk_many(world.engine, ManyParameters(world.account, "Inbox", (first, second), undo=1))
    assert learned(world.engine)[0] == before[0] + 2
    undo(world.engine)
    assert learned(world.engine) == before and set(held(world, "Inbox").values()) >= {first, second}
    redo(world.engine)
    assert learned(world.engine)[0] == before[0] + 2 and set(held(world, "Junk").values()) == {first, second}
    message_not_junk_many(world.engine, ManyParameters(world.account, "Junk", (first,), undo=2))
    assert undo(world.engine).action == "Not junk (1 message)"
    assert learned(world.engine)[0] == before[0] + 2 and set(held(world, "Junk").values()) == {first, second}
    in_step(world, ("Inbox", "INBOX"), ("Junk", "Junk"))


def test_undo_of_not_junk_takes_away_what_the_rules_did(world: World) -> None:
    first = fill(world, 2)[0]
    call(world.engine, "folder.create", account=world.account, folder="Kept")
    call(world.engine, "rule.add", name="Keep", condition=[], action=["add_tag:Work", "mark_read", "move_to:Kept"], account=[world.account])
    message_junk_many(world.engine, ManyParameters(world.account, "Inbox", (first,), undo=1))
    message_not_junk_many(world.engine, ManyParameters(world.account, "Junk", (first,), undo=2))
    assert set(held(world, "Kept").values()) == {first}
    undo(world.engine)
    assert set(held(world, "Junk").values()) == {first} and MessageFlag.SEEN not in flags(world, "Junk", first)
    with open_mailbox(world.engine, world.account) as mailbox:
        assert mailbox.tags_of(first) == ()


def test_undo_of_copy_removes_the_copy_only_and_redo_copies_again(world: World) -> None:
    first, _, _ = fill(world, 3)
    call(world.engine, "folder.create", account=world.account, folder="Kept")
    message_copy_many(world.engine, ManyTargetParameters(world.account, "Inbox", "Kept", (first,), undo=1))
    assert undo(world.engine).messages == ()
    assert held(world, "Kept") == {} and world.imap.uids("Kept") == [] and first in held(world, "Inbox").values()
    copied = redo(world.engine)
    assert set(held(world, "Kept").values()) == set(copied.messages) and len(world.imap.uids("Kept")) == 1
    undo(world.engine)  # the copy made again is the one removed
    assert held(world, "Kept") == {}
    in_step(world, ("Inbox", "INBOX"), ("Kept", "Kept"))


def test_tags_come_back_as_they_were(world: World) -> None:
    first, _, _ = fill(world, 3)
    message_tag(world.engine, MailTagParameters(world.account, "Inbox", (first,), add=("Work",), undo=1))
    message_tag(world.engine, MailTagParameters(world.account, "Inbox", (first,), add=("Home",), undo=2))
    assert undo(world.engine).action == "Tag Home (1 message)"
    with open_mailbox(world.engine, world.account) as mailbox:
        assert mailbox.tags_of(first) == ("Work",)


def test_a_message_changed_since_is_left_alone_and_the_others_are_undone(world: World) -> None:
    first, second, _ = fill(world, 3)
    message_delete_many(world.engine, ManyDeleteParameters(world.account, "Inbox", (first, second), undo=1))
    message_delete_many(world.engine, ManyDeleteParameters(world.account, "Trash", (first,)))  # for good, not undone
    result = undo(world.engine)
    assert result.messages == (second,) and result.not_done == 1 and result.reason == CHANGED_SINCE
    assert mail_undo_labels(world.engine, NoParameters()).redo == "Move to Trash (1 message)"


def test_a_refusal_of_the_server_changes_nothing_here(world: World) -> None:
    first, _, _ = fill(world, 3)
    message_delete_many(world.engine, ManyDeleteParameters(world.account, "Inbox", (first,), undo=1))
    world.imap.refuse_moves = True
    result = undo(world.engine)
    assert result.messages == () and result.not_done == 1
    assert set(held(world, "Trash").values()) == {first} and len(world.imap.uids("Trash")) == 1


def test_offline_the_undo_is_done_here_and_sent_at_the_next_check(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    first, _, _ = fill(world, 3)
    message_delete_many(world.engine, ManyDeleteParameters(world.account, "Inbox", (first,), undo=1))
    world.engine.notice = lambda text: None
    world.engine.close_imap_links()

    def unreachable(self: ImapClient) -> None:
        raise ProtocolError("Connection failed: no route")

    monkeypatch.setattr(ImapClient, "connect", unreachable)
    assert undo(world.engine).messages == (first,)
    assert first in held(world, "Inbox").values() and len(world.imap.uids("Trash")) == 1
    monkeypatch.undo()
    world.fetch()
    assert world.imap.uids("Trash") == [] and len(world.imap.uids("INBOX")) == 3
    in_step(world, ("Inbox", "INBOX"), ("Trash", "Trash"))


def test_a_new_action_ends_redo_and_the_history_keeps_the_last_hundred() -> None:
    history = UndoHistory()
    step = UndoStep("a", "m", ("Inbox",), ("Trash",))
    history.record(1, "Move to Trash", [step])
    taken = history.take(redo=False)
    assert taken is not None
    history.put(taken, redo=True)
    assert history.labels() == ("", "Move to Trash (1 message)")
    history.record(2, "Archive", [step])
    assert history.labels() == ("Archive (1 message)", "")
    for number in range(3, MAXIMUM_ACTIONS + 10):
        history.record(number, f"Action {number}", [step])
    undone = 0
    while history.take(redo=False) is not None:
        undone += 1
    assert undone == MAXIMUM_ACTIONS


def test_a_step_reversed_twice_is_itself() -> None:
    step = UndoStep("a", "m", ("Inbox",), ("Junk",), flags_added=frozenset({MessageFlag.SEEN}), tags_added=("Work",), verdict=(None, "j"))
    assert step.reversed().reversed() == step
    assert step.reversed().before == ("Junk",) and step.reversed().flags_removed == frozenset({MessageFlag.SEEN}) and step.reversed().verdict == ("j", None)


class Clock:
    """The time the Undo reads: it moves on by step seconds at every look (0: it stands still)."""

    def __init__(self, step: float) -> None:
        self.now, self.step = 0.0, step

    def monotonic(self) -> float:
        self.now += self.step
        return self.now


def counted(world: World) -> list[tuple[str, int, int | None]]:
    """Every count the engine gives the status line from now on."""
    said: list[tuple[str, int, int | None]] = []

    def progress(what: str, done: int, total: int | None) -> None:
        said.append((what, done, total))

    world.engine.progress = progress
    return said


def test_a_long_undo_and_redo_count_their_messages_for_the_status_line(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner (4.1.381): an Undo of 15,346 messages ran for minutes with nothing in the status line but the busy bar."""
    from chrono_mail.engine import undo as undo_module

    monkeypatch.setattr(undo_module, "time", Clock(1.0))  # each message takes a second: every one is counted
    ids = fill(world, 5)
    message_delete_many(world.engine, ManyDeleteParameters(world.account, "Inbox", tuple(ids), undo=1))
    said = counted(world)
    undo(world.engine)
    assert said and {what for what, _, _ in said} == {"Undoing Move to Trash"} and {total for _, _, total in said} == {5}
    counts = [done for _, done, _ in said]
    assert counts == sorted(set(counts)) and counts[-1] == 5  # only up, and every message counted
    said.clear()
    redo(world.engine)
    assert said and {what for what, _, _ in said} == {"Redoing Move to Trash"} and said[-1][1:] == (5, 5)


def test_a_quick_undo_says_only_its_last_line(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.engine import undo as undo_module

    monkeypatch.setattr(undo_module, "time", Clock(0.0))  # done within the second
    ids = fill(world, 2)
    message_delete_many(world.engine, ManyDeleteParameters(world.account, "Inbox", tuple(ids), undo=1))
    said = counted(world)
    assert undo(world.engine).not_done == 0
    assert said == []

