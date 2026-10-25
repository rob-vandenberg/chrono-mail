import json
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from chrono_mail.core import ConnectionSecurity, Folder, FolderRole, MessageFlag, MessageRef, ServerSettings, StorageError
from chrono_mail.crypto import generate_data_key
from chrono_mail.index import Index
from chrono_mail.protocols.imap import FolderEntry, ImapClient, ImapLimits
from chrono_mail.store import Store
from chrono_mail.sync import FolderReport, ImapReport, ImapState, ImapSyncOptions, plan_folders, sync_imap
from chrono_mail.sync.announce import SILENT, Announcer
from tests.fakeimap import FakeImapServer
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_sync_imap.py 3.6.355"

# --- Version history ----------------------------------------------------
# v3.6.355: Snoozed deleted on the server goes here too, although it has a role of its own (CR-36)
# v2.4.330: a Junk folder the server has but does not show (unsubscribed) is used, by its special-use mark or its name, never made again (W7)
# v2.0.285: a mark the server changed meanwhile shows in the same check that sends the mark made here (F2)
# v1.9.266: R-45: the compaction tests put COMPACT_MINIMUM_LINES back (it stayed 1 or 100 for every later test)
# v1.1.241: the server's list of folders to show is followed: a folder taken off it goes here after one last synchronisation, a folder put on it
#           comes; the person's ticks, standard folders and mail only here win; two computers end up the same (R-34)
# v1.1.240: folder renames and deletes made offline survive a reload and a compaction of the state (R-31)
# v1.1.238: an offline move on a server without MOVE and UIDPLUS leaves another program's deleted-marked mail alone (R-35)
# v1.1.237: a folder deleted or renamed on the server follows here, only on the server's confirmation; with changes here a sync conflict; a
#           standard folder is kept and made again (R-30); a folder that vanished no longer keeps its mail here (confirmed deleted)
# v1.1.227: a refused upload is a sync conflict and is not tried again while it is open (D-195)
# v1.1.200: A server folder named like a local-only folder (Outbox, Recovered, Scheduled) is not synchronised
# v0.5.164: each change made here is told in one step with its index write (new, marks, removed, a folder made)
# v0.4.119: A folder held back by a safety rule is reported; the person's four answers
# v0.3.114: Messages gone on both sides are forgotten without a warning
# v0.0.44: Folder choices, roles and check interval per account
# v0.0.31: IMAP accounts in the engine and the interface
# v0.0.30: IMAP sync, store to server
# v0.0.29: Initial IMAP sync tests

INBOX = ("Inbox",)
LIMITS = ImapLimits(read_timeout_seconds=3, connect_timeout_seconds=3)


class Recording:
    """An announcer that notes each change it is told about, whether it was told inside changing(), and whether the index
    already had the change then (the index write and the fact are one step, D-151)."""

    def __init__(self, index: Index) -> None:
        self.index = index
        self.inside = 0
        self.told: list[tuple[str, str, bool, bool]] = []  # kind, store id, inside changing, in the index

    @contextmanager
    def changing(self) -> Generator[None]:
        self.inside += 1
        try:
            yield
        finally:
            self.inside -= 1

    def _note(self, kind: str, reference: MessageRef) -> None:
        self.told.append((kind, reference.store_id, self.inside > 0, self.index.get(reference.store_id) is not None))

    def added(self, reference: MessageRef) -> None:
        self._note("added", reference)

    def removed(self, reference: MessageRef, was_unread: bool) -> None:
        self._note("removed", reference)

    def flags(self, reference: MessageRef, flags: frozenset[MessageFlag], was_unread: bool) -> None:
        self._note("flags", reference)

    def tags(self, reference: MessageRef, tags: tuple[str, ...]) -> None:
        self._note("tags", reference)

    def moved(self, before: MessageRef, now: MessageRef) -> None:
        self._note("moved", now)

    def folder_made(self, folder: Folder) -> None:
        self.told.append(("folder_made", "/".join(folder.path), self.inside > 0, True))

    def folder_renamed(self, old: tuple[str, ...], new: tuple[str, ...]) -> None:
        self.told.append(("folder_renamed", "/".join(old) + ">" + "/".join(new), self.inside > 0, True))


