from collections.abc import Callable, Mapping
from typing import Any

import pytest
from PySide6.QtCore import QObject, Qt
from pytestqt.qtbot import QtBot

from chrono_mail.ui import messagelist
from chrono_mail.ui.backend import Failure, Success
from chrono_mail.ui.messagelist import MessageListModel, ViewSpec
from tests.ui.world import World, mail, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_list_changes.py 4.2.408"

# --- Version history ----------------------------------------------------
# v4.2.408: a view narrowed by Send at days follows a scheduled message whose time moved out of them (D-288)
# v4.2.406: a search of a folder and its subfolders takes in a message that came into a subfolder, and lets go of one that left; it
#           follows a folder moved out or in and one deleted with its mail (D-287)
# v2.4.334: a delete right after another comes within a tenth of a second: the test waits for it (D-223)
# v1.6.260: One way of holding rows (R-15): a row asked before a change shows the change and is never asked again; a message gone from a part
#           not loaded asks nothing; rows asked before a message came in go to their own messages
# v0.5.166: messages of one name from several addresses share one heading
# v0.5.165: mail that streams in from checks at the same moment is placed while it comes (the Windows computer's case)
# v0.5.164: a message that arrives during another check is placed where the engine has it (the owner's case); an answer and a page asked before a fact about the view are asked again; message rows never have children
# v0.5.163: messages that come in at once are placed with one question; messages gone from a part not loaded give one rearrange, never a reset
# v0.5.156: Initial tests: the list applies each fact of the engine to its own rows, without fetching the view again (D-146 step 2)


class Watched:
    """A list fed only by the facts of the engine (not yet wired into the window), with the commands it asks for."""

    def __init__(self, world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, spec: ViewSpec, groups: bool = False) -> None:
        self.world, self.qtbot = world, qtbot
        self.asked: list[str] = []
        self.resets = 0
        self.model = MessageListModel(world.backend)
        self.model.show_groups = groups
        self.model.modelReset.connect(self._reset)
        real_call = world.backend.call

        def call(
            command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
            in_order: bool = False, checking: bool = False,
        ) -> int:
            self.asked.append(command)
            return real_call(command, values, on_success, on_error, owner, waiting, in_order, checking)

        monkeypatch.setattr(world.backend, "call", call)
        world.backend.changed.connect(self.model.apply)
        self.model.show(spec)
        self.settle()
        self.asked.clear()
        self.resets = 0

    def _reset(self) -> None:
        self.resets += 1

    def settle(self) -> None:
        wait(self.qtbot, lambda: self.world.backend._pending == {} and self.model.settled)  # noqa: SLF001

    def subjects(self) -> list[str]:
        model = self.model
        return [str(getattr(model.row_at(row), "subject")) for row in range(model.rowCount()) if not model.is_header(row) and model.row_at(row) is not None]

    def headers(self) -> list[str]:
        return [str(self.model.index(row, 0).data()) for row in self.model.header_rows()]


def ids(world: World, folder: str = "Inbox", **values: Any) -> dict[str, str]:  # noqa: ANN401 - forwarded to message.list
    """subject -> message id in a folder."""
    return {row.subject: row.message_id for row in world.call("message.list", account=world.account_id, folder=folder, limit=5000, **values).messages}


def listed(world: World, folder: str = "Inbox", **values: Any) -> list[str]:  # noqa: ANN401 - forwarded to message.list
    return [row.subject for row in world.call("message.list", account=world.account_id, folder=folder, limit=5000, **values).messages]


