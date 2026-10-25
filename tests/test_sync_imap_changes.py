from collections.abc import Callable, Generator
from pathlib import Path

import pytest

from chrono_mail.core import FolderRole, MessageFlag
from chrono_mail.crypto import generate_data_key
from chrono_mail.index import Index
from chrono_mail.store import Store
from chrono_mail.sync import ImapState, ImapSyncOptions
from chrono_mail.sync.imap_state import Checkpoint
from tests.fakeimap import FakeImapServer
from tests.test_sync_imap import INBOX, World, mail, of

# --- Version ------------------------------------------------------------
__version__ = "test_sync_imap_changes.py 3.6.351"

# --- Version history ----------------------------------------------------
# v3.6.351: after a message is left (too large), the checkpoint keeps it and the next check asks only it again
# v2.0.285: a mark made here offline and another set on the server meanwhile both show after one check, on every kind of server (F2)
# v1.1.249: a message removed on the server gives no error line and is never sent back (R-40)
# v1.1.248: Initial version: a check asks the server only what changed since the last one (CONDSTORE, QRESYNC), reads this computer's mail once,
#           and asks for everything again whenever something does not add up or was left open (R-09)

Make = Callable[..., World]


@pytest.fixture
def make(tmp_path: Path) -> Generator[Make]:
    servers: list[FakeImapServer] = []

    def build(**options: bool) -> World:
        store = Store.create(tmp_path / "store", "acc", generate_data_key(), durable=False)
        store.create_folder(INBOX, FolderRole.INBOX)
        server = FakeImapServer(plain=False, **options)
        server.__enter__()
        servers.append(server)
        return World(store, Index(tmp_path / "index.sqlite"), ImapState(tmp_path / "state.log", durable=False), server, tmp_path)

    yield build
    for server in servers:
        server.__exit__(None, None, None)


def fetches(world: World) -> list[str]:
    return [command for command in world.server.commands if command.upper().startswith("UID FETCH")]


def again(world: World) -> list[str]:
    """The UID FETCH commands of one more check."""
    world.server.commands.clear()
    report = world.sync()
    assert report.aborted is None and report.failed == ()
    return fetches(world)


FULL = "UID FETCH 1:* (UID FLAGS RFC822.SIZE)"


def test_an_unchanged_folder_asks_the_server_for_no_list(make: Make) -> None:
    world = make(qresync=True)
    for number in range(1, 6):
        world.server.add("INBOX", mail(number))
    world.sync()
    assert world.state.checkpoints[INBOX].modseq is not None
    assert again(world) == []  # the change counter stayed: nothing is listed, nothing downloaded


def test_a_mark_changed_on_the_server_comes_in_alone(make: Make) -> None:
    world = make(qresync=True)
    uids = [world.server.add("INBOX", mail(number)) for number in range(1, 6)]
    world.sync()
    world.server.set_flags("INBOX", uids[1], {"\\Seen", "\\Flagged"})
    asked = again(world)
    assert asked and all("CHANGEDSINCE" in command for command in asked) and FULL not in asked
    flags = {raw: marks for raw, marks in world.messages(INBOX).values()}
    assert flags[mail(2)] == {MessageFlag.SEEN, MessageFlag.FLAGGED} and flags[mail(1)] == frozenset()


def test_a_message_removed_on_the_server_goes_here_without_a_full_list(make: Make) -> None:
    world = make(qresync=True)
    uids = [world.server.add("INBOX", mail(number)) for number in range(1, 6)]
    world.sync()
    world.server.remove("INBOX", uids[2])
    world.server.commands.clear()
    report = world.sync()
    assert of(report, "Inbox").removed == 1 and FULL not in fetches(world)
    assert sorted(raw for raw, _ in world.messages(INBOX).values()) == sorted([mail(1), mail(2), mail(4), mail(5)])