def mail(number: int) -> bytes:
    return raw_message(subject=f"Message {number}", body=f"body {number}\r\n", message_id=f"<m{number}@x>")


@dataclass
class World:
    store: Store
    index: Index
    state: ImapState
    server: FakeImapServer
    path: Path

    def sync(self, options: ImapSyncOptions = ImapSyncOptions(), state: ImapState | None = None, announcer: Announcer = SILENT) -> ImapReport:
        settings = ServerSettings("127.0.0.1", self.server.port, ConnectionSecurity.NONE, "user")
        with ImapClient(settings, "secret", LIMITS) as client:
            return sync_imap(client, self.store, self.index, self.state if state is None else state, options, announcer=announcer)

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
    assert world.store.list_messages(INBOX)[0].flags == {MessageFlag.FLAGGED, MessageFlag.SEEN}  # the change here is kept, the server's comes along (F2)
    assert world.server.flags_of("INBOX", world.server.uids("INBOX")[0]) == {"\\Flagged", "\\Seen"}


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
    del world.server.folders["Other"]  # deleted on the server
    report = world.sync()
    assert report.aborted is None and len(world.messages(INBOX)) == 1  # the others go on
    assert ("Other",) not in {folder.path for folder in world.store.list_folders()}  # confirmed deleted there: gone here too (R-30)
    world.server.folders["Other"] = original


def test_a_bad_state_file_is_survivable(tmp_path: Path) -> None:
    path = tmp_path / "state.log"
    good = json.dumps({"e": "v", "f": ["Inbox"], "v": 7})
    path.write_text("\n".join([good, "not json", '{"e":"m","f":["Inbox"],"u":1,"s":"short","g":[]}', '{"e":"v","f":[],"v":1}', '{"e":"future","f":["x"]}', '{"e":"m","f":["Inbox"],"u":1,"s":"' + "a" * 32 + '","g":["\\\\seen"]}', '{"e":"v","f":["Inbox"]']))
    state = ImapState(path, durable=False)
    assert state.validity == {("Inbox",): 7} and state.entries[("Inbox",)][1].flags == {"\\seen"} and state.ignored_lines >= 4
    state.map(("Inbox",), 2, "b" * 32, frozenset())  # appending after a cut-off last line starts on a new line
    assert ImapState(path, durable=False).entries[("Inbox",)][2].store_id == "b" * 32


def test_the_state_is_compacted_when_it_has_grown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.sync import imap_state

    state = ImapState(tmp_path / "state.log", durable=False)
    state.set_validity(("Inbox",), 5)
    for uid in range(1, 21):
        state.map(("Inbox",), uid, f"{uid:032x}", frozenset())
        for round_number in range(60):
            state.set_flags(("Inbox",), uid, frozenset({"\\seen"} if round_number % 2 else set()))
    assert state.compact_if_needed() is False  # not yet: the file is small
    monkeypatch.setattr(imap_state, "COMPACT_MINIMUM_LINES", 100)  # put back after the test (R-45: the others then saw 100)
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
    assert world.store.list_messages(INBOX)[0].flags == {MessageFlag.SEEN, MessageFlag.FLAGGED}  # the server's flag comes here in the same check (F2)
    world.sync()
    assert world.store.list_messages(INBOX)[0].flags == {MessageFlag.SEEN, MessageFlag.FLAGGED}


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
    (left,) = world.server.uids("INBOX")  # without UIDPLUS the original stays, marked deleted; the folder is never expunged (R-35)
    assert "\\Deleted" in world.server.flags_of("INBOX", left) and len(world.server.uids("Trash")) == 1
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


def test_messages_gone_on_both_sides_are_forgotten_without_a_warning(world: World) -> None:
    uids = [world.server.add("INBOX", mail(number)) for number in range(1, 13)]
    world.sync()
    for message in world.store.list_messages(INBOX)[:9]:
        world.store.delete(message.ref)
    assert "missing from the store" in (of(world.sync(), "Inbox").note or "")  # blocked: the server keeps all 12
    for uid in uids:
        if world.state.entries[INBOX][uid].store_id not in world.store.all_store_ids():
            world.server.remove("INBOX", uid)  # the person empties them on the server too
    report = world.sync()
    assert of(report, "Inbox").note is None and of(report, "Inbox").removed == 0 and len(world.state.entries[INBOX]) == 3
    assert len(world.messages(INBOX)) == 3 and len(world.server.uids("INBOX")) == 3


