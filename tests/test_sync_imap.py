import json
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import pytest

from chrono_mail.core import ConnectionSecurity, FolderRole, MessageFlag, ServerSettings, StorageError
from chrono_mail.crypto import generate_data_key
from chrono_mail.index import Index
from chrono_mail.protocols.imap import FolderEntry, ImapClient, ImapLimits
from chrono_mail.store import Store
from chrono_mail.sync import FolderReport, ImapReport, ImapState, ImapSyncOptions, plan_folders, sync_imap
from tests.fakeimap import FakeImapServer
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_sync_imap.py 0.0.44"

# --- Version history ----------------------------------------------------
# v0.0.44: Folder choices, roles and check interval per account
# v0.0.31: IMAP accounts in the engine and the interface
# v0.0.30: IMAP sync, store to server
# v0.0.29: Initial IMAP sync tests

INBOX = ("Inbox",)
LIMITS = ImapLimits(read_timeout_seconds=3, connect_timeout_seconds=3)


def mail(number: int) -> bytes:
    return raw_message(subject=f"Message {number}", body=f"body {number}\r\n", message_id=f"<m{number}@x>")


@dataclass
class World:
    store: Store
    index: Index
    state: ImapState
    server: FakeImapServer
    path: Path

    def sync(self, options: ImapSyncOptions = ImapSyncOptions(), state: ImapState | None = None) -> ImapReport:
        settings = ServerSettings("127.0.0.1", self.server.port, ConnectionSecurity.NONE, "user")
        with ImapClient(settings, "secret", LIMITS) as client:
            return sync_imap(client, self.store, self.index, self.state if state is None else state, options)

    def messages(self, folder: tuple[str, ...]) -> dict[str, tuple[bytes, frozenset[MessageFlag]]]:
        return {m.ref.store_id: (self.store.read(m.ref), m.flags) for m in self.store.list_messages(folder)}

    def subjects(self, folder: tuple[str, ...]) -> list[str]:
        return sorted(row.subject for row in self.index.page(folder, limit=1000))


def of(report: ImapReport, folder: str) -> FolderReport:
    return next(item for item in report.folders if item.folder == folder)


@pytest.fixture
def world(tmp_path: Path) -> Generator[World]:
    store = Store.create(tmp_path / "store", "acc", generate_data_key(), durable=False)
    store.create_folder(INBOX, FolderRole.INBOX)
    server = FakeImapServer(plain=False)  # the plain LOGIN command is enough here
    server.__enter__()
    yield World(store, Index(tmp_path / "index.sqlite"), ImapState(tmp_path / "state.log", durable=False), server, tmp_path)
    server.__exit__(None, None, None)


# --- which folder is which -----------------------------------------------------------------------------------------------------------------------


def entry(name: str, *attributes: str, delimiter: str | None = "/") -> FolderEntry:
    return FolderEntry(name, delimiter, frozenset(attributes))


def test_the_standard_folders_are_matched_by_attribute_or_usual_name() -> None:
    plans, notes = plan_folders([entry("INBOX"), entry("Sent Items", "\\sent"), entry("Drafts"), entry("Deleted Items"), entry("Spam"), entry("Archives"), entry("Work/Clients")])
    by_name = {plan.server_name: (plan.local_path, plan.role) for plan in plans}
    assert by_name["INBOX"] == (("Inbox",), FolderRole.INBOX) and by_name["Sent Items"] == (("Sent",), FolderRole.SENT)
    assert by_name["Drafts"][0] == ("Drafts",) and by_name["Deleted Items"][0] == ("Trash",) and by_name["Spam"][0] == ("Junk",) and by_name["Archives"] == (("Archive",), FolderRole.ARCHIVE)
    assert by_name["Work/Clients"] == (("Work", "Clients"), FolderRole.OTHER) and notes == []


def test_an_explicit_claim_beats_a_usual_name_and_a_role_is_given_once() -> None:
    plans, notes = plan_folders([entry("Sent"), entry("Verzonden", "\\sent")])
    assert {plan.server_name: plan.local_path for plan in plans} == {"Verzonden": ("Sent",)}  # the explicit claim wins
    assert len(notes) == 1 and "share its place" in notes[0]