def test_new_mail_comes_with_the_changes(make: Make) -> None:
    world = make(qresync=True)
    world.server.add("INBOX", mail(1))
    world.sync()
    world.server.add("INBOX", mail(2))
    world.server.commands.clear()
    report = world.sync()
    assert report.downloaded == 1 and FULL not in fetches(world)
    assert sorted(raw for raw, _ in world.messages(INBOX).values()) == [mail(1), mail(2)]


def test_without_qresync_a_removal_is_seen_by_the_count_and_everything_is_asked(make: Make) -> None:
    world = make(condstore=True)
    uids = [world.server.add("INBOX", mail(number)) for number in range(1, 6)]
    world.sync()
    world.server.set_flags("INBOX", uids[0], {"\\Seen"})
    assert all("CHANGEDSINCE" in command for command in again(world))  # a change of marks only: the count adds up
    world.server.remove("INBOX", uids[3])
    world.server.commands.clear()
    report = world.sync()
    assert of(report, "Inbox").removed == 1 and FULL in fetches(world)


def test_a_server_without_change_counters_asks_sizes_only_for_new_mail(make: Make) -> None:
    world = make()
    world.server.add("INBOX", mail(1))
    world.server.add("INBOX", mail(2))
    world.sync()
    assert world.state.checkpoints[INBOX] == Checkpoint(3, None)
    world.server.add("INBOX", mail(3))
    world.server.commands.clear()
    report = world.sync()
    assert report.downloaded == 1
    assert fetches(world)[:2] == ["UID FETCH 1:2 (UID FLAGS)", "UID FETCH 3:* (UID FLAGS RFC822.SIZE)"]


def test_after_something_left_open_the_next_check_asks_everything(make: Make) -> None:
    world = make(qresync=True)
    world.server.add("INBOX", mail(1))
    large = world.server.add("INBOX", b"X" * 2000 + mail(2))  # larger than the limit below: not downloaded, a failure
    options = ImapSyncOptions(maximum_message_bytes=1000)
    report = world.sync(options)
    checkpoint = world.state.checkpoints.get(INBOX)  # since 3.6.351 the checkpoint keeps it, to ask only it again (RV-49)
    assert len(report.failed) == 1 and checkpoint is not None and checkpoint.waiting == frozenset({large})
    world.server.commands.clear()
    again = world.sync(options)
    assert len(again.failed) == 1 and FULL not in fetches(world)  # never "nothing changed" while the server holds a message not here,
    assert any(str(large) in command and "RFC822.SIZE" in command.upper() for command in world.server.commands)  # and only it is asked


def test_a_mark_changed_here_is_sent_with_the_marks_the_server_has_now(make: Make) -> None:
    world = make(qresync=True)
    uid = world.server.add("INBOX", mail(1), {"Invoice"})
    world.sync()
    message = next(iter(world.store.list_messages(INBOX)))
    assert world.store.properties.tags_of(message.ref.store_id) == ("Invoice",)
    world.store.properties.set_tags(message.ref.store_id, ())  # the tag removed here (offline)
    world.store.set_flags(message.ref, frozenset({MessageFlag.SEEN}))
    world.server.commands.clear()
    world.sync()
    assert f"UID FETCH {uid} (UID FLAGS)" in fetches(world)  # asked first: a keyword is removed as the server spells it
    assert world.server.flags_of("INBOX", uid) == {"\\Seen"}