def deleted_here(world: World) -> None:
    """12 messages synchronised, 9 deleted for good here: the safety rule holds the Inbox back."""
    for number in range(1, 13):
        world.server.add("INBOX", mail(number))
    world.sync()
    for message in world.store.list_messages(INBOX)[:9]:
        world.store.delete(message.ref)


def test_a_folder_held_back_is_reported_and_the_person_may_delete_on_the_server(world: World) -> None:
    deleted_here(world)
    report = world.sync()
    assert (of(report, "Inbox").blocked, of(report, "Inbox").blocked_count) == ("missing_here", 9)
    report = world.sync(ImapSyncOptions(confirmed=((INBOX, "delete_on_server"),)))
    assert of(report, "Inbox").expunged == 9 and of(report, "Inbox").blocked is None and len(world.server.uids("INBOX")) == 3
    assert of(world.sync(), "Inbox").blocked is None


def test_or_bring_the_messages_back_here(world: World) -> None:
    deleted_here(world)
    report = world.sync(ImapSyncOptions(confirmed=((INBOX, "restore_here"),)))
    assert of(report, "Inbox").downloaded == 9 and len(world.store.list_messages(INBOX)) == 12 and len(world.server.uids("INBOX")) == 12
    assert of(world.sync(), "Inbox").blocked is None


def test_most_gone_from_the_server_is_reported_and_the_person_may_remove_or_keep(world: World) -> None:
    uids = [world.server.add("INBOX", mail(number)) for number in range(1, 13)]
    world.sync()
    for uid in uids[:9]:
        world.server.remove("INBOX", uid)
    report = world.sync()
    assert (of(report, "Inbox").blocked, of(report, "Inbox").blocked_count) == ("gone_from_server", 9) and len(world.store.list_messages(INBOX)) == 12
    report = world.sync(ImapSyncOptions(confirmed=((INBOX, "keep_here"),)))
    assert of(report, "Inbox").uploaded == 9 and len(world.server.uids("INBOX")) == 12 and of(world.sync(), "Inbox").blocked is None
    for uid in world.server.uids("INBOX")[:9]:
        world.server.remove("INBOX", uid)
    assert of(world.sync(), "Inbox").blocked == "gone_from_server"
    report = world.sync(ImapSyncOptions(confirmed=((INBOX, "remove_here"),)))
    assert of(report, "Inbox").removed == 9 and len(world.store.list_messages(INBOX)) == 3


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


def test_an_upload_the_server_refuses_is_a_sync_conflict_and_not_tried_again_while_it_is_open(world: World) -> None:
    from chrono_mail.sync.mailconflicts import MESSAGE_REFUSED, forget, load

    world.store.create_folder(("Sent",), FolderRole.SENT)
    reference = world.store.deliver(("Sent",), mail(7), verify=True)
    world.server.refuse_appends = True
    report = world.sync()
    world.server.refuse_appends = False
    assert of(report, "Sent").uploaded == 0 and of(report, "Sent").failed == () and reference.store_id not in world.state.uploaded
    (conflict,) = load(world.store)
    assert (conflict.reason, conflict.store_id, conflict.folder) == (MESSAGE_REFUSED, reference.store_id, ("Sent",)) and "mailbox full" in conflict.detail
    assert of(world.sync(), "Sent").uploaded == 0 and len(load(world.store)) == 1  # not sent again, not doubled, no line at every check
    forget(world.store, conflict.conflict_id)
    assert of(world.sync(), "Sent").uploaded == 1  # once the conflict is gone it goes


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


def test_the_uploaded_marks_survive_compaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = ImapState(tmp_path / "state.log", durable=False)
    state.set_validity(("Inbox",), 3)
    state.mark_uploaded("a" * 32)
    from chrono_mail.sync import imap_state

    monkeypatch.setattr(imap_state, "COMPACT_MINIMUM_LINES", 1)  # put back after the test (R-45)
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