def test_folders_under_a_dot_prefix_and_odd_names() -> None:
    plans, notes = plan_folders([entry("INBOX", delimiter="."), entry("INBOX.Sent", "\\sent", delimiter="."), entry("INBOX.Projects.2026", delimiter="."), entry("a..b", delimiter="."), entry("bad\x1fname", delimiter=".")])
    paths = {plan.server_name: plan.local_path for plan in plans}
    assert paths["INBOX.Sent"] == ("Sent",) and paths["INBOX.Projects.2026"] == ("Inbox", "Projects", "2026")
    assert "a..b" not in paths and "bad\x1fname" not in paths and len(notes) == 2


def test_unselectable_and_unsubscribed_folders_are_left_out() -> None:
    plans, _ = plan_folders([entry("INBOX"), entry("Parent", "\\noselect"), entry("Shown"), entry("Hidden")], subscribed=frozenset({"Shown"}))
    assert {plan.server_name for plan in plans} == {"INBOX", "Shown"}
    everything, _ = plan_folders([entry("INBOX"), entry("A"), entry("B")])
    assert len(everything) == 3  # when nothing is subscribed at all, all folders are shown


# --- downloading ------------------------------------------------------------------------------------------------------------------------------


def test_new_messages_are_stored_with_their_flags_indexed_and_remembered(world: World) -> None:
    world.server.add("INBOX", mail(1), {"\\Seen"})
    world.server.add("INBOX", mail(2), {"\\Flagged", "\\Answered", "$Label1"})
    world.server.add("INBOX", mail(3))
    report = world.sync()
    assert report.aborted is None and report.downloaded == 3 and report.failed == ()
    stored = world.messages(INBOX)
    assert sorted(raw for raw, _ in stored.values()) == sorted([mail(1), mail(2), mail(3)])
    flags = {raw: flag_set for raw, flag_set in stored.values()}
    assert flags[mail(1)] == {MessageFlag.SEEN} and flags[mail(2)] == {MessageFlag.FLAGGED, MessageFlag.REPLIED} and flags[mail(3)] == frozenset()
    assert world.subjects(INBOX) == ["Message 1", "Message 2", "Message 3"]
    assert world.state.validity[INBOX] == 1000 and len(world.state.entries[INBOX]) == 3


def test_a_second_run_downloads_nothing_again(world: World) -> None:
    world.server.add("INBOX", mail(1))
    assert world.sync().downloaded == 1
    again = world.sync()
    assert again.downloaded == 0 and again.failed == () and len(world.messages(INBOX)) == 1
    world.server.add("INBOX", mail(2))
    assert world.sync().downloaded == 1 and len(world.messages(INBOX)) == 2


def test_other_folders_are_created_and_filled(world: World) -> None:
    world.server.add("Sent", mail(1), {"\\Seen"})
    world.server.add("Projects/Alpha", mail(2))
    world.sync()
    paths = {folder.path: folder.role for folder in world.store.list_folders()}
    assert paths[("Sent",)] is FolderRole.SENT and paths[("Projects", "Alpha")] is FolderRole.OTHER
    assert world.subjects(("Sent",)) == ["Message 1"] and world.subjects(("Projects", "Alpha")) == ["Message 2"]


def test_batches_cover_many_messages_and_big_ones_are_skipped(world: World) -> None:
    for number in range(1, 8):
        world.server.add("INBOX", mail(number))
    world.server.add("INBOX", raw_message(subject="Huge", body="x" * 5000 + "\r\n", message_id="<huge@x>"))
    report = world.sync(ImapSyncOptions(maximum_message_bytes=2000, batch_messages=3))
    assert report.downloaded == 7 and len(report.failed) == 1 and "larger than the limit" in report.failed[0]
    assert "Huge" not in world.subjects(INBOX)
    assert world.sync(ImapSyncOptions(maximum_message_bytes=1_000_000)).downloaded == 1  # raised limit: now it comes


