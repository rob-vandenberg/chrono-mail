import dataclasses
import threading
from collections.abc import Callable

import pytest

from chrono_mail.config import ServerRole
from chrono_mail.core import MessageRef, StorageError
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine import commands_schedule
from chrono_mail.engine.mailbox import Mailbox
from chrono_mail.mime import ParsedMessage
from chrono_mail.store import Store
from chrono_mail.store.store import Placed
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_recurring import repeating
from tests.test_engine_schedule import at, iso, rows, schedule

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_send_once.py 3.6.351"

# --- Version history ----------------------------------------------------
# v3.6.351: a send the server took is never told as failed when the list cannot follow (also a repeating one, never sent again, and in
#           Send and Receive); a replacement that cannot be removed again is a storage error
# v3.6.350: on a full disk (the move to Sent and the details file both fail) a message the server took is never sent again, also after a
#           restart (the letter in its file name), and in the run when not even that could be written; a message leaves Sent only without
#           the marks left on it there (moved or copied out it is ordinary; when they cannot be cleared it stays); a delayed send whose
#           waiting time cannot be written leaves nothing in the Outbox; a failed repeating send whose copy stays is not tried again; a
#           message the server took moves only to Sent, Trash or the Outbox; a draft of one in Trash is refused with where it can go
# v3.6.349: a message sent from the Outbox whose move to Sent fails, also twice, is never sent again (RV-178); a scheduled message whose
#           removal fails after it is gone is sent, never lost (RV-179); a pause made just before the send counts (RV-180); a message sent already is never made a draft (RV-181);
#           the review of these fixes: a message moved out of Scheduled meanwhile is not sent, a sent one never goes to Drafts, it is filed
#           in Sent without a password, a message made repeating meanwhile waits
# v3.1.341: Initial: a message is never sent twice (the code review's batch 1: RV-109, RV-140, RV-50, RV-123, RV-126, RV-127, RV-132; D-256)


def held(setup: Setup) -> tuple[threading.Event, threading.Event]:
    """The server holds every message it receives until released: (a message arrived, release)."""
    arrived, release = threading.Event(), threading.Event()

    def hold() -> None:
        arrived.set()
        release.wait(10)

    setup.smtp.before_queued = hold
    return arrived, release


def in_background(work: Callable[[], object]) -> threading.Thread:
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


def subjects(setup: Setup, folder: str) -> list[str]:
    return [str(row.subject) for row in rows(setup, folder)]