def test_the_choices_survive_the_file_and_compaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.sync import imap_state

    state = ImapState(tmp_path / "state.log", durable=False)
    state.set_hidden("A", True)
    state.set_hidden("B", False)
    state.set_role("C", "junk")
    state.set_role("D", "trash")
    state.set_role("D", "")
    reloaded = ImapState(tmp_path / "state.log", durable=False)
    assert (reloaded.hidden, reloaded.forced, reloaded.roles) == ({"A"}, {"B"}, {"C": "junk"})
    monkeypatch.setattr(imap_state, "COMPACT_MINIMUM_LINES", 1)  # put back after the test (R-45)
    for number in range(30):
        state.set_role("C", "junk")
    assert state.compact_if_needed()
    again = ImapState(tmp_path / "state.log", durable=False)
    assert (again.hidden, again.forced, again.roles) == ({"A"}, {"B"}, {"C": "junk"}) and again.ignored_lines == 0


def test_each_change_made_here_is_told_in_one_step_with_its_index_write(world: World) -> None:
    first = world.server.add("INBOX", mail(1))
    second = world.server.add("INBOX", mail(2))
    recording = Recording(world.index)
    world.sync(announcer=recording)
    assert [kind for kind, _, _, _ in recording.told if kind != "folder_made"] == ["added", "added"]  # and the server's folders made here
    assert all(inside and indexed for kind, _, inside, indexed in recording.told if kind == "added")
    recording.told.clear()
    with world.server.lock:
        next(message for message in world.server.folders["INBOX"].messages if message.uid == first).flags.add("\\Seen")
    world.server.remove("INBOX", second)
    world.server.add("Projects", mail(3))
    world.sync(announcer=recording)
    kinds = [kind for kind, _, _, _ in recording.told]
    assert sorted(kinds) == ["added", "flags", "folder_made", "removed"]
    assert all(inside for kind, _, inside, _ in recording.told if kind != "folder_made")
    assert all(indexed for kind, _, _, indexed in recording.told if kind in ("added", "flags"))
    assert not next(indexed for kind, _, _, indexed in recording.told if kind == "removed")  # gone from the index when told
    assert kinds.index("folder_made") < kinds.index("added")  # the folder before its mail


def test_a_server_folder_named_like_a_local_only_folder_is_not_synchronised() -> None:
    plans, notes = plan_folders([entry("INBOX"), entry("Scheduled"), entry("Outbox"), entry("Recovered/Inbox"), entry("Work")])
    assert {plan.local_path for plan in plans} == {("Inbox",), ("Work",)}  # waiting, unsent and recovered mail never meets server mail
    assert sum("only on this computer" in note for note in notes) == 3


# --- folders deleted or renamed on the server (R-30) -------------------------------------------------------------------------------------------


def paths(world: World) -> set[tuple[str, ...]]:
    return {folder.path for folder in world.store.list_folders()}


def test_a_folder_renamed_on_the_server_is_renamed_here_and_nothing_is_downloaded_again(world: World) -> None:
    world.server.add("Projects", mail(1))
    world.server.add("Projects/Old", mail(2))
    world.sync()
    ids = set(world.messages(("Projects",))) | set(world.messages(("Projects", "Old")))
    for name in [name for name in world.server.folders if name.startswith("Projects")]:  # renamed in webmail, which subscribes the new names
        world.server.folders["Work" + name[len("Projects"):]] = world.server.folders.pop(name)
    recording = Recording(world.index)
    report = world.sync(announcer=recording)
    assert ("Projects",) not in paths(world) and {("Work",), ("Work", "Old")} <= paths(world)
    assert set(world.messages(("Work",))) | set(world.messages(("Work", "Old"))) == ids and report.downloaded == 0
    assert ("folder_renamed", "Projects>Work", True, True) in recording.told
    assert world.sync().downloaded == 0 and world.state.server_names[("Work", "Old")] == "Work/Old"


def test_a_folder_the_server_does_not_confirm_deleted_is_kept(world: World) -> None:
    world.server.add("Other", mail(1))
    world.sync()
    original = world.server.folders.pop("Other")
    world.server.refuse_single_list = True
    report = world.sync()
    assert ("Other",) in paths(world) and len(world.messages(("Other",))) == 1
    assert any("did not confirm it was deleted" in note for note in report.notes)
    world.server.refuse_single_list = False
    world.server.folders["Other"] = original