def test_a_message_the_server_will_not_send_is_reported_and_retried(world: World) -> None:
    uid = world.server.add("INBOX", mail(1))
    world.server.add("INBOX", mail(2))
    original = world.server.folders["INBOX"].messages[0].raw
    world.server.folders["INBOX"].messages[0].raw = b""  # an empty body is sent as nothing at all
    report = world.sync()
    assert report.downloaded in (1, 2)
    world.server.folders["INBOX"].messages[0].raw = original
    world.sync()
    assert len(world.messages(INBOX)) == 2 and uid in world.state.entries[INBOX]


# --- changes on the server --------------------------------------------------------------------------------------------------------------------


def test_flags_changed_on_the_server_are_applied_here(world: World) -> None:
    uid = world.server.add("INBOX", mail(1))
    world.sync()
    world.server.folders["INBOX"].messages[0].flags = {"\\Seen", "\\Flagged", "$Junk", "\\Recent"}
    report = world.sync()
    assert of(report, 'Inbox').flags_updated == 1
    (raw_flags,) = world.messages(INBOX).values()
    assert raw_flags[1] == {MessageFlag.SEEN, MessageFlag.FLAGGED}
    assert world.index.page(INBOX)[0].flags == {MessageFlag.SEEN, MessageFlag.FLAGGED}
    assert world.state.entries[INBOX][uid].flags == {"\\seen", "\\flagged"}
    assert of(world.sync(), 'Inbox').flags_updated == 0  # nothing more to do


def test_a_local_flag_change_waiting_to_be_sent_is_not_overwritten(world: World) -> None:
    world.server.add("INBOX", mail(1))
    world.sync()
    (message,) = world.store.list_messages(INBOX)
    world.store.set_flags(message.ref, frozenset({MessageFlag.FLAGGED}))  # changed here, not sent yet
    world.server.folders["INBOX"].messages[0].flags = {"\\Seen"}  # changed there
    report = world.sync()
    assert of(report, 'Inbox').flags_updated == 0
    assert world.store.list_messages(INBOX)[0].flags == {MessageFlag.FLAGGED}


def test_messages_deleted_on_the_server_are_removed_here(world: World) -> None:
    uids = [world.server.add("INBOX", mail(number)) for number in range(1, 4)]
    world.sync()
    world.server.remove("INBOX", uids[1])
    report = world.sync()
    assert of(report, 'Inbox').removed == 1 and world.subjects(INBOX) == ["Message 1", "Message 3"]
    assert len(world.messages(INBOX)) == 2 and uids[1] not in world.state.entries[INBOX]


def test_a_folder_that_suddenly_looks_empty_is_not_wiped(world: World) -> None:
    for number in range(1, 13):
        world.server.add("INBOX", mail(number))
    world.sync()
    for uid in world.server.uids("INBOX")[:9]:
        world.server.remove("INBOX", uid)
    report = world.sync()
    assert of(report, 'Inbox').removed == 0 and "gone from the server" in (of(report, 'Inbox').note or "")
    assert len(world.messages(INBOX)) == 12