def test_a_delete_takes_out_that_one_row_and_asks_for_nothing(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox"))
    assert watched.subjects() == ["Message 3", "Message 2", "Message 1"]
    world.call("message.delete", account=world.account_id, folder="Inbox", message=ids(world)["Message 2"])
    assert watched.subjects() == ["Message 3", "Message 1"] and watched.asked == [] and watched.resets == 0
    assert watched.model.message_count() == 2


def test_a_mark_changes_that_one_row_and_the_unread_count(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox"))
    counts: list[tuple[int, int]] = []
    def counted(total: int, unread: int) -> None:
        counts.append((total, unread))

    watched.model.counts_changed.connect(counted)
    world.call("message.flag", account=world.account_id, folder="Inbox", message=ids(world)["Message 1"], seen=True, flagged=True)
    row = watched.model.row_at(2)
    assert set(getattr(row, "flags")) == {"seen", "flagged"} and counts[-1] == (3, 2) and watched.asked == [] and watched.resets == 0


def test_a_message_moved_into_the_folder_shown_comes_in_at_its_place(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.call("folder.create", account=world.account_id, folder="Kept")
    for subject in ("Message 3", "Message 1"):
        world.call("message.move", account=world.account_id, folder="Inbox", message=ids(world)[subject], target="Kept")
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Kept", sort="subject", oldest_first=True))
    assert watched.subjects() == ["Message 1", "Message 3"]
    world.call("message.move", account=world.account_id, folder="Inbox", message=ids(world)["Message 2"], target="Kept")
    wait(qtbot, lambda: watched.subjects() == ["Message 1", "Message 2", "Message 3"])
    assert watched.subjects() == listed(world, "Kept", sort="subject", oldest_first=True) and watched.resets == 0
    assert "message.list" not in watched.asked  # only where it stands was asked


def test_group_headings_follow_and_an_emptied_heading_goes(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox", sort="subject", oldest_first=True), groups=True)
    assert watched.headers() == ["Message 1", "Message 2", "Message 3"]
    world.call("message.delete", account=world.account_id, folder="Inbox", message=ids(world)["Message 2"])
    assert watched.headers() == ["Message 1", "Message 3"] and watched.subjects() == ["Message 1", "Message 3"] and watched.resets == 0


def test_favorites_unread_takes_out_a_deleted_message_and_does_not_take_it_back_from_trash(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec("", combined="unread"))
    assert sorted(watched.subjects()) == ["Message 1", "Message 2", "Message 3"]
    world.call("message.delete", account=world.account_id, folder="Inbox", message=ids(world)["Message 3"])
    assert sorted(watched.subjects()) == ["Message 1", "Message 2"] and watched.asked == []  # Trash is not in Unread: nothing asked


def test_a_search_of_all_folders_keeps_a_message_that_moved_and_puts_it_at_its_new_place(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, None, query="Message"))
    world.call("message.delete", account=world.account_id, folder="Inbox", message=ids(world)["Message 2"])
    wait(qtbot, lambda: len(watched.subjects()) == 3 and watched.asked == ["message.places"])  # one question for the messages that came in
    assert watched.subjects() == ["Message 3", "Message 2", "Message 1"]
    assert str(getattr(watched.model.row_at(1), "folder")) == "Trash"


def test_a_search_with_subfolders_takes_in_a_message_of_a_subfolder_and_lets_go_of_one_that_left(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-287: a message moved into a folder inside the one searched joins the list at once; one moved out of them all leaves."""
    world.call("folder.create", account=world.account_id, folder="Inbox/Old/2025")
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox", query="Message", subfolders=True))
    assert len(watched.subjects()) == 3
    world.call("message.move", account=world.account_id, folder="Inbox", message=ids(world)["Message 2"], target="Junk")
    wait(qtbot, lambda: len(watched.subjects()) == 2)
    world.call("message.move", account=world.account_id, folder="Junk", message=ids(world, "Junk")["Message 2"], target="Inbox/Old/2025")
    wait(qtbot, lambda: len(watched.subjects()) == 3)
    assert "Inbox/Old/2025" in {str(getattr(watched.model.row_at(row), "folder")) for row in range(3)}


def test_a_search_with_subfolders_follows_a_folder_moved_out_or_in_and_one_deleted(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-287: a folder moved out of the folder searched with its subfolders (as a server may do) takes its mail out of the list,
    moved back in brings it back, and a folder deleted with its mail takes it out (no fact per message comes then)."""
    world.call("folder.create", account=world.account_id, folder="Inbox/Old")
    world.call("message.move", account=world.account_id, folder="Inbox", message=ids(world)["Message 1"], target="Inbox/Old")
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox", query="Message", subfolders=True))
    watched.model.refetch_wanted.connect(watched.model.reload)  # what the pane does, one fetch at a time
    assert len(watched.subjects()) == 3
    world.call("folder.rename", account=world.account_id, folder="Inbox/Old", new_name="Elsewhere")
    wait(qtbot, lambda: sorted(watched.subjects()) == ["Message 2", "Message 3"])
    world.call("folder.rename", account=world.account_id, folder="Elsewhere", new_name="Inbox/Old")
    wait(qtbot, lambda: len(watched.subjects()) == 3)
    world.call("folder.delete", account=world.account_id, folder="Inbox/Old", with_messages=True)
    wait(qtbot, lambda: sorted(watched.subjects()) == ["Message 2", "Message 3"])
    assert watched.model.spec is not None and watched.model.spec.folder == "Inbox"


def test_a_view_of_send_days_lets_go_of_a_message_whose_time_moved_out_of_them(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-288: a scheduled message edited to go later leaves a view narrowed by Send at days (its fact only says it changed)."""
    from datetime import datetime, timedelta

    now = datetime.fromtimestamp(world.engine.clock())
    soon, later = now + timedelta(days=1), now + timedelta(days=30)
    sent = world.call("mail.send", account=world.account_id, to="you@example.com", subject="Later", body="x", send_at=soon.strftime("%Y-%m-%dT%H:%M"))
    days = (now.strftime("%Y-%m-%d"), (now + timedelta(days=3)).strftime("%Y-%m-%d"))
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, None, send_at_from=days[0], send_at_to=days[1]))
    watched.model.refetch_wanted.connect(watched.model.reload)  # what the pane does, one fetch at a time
    assert watched.subjects() == ["Later"]
    world.call(
        "mail.edit-scheduled", account=world.account_id, message=getattr(sent, "message_id"), to="you@example.com", subject="Later", body="x",
        send_at=later.strftime("%Y-%m-%dT%H:%M"),
    )
    wait(qtbot, lambda: watched.subjects() == [])


def test_folder_read_emptied_and_renamed(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox"))
    world.call("folder.mark-read", account=world.account_id, folder="Inbox")
    assert all("seen" in getattr(watched.model.row_at(row), "flags") for row in range(3)) and watched.asked == []
    trash = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Trash"))
    for message_id in ids(world).values():
        world.call("message.delete", account=world.account_id, folder="Inbox", message=message_id)
    wait(qtbot, lambda: len(trash.subjects()) == 3)
    world.call("folder.empty", account=world.account_id, folder="Trash")
    assert trash.subjects() == [] and watched.subjects() == []
    world.call("folder.create", account=world.account_id, folder="Projects")
    projects = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Projects"))
    world.call("folder.rename", account=world.account_id, folder="Projects", new_name="Work")
    assert projects.model.spec is not None and projects.model.spec.folder == "Work"


def test_a_row_asked_before_a_change_shows_the_change(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """R-15: rows are asked by the message, and what changed while they were on their way is applied to them: the answer is
    used, never thrown away and asked again."""
    monkeypatch.setattr(messagelist, "PAGE_SIZE", 1)  # one message per row question: the second row is asked on its own
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox"))
    held: list[Callable[[], None]] = []
    holding_done: list[bool] = []
    passing = world.backend.call

    def holding(
        command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        watched.asked.append(command)
        if command == "message.rows" and not holding_done:
            holding_done.append(True)
            answer = world.call(command, **values)
            held.append(lambda: on_success(answer))  # the answer as it was when asked, delivered after two changes
            return 0
        return passing(command, values, on_success, on_error, owner, waiting, in_order, checking)

    monkeypatch.setattr(world.backend, "call", holding)
    message_ids = ids(world)
    assert watched.model.row_at(1, fetch=True) is None and held  # Message 2's row is on its way
    world.call("message.delete", account=world.account_id, folder="Inbox", message=message_ids["Message 3"])  # the first row goes: places move
    world.call("message.flag", account=world.account_id, folder="Inbox", message=message_ids["Message 2"], seen=True, flagged=True)
    held.pop()()  # the late answer: Message 2 unread and not flagged
    assert watched.subjects() == ["Message 2"] and set(getattr(watched.model.row_at(0), "flags")) == {"seen", "flagged"}
    assert watched.asked.count("message.rows") == 1  # nothing asked again
    wait(qtbot, lambda: watched.model.row_at(1, fetch=True) is not None)
    assert watched.subjects() == ["Message 2", "Message 1"] and watched.model.message_count() == 2


def test_a_large_folder_deletes_a_row_without_asking_the_engine_anything(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    store_folder = "Bulk"
    world.call("folder.create", account=world.account_id, folder=store_folder)
    with world.engine.quietly():
        raws = [mail(number, message_id=f"<bulk{number}@x>", subject=f"Bulk {number:04d}") for number in range(3000)]
        from chrono_mail.engine.mailbox import open_mailbox

        with open_mailbox(world.engine, world.account_id) as mailbox:
            for raw in raws:
                mailbox.deliver((store_folder,), raw, frozenset())
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, store_folder))
    assert watched.model.message_count() == 3000
    first = str(getattr(watched.model.row_at(0), "message_id"))
    world.call("message.delete", account=world.account_id, folder=store_folder, message=first)
    assert watched.model.message_count() == 2999 and watched.asked == [] and watched.resets == 0


def test_a_message_that_was_not_loaded_goes_without_a_reset(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(messagelist, "PAGE_SIZE", 1)  # only the first message is loaded
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox"))
    assert watched.model.message_count() == 3 and watched.model.row_at(2) is None
    world.call("message.delete", account=world.account_id, folder="Inbox", message=ids(world)["Message 1"])  # the last one: not loaded
    assert watched.model.message_count() == 2 and watched.resets == 0 and watched.asked == []
    wait(qtbot, lambda: watched.model.row_at(1, fetch=True) is not None)
    assert watched.subjects() == ["Message 3", "Message 2"]


def test_messages_that_come_in_at_once_are_placed_with_one_question(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox"))
    for number in (4, 5, 6):
        world.pop.messages[f"uid{number}"] = mail(number)
    world.call("mail.fetch", account=world.account_id)  # three new messages, each told as a fact
    wait(qtbot, lambda: watched.model.message_count() == 6 and world.backend._pending == {})  # noqa: SLF001
    assert watched.asked == ["message.places"] and watched.resets == 0  # one question for all three, no fetch of the view
    assert watched.subjects() == listed(world)


def test_a_message_gone_from_a_part_not_loaded_under_headings_takes_its_heading_and_asks_nothing(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(messagelist, "PAGE_SIZE", 1)  # only the first message is loaded
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox", sort="subject"), groups=True)
    assert watched.model.row_at(watched.model.display_row(2)) is None
    message_ids = ids(world)
    world.call("message.delete", account=world.account_id, folder="Inbox", message=message_ids["Message 3"])
    world.call("message.delete", account=world.account_id, folder="Inbox", message=message_ids["Message 2"])
    wait(qtbot, lambda: watched.model.message_count() == 1)  # the second right after the first: within a tenth of a second
    assert watched.resets == 0 and watched.asked == []  # the list knows where it was: nothing asked
    assert watched.headers() == ["Message 1"]


def deliver(world: World, number: int) -> None:
    from chrono_mail.engine.mailbox import open_mailbox

    with open_mailbox(world.engine, world.account_id) as mailbox:
        mailbox.deliver(mailbox.folder("Inbox"), mail(number), frozenset())


def late(world: World, monkeypatch: pytest.MonkeyPatch, watched: Watched, command: str, wanted: Callable[[Mapping[str, object]], bool], meanwhile: Callable[[], None]) -> None:
    """The first answer to this command arrives after something else happened (meanwhile runs just before it is used)."""
    asking = world.backend.call
    done: list[bool] = []

    def call(
        name: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        if name == command and wanted(values) and not done:
            done.append(True)

            def arrived(result: object) -> None:
                meanwhile()
                on_success(result)

            return asking(name, values, arrived, on_error, owner, waiting, in_order, checking)
        return asking(name, values, on_success, on_error, owner, waiting, in_order, checking)

    monkeypatch.setattr(world.backend, "call", call)


def test_a_place_asked_before_another_message_came_in_is_still_right(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox"))
    late(world, monkeypatch, watched, "message.places", lambda values: True, lambda: deliver(world, 5))
    deliver(world, 4)  # its place is asked; before the answer is used, another message comes in
    wait(qtbot, lambda: watched.model.message_count() == 5 and world.backend._pending == {})  # noqa: SLF001
    assert watched.subjects() == listed(world) and watched.resets == 0


def test_rows_asked_before_a_message_came_in_go_to_their_own_messages(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(messagelist, "PAGE_SIZE", 1)  # one message per row question
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox"))
    late(world, monkeypatch, watched, "message.rows", lambda values: True, lambda: deliver(world, 4))
    watched.model.row_at(2, fetch=True)  # the last row is asked; before it is used, a newer message comes in (all places move one down)
    wait(qtbot, lambda: world.backend._pending == {} and watched.model.message_count() == 4)  # noqa: SLF001
    for row in range(4):
        watched.model.row_at(row, fetch=True)
    wait(qtbot, lambda: world.backend._pending == {} and all(watched.model.row_at(row) is not None for row in range(4)))  # noqa: SLF001
    assert watched.subjects() == listed(world)  # every row at its place
    assert watched.asked.count("message.rows") == 2  # Message 1, and Message 2 when it came into view; Message 4 came with its row: none asked again


def test_message_rows_never_have_children(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox"))
    model = watched.model
    assert all(model.flags(model.index(row, 0)) & Qt.ItemFlag.ItemNeverHasChildren for row in range(model.rowCount()))
    assert "index" not in MessageListModel.__dict__  # Qt's own, without a call into Python for every row


def test_a_message_that_arrives_during_another_check_is_placed_where_the_engine_has_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner's case: a check stored its first new message and is still downloading the next, while another message
    comes in. Its place must be counted against the rows the list holds, never against mail it has not heard of."""
    import threading

    from tests.fakepop3 import FakePop3Server
    from tests.fakeserver import Conversation

    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox", sort="subject"))
    world.pop.messages["uid4"] = mail(4)
    world.pop.messages["uid5"] = mail(5)
    second, release = threading.Event(), threading.Event()
    sending = FakePop3Server._send_message

    def slow(conversation: Conversation, message: bytes) -> None:
        if b"Subject: Message 5" in message:
            second.set()
            release.wait(5)  # the check waits here: Message 4 is stored, Message 5 not yet
        sending(conversation, message)

    monkeypatch.setattr(FakePop3Server, "_send_message", staticmethod(slow))
    checking = threading.Thread(target=lambda: world.call("mail.fetch", account=world.account_id))
    checking.start()
    assert second.wait(5)
    from chrono_mail.engine.mailbox import open_mailbox

    with open_mailbox(world.engine, world.account_id) as mailbox:  # meanwhile another message comes in (an action, a rule, another check)
        mailbox.deliver(mailbox.folder("Inbox"), mail(35), frozenset())
    wait(qtbot, lambda: world.backend._pending == {} and "Message 35" in watched.subjects())  # noqa: SLF001
    release.set()
    checking.join(5)
    wait(qtbot, lambda: world.backend._pending == {} and watched.model.message_count() == 6)  # noqa: SLF001
    assert watched.subjects() == listed(world, sort="subject")  # every row where the engine has it


def test_mail_that_streams_in_from_checks_at_the_same_moment_is_placed_while_it_comes(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """the Windows computer's case: two checks bring 150 messages each at the same moment into the view on screen (All Inboxes).
    Every message is told as it is stored; the list places them while they come, with few questions, and holds them all,
    in the engine's order, at once after the checks."""
    import threading

    from chrono_mail.engine.mailbox import open_mailbox

    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, None, combined="inboxes", sort="subject"))
    for number in range(100, 400, 2):
        world.pop.messages[f"uid{number}"] = mail(number)

    def other_check() -> None:  # the second account: its messages are stored and told one by one, as a check does
        with open_mailbox(world.engine, world.account_id) as mailbox:
            for number in range(101, 400, 2):
                mailbox.deliver(mailbox.folder("Inbox"), mail(number), frozenset())

    checks = [threading.Thread(target=lambda: world.call("mail.fetch", account=world.account_id)), threading.Thread(target=other_check)]
    for check in checks:
        check.start()
    wait(qtbot, lambda: watched.model.message_count() > 3, timeout=10_000)  # mail shows while the checks run
    for check in checks:
        check.join(30)
    wait(qtbot, lambda: world.backend._pending == {} and not watched.model._waiting()[0] and watched.model._placing is None, timeout=10_000)  # noqa: SLF001 - everything heard is placed
    assert watched.model.message_count() == 303
    for row in range(watched.model.rowCount()):  # scrolled to the end: the rows come as they come into view
        watched.model.row_at(row, fetch=True)
    wait(qtbot, lambda: world.backend._pending == {} and all(watched.model.row_at(row) is not None for row in range(watched.model.rowCount())))  # noqa: SLF001
    engine = [row.subject for row in world.call("message.combined", view="inboxes", sort="subject", limit=1000)]
    assert watched.subjects() == engine and watched.resets == 0
    assert watched.asked.count("message.combined-places") < 100  # one question per burst, not one per message


def test_messages_of_one_name_from_several_addresses_share_one_heading(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.engine.mailbox import open_mailbox

    with open_mailbox(world.engine, world.account_id) as mailbox:
        for number, sender in ((7, "Corner Shop Team <one@spam.example>"), (8, "Corner Shop Team <two@other.example>")):
            mailbox.deliver(mailbox.folder("Inbox"), mail(number, sender=sender), frozenset())
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox", sort="sender", oldest_first=True), groups=True)
    assert watched.headers().count("Corner Shop Team") == 1  # the heading shows the name, and groups by it