def test_a_folder_taken_off_the_servers_list_goes_here_and_its_mail_stays_on_the_server(world: World) -> None:
    world.server.add("Other", mail(1))
    world.sync()
    world.server.folders["Other"].subscribed = False  # another program unsubscribed it
    report = world.sync()
    assert ("Other",) not in paths(world) and world.index.count(("Other",)) == 0
    assert "Other" in world.server.folders and len(world.server.folders["Other"].messages) == 1  # the server is untouched
    assert any("no longer on the server's list of folders to show" in note for note in report.notes)
    assert ("Other",) not in world.state.validity and "Other" not in world.state.hidden  # not hidden: put on the list again, it comes back
    world.sync()
    assert ("Other",) not in paths(world)


def test_a_folder_put_on_the_servers_list_comes_here(world: World) -> None:
    world.server.add("Other", mail(1))
    world.server.folders["Other"].subscribed = False
    world.sync()
    assert ("Other",) not in paths(world)
    world.server.folders["Other"].subscribed = True  # another program subscribed it
    world.sync()
    assert len(world.messages(("Other",))) == 1


def test_changes_here_reach_the_server_before_a_folder_off_the_list_goes(world: World) -> None:
    world.server.add("Other", mail(1))
    world.sync()
    message = world.store.list_messages(("Other",))[0]
    world.store.set_flags(message.ref, frozenset({MessageFlag.FLAGGED}))  # changed here offline
    world.server.folders["Other"].subscribed = False
    world.sync()
    assert ("Other",) not in paths(world)
    (on_server,) = world.server.folders["Other"].messages
    assert "\\Flagged" in on_server.flags


def test_a_folder_off_the_list_with_a_change_the_server_refused_stays_until_it_is_settled(world: World) -> None:
    from chrono_mail.sync.mailconflicts import MARKS_REFUSED, listed

    world.server.add("Other", mail(1))
    world.sync()
    message = world.store.list_messages(("Other",))[0]
    world.store.set_flags(message.ref, frozenset({MessageFlag.FLAGGED}))
    world.server.refuse_stores = True
    world.server.folders["Other"].subscribed = False
    world.sync()
    assert [conflict.reason for conflict in listed(world.store)] == [MARKS_REFUSED] and ("Other",) in paths(world)  # nothing lost (Rule 4.1)
    world.sync()
    assert ("Other",) in paths(world)


def test_mail_only_here_keeps_a_folder_off_the_list(world: World) -> None:
    world.server.add("Other", mail(1))
    world.sync()
    world.store.deliver(("Other",), mail(9), verify=True)  # put there offline
    world.server.refuse_appends = True
    world.server.folders["Other"].subscribed = False
    world.sync()
    assert ("Other",) in paths(world) and len(world.messages(("Other",))) == 2


def test_a_folder_hidden_here_stays_hidden_whatever_the_list_says(world: World) -> None:
    world.server.add("Other", mail(1))
    world.state.set_hidden("Other", True)
    world.sync()
    assert ("Other",) not in paths(world)
    world.server.folders["Other"].subscribed = False
    world.sync()
    world.server.folders["Other"].subscribed = True
    world.sync()
    assert ("Other",) not in paths(world)


def test_a_folder_ticked_here_off_the_list_stays_and_follows_the_list_once_it_is_on_it(world: World) -> None:
    world.server.add("Other", mail(1))
    world.server.folders["Other"].subscribed = False
    world.state.set_hidden("Other", False)  # the person ticked it here
    world.sync()
    world.sync()
    assert len(world.messages(("Other",))) == 1 and "Other" in world.state.forced  # the person's own choice stays
    world.server.folders["Other"].subscribed = True
    world.sync()
    assert "Other" not in world.state.forced  # on the list now: it follows the list
    assert "Other" not in ImapState(world.state.path, durable=False).forced  # also after a restart
    world.server.folders["Other"].subscribed = False
    world.sync()
    assert ("Other",) not in paths(world)