def test_removal_is_repaired_after_a_crash_between_the_steps(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    uids = [world.server.add("INBOX", mail(number)) for number in range(1, 4)]
    world.sync()
    world.server.remove("INBOX", uids[0])

    class Crash(BaseException):
        pass

    def crash(self: ImapState, folder: tuple[str, ...], uid: int) -> None:
        raise Crash

    monkeypatch.setattr(ImapState, "unmap", crash)
    with pytest.raises(Crash):
        world.sync()
    monkeypatch.undo()
    assert len(world.messages(INBOX)) == 2  # the file is gone already, the state still has the old line
    reloaded = ImapState(world.state.path, durable=False)
    report = world.sync(state=reloaded)
    assert report.failed == () and uids[0] not in reloaded.entries[INBOX] and len(world.messages(INBOX)) == 2


# --- a renumbered folder --------------------------------------------------------------------------------------------------------------------------


def test_a_renumbered_folder_matches_messages_again_by_message_id(world: World) -> None:
    for number in range(1, 4):
        world.server.add("INBOX", mail(number), {"\\Seen"} if number == 1 else set())
    world.sync()
    before = set(world.messages(INBOX))
    world.server.new_uid_validity("INBOX")
    world.server.folders["INBOX"].messages[1].flags = {"\\Flagged"}
    report = world.sync()
    folder = of(report, 'Inbox')
    assert folder.adopted == 3 and folder.downloaded == 0 and folder.recovered == 0 and "renumbered" in (folder.note or "")
    assert set(world.messages(INBOX)) == before  # the same local messages, no copies
    assert world.state.validity[INBOX] == 1001 and sorted(world.state.entries[INBOX]) == [1, 2, 3]


def test_messages_the_renumbered_folder_no_longer_has_are_kept_in_recovered(world: World) -> None:
    for number in range(1, 4):
        world.server.add("INBOX", mail(number))
    world.sync()
    world.server.new_uid_validity("INBOX")
    world.server.remove("INBOX", world.server.uids("INBOX")[0])
    world.server.add("INBOX", mail(9))
    report = world.sync()
    folder = of(report, 'Inbox')
    assert folder.adopted == 2 and folder.downloaded == 1 and folder.recovered == 1
    assert world.subjects(("Recovered", "Inbox")) == ["Message 1"] and world.subjects(INBOX) == ["Message 2", "Message 3", "Message 9"]


def test_the_first_sync_of_a_folder_leaves_local_only_messages_alone(world: World) -> None:
    world.store.deliver(INBOX, mail(50), verify=True)
    world.server.add("INBOX", mail(1))
    world.sync()
    assert len(world.messages(INBOX)) == 2 and ("Recovered", "Inbox") not in {folder.path for folder in world.store.list_folders()}


# --- trouble --------------------------------------------------------------------------------------------------------------------------------


def test_a_dropped_connection_keeps_what_was_done(world: World) -> None:
    for number in range(1, 7):
        world.server.add("INBOX", mail(number))
    dropped = FakeImapServer(plain=False, fault=lambda verb, count: verb == "UID" and count == 3)
    with dropped:
        for number in range(1, 7):
            dropped.add("INBOX", mail(number))
        settings = ServerSettings("127.0.0.1", dropped.port, ConnectionSecurity.NONE, "user")
        with ImapClient(settings, "secret", LIMITS) as client:
            report = sync_imap(client, world.store, world.index, world.state, ImapSyncOptions(batch_messages=2))
    assert report.aborted is not None and "closed the connection" in report.aborted
    assert 0 < len(world.messages(INBOX)) < 6 and len(world.state.entries[INBOX]) == len(world.messages(INBOX))
    resumed = world.sync(ImapSyncOptions(batch_messages=2))
    assert resumed.aborted is None and len(world.messages(INBOX)) == 6  # the rest comes next time, nothing twice
    assert len({raw for raw, _ in world.messages(INBOX).values()}) == 6


def test_a_folder_the_server_refuses_does_not_stop_the_others(world: World) -> None:
    world.server.add("INBOX", mail(1))
    world.server.add("Other", mail(2))
    original = world.server.folders["Other"]
    world.sync()
    del world.server.folders["Other"]  # listed no more
    report = world.sync()
    assert report.aborted is None and len(world.messages(("Other",))) == 1  # a folder that vanished on the server keeps its local mail
    world.server.folders["Other"] = original


def test_a_bad_state_file_is_survivable(tmp_path: Path) -> None:
    path = tmp_path / "state.log"
    good = json.dumps({"e": "v", "f": ["Inbox"], "v": 7})
    path.write_text("\n".join([good, "not json", '{"e":"m","f":["Inbox"],"u":1,"s":"short","g":[]}', '{"e":"v","f":[],"v":1}', '{"e":"future","f":["x"]}', '{"e":"m","f":["Inbox"],"u":1,"s":"' + "a" * 32 + '","g":["\\\\seen"]}', '{"e":"v","f":["Inbox"]']))
    state = ImapState(path, durable=False)
    assert state.validity == {("Inbox",): 7} and state.entries[("Inbox",)][1].flags == {"\\seen"} and state.ignored_lines >= 4
    state.map(("Inbox",), 2, "b" * 32, frozenset())  # appending after a cut-off last line starts on a new line
    assert ImapState(path, durable=False).entries[("Inbox",)][2].store_id == "b" * 32


def test_the_state_is_compacted_when_it_has_grown(tmp_path: Path) -> None:
    from chrono_mail.sync import imap_state

    state = ImapState(tmp_path / "state.log", durable=False)
    state.set_validity(("Inbox",), 5)
    for uid in range(1, 21):
        state.map(("Inbox",), uid, f"{uid:032x}", frozenset())
        for round_number in range(60):
            state.set_flags(("Inbox",), uid, frozenset({"\\seen"} if round_number % 2 else set()))
    assert state.compact_if_needed() is False  # not yet: the file is small
    imap_state.COMPACT_MINIMUM_LINES = 100
    assert state.compact_if_needed() is True
    reloaded = ImapState(tmp_path / "state.log", durable=False)
    assert reloaded.validity == state.validity and reloaded.entries == state.entries and reloaded.ignored_lines == 0
    assert len((tmp_path / "state.log").read_text().splitlines()) == 21


def test_the_state_cannot_be_read_or_written_when_the_file_is_a_directory(tmp_path: Path) -> None:
    (tmp_path / "state.log").mkdir()
    with pytest.raises(StorageError):
        ImapState(tmp_path / "state.log", durable=False)


# --- sending local changes to the server (0.0.30) ------------------------------------------------------------------------------------------------


def only(world: World, folder: tuple[str, ...]) -> str:
    (store_id,) = [m.ref.store_id for m in world.store.list_messages(folder)]
    return store_id


def test_a_flag_changed_here_is_sent_and_only_the_difference(world: World) -> None:
    uid = world.server.add("INBOX", mail(1), {"\\Seen", "$Label1"})
    world.sync()
    (message,) = world.store.list_messages(INBOX)
    world.store.set_flags(message.ref, frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED}))
    report = world.sync()
    assert of(report, "Inbox").flags_sent == 1
    assert world.server.flags_of("INBOX", uid) == {"\\Seen", "\\Flagged", "$Label1"}  # the server's own keyword stays
    assert of(world.sync(), "Inbox").flags_sent == 0 and world.state.entries[INBOX][uid].flags == {"\\seen", "\\flagged"}
    world.store.set_flags(world.store.list_messages(INBOX)[0].ref, frozenset())
    world.sync()
    assert world.server.flags_of("INBOX", uid) == {"$Label1"}