def test_a_refresh_during_a_send_never_sends_the_same_message_again(setup: Setup) -> None:
    """RV-109: Send put its copy in the Outbox and talked to the server; a refresh meanwhile found that copy and sent it too."""
    arrived, release = held(setup)
    sending = in_background(lambda: call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x"))
    assert arrived.wait(5)
    refresh = in_background(lambda: call(setup.engine, "mail.send-outbox"))
    refresh.join(0.5)  # it waits for the send that runs
    release.set()
    sending.join(10)
    refresh.join(10)
    assert len(setup.smtp.received) == 1 and subjects(setup, "Outbox") == [] and subjects(setup, "Sent") == ["Once"]


def test_a_server_that_took_the_message_and_dropped_the_goodbye_has_sent_it(setup: Setup) -> None:
    """RV-140: the server answered "queued", then did not answer QUIT; the send counted as failed and every refresh sent it again."""
    setup.smtp.drop_on_quit = True
    result = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    assert result.sent and subjects(setup, "Outbox") == [] and subjects(setup, "Sent") == ["Once"]
    call(setup.engine, "mail.send-outbox")
    assert len(setup.smtp.received) == 1


def test_a_message_sent_but_not_moved_to_sent_is_never_sent_again(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-50: the move to Sent failed after the server took the message; it stayed in the Outbox and the next refresh sent it again."""
    real_move = Store.move_placed
    failures: list[str] = []

    def move_failing_once(self: Store, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> Placed:
        if not failures:
            failures.append("the disk is full")
            raise StorageError("No space left on the disk")
        return real_move(self, ref, target_folder, hint)

    monkeypatch.setattr(Store, "move_placed", move_failing_once)
    result = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    assert result.sent and "as sent" in result.warning and subjects(setup, "Outbox") == ["Once"]
    done = call(setup.engine, "mail.send-outbox")
    assert done.failed == () and len(setup.smtp.received) == 1  # only moved, never sent again
    assert subjects(setup, "Outbox") == [] and subjects(setup, "Sent") == ["Once"]


def test_a_message_sent_from_the_outbox_but_not_moved_to_sent_is_never_sent_again(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-178: sent from the Outbox (a refresh, Send now, a delayed send), its move to Sent failed: the "sent" mark was written and wiped at
    once, and the next refresh sent it again. Also when the move fails again at the next refresh."""
    real_move = Store.move_placed
    failures: list[str] = []

    def move_failing_twice(self: Store, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> Placed:
        if len(failures) < 2:
            failures.append("the disk is full")
            raise StorageError("No space left on the disk")
        return real_move(self, ref, target_folder, hint)

    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x", hold=True)
    monkeypatch.setattr(Store, "move_placed", move_failing_twice)
    for _ in range(3):
        call(setup.engine, "mail.send-outbox")
    assert len(setup.smtp.received) == 1 and len(failures) == 2
    assert subjects(setup, "Outbox") == [] and subjects(setup, "Sent") == ["Once"]


def _full_disk(monkeypatch: pytest.MonkeyPatch, failures: list[str], moves: int) -> None:
    """The disk is full: the first moves to Sent fail, and so does every write of a message's details (sent_at)."""
    from chrono_mail.store.properties import MessageProperties

    real_move = Store.move_placed

    def move(self: Store, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> Placed:
        if len(failures) < moves:
            failures.append("the disk is full")
            raise StorageError("No space left on the disk")
        return real_move(self, ref, target_folder, hint)

    def no_space(*_: object, **__: object) -> None:
        raise StorageError("No space left on the disk")

    monkeypatch.setattr(Store, "move_placed", move)
    monkeypatch.setattr(MessageProperties, "change", no_space)


def test_on_a_full_disk_a_message_the_server_took_is_never_sent_again(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The open question of 3.6.349: the move to Sent and the "sent" mark both failed on a full disk; the next refresh sent it again. The
    letter in its file name needs no free space; after a restart (the run's memory gone) it still says so."""
    failures: list[str] = []
    _full_disk(monkeypatch, failures, 2)
    result = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    assert result.sent and subjects(setup, "Outbox") == ["Once"]
    setup.engine.sent_unfiled.clear()  # a restart: only what is on the disk counts
    call(setup.engine, "mail.send-outbox")  # the move fails again
    assert len(setup.smtp.received) == 1 and len(failures) == 2
    setup.engine.sent_unfiled.clear()
    call(setup.engine, "mail.send-outbox")  # the disk has room again: only filed
    assert len(setup.smtp.received) == 1 and subjects(setup, "Outbox") == [] and subjects(setup, "Sent") == ["Once"]


def test_a_message_the_server_took_without_any_mark_on_the_disk_is_not_sent_again_in_the_run(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """Not even the rename for the letter worked: the run remembers it, so no refresh of this run sends it again."""
    failures: list[str] = []
    _full_disk(monkeypatch, failures, 3)

    def no_rename(*_: object, **__: object) -> str:
        raise StorageError("The flags could not be changed: No space left on the disk")

    monkeypatch.setattr(Store, "set_letter", no_rename)
    result = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    assert result.sent and "marked as sent" in result.warning
    for _ in range(2):
        call(setup.engine, "mail.send-outbox")
    assert len(setup.smtp.received) == 1 and len(failures) == 3


def test_a_message_the_server_took_never_becomes_a_draft_from_the_command_line(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350: draft.save with the Outbox message to replace made a draft of a message the server had taken."""
    failures: list[str] = []
    _full_disk(monkeypatch, failures, 1)
    result = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    (waiting,) = rows(setup, "Outbox")
    with pytest.raises(UsageError, match="sent already"):
        call(setup.engine, "draft.save", account=setup.account_id, to="you@example.com", subject="Once", body="x", replace_folder="Outbox", replace_message=waiting.message_id)
    assert result.sent and subjects(setup, "Drafts") == [] and subjects(setup, "Outbox") == ["Once"]


def test_a_sent_message_moved_to_sent_by_hand_is_an_ordinary_message_after(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350: a message marked as sent that reached Sent another way (moved by hand) kept its marks; moved on to Trash it
    could not go to Drafts any more ("it goes to Sent at the next Send and Receive")."""
    real_move = Store.move_placed
    once: list[str] = []

    def move_failing_once(self: Store, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> Placed:
        if not once:
            once.append("x")
            raise StorageError("No space left on the disk")
        return real_move(self, ref, target_folder, hint)

    monkeypatch.setattr(Store, "move_placed", move_failing_once)
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    (waiting,) = rows(setup, "Outbox")
    moved = call(setup.engine, "message.move", account=setup.account_id, folder="Outbox", message=waiting.message_id, target="Sent")
    trashed = call(setup.engine, "message.move", account=setup.account_id, folder="Sent", message=moved.message_id, target="Trash")
    call(setup.engine, "message.move", account=setup.account_id, folder="Trash", message=trashed.message_id, target="Drafts")
    assert subjects(setup, "Drafts") == ["Once"] and len(setup.smtp.received) == 1


def test_draft_save_takes_the_outbox_in_any_letter_case(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350: "outbox" in small letters skipped the check and the lock of the Outbox."""
    failures: list[str] = []
    _full_disk(monkeypatch, failures, 1)
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    (waiting,) = rows(setup, "Outbox")
    with pytest.raises(UsageError, match="sent already"):
        call(setup.engine, "draft.save", account=setup.account_id, to="you@example.com", subject="Once", body="x", replace_folder="outbox", replace_message=waiting.message_id)


def test_a_send_that_replaces_an_outbox_message_the_server_took_is_refused(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350: mail.send naming an Outbox message the server took as the one it replaces sent it again and deleted it."""
    failures: list[str] = []
    _full_disk(monkeypatch, failures, 1)
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    (waiting,) = rows(setup, "Outbox")
    for extra in (dict[str, object](), {"hold": True}, {"send_at": "2099-01-01T09:00"}):
        with pytest.raises(UsageError, match="sent already"):
            call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Again", body="x", replaces_folder="outbox", replaces_message=waiting.message_id, **extra)
    assert len(setup.smtp.received) == 1 and subjects(setup, "Outbox") == ["Once"]


def test_a_copy_of_a_sent_message_in_sent_is_an_ordinary_message(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350: a copy into Sent kept the letter of a message the server took, so moved on it could never become a draft."""
    real_move = Store.move_placed
    once: list[str] = []

    def move_failing_once(self: Store, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> Placed:
        if not once:
            once.append("x")
            raise StorageError("No space left on the disk")
        return real_move(self, ref, target_folder, hint)

    monkeypatch.setattr(Store, "move_placed", move_failing_once)
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    (waiting,) = rows(setup, "Outbox")
    copied = call(setup.engine, "message.copy", account=setup.account_id, folder="Outbox", message=waiting.message_id, target="Sent")
    moved = call(setup.engine, "message.move", account=setup.account_id, folder="Sent", message=copied.message_id, target="Inbox")
    call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=moved.message_id, target="Drafts")
    assert subjects(setup, "Drafts") == ["Once"]


def test_an_outbox_message_sent_by_a_refresh_while_it_is_replaced_is_never_sent_twice(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350: mail.send replacing an Outbox message checked it before a refresh sent it, then sent its replacement too.
    The replaced message now goes in the same locked step that sends or stores what replaces it; when a send took it meanwhile, nothing is
    done (also for a delayed or scheduled send, and for a draft)."""
    from chrono_mail.engine import commands_mail

    from datetime import datetime, timedelta

    real_send = commands_mail._send  # noqa: SLF001
    later = (datetime.fromtimestamp(setup.engine.clock()) + timedelta(days=2)).strftime("%Y-%m-%dT09:00")  # the engine's fixed clock
    for extra in (dict[str, object](), {"hold": True}, {"send_at": later}):
        call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Old", body="x", hold=True)
        (waiting,) = rows(setup, "Outbox")
        before = len(setup.smtp.received)

        def refresh_first(engine: object, mailbox: object, parameters: object, from_outbox: object) -> object:
            call(setup.engine, "mail.send-outbox")  # a refresh while the replacement is being made
            return real_send(engine, mailbox, parameters, from_outbox)  # type: ignore[arg-type]

        monkeypatch.setattr(commands_mail, "_send", refresh_first)
        with pytest.raises(UsageError, match="no longer in the Outbox"):
            call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="New", body="x", replaces_folder="Outbox", replaces_message=waiting.message_id, **extra)
        monkeypatch.setattr(commands_mail, "_send", real_send)
        assert len(setup.smtp.received) == before + 1  # the old one only, sent by the refresh
        assert subjects(setup, "Outbox") == [] and "New" not in subjects(setup, "Sent")
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Kept", body="x", hold=True)
    (waiting,) = rows(setup, "Outbox")
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Instead", body="x", replaces_folder="Outbox", replaces_message=waiting.message_id)
    assert subjects(setup, "Outbox") == [] and "Instead" in subjects(setup, "Sent") and "Kept" not in subjects(setup, "Sent")


def test_a_draft_of_an_outbox_message_sent_meanwhile_is_not_kept(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350: Cancel or draft.save on an Outbox message that a refresh sent meanwhile left it both sent and a draft."""
    from chrono_mail.engine.mailbox import Mailbox

    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Old", body="x", hold=True)
    (waiting,) = rows(setup, "Outbox")
    real_deliver = Mailbox.deliver

    def deliver_then_refresh(self: Mailbox, folder: tuple[str, ...], raw: bytes, flags: frozenset[object]) -> MessageRef:
        made = real_deliver(self, folder, raw, flags)  # type: ignore[arg-type]
        call(setup.engine, "mail.send-outbox")  # a refresh right after the draft was stored
        return made

    monkeypatch.setattr(Mailbox, "deliver", deliver_then_refresh)
    with pytest.raises(UsageError, match="no longer in the Outbox"):
        call(setup.engine, "draft.save", account=setup.account_id, to="you@example.com", subject="Old", body="x", replace_folder="Outbox", replace_message=waiting.message_id)
    monkeypatch.undo()
    assert subjects(setup, "Drafts") == [] and len(setup.smtp.received) == 1


def test_an_outbox_message_is_never_lost_when_what_replaces_it_cannot_be_stored(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350: the replaced Outbox message was removed before the new one was stored; a full disk then lost it, with the
    attachments it kept."""
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Old", body="x", hold=True)
    (waiting,) = rows(setup, "Outbox")

    def no_room(*_: object, **__: object) -> object:
        raise StorageError("No space left on the disk")

    monkeypatch.setattr(Store, "deliver_placed", no_room)
    for extra in (dict[str, object](), {"hold": True}):
        with pytest.raises(StorageError):
            call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="New", body="x", replaces_folder="Outbox", replaces_message=waiting.message_id, **extra)
    monkeypatch.undo()
    assert subjects(setup, "Outbox") == ["Old"] and setup.smtp.received == []


def test_a_message_is_not_taken_out_of_the_outbox_while_a_send_runs(setup: Setup) -> None:
    """The review of 3.6.350 (D-262, D-193): a message dragged out of the Outbox while its send ran was marked sent only on this computer;
    on an IMAP account it reached the server's Drafts as an ordinary draft, which another computer could send again. While a send runs,
    taking a message out of the Outbox is refused at once (never waited for: no standstill), to be tried again a moment later."""
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x", hold=True)
    (waiting,) = rows(setup, "Outbox")
    arrived, release = held(setup)
    sending = in_background(lambda: call(setup.engine, "mail.send-outbox"))
    assert arrived.wait(5)
    for command, target in (("message.move", "Drafts"), ("message.copy", "Inbox"), ("message.move", "Trash")):
        with pytest.raises(UsageError, match="being sent right now"):
            call(setup.engine, command, account=setup.account_id, folder="Outbox", message=waiting.message_id, target=target)
    with pytest.raises(UsageError, match="being sent right now"):
        call(setup.engine, "message.delete", account=setup.account_id, folder="Outbox", message=waiting.message_id, permanent=True)
    release.set()
    sending.join(10)
    assert len(setup.smtp.received) == 1 and subjects(setup, "Sent") == ["Once"] and subjects(setup, "Drafts") == []


def test_a_draft_that_a_send_took_meanwhile_is_never_sent_again(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350: the "sent already" check of a draft that a send replaces ran before the lock; a send that took it meanwhile
    went unseen, and it was sent a second time. It is asked again under the lock."""
    from chrono_mail.engine.mailbox import Mailbox

    draft = call(setup.engine, "draft.save", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    real_refuse = Mailbox.refuse_if_sent

    def taken_meanwhile(self: Mailbox, ref: MessageRef) -> None:
        self.engine.sent_unfiled.add(ref.store_id)  # as a send that took it a moment ago leaves it
        real_refuse(self, ref)

    monkeypatch.setattr(Mailbox, "refuse_if_sent", taken_meanwhile)
    for extra in (dict[str, object](), {"hold": True}):
        with pytest.raises(UsageError, match="sent already"):
            call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x", replaces_folder="Drafts", replaces_message=draft.message_id, **extra)
        setup.engine.sent_unfiled.clear()
    assert setup.smtp.received == [] and subjects(setup, "Outbox") == [] and subjects(setup, "Drafts") == ["Once"]


def test_saving_a_draft_never_waits_for_a_send(setup: Setup) -> None:
    """The review of 3.6.350: every save of a draft after its first waited for a running send (the Outbox lock is held for the whole talk
    with the mail server), also when the draft had nothing to do with it. Only a message a send may take (the Outbox, Scheduled) waits."""
    draft = call(setup.engine, "draft.save", account=setup.account_id, to="you@example.com", subject="Mine", body="x")
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Big", body="x", hold=True)
    arrived, release = held(setup)
    sending = in_background(lambda: call(setup.engine, "mail.send-outbox"))
    assert arrived.wait(5)
    saving = in_background(lambda: call(setup.engine, "draft.save", account=setup.account_id, to="you@example.com", subject="Mine 2", body="x", replace_folder="Drafts", replace_message=draft.message_id))
    saving.join(5)
    done_while_sending = not saving.is_alive()
    release.set()
    sending.join(10)
    saving.join(10)
    assert done_while_sending and subjects(setup, "Drafts") == ["Mine 2"]


def test_a_draft_left_beside_a_sent_outbox_message_is_told(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350: a send took the Outbox message a draft replaces, and the new draft could not be removed again: it stayed
    without a word, a draft of a message the server took. The person is told where it is."""
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Old", body="x", hold=True)
    (waiting,) = rows(setup, "Outbox")
    real_delete = Mailbox.delete

    def take_out_sent(self: Mailbox, ref: MessageRef) -> None:
        raise UsageError("This message is no longer in the Outbox: it was sent")

    def cannot_delete_drafts(self: Mailbox, ref: MessageRef, permanent: bool = False) -> object:
        if ref.folder_path == ("Drafts",):
            raise StorageError("The disk refused")
        return real_delete(self, ref, permanent=permanent)

    monkeypatch.setattr(Mailbox, "take_out_of_outbox", take_out_sent)
    monkeypatch.setattr(Mailbox, "delete", cannot_delete_drafts)
    with pytest.raises(StorageError, match="look in Drafts"):  # since 3.6.351 never a refusal: something was stored (the window says to look)
        call(setup.engine, "draft.save", account=setup.account_id, to="you@example.com", subject="Old", body="x", replace_folder="Outbox", replace_message=waiting.message_id)


def test_undo_of_a_delete_in_the_outbox_puts_it_back(setup: Setup) -> None:
    """The review of 3.6.350 (D-240): a rule of round 11 refused every move into the Outbox, so Undo of a delete there, or the end of a
    snooze, could never put a message back. Only the copy on an IMAP server had to go (it does, server first)."""
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Held", body="x", hold=True)
    (waiting,) = rows(setup, "Outbox")
    trashed = call(setup.engine, "message.move", account=setup.account_id, folder="Outbox", message=waiting.message_id, target="Trash")
    call(setup.engine, "message.move", account=setup.account_id, folder="Trash", message=trashed.message_id, target="Outbox")
    assert subjects(setup, "Outbox") == ["Held"]


def _try(work: Callable[[], object]) -> object:
    try:
        return work()
    except Exception as error:  # noqa: BLE001 - the answer of the command, whatever it is
        return error


def test_a_removal_that_failed_after_the_file_went_keeps_what_replaces_it(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350 (as RV-179): the old Outbox message's file went, then the removal failed; what replaced it was thrown away
    too, and nothing of the message was left."""
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Old", body="x", hold=True)
    (waiting,) = rows(setup, "Outbox")
    real_delete = Store.delete

    def gone_then_failed(self: Store, ref: MessageRef, *args: object, **kwargs: object) -> object:
        result = real_delete(self, ref, *args, **kwargs)  # type: ignore[arg-type]
        if ref.store_id == waiting.message_id:
            raise StorageError("The folder could not be written to the disk")
        return result

    monkeypatch.setattr(Store, "delete", gone_then_failed)
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="New", body="x", hold=True, replaces_folder="Outbox", replaces_message=waiting.message_id)
    monkeypatch.undo()
    assert subjects(setup, "Outbox") == ["New"]


def test_a_repeating_send_that_fails_leaves_no_copy_to_be_sent_by_a_refresh(setup: Setup) -> None:
    """RV-123: each failed try of a repeating message left a copy in the Outbox, and the next refresh sent every one of them."""
    at(setup, 0)
    repeating(setup)
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "wrong")
    at(setup, 2 * 3600)
    for _ in range(2):  # two tries of the same send
        assert call(setup.engine, "mail.send-due").sent == 0
        assert subjects(setup, "Outbox") == []
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "secret")
    assert call(setup.engine, "mail.send-outbox").sent == 0 and setup.smtp.received == []
    assert call(setup.engine, "mail.send-due").sent == 1 and len(setup.smtp.received) == 1  # the series sends it itself, once


def test_a_scheduled_message_due_and_sent_now_at_the_same_moment_goes_once(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-126: the minute check and Send now both read the scheduled message before either removed it, and both sent it."""
    at(setup, 0)
    scheduled = schedule(setup, hours=0.01)
    at(setup, 3600)
    real_read = commands_schedule._read  # noqa: SLF001
    both_read = threading.Barrier(2, timeout=5)
    reads: list[str] = []

    def read_together(mailbox: Mailbox, reference: MessageRef) -> ParsedMessage:
        parsed = real_read(mailbox, reference)
        reads.append(reference.store_id)
        if len(reads) <= 2:
            both_read.wait()  # both sends have read it before either goes on
        return parsed

    monkeypatch.setattr(commands_schedule, "_read", read_together)
    results: list[object] = []
    first = in_background(lambda: results.append(call(setup.engine, "mail.send-due")))
    second = in_background(lambda: results.append(call(setup.engine, "mail.send-due", message=scheduled.message_id)))
    first.join(10)
    second.join(10)
    assert len(results) == 2 and len(setup.smtp.received) == 1
    assert subjects(setup, "Scheduled") == [] and subjects(setup, "Outbox") == [] and subjects(setup, "Sent") == ["Later"]


def test_a_scheduled_message_that_comes_due_while_it_is_replaced_goes_once(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350 (D-262): a scheduled message that came due while it was rescheduled or saved as a draft was sent by the minute
    check, and its new version was kept as well (sent again later, or a draft of a message sent). It goes in the locked step that stores
    what replaces it, as in the Outbox; when the due send took it first, what replaces it goes again."""
    real_deliver = Mailbox.deliver
    for replace_with in ("schedule", "draft"):
        at(setup, 0)
        scheduled = schedule(setup, hours=0.01)
        at(setup, 3600)
        due_send = [True]

        def deliver_then_due_send(self: Mailbox, folder: tuple[str, ...], raw: bytes, flags: frozenset[object]) -> MessageRef:
            made = real_deliver(self, folder, raw, flags)  # type: ignore[arg-type]
            if due_send:
                due_send.clear()
                call(setup.engine, "mail.send-due")  # the minute check, right after the new version was stored
            return made

        monkeypatch.setattr(Mailbox, "deliver", deliver_then_due_send)
        values = {"account": setup.account_id, "to": "you@example.com", "subject": "Later", "body": "edited"}
        with pytest.raises(UsageError, match="no longer in Scheduled"):
            if replace_with == "schedule":
                schedule(setup, hours=5, replaces_folder="Scheduled", replaces_message=scheduled.message_id)
            else:
                call(setup.engine, "draft.save", replace_folder="Scheduled", replace_message=scheduled.message_id, **values)
        monkeypatch.undo()
        at(setup, 6 * 3600)
        call(setup.engine, "mail.send-due")
        assert subjects(setup, "Scheduled") == [] and subjects(setup, "Drafts") == []
    assert len(setup.smtp.received) == 2  # each once


def test_a_scheduled_message_whose_original_cannot_be_removed_is_not_sent(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-127: the copy went to the Outbox, removing the scheduled original failed; both were sent later."""
    at(setup, 0)
    schedule(setup, hours=0.01)
    at(setup, 3600)

    def cannot_remove(mailbox: Mailbox, reference: MessageRef) -> Callable[[], None]:
        def remove() -> None:
            raise StorageError("Access is denied")

        return remove

    monkeypatch.setattr(commands_schedule, "_remover", cannot_remove)
    done = call(setup.engine, "mail.send-due")
    assert done.sent == 0 and "Access is denied" in done.failed[0] and setup.smtp.received == []
    assert subjects(setup, "Outbox") == [] and subjects(setup, "Scheduled") == ["Later"]  # it waits where it was, for the next check
    monkeypatch.undo()
    at(setup, 3700)
    assert call(setup.engine, "mail.send-due").sent == 1 and len(setup.smtp.received) == 1


def test_a_failed_scheduled_send_says_it_is_tried_again(setup: Setup) -> None:
    """RV-132: the text said the message was not sent again by itself, while every refresh that sends tries it again (D-028)."""
    at(setup, 0)
    schedule(setup, hours=0.01)
    at(setup, 3600)
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "wrong")
    done = call(setup.engine, "mail.send-due")
    assert "tried again at the next refresh that sends" in done.failed[0] and subjects(setup, "Outbox") == ["Later"]


def test_a_scheduled_message_gone_although_its_removal_failed_is_sent_not_lost(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-179: removing the scheduled original failed after the file was gone (the folder could not be written to disk); the copy in the
    Outbox was thrown away too, and the message was lost unsent."""
    at(setup, 0)
    schedule(setup, hours=0.01)
    at(setup, 3600)
    real_delete = Mailbox.delete

    def delete_then_fail(self: Mailbox, ref: MessageRef, permanent: bool) -> str:
        real_delete(self, ref, permanent)
        raise StorageError("The folder could not be written to the disk")

    monkeypatch.setattr(Mailbox, "delete", delete_then_fail)
    done = call(setup.engine, "mail.send-due")
    assert done.sent == 1 and len(setup.smtp.received) == 1
    assert subjects(setup, "Scheduled") == [] and subjects(setup, "Outbox") == [] and subjects(setup, "Sent") == ["Later"]


def test_a_repeating_message_saved_as_a_draft_while_it_is_sent_waits_for_that_send(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350 (D-262): a repeating send wrote its timetable, then sent from the copy it had read; a draft or a new schedule
    made of the pattern in between took only the Outbox lock, so the pattern went and its occurrence was sent after it was taken back.
    What replaces a scheduled message takes the repeat lock too: it waits for that send, which never sends a pattern already taken back."""
    at(setup, 0)
    pattern = repeating(setup)
    at(setup, 2 * 3600)
    real_record = commands_schedule._record  # noqa: SLF001
    saving: list[threading.Thread] = []
    blocked: list[bool] = []

    def record_then_draft(mailbox: Mailbox, reference: MessageRef, repeat: object, now: float) -> None:
        real_record(mailbox, reference, repeat, now)  # type: ignore[arg-type]
        if not saving:
            values = {"account": setup.account_id, "to": "you@example.com", "subject": "Again", "body": "edited"}
            saving.append(in_background(lambda: call(setup.engine, "draft.save", replace_folder="Scheduled", replace_message=pattern.message_id, **values)))
            saving[0].join(0.5)
            blocked.append(saving[0].is_alive())  # it waits for the send

    monkeypatch.setattr(commands_schedule, "_record", record_then_draft)
    call(setup.engine, "mail.send-due")
    saving[0].join(10)
    assert blocked == [True] and len(setup.smtp.received) == 1 and subjects(setup, "Drafts") == ["Again"] and subjects(setup, "Scheduled") == []


def test_a_repeating_message_saved_again_after_its_time_was_sent_is_not_sent_twice(setup: Setup) -> None:
    """The review of 3.6.350 (D-262): a repeating message stays in Scheduled after each send; changed in the window while a check sent it,
    and saved again with the same first time, the new one sent that time a second time. Such a save is refused: nothing changes."""
    at(setup, 0)
    pattern = repeating(setup)  # first at one hour, every five days
    at(setup, 3605)
    call(setup.engine, "mail.send-due")  # sent at its time, the pattern stays
    assert len(setup.smtp.received) == 1
    with pytest.raises(UsageError, match="a moment ago"):
        repeating(setup, repeat_days=6, replaces_folder="Scheduled", replaces_message=pattern.message_id)  # its time unchanged
    call(setup.engine, "mail.send-due")
    assert len(setup.smtp.received) == 1 and subjects(setup, "Scheduled") == ["Again"]
    repeating(setup, send_at=iso(5 * 86_400), replaces_folder="Scheduled", replaces_message=pattern.message_id)  # its next time: taken
    assert subjects(setup, "Scheduled") == ["Again"]


def test_a_pause_made_just_before_the_send_counts(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-180: the minute check read the message, then waited for its locks while the person paused it; it sent the old text anyway and
    removed the paused message."""
    at(setup, 0)
    schedule(setup, hours=0.01)
    at(setup, 3600)
    real_lock = commands_schedule._REPEAT_LOCK  # noqa: SLF001
    paused: list[str] = []

    class PausedMeanwhile:
        """The lock the minute check takes: the person pauses the message just before it gets it."""

        def __enter__(self) -> None:
            if not paused:
                monkeypatch.setattr(commands_schedule, "_REPEAT_LOCK", real_lock)
                for row in rows(setup, "Scheduled"):
                    call(setup.engine, "mail.repeat-pause", account=setup.account_id, folder="Scheduled", message=row.message_id)
                    paused.append(row.message_id)
            real_lock.acquire()

        def __exit__(self, *_: object) -> None:
            real_lock.release()

    monkeypatch.setattr(commands_schedule, "_REPEAT_LOCK", PausedMeanwhile())
    call(setup.engine, "mail.send-due")
    assert paused and setup.smtp.received == [] and subjects(setup, "Scheduled") == ["Later"] and subjects(setup, "Outbox") == []


def test_a_message_sent_already_is_never_made_a_draft(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-181: opening an Outbox message the server took (its move to Sent failed) made it a draft; Send then sent it a second time."""
    from chrono_mail.engine.commands_outbox import SentAlready

    real_move = Store.move_placed

    def move_failing_once(self: Store, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> Placed:
        monkeypatch.setattr(Store, "move_placed", real_move)
        raise StorageError("No space left on the disk")

    monkeypatch.setattr(Store, "move_placed", move_failing_once)
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    (row,) = rows(setup, "Outbox")
    with pytest.raises(SentAlready):
        call(setup.engine, "mail.cancel-send", account=setup.account_id, message=row.message_id)
    assert subjects(setup, "Outbox") == ["Once"] and subjects(setup, "Drafts") == [] and len(setup.smtp.received) == 1


def test_a_scheduled_message_moved_away_meanwhile_is_not_sent(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-179: while the minute check sent it, the person dragged it to Drafts; the removal of the original failed, the
    copy was sent anyway and the line of the draft was removed from the index."""
    at(setup, 0)
    schedule(setup, hours=0.01)
    at(setup, 3600)
    real_delete = Mailbox.delete

    def moved_meanwhile(self: Mailbox, ref: MessageRef, permanent: bool) -> str:
        self.move(ref, self.folder("Drafts"))  # the person, a moment before
        return real_delete(self, ref, permanent)

    monkeypatch.setattr(Mailbox, "delete", moved_meanwhile)
    done = call(setup.engine, "mail.send-due")
    assert done.sent == 0 and setup.smtp.received == [] and subjects(setup, "Outbox") == [] and subjects(setup, "Drafts") == ["Later"]


def _sent_but_not_filed(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> str:
    real_move = Store.move_placed

    def move_failing_once(self: Store, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> Placed:
        monkeypatch.setattr(Store, "move_placed", real_move)
        raise StorageError("No space left on the disk")

    monkeypatch.setattr(Store, "move_placed", move_failing_once)
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    (row,) = rows(setup, "Outbox")
    return str(row.message_id)


def test_a_message_sent_already_never_goes_to_drafts(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-181: dragged from the Outbox to Drafts it became an ordinary draft, and Send sent it a second time."""
    message = _sent_but_not_filed(setup, monkeypatch)
    with pytest.raises(UsageError, match="sent already"):
        call(setup.engine, "message.move", account=setup.account_id, folder="Outbox", message=message, target="Drafts")
    assert subjects(setup, "Drafts") == [] and subjects(setup, "Outbox") == ["Once"]


def test_a_message_sent_already_is_filed_in_sent_without_a_password(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-178: a message the server took waited for its password and its countdown before it was only moved to Sent."""
    _sent_but_not_filed(setup, monkeypatch)
    setup.engine.passwords.delete(setup.account_id, ServerRole.OUTGOING)
    call(setup.engine, "mail.send-outbox")
    assert subjects(setup, "Outbox") == [] and subjects(setup, "Sent") == ["Once"] and len(setup.smtp.received) == 1


def test_a_message_made_repeating_meanwhile_waits_for_the_next_check(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-180: made repeating while the minute check waited for its locks, it was sent once as a one-off and its series ended."""
    at(setup, 0)
    schedule(setup, hours=0.01)
    at(setup, 3600)
    real_read = commands_schedule._read  # noqa: SLF001
    reads: list[str] = []

    def read_repeating_the_second_time(mailbox: Mailbox, reference: MessageRef) -> ParsedMessage:
        parsed = real_read(mailbox, reference)
        reads.append("read")
        if len(reads) == 2:  # the read under the locks: the person made it repeating a moment before
            return dataclasses.replace(parsed, chrono_headers=(*parsed.chrono_headers, ("x-chrono-mail-kind", "recurring")))
        return parsed

    monkeypatch.setattr(commands_schedule, "_read", read_repeating_the_second_time)
    done = call(setup.engine, "mail.send-due")
    assert done.sent == 0 and setup.smtp.received == [] and subjects(setup, "Scheduled") == ["Later"]


def test_unscheduling_that_cannot_take_the_original_away_leaves_no_draft(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The second review of RV-180: when the scheduled original could not be removed, it stayed scheduled and a draft of it was made too;
    it was sent at its time and the draft could be sent again."""
    at(setup, 0)
    schedule(setup, hours=2)
    (row,) = rows(setup, "Scheduled")
    real_delete = Mailbox.delete

    def scheduled_cannot_go(self: Mailbox, ref: MessageRef, permanent: bool) -> str:
        if ref.folder_path == ("Scheduled",):
            raise StorageError("Access is denied")
        return real_delete(self, ref, permanent)

    monkeypatch.setattr(Mailbox, "delete", scheduled_cannot_go)
    with pytest.raises(StorageError, match="stays scheduled"):
        call(setup.engine, "mail.unschedule", account=setup.account_id, folder="Scheduled", message=row.message_id)
    assert subjects(setup, "Scheduled") == ["Later"] and subjects(setup, "Drafts") == []


def test_a_scheduled_message_deleted_for_good_just_before_its_send_is_not_sent(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The third review of RV-179: deleted for good a moment before its send (Shift+Delete takes no send lock), the removal answered
    "gone" without an error and the copy was sent anyway."""
    at(setup, 0)
    schedule(setup, hours=0.01)
    at(setup, 3600)
    real_remover = commands_schedule._remover  # noqa: SLF001

    def deleted_by_the_person_first(mailbox: Mailbox, reference: MessageRef) -> Callable[[], None]:
        remove = real_remover(mailbox, reference)

        def person_then_remove() -> None:
            mailbox.delete(reference, permanent=True)  # the person's Shift+Delete, a moment before the send's own removal
            remove()

        return person_then_remove

    monkeypatch.setattr(commands_schedule, "_remover", deleted_by_the_person_first)
    done = call(setup.engine, "mail.send-due")
    assert done.sent == 0 and setup.smtp.received == [] and subjects(setup, "Outbox") == []


def test_send_now_of_a_repeating_message_that_is_due_takes_the_place_of_that_send(setup: Setup) -> None:
    """The final review: Send now of a repeating message whose send was due did not move its timetable on: the next minute check sent it
    again."""
    at(setup, 0)
    repeating(setup)
    at(setup, 3700)  # the first send is due now
    (row,) = rows(setup, "Scheduled")
    call(setup.engine, "mail.send-due", message=row.message_id)  # Send now
    call(setup.engine, "mail.send-due")  # the minute check a moment later
    assert len(setup.smtp.received) == 1


def test_a_message_sent_already_is_copied_only_from_sent(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The final review of RV-181: a copy lost the "sent" mark, so copy to Archive, then drag the copy to Drafts made it a draft again."""
    message = _sent_but_not_filed(setup, monkeypatch)
    with pytest.raises(UsageError, match="sent already"):
        call(setup.engine, "message.copy", account=setup.account_id, folder="Outbox", message=message, target="Trash")



def _in_sent_with_its_marks(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> str:
    """A message the server took is filed in Sent while its marks could not be cleared (the disk still full then)."""
    message = _sent_but_not_filed(setup, monkeypatch)
    real_forget = Mailbox.forget_sent_marks
    def not_cleared(self: Mailbox, ref: MessageRef) -> None:
        pass  # the disk is full: nothing of it could be written

    monkeypatch.setattr(Mailbox, "forget_sent_marks", not_cleared)
    filed = call(setup.engine, "message.move", account=setup.account_id, folder="Outbox", message=message, target="Sent")
    monkeypatch.setattr(Mailbox, "forget_sent_marks", real_forget)
    return str(filed.message_id)


def test_a_message_moved_out_of_sent_with_marks_left_is_an_ordinary_message(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 17 of the review of 3.6.350: the marks left on a message in Sent came along when it was moved out, so there it could not be
    copied or made a draft ("sent already")."""
    message = _in_sent_with_its_marks(setup, monkeypatch)
    moved = call(setup.engine, "message.move", account=setup.account_id, folder="Sent", message=message, target="Inbox")
    copied = call(setup.engine, "message.copy", account=setup.account_id, folder="Inbox", message=moved.message_id, target="Trash")
    call(setup.engine, "message.move", account=setup.account_id, folder="Trash", message=copied.message_id, target="Drafts")
    call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=moved.message_id, target="Drafts")
    assert subjects(setup, "Drafts") == ["Once", "Once"] and len(setup.smtp.received) == 1


def test_a_copy_out_of_sent_never_carries_the_marks_left(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 17 of the review of 3.6.350: a copy out of Sent took the letter of the send along."""
    message = _in_sent_with_its_marks(setup, monkeypatch)
    copied = call(setup.engine, "message.copy", account=setup.account_id, folder="Sent", message=message, target="Trash")
    call(setup.engine, "message.move", account=setup.account_id, folder="Trash", message=copied.message_id, target="Drafts")
    assert subjects(setup, "Drafts") == ["Once"] and subjects(setup, "Sent") == ["Once"]


def test_a_message_whose_marks_cannot_be_cleared_stays_in_sent(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 17 of the review of 3.6.350: when the marks left cannot be cleared (the disk is full), it does not leave Sent half-marked."""
    from chrono_mail.store.properties import MessageProperties

    message = _in_sent_with_its_marks(setup, monkeypatch)

    def no_space(*_: object, **__: object) -> None:
        raise StorageError("No space left on the disk")

    monkeypatch.setattr(MessageProperties, "change", no_space)
    with pytest.raises(UsageError, match="taken out of Sent"):
        call(setup.engine, "message.move", account=setup.account_id, folder="Sent", message=message, target="Inbox")
    assert subjects(setup, "Sent") == ["Once"] and subjects(setup, "Inbox") == []


def test_a_delayed_send_whose_waiting_time_cannot_be_written_leaves_nothing_in_the_outbox(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The final review of 3.6.350: the copy stayed in the Outbox without its waiting time while the window said nothing was stored; the
    next refresh sent it, beside the Send pressed again. The draft it came from stays."""
    from chrono_mail.store.properties import MessageProperties

    draft = call(setup.engine, "draft.save", account=setup.account_id, to="you@example.com", subject="Once", body="x")

    def no_space(*_: object, **__: object) -> None:
        raise StorageError("No space left on the disk")

    monkeypatch.setattr(MessageProperties, "change", no_space)
    with pytest.raises(StorageError):
        call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x", delay_seconds=10,
             replaces_folder="Drafts", replaces_message=draft.message_id)
    assert subjects(setup, "Outbox") == [] and subjects(setup, "Drafts") == ["Once"]


def test_a_failed_repeating_send_whose_outbox_copy_stays_is_not_tried_again_by_the_series(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The final review of 3.6.350: the copy of a failed try could not be taken out of the Outbox; the series tried the same send again
    and the Outbox sent its copy too."""
    at(setup, 0)
    repeating(setup)
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "wrong")
    at(setup, 2 * 3600)
    real_delete = Mailbox.delete

    def outbox_stuck(self: Mailbox, ref: MessageRef, permanent: bool = False) -> str:
        if ref.folder_path == ("Outbox",):
            raise StorageError("Permission denied")
        return real_delete(self, ref, permanent)

    monkeypatch.setattr(Mailbox, "delete", outbox_stuck)
    assert call(setup.engine, "mail.send-due").sent == 0 and subjects(setup, "Outbox") == ["Again"]
    monkeypatch.setattr(Mailbox, "delete", real_delete)
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "secret")
    assert call(setup.engine, "mail.send-due").sent == 0  # the series went on to the next send
    assert call(setup.engine, "mail.send-outbox").sent == 1 and len(setup.smtp.received) == 1  # the Outbox sends it, once


def test_a_message_the_server_took_moves_only_to_sent_or_trash(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The final review of 3.6.350 (D-262): dragged to Inbox or Archive it never reached Sent, and reached the server as ordinary mail."""
    message = _sent_but_not_filed(setup, monkeypatch)
    with pytest.raises(UsageError, match="sent already"):
        call(setup.engine, "message.move", account=setup.account_id, folder="Outbox", message=message, target="Inbox")
    trashed = call(setup.engine, "message.move", account=setup.account_id, folder="Outbox", message=message, target="Trash")
    call(setup.engine, "message.move", account=setup.account_id, folder="Trash", message=trashed.message_id, target="Outbox")
    assert subjects(setup, "Outbox") == ["Once"] and subjects(setup, "Inbox") == []


def test_a_draft_of_a_sent_message_in_trash_is_refused_with_where_it_can_go(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The last review of 3.6.350: the refusal promised "it goes to Sent at the next Send and Receive", which only the Outbox does."""
    message = _sent_but_not_filed(setup, monkeypatch)
    trashed = call(setup.engine, "message.move", account=setup.account_id, folder="Outbox", message=message, target="Trash")
    with pytest.raises(UsageError, match="only to Sent, or back to the Outbox"):
        call(setup.engine, "draft.save", account=setup.account_id, to="you@example.com", subject="Once", body="x", replace_folder="Trash", replace_message=trashed.message_id)


def test_a_send_the_server_took_is_never_told_as_failed_when_the_list_cannot_follow(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The three reviewers of 2026-10-26 (RV-155): the server took the message, then the index refused a read (a raw database error); the
    send came back as an error, the window said "Nothing was stored or sent", and a second Send sent it twice."""
    import sqlite3

    def locked(*_: object, **__: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(Mailbox, "report_added", locked)
    result = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    assert result.sent and "Repair the search index" in result.warning and len(setup.smtp.received) == 1


def test_a_repeating_send_the_server_took_is_never_sent_again_when_the_list_cannot_follow(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The three reviewers of 2026-10-26 (RV-123): an error after a repeating send the server took counted it as failed, and the series
    sent the same occurrence again at the next minute."""
    import sqlite3

    at(setup, 0)
    repeating(setup)
    at(setup, 2 * 3600)

    def locked(*_: object, **__: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(Mailbox, "report_added", locked)
    assert call(setup.engine, "mail.send-due").sent == 1
    monkeypatch.undo()
    at(setup, 2 * 3600 + 60)
    assert call(setup.engine, "mail.send-due").sent == 0 and len(setup.smtp.received) == 1


def test_send_and_receive_goes_on_when_the_list_cannot_follow_a_sent_message(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.351: in Send and Receive, an error after the server took a message (the list following it to Sent) stopped the
    rest of the Outbox and was told as a failed send."""
    import sqlite3

    for subject in ("One", "Two"):
        call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject=subject, body="x", hold=True)

    def locked(*_: object, **__: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(Mailbox, "report_moved", locked)
    result = call(setup.engine, "mail.send-outbox")
    assert result.sent == 2 and len(setup.smtp.received) == 2 and all("it was sent" in line for line in result.failed)


def test_a_replacement_that_cannot_be_removed_again_is_a_storage_error(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.351: when removing a new version failed, the error kept its kind (a refusal), so the window said "Nothing was
    stored or sent" while the new version waited in the Outbox beside the one already sent."""
    from chrono_mail.core import StorageError as Storage
    from chrono_mail.engine.mailbox import SentAlready, open_mailbox

    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        stored = mailbox.deliver(("Outbox",), b"Subject: x\r\n\r\nx\r\n", frozenset())

        def stuck(self: Mailbox, ref: MessageRef, permanent: bool = False) -> str:
            raise Storage("Permission denied")

        monkeypatch.setattr(Mailbox, "delete", stuck)
        with pytest.raises(Storage, match="may not have been removed"):
            mailbox.drop_replacement(stored, SentAlready("The message this replaces was sent already"))