def test_the_mail_here_is_read_once_per_check(make: Make, monkeypatch: pytest.MonkeyPatch) -> None:
    world = make(qresync=True)
    for name in ("Work", "Private", "Old"):
        world.server.folders.setdefault(name, type(world.server.folders["INBOX"])())
        world.server.add(name, mail(len(name)))
    world.server.add("INBOX", mail(1))
    world.sync()
    calls: dict[str, int] = {"list": 0, "locate_all": 0, "properties": 0}
    store = world.store
    real_list, real_locate, real_ids = store.list_messages, store.locate_all, store.properties.stored_ids

    def listing(path: tuple[str, ...]):  # type: ignore[no-untyped-def]  # a counting wrapper of the store's own method
        calls["list"] += 1
        return real_list(path)

    def locating():  # type: ignore[no-untyped-def]
        calls["locate_all"] += 1
        return real_locate()

    def ids():  # type: ignore[no-untyped-def]
        calls["properties"] += 1
        return real_ids()

    monkeypatch.setattr(store, "list_messages", listing)
    monkeypatch.setattr(store, "locate_all", locating)
    monkeypatch.setattr(store.properties, "stored_ids", ids)
    world.sync()
    folders = len(store.list_folders())
    assert calls == {"list": folders, "locate_all": 0, "properties": 1}  # before: every folder again for every folder


def test_the_checkpoint_is_kept_moved_and_forgotten_with_the_folder(tmp_path: Path) -> None:
    state = ImapState(tmp_path / "state.log", durable=False)
    state.set_validity(("Work",), 7)
    state.set_checkpoint(("Work",), Checkpoint(12, 340))
    assert ImapState(tmp_path / "state.log").checkpoints == {("Work",): Checkpoint(12, 340)}
    state.rename_folder(("Work",), ("Jobs",), {"Work": "Jobs"})
    assert ImapState(tmp_path / "state.log").checkpoints == {("Jobs",): Checkpoint(12, 340)}
    state.set_validity(("Jobs",), 8)  # renumbered: everything is asked again
    assert ImapState(tmp_path / "state.log").checkpoints == {}
    state.set_checkpoint(("Jobs",), Checkpoint(3, None))
    state.set_checkpoint(("Jobs",), None)
    assert ImapState(tmp_path / "state.log").checkpoints == {}


@pytest.mark.parametrize("options", [{}, {"qresync": True}], ids=["no-counter", "qresync"])
def test_a_message_removed_on_the_server_gives_no_error_line(make: Make, options: dict[str, bool]) -> None:
    world = make(**options)
    uids = [world.server.add("INBOX", mail(number)) for number in range(1, 4)]
    world.sync()
    world.server.remove("INBOX", uids[1])
    report = world.sync()
    assert of(report, "Inbox").removed == 1 and report.failed == ()  # before: "A local message could not be read for sending" (R-40)
    assert world.server.uids("INBOX") == [uids[0], uids[2]]  # nothing sent back to the server
    assert INBOX in world.state.checkpoints  # the check left nothing open


@pytest.mark.parametrize("kind", ["plain", "condstore", "qresync"])
def test_a_mark_made_here_and_another_set_on_the_server_meanwhile_both_show_after_one_check(make: Make, kind: str) -> None:
    """The Windows computer's F2: the server's flag on the same message came here only one check later."""
    world = make(**({} if kind == "plain" else {kind: True}))
    uid = world.server.add("INBOX", mail(1))
    world.sync()
    (message,) = world.store.list_messages(INBOX)
    world.store.set_flags(message.ref, frozenset({MessageFlag.SEEN}))  # read here, not sent yet
    world.server.set_flags("INBOX", uid, world.server.flags_of("INBOX", uid) | {"\\Flagged"})  # flagged there meanwhile
    report = world.sync()
    assert report.aborted is None and report.failed == ()
    assert world.server.flags_of("INBOX", uid) == {"\\Seen", "\\Flagged"}
    assert world.store.list_messages(INBOX)[0].flags == {MessageFlag.SEEN, MessageFlag.FLAGGED}
    assert world.state.folder_entries(INBOX)[uid].flags == {"\\seen", "\\flagged"}  # the agreed marks are the server's
    world.sync()
    assert world.store.list_messages(INBOX)[0].flags == {MessageFlag.SEEN, MessageFlag.FLAGGED} and world.server.flags_of("INBOX", uid) == {"\\Seen", "\\Flagged"}