def test_a_flag_changed_on_both_sides_merges_and_the_local_change_is_not_lost(world: World) -> None:
    uid = world.server.add("INBOX", mail(1))
    world.sync()
    (message,) = world.store.list_messages(INBOX)
    world.store.set_flags(message.ref, frozenset({MessageFlag.SEEN}))  # here: read
    world.server.folders["INBOX"].messages[0].flags = {"\\Flagged"}  # there: flagged
    world.sync()
    assert world.server.flags_of("INBOX", uid) == {"\\Flagged", "\\Seen"}
    world.sync()
    assert world.store.list_messages(INBOX)[0].flags == {MessageFlag.SEEN, MessageFlag.FLAGGED}  # and now the flag comes here too


def test_a_message_moved_here_is_moved_on_the_server(world: World) -> None:
    uid = world.server.add("INBOX", mail(1))
    world.server.add("INBOX", mail(2))
    world.sync()
    (first,) = [m for m in world.store.list_messages(INBOX) if world.state.find(m.ref.store_id) == (INBOX, uid)]
    world.store.move(first.ref, ("Junk",))
    report = world.sync()
    assert report.moved == 1 and report.failed == ()
    assert world.server.uids("INBOX") == [2] and world.server.raw_of("Junk", world.server.uids("Junk")[0]) == mail(1)
    assert len(world.messages(("Junk",))) == 1 and len(world.messages(INBOX)) == 1  # nothing was downloaded twice
    assert world.state.find(first.ref.store_id) == (("Junk",), world.server.uids("Junk")[0])
    again = world.sync()
    assert again.moved == 0 and again.downloaded == 0