def test_a_standard_folder_off_the_list_stays_and_is_kept_in_step(world: World) -> None:
    world.server.add("Trash", mail(1))
    world.sync()
    world.server.folders["Trash"].subscribed = False
    world.server.add("Trash", mail(2))
    world.sync()
    assert len(world.messages(("Trash",))) == 2
    assert world.state.server_names[("Trash",)] == "Trash"


def test_a_folder_off_the_list_with_a_shown_folder_inside_it_stays(world: World) -> None:
    world.server.add("Projects", mail(1))
    world.server.add("Projects/Alpha", mail(2))
    world.sync()
    world.server.folders["Projects"].subscribed = False
    world.sync()
    assert {("Projects",), ("Projects", "Alpha")} <= paths(world)  # a folder cannot be shown without the one it is in
    world.server.folders["Projects/Alpha"].subscribed = False
    world.sync()
    assert not {("Projects",), ("Projects", "Alpha")} & paths(world)


def test_a_server_without_any_list_shows_every_folder(world: World) -> None:
    world.server.add("Other", mail(1))
    world.sync()
    for folder in world.server.folders.values():
        folder.subscribed = False
    world.sync()
    assert len(world.messages(("Other",))) == 1


def test_two_computers_follow_the_list_alike(world: World) -> None:
    second = Store.create(world.path / "second", "acc", generate_data_key(), durable=False)
    second.create_folder(INBOX, FolderRole.INBOX)
    other = World(second, Index(world.path / "second.sqlite"), ImapState(world.path / "second.log", durable=False), world.server, world.path)
    world.server.add("Other", mail(1))
    world.sync()
    other.sync()
    assert ("Other",) in paths(world) and ("Other",) in paths(other)
    world.server.folders["Other"].subscribed = False
    world.sync()
    other.sync()
    assert ("Other",) not in paths(world) and ("Other",) not in paths(other)


def test_a_folder_deleted_on_the_server_with_mail_only_here_is_a_sync_conflict(world: World) -> None:
    from chrono_mail.sync.mailconflicts import FOLDER_GONE_WITH_MAIL, held, listed

    world.server.add("Other", mail(1))
    world.sync()
    world.store.deliver(("Other",), mail(9), verify=True)  # put there offline: the server never had it
    del world.server.folders["Other"]
    report = world.sync()
    (conflict,) = listed(world.store)
    assert conflict.reason == FOLDER_GONE_WITH_MAIL and conflict.folder == ("Other",) and ("Other",) in paths(world)
    assert len(world.messages(("Other",))) == 2 and "Other" not in world.server.folders  # nothing lost, nothing made again on the server
    assert any("Sync conflicts" in note for note in report.notes)
    world.sync()
    assert len(listed(world.store)) == 1 and "Other" not in world.server.folders and held(world.store)[1] == {("Other",)}


def test_a_folder_deleted_on_the_server_with_marks_changed_here_is_a_sync_conflict(world: World) -> None:
    from chrono_mail.sync.mailconflicts import FOLDER_GONE_CHANGED, listed

    world.server.add("Other", mail(1))
    world.sync()
    message = world.store.list_messages(("Other",))[0]
    world.store.set_flags(message.ref, frozenset({MessageFlag.FLAGGED}))  # changed here, not sent yet
    del world.server.folders["Other"]
    world.sync()
    assert [c.reason for c in listed(world.store)] == [FOLDER_GONE_CHANGED]


def test_a_standard_folder_deleted_on_the_server_is_kept_and_made_there_again(world: World) -> None:
    world.server.add("Trash", mail(1))
    world.sync()
    world.store.deliver(("Trash",), mail(9), verify=True)  # only here
    del world.server.folders["Trash"]
    world.sync()
    assert ("Trash",) in paths(world) and [b"Message 9" in raw for raw, _ in world.messages(("Trash",)).values()] == [True]  # the server's mail went, what is only here stayed
    assert "Trash" in world.server.folders and len(world.server.folders["Trash"].messages) == 1  # made again, with what was only here