def test_a_move_without_the_new_uid_is_matched_by_message_id_afterwards(world: World) -> None:
    world.server.__exit__(None, None, None)
    world.server = FakeImapServer(plain=False, uidplus=False, move=False)
    world.server.__enter__()
    world.server.add("INBOX", mail(1))
    world.sync()
    (message,) = world.store.list_messages(INBOX)
    world.store.move(message.ref, ("Trash",))
    world.sync()
    world.sync()
    assert world.server.uids("INBOX") == [] and len(world.server.uids("Trash")) == 1
    assert len(world.messages(("Trash",))) == 1 and world.state.find(message.ref.store_id) is not None


def test_a_message_deleted_for_good_here_is_deleted_on_the_server(world: World) -> None:
    uids = [world.server.add("INBOX", mail(number)) for number in range(1, 4)]
    world.sync()
    victim = next(m for m in world.store.list_messages(INBOX) if world.state.find(m.ref.store_id) == (INBOX, uids[1]))
    world.index.remove(victim.ref.store_id)
    world.store.delete(victim.ref)
    report = world.sync()
    assert of(report, "Inbox").expunged == 1 and world.server.uids("INBOX") == [uids[0], uids[2]]
    assert uids[1] not in world.state.entries[INBOX] and len(world.messages(INBOX)) == 2


def test_nothing_is_deleted_on_the_server_when_most_of_a_folder_is_missing_here(world: World) -> None:
    for number in range(1, 13):
        world.server.add("INBOX", mail(number))
    world.sync()
    for message in world.store.list_messages(INBOX)[:9]:
        world.store.delete(message.ref)
    report = world.sync()
    assert of(report, "Inbox").expunged == 0 and "missing from the store" in (of(report, "Inbox").note or "")
    assert len(world.server.uids("INBOX")) == 12


def test_nothing_is_deleted_on_the_server_when_the_store_is_damaged(world: World) -> None:
    uid = world.server.add("INBOX", mail(1))
    world.server.add("INBOX", mail(2))
    world.sync()
    world.store.delete(world.store.list_messages(INBOX)[0].ref)
    world.store.damaged_folders.append("f-broken")
    report = world.sync()
    assert of(report, "Inbox").expunged == 0 and "damaged" in (of(report, "Inbox").note or "") and uid in world.server.uids("INBOX")


def test_local_messages_are_uploaded_once_and_mapped(world: World) -> None:
    world.store.create_folder(("Sent",), FolderRole.SENT)
    reference = world.store.deliver(("Sent",), mail(7), flags=frozenset({MessageFlag.SEEN}), verify=True)
    world.sync()
    assert world.server.raw_of("Sent", world.server.uids("Sent")[0]) == mail(7) and world.server.flags_of("Sent", world.server.uids("Sent")[0]) == {"\\Seen"}
    assert reference.store_id in world.state.uploaded and world.state.find(reference.store_id) is not None
    report = world.sync()
    assert of(report, "Sent").uploaded == 0 and len(world.server.uids("Sent")) == 1


def test_a_local_copy_of_a_message_the_server_has_is_not_uploaded(world: World) -> None:
    world.server.add("Sent", mail(7))
    world.store.create_folder(("Sent",), FolderRole.SENT)
    world.store.deliver(("Sent",), mail(7), verify=True)  # for example the copy kept when it was sent
    report = world.sync()
    assert of(report, "Sent").adopted == 1 and of(report, "Sent").uploaded == 0 and of(report, "Sent").downloaded == 0
    assert len(world.server.uids("Sent")) == 1 and len(world.messages(("Sent",))) == 1


def test_an_upload_the_server_refuses_is_reported_and_tried_again(world: World) -> None:
    world.store.create_folder(("Sent",), FolderRole.SENT)
    reference = world.store.deliver(("Sent",), mail(7), verify=True)
    world.server.refuse_appends = True
    report = world.sync()
    world.server.refuse_appends = False
    assert of(report, "Sent").uploaded == 0 and any("mailbox full" in reason for reason in of(report, "Sent").failed) and reference.store_id not in world.state.uploaded
    assert of(world.sync(), "Sent").uploaded == 1


def test_a_connection_lost_during_an_upload_is_an_error_of_the_run_not_a_crash(world: World) -> None:
    world.store.create_folder(("Sent",), FolderRole.SENT)
    world.store.deliver(("Sent",), mail(7), verify=True)
    dropped = FakeImapServer(plain=False, fault=lambda verb, count: verb == "APPEND")
    with dropped:
        settings = ServerSettings("127.0.0.1", dropped.port, ConnectionSecurity.NONE, "user")
        with ImapClient(settings, "secret", LIMITS) as client:
            report = sync_imap(client, world.store, world.index, world.state)
    assert report.aborted is not None and len(world.messages(("Sent",))) == 1


def test_a_crash_after_the_server_took_a_message_does_not_duplicate_it(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.store.create_folder(("Sent",), FolderRole.SENT)
    world.store.deliver(("Sent",), mail(7), verify=True)

    class Crash(BaseException):
        pass

    def crash(self: ImapState, store_id: str) -> None:
        raise Crash

    monkeypatch.setattr(ImapState, "mark_uploaded", crash)
    with pytest.raises(Crash):
        world.sync()
    monkeypatch.undo()
    assert len(world.server.uids("Sent")) == 1  # it did reach the server, but the state does not know
    report = world.sync()
    assert len(world.server.uids("Sent")) == 1 and of(report, "Sent").adopted == 1 and len(world.messages(("Sent",))) == 1


def test_the_uploaded_marks_survive_compaction(tmp_path: Path) -> None:
    state = ImapState(tmp_path / "state.log", durable=False)
    state.set_validity(("Inbox",), 3)
    state.mark_uploaded("a" * 32)
    from chrono_mail.sync import imap_state

    imap_state.COMPACT_MINIMUM_LINES = 1
    for number in range(40):
        state.set_validity(("Inbox",), 3)
    assert state.compact_if_needed()
    assert ImapState(tmp_path / "state.log", durable=False).uploaded == {"a" * 32}


# --- folders made and deleted here (0.0.31) ----------------------------------------------------------------------------------------------------------


def test_a_folder_made_here_is_made_on_the_server_and_then_synchronised(world: World) -> None:
    world.sync()
    world.store.create_folder(("Projects", "Beta"), FolderRole.OTHER)
    world.store.deliver(("Projects", "Beta"), mail(5), verify=True)
    report = world.sync()
    assert "Projects/Beta" in world.server.folders and len(world.server.uids("Projects/Beta")) == 1
    assert report.aborted is None
    again = world.sync()
    assert of(again, "Projects/Beta").uploaded == 0 and len(world.server.uids("Projects/Beta")) == 1 and len(world.messages(("Projects", "Beta"))) == 1


def test_local_only_folders_are_not_sent_to_the_server(world: World) -> None:
    world.sync()
    world.store.create_folder(("Outbox",), FolderRole.OTHER)
    world.store.deliver(("Outbox",), mail(5), verify=True)
    world.sync()
    assert "Outbox" not in world.server.folders and "Recovered" not in world.server.folders


def test_a_standard_folder_the_server_lacks_is_made_there(world: World) -> None:
    del world.server.folders["Junk"]
    world.sync()
    world.store.create_folder(("Junk",), FolderRole.JUNK)
    world.sync()
    assert "Junk" in world.server.folders


def test_a_folder_deleted_here_is_hidden_and_stays_on_the_server(world: World) -> None:
    world.server.add("Archive2", mail(1))
    world.server.add("Archive2", mail(2))
    world.sync()
    assert len(world.messages(("Archive2",))) == 2
    for message in world.store.list_messages(("Archive2",)):
        world.index.remove(message.ref.store_id)
    world.store.delete_folder(("Archive2",), require_empty=False)
    report = world.sync()
    assert any("deleted here" in note for note in report.notes)
    assert ("Archive2",) not in {f.path for f in world.store.list_folders()} and len(world.server.uids("Archive2")) == 2
    again = world.sync()
    assert again.notes == () and ("Archive2",) not in {f.path for f in world.store.list_folders()}  # not brought back
    reloaded = ImapState(world.state.path, durable=False)
    assert "Archive2" in reloaded.hidden and ("Archive2",) not in reloaded.validity


def test_a_folder_missing_from_a_damaged_store_is_not_concluded_deleted(world: World) -> None:
    world.server.add("Archive2", mail(1))
    world.sync()
    world.store.delete_folder(("Archive2",), require_empty=False)
    world.store.damaged_folders.append("f-broken")
    report = world.sync()
    assert any("looks damaged" in note for note in report.notes) and "Archive2" not in world.state.hidden


# --- the person's choices of folders and roles (0.0.44) ------------------------------------------------------------------------------------


def test_a_role_chosen_by_the_person_beats_the_automatic_one() -> None:
    entries = [entry("INBOX"), entry("Sent", "\\sent"), entry("Verzonden"), entry("Rommel")]
    plans, _ = plan_folders(entries, roles={"Verzonden": "sent", "Rommel": "junk", "INBOX": "trash", "Sent": "nonsense"})
    by_name = {plan.server_name: plan.local_path for plan in plans}
    assert by_name["Verzonden"] == ("Sent",) and by_name["Rommel"] == ("Junk",) and by_name["INBOX"] == ("Inbox",)
    assert "Sent" not in by_name or by_name["Sent"] != ("Sent",)  # the place is taken: the old Sent folder is an ordinary folder now


def test_a_folder_the_server_does_not_list_as_subscribed_can_be_forced(world: World) -> None:
    world.server.add("Hidden", mail(1))
    world.server.folders["Hidden"].subscribed = False
    world.sync()
    assert ("Hidden",) not in {f.path for f in world.store.list_folders()}  # the others are subscribed: this one is not shown
    world.state.set_hidden("Hidden", False)  # the person wants it
    assert "Hidden" in world.state.forced and "Hidden" not in world.state.hidden
    world.sync()
    assert len(world.messages(("Hidden",))) == 1


def test_hiding_a_folder_stops_its_synchronisation_and_showing_brings_it_back(world: World) -> None:
    world.server.add("Projects", mail(1))
    world.sync()
    assert len(world.messages(("Projects",))) == 1
    world.state.set_hidden("Projects", True)
    world.server.add("Projects", mail(2))
    world.sync()
    assert len(world.messages(("Projects",))) == 1  # not synchronised: the new message does not come, the old one stays
    world.state.set_hidden("Projects", False)
    world.sync()
    assert len(world.messages(("Projects",))) == 2


def test_the_choices_survive_the_file_and_compaction(tmp_path: Path) -> None:
    from chrono_mail.sync import imap_state

    state = ImapState(tmp_path / "state.log", durable=False)
    state.set_hidden("A", True)
    state.set_hidden("B", False)
    state.set_role("C", "junk")
    state.set_role("D", "trash")
    state.set_role("D", "")
    reloaded = ImapState(tmp_path / "state.log", durable=False)
    assert (reloaded.hidden, reloaded.forced, reloaded.roles) == ({"A"}, {"B"}, {"C": "junk"})
    imap_state.COMPACT_MINIMUM_LINES = 1
    for number in range(30):
        state.set_role("C", "junk")
    assert state.compact_if_needed()
    again = ImapState(tmp_path / "state.log", durable=False)
    assert (again.hidden, again.forced, again.roles) == ({"A"}, {"B"}, {"C": "junk"}) and again.ignored_lines == 0