def test_snoozed_deleted_on_the_server_goes_here_too(world: World) -> None:
    """Snoozed is a folder on the server in an IMAP account: although it has a role of its own (CR-36), the server's delete is followed (D-193)."""
    world.server.add("Snoozed", mail(1))
    world.sync()
    assert [folder.role for folder in world.store.list_folders() if folder.path == ("Snoozed",)] == [FolderRole.SNOOZED]
    del world.server.folders["Snoozed"]
    world.sync()
    assert ("Snoozed",) not in paths(world) and "Snoozed" not in world.server.folders  # gone here, never made again there


def test_a_state_from_before_without_server_names_changes_nothing(world: World) -> None:
    world.server.add("Other", mail(1))
    world.sync()
    world.state.server_names.clear()  # as a state file written before 1.1.237
    original = world.server.folders.pop("Other")
    world.sync(state=world.state)
    assert ("Other",) in paths(world)
    world.server.folders["Other"] = original


def test_an_offline_move_never_removes_another_program_s_deleted_mail(world: World) -> None:
    world.server.__exit__(None, None, None)
    world.server = FakeImapServer(plain=False, uidplus=False, move=False)
    world.server.__enter__()
    world.server.add("INBOX", mail(1))
    world.sync()
    other = world.server.add("INBOX", mail(2))
    with world.server.lock:  # another program marked it deleted and has not expunged the folder yet
        next(m for m in world.server.folders["INBOX"].messages if m.uid == other).flags.add("\\Deleted")
    (message,) = [m for m in world.store.list_messages(INBOX)]
    world.store.move(message.ref, ("Trash",))  # moved here while offline: the sync moves it on the server
    world.sync()
    assert other in world.server.uids("INBOX") and len(world.server.uids("Trash")) == 1


def test_folder_changes_made_offline_survive_a_reload_and_a_compaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.sync import imap_state

    path = tmp_path / "state.log"
    state = ImapState(path, durable=False)
    for folder, name, validity in ((("A",), "A", 5), (("B",), "B", 6)):
        state.set_validity(folder, validity)
        state.set_server_name(folder, name)
    state.map(("B",), 3, "c" * 32, frozenset())
    state.rename_later(("A",), ("A2",))
    state.rename_later(("A2",), ("A3",))  # renamed twice offline: one rename on the server, from the server's name
    state.delete_later(("B",))
    for loaded in (ImapState(path, durable=False), None):
        if loaded is None:
            monkeypatch.setattr(imap_state, "COMPACT_MINIMUM_LINES", 0)
            monkeypatch.setattr(imap_state, "COMPACT_FACTOR", 0)
            reloaded = ImapState(path, durable=False)
            assert reloaded.compact_if_needed()
            loaded = ImapState(path, durable=False)
        rename, delete = loaded.pending[("A3",)], loaded.pending[("B",)]
        assert (rename.kind, rename.server_name, rename.old_path) == ("rename", "A", ("A",)) and ("A2",) not in loaded.pending
        assert (delete.kind, delete.server_name, delete.validity, delete.uids) == ("delete", "B", 6, frozenset({3}))
        assert ("A3",) in loaded.validity and ("B",) not in loaded.validity


def test_a_standard_folder_the_server_marks_is_planned_whether_or_not_it_is_subscribed() -> None:
    """The Windows computer's W7: the Dovecot test account's Junk (special-use \\Junk) was not subscribed and never mapped."""
    plans, _ = plan_folders([entry("INBOX"), entry("Sent", "\\sent"), entry("Junk", "\\junk"), entry("Old")], subscribed=frozenset({"INBOX", "Sent"}))
    assert {(plan.server_name, plan.role) for plan in plans} == {("INBOX", FolderRole.INBOX), ("Sent", FolderRole.SENT), ("Junk", FolderRole.JUNK)}


@pytest.mark.parametrize("marked", [True, False])
def test_an_unsubscribed_junk_folder_on_the_server_is_used_never_made_again(world: World, marked: bool) -> None:
    world.server.add("INBOX", mail(1))
    world.server.add("Junk", mail(2))
    world.server.folders["Junk"].subscribed = False
    if marked:
        world.server.folders["Junk"].attributes = ["\\Junk"]
    world.sync()
    assert world.state.server_names.get(("Junk",)) == "Junk" and not any(command.startswith("CREATE") for command in world.server.commands)
    assert len(world.messages(("Junk",))) == 1
