from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from chrono_mail.config import Pop3DeletePolicy, Pop3Options
from chrono_mail.core import ConnectionSecurity, Folder, FolderRole, MessageFlag, MessageRef, ServerSettings, StorageError
from chrono_mail.crypto import generate_data_key
from chrono_mail.index import Index
from chrono_mail.protocols.pop3 import Pop3Client, Pop3Limits
from chrono_mail.store import Store
from chrono_mail.sync import FetchReport, Pop3State, fetch_pop3
from chrono_mail.sync.announce import SILENT, Announcer
from tests.fakepop3 import FakePop3Server
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_sync_pop3.py 0.5.164"

# --- Version history ----------------------------------------------------
# v0.5.164: each new message is told in one step with its index entry (announcer)
# v0.0.10: Initial POP3 sync tests

INBOX = ("Inbox",)
DAY = 86_400
LIMIT = 1_000_000
CLIENT_LIMITS = Pop3Limits(read_timeout_seconds=3, connect_timeout_seconds=3)


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


class SimulatedCrash(BaseException):
    pass


def messages(count: int = 3) -> dict[str, bytes]:
    return {f"uid{number}": raw_message(subject=f"Message {number}", body=f"body {number}\r\n", message_id=f"<m{number}@x>") for number in range(1, count + 1)}


@dataclass
class World:
    store: Store
    index: Index
    state: Pop3State
    server: FakePop3Server
    clock: list[float]

    def fetch(
        self, options: Pop3Options = Pop3Options(), checkpoint: Callable[[str], None] = lambda name: None, index: Index | None = None, announcer: Announcer = SILENT,
    ) -> FetchReport:
        settings = ServerSettings("127.0.0.1", self.server.port, ConnectionSecurity.NONE, "user")
        with Pop3Client(settings, "secret", CLIENT_LIMITS) as client:
            return fetch_pop3(client, self.store, self.index if index is None else index, self.state, INBOX, options, LIMIT, lambda: self.clock[0], checkpoint, announcer=announcer)

    def stored_bodies(self) -> list[bytes]:
        return sorted(self.store.read(m.ref) for folder in self.store.list_folders() for m in self.store.list_messages(folder.path))


def make_world(tmp_path: Path, server_messages: dict[str, bytes] | None = None) -> World:
    store = Store.create(tmp_path / "store", "acc", generate_data_key(), durable=False)
    store.create_folder(INBOX, FolderRole.INBOX)
    server = FakePop3Server(messages() if server_messages is None else server_messages)
    server.__enter__()
    return World(store, Index(tmp_path / "index.sqlite"), Pop3State(tmp_path / "state.log", durable=False), server, [1_000_000.0])


@pytest.fixture
def world(tmp_path: Path) -> Generator[World]:
    created = make_world(tmp_path)
    yield created
    created.server.__exit__(None, None, None)


def test_new_messages_are_stored_indexed_and_remembered(world: World) -> None:
    report = world.fetch()
    assert (report.downloaded, report.already_known, report.failed, report.aborted) == (3, 0, (), None)
    assert world.stored_bodies() == sorted(messages().values())
    assert world.store.list_messages(INBOX)[0].relative_path.startswith("new/")
    assert world.index.count(INBOX) == 3 and [m.subject for m in world.index.search("body")] != []
    assert set(world.state.records) == {"uid1", "uid2", "uid3"}
    assert set(world.server.messages) == {"uid1", "uid2", "uid3"}  # the default keeps the server copy


def test_each_new_message_is_told_in_one_step_with_its_index_entry(world: World) -> None:
    recording = Recording(world.index)
    report = world.fetch(announcer=recording)
    assert [kind for kind, _, _, _ in recording.told] == ["added"] * 3 == ["added"] * report.downloaded
    assert all(inside and indexed for _, _, inside, indexed in recording.told)  # told inside changing(), the index already had it


def test_second_run_downloads_nothing(world: World) -> None:
    world.fetch()
    report = world.fetch()
    assert (report.downloaded, report.already_known) == (0, 3)
    assert len(world.stored_bodies()) == 3


def test_state_survives_a_restart(world: World, tmp_path: Path) -> None:
    world.fetch()
    world.state = Pop3State(tmp_path / "state.log", durable=False)
    assert world.fetch().downloaded == 0


def test_only_new_messages_are_downloaded(world: World) -> None:
    world.fetch()
    world.server.messages["uid9"] = raw_message(subject="Late arrival")
    report = world.fetch()
    assert (report.downloaded, report.already_known) == (1, 3)


def test_delete_after_download(world: World) -> None:
    report = world.fetch(Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD))
    assert (report.downloaded, report.deleted_on_server) == (3, 3)
    assert world.server.messages == {}
    assert world.stored_bodies() == sorted(messages().values())
    assert all(record.deleted_on_server for record in world.state.records.values())
    assert world.fetch(Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD)).deleted_on_server == 0


def test_message_the_server_will_not_send_stays_on_the_server_and_is_retried(world: World) -> None:
    world.server.refuse_retr = {"uid2"}
    report = world.fetch(Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD))
    assert report.downloaded == 2 and [item.unique_id for item in report.failed] == ["uid2"]
    assert set(world.server.messages) == {"uid2"}  # only the ones that were stored are gone
    world.server.refuse_retr = set()
    assert world.fetch(Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD)).downloaded == 1
    assert world.server.messages == {}


def test_oversized_message_is_reported_and_left_on_the_server(tmp_path: Path) -> None:
    created = make_world(tmp_path, {"small": raw_message(), "huge": b"Subject: big\r\n\r\n" + b"x" * 5000 + b"\r\n"})
    try:
        settings = ServerSettings("127.0.0.1", created.server.port, ConnectionSecurity.NONE, "user")
        with Pop3Client(settings, "secret", CLIENT_LIMITS) as client:
            report = fetch_pop3(client, created.store, created.index, created.state, INBOX, Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD), 1000)
        assert report.downloaded == 1 and [(item.unique_id) for item in report.failed] == ["huge"]
        assert "larger" in report.failed[0].reason and set(created.server.messages) == {"huge"}
    finally:
        created.server.__exit__(None, None, None)


def test_message_that_cannot_be_stored_safely_is_not_deleted_on_the_server(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    original = Store.deliver
    calls = {"count": 0}

    def flaky(self: Store, folder_path: tuple[str, ...], raw: bytes, flags: frozenset[MessageFlag] = frozenset(), verify: bool = True) -> MessageRef:
        calls["count"] += 1
        if calls["count"] == 2:
            raise StorageError("disk full")
        return original(self, folder_path, raw, flags, verify)

    monkeypatch.setattr(Store, "deliver", flaky)
    report = world.fetch(Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD))
    monkeypatch.undo()
    assert report.downloaded == 2 and len(report.failed) == 1 and "disk full" in report.failed[0].reason
    assert set(world.server.messages) == {"uid2"}
    assert "uid2" not in world.state.records


def test_index_failure_does_not_lose_or_block_mail(world: World, tmp_path: Path) -> None:
    broken = Index(tmp_path / "other.sqlite")
    broken.close()
    report = world.fetch(Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD), index=broken)
    assert report.downloaded == 3 and report.index_problems == 3 and report.deleted_on_server == 3
    assert len(world.stored_bodies()) == 3


def test_fetch_without_an_index(world: World) -> None:
    settings = ServerSettings("127.0.0.1", world.server.port, ConnectionSecurity.NONE, "user")
    with Pop3Client(settings, "secret", CLIENT_LIMITS) as client:
        assert fetch_pop3(client, world.store, None, world.state, INBOX, Pop3Options(), LIMIT).downloaded == 3


def test_server_listing_the_same_id_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    created = make_world(tmp_path, {"same": raw_message()})
    try:
        original = Pop3Client.uidl

        def doubled(self: Pop3Client) -> list[tuple[int, str]]:
            return [*original(self), (2, "same")]

        monkeypatch.setattr(Pop3Client, "uidl", doubled)
        assert created.fetch().downloaded == 1
    finally:
        created.server.__exit__(None, None, None)


# --- the connection fails part-way ---------------------------------------------------------------


def test_connection_lost_in_the_middle_keeps_what_was_done_and_resumes(tmp_path: Path) -> None:
    created = make_world(tmp_path, messages(5))
    try:
        created.server._fault = lambda verb, count: verb == "RETR" and count == 3  # noqa: SLF001
        report = created.fetch(Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD))
        assert report.aborted is not None and report.downloaded == 2 and report.deleted_on_server == 0
        assert len(created.server.messages) == 5  # nothing deleted: the connection never committed
        created.server._fault = None  # noqa: SLF001
        resumed = created.fetch(Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD))
        assert resumed.downloaded == 3 and resumed.already_known == 2 and resumed.deleted_on_server == 5
        assert created.server.messages == {} and created.stored_bodies() == sorted(messages(5).values())
    finally:
        created.server.__exit__(None, None, None)


# --- interruption: a crash at every step (AGENTS.md section 4.1.4) ---------------------------------


@pytest.mark.parametrize("occurrence", [1, 2, 3])
@pytest.mark.parametrize("point", ["fetch:after-retr", "fetch:after-deliver", "fetch:after-state"])
def test_crash_while_downloading_never_loses_a_message(tmp_path: Path, point: str, occurrence: int) -> None:
    created = make_world(tmp_path)
    try:
        seen = {"count": 0}

        def crash(name: str) -> None:
            if name == point:
                seen["count"] += 1
                if seen["count"] == occurrence:
                    raise SimulatedCrash(name)

        with pytest.raises(SimulatedCrash):
            created.fetch(Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD), crash)
        assert len(created.server.messages) == 3  # nothing was deleted on the server before the crash
        restarted = Pop3State(tmp_path / "state.log", durable=False)
        created.state = restarted
        created.fetch(Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD))
        bodies = created.stored_bodies()
        for original in messages().values():
            assert original in bodies  # every message is stored at least once
        extra = len(bodies) - 3
        assert extra == (1 if point == "fetch:after-deliver" else 0)  # a duplicate is possible only there
        assert created.server.messages == {}
    finally:
        created.server.__exit__(None, None, None)


@pytest.mark.parametrize("point", ["fetch:after-dele", "fetch:before-quit", "fetch:after-quit"])
def test_crash_while_deleting_on_the_server(tmp_path: Path, point: str) -> None:
    created = make_world(tmp_path)
    try:
        def crash(name: str) -> None:
            if name == point:
                raise SimulatedCrash(name)

        with pytest.raises(SimulatedCrash):
            created.fetch(Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD), crash)
        assert created.stored_bodies() == sorted(messages().values())  # all stored before any deletion
        if point == "fetch:after-quit":
            assert created.server.messages == {}
        else:
            assert len(created.server.messages) == 3  # marked but never committed
        created.state = Pop3State(tmp_path / "state.log", durable=False)
        report = created.fetch(Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD))
        assert report.downloaded == 0 and created.server.messages == {}
        assert len(created.stored_bodies()) == 3  # no duplicates
    finally:
        created.server.__exit__(None, None, None)


# --- delete policies ----------------------------------------------------------------------------


def test_keep_never_deletes(world: World) -> None:
    world.fetch()
    world.clock[0] += 1000 * DAY
    assert world.fetch(Pop3Options(Pop3DeletePolicy.KEEP)).deleted_on_server == 0
    assert len(world.server.messages) == 3


def test_delete_after_days(world: World) -> None:
    options = Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DAYS, 10)
    world.fetch(options)
    assert len(world.server.messages) == 3
    world.clock[0] += 9 * DAY
    assert world.fetch(options).deleted_on_server == 0
    world.clock[0] += 2 * DAY
    assert world.fetch(options).deleted_on_server == 3
    assert world.server.messages == {} and len(world.stored_bodies()) == 3


def test_delete_after_days_requires_the_local_copy_to_be_intact(world: World, tmp_path: Path) -> None:
    options = Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DAYS, 1)
    world.fetch(options)
    victim = world.store.list_messages(INBOX)[0]
    path = next(p for p in (tmp_path / "store" / "folders").rglob(victim.ref.store_id))
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(bytes(data))  # damaged on disk
    gone = world.store.list_messages(INBOX)[1]
    world.store.delete(gone.ref)  # and one removed
    world.clock[0] += 5 * DAY
    report = world.fetch(options)
    assert report.deleted_on_server == 1  # only the intact one
    assert len(world.server.messages) == 2


def test_delete_when_deleted_locally(world: World) -> None:
    options = Pop3Options(Pop3DeletePolicy.DELETE_WHEN_DELETED_LOCALLY)
    world.fetch(options)
    assert len(world.server.messages) == 3
    victim = world.store.list_messages(INBOX)[0]
    world.store.delete(victim.ref)
    report = world.fetch(options)
    assert report.deleted_on_server == 1 and len(world.server.messages) == 2
    assert world.state.records[next(uid for uid, r in world.state.records.items() if r.store_id == victim.ref.store_id)].deleted_on_server


def test_moving_a_message_is_not_deleting_it(world: World) -> None:
    options = Pop3Options(Pop3DeletePolicy.DELETE_WHEN_DELETED_LOCALLY)
    world.fetch(options)
    world.store.create_folder(("Trash",), FolderRole.TRASH)
    for message in world.store.list_messages(INBOX):
        world.store.move(message.ref, ("Trash",))
    assert world.fetch(options).deleted_on_server == 0 and len(world.server.messages) == 3


def test_a_wiped_store_does_not_delete_the_server_copies(tmp_path: Path) -> None:
    created = make_world(tmp_path, messages(12))
    try:
        options = Pop3Options(Pop3DeletePolicy.DELETE_WHEN_DELETED_LOCALLY)
        created.fetch(options)
        for message in created.store.list_messages(INBOX):
            created.store.delete(message.ref)  # everything "disappears" locally (disk problem, wrong mount)
        report = created.fetch(options)
        assert report.deleted_on_server == 0 and report.deletion_blocked is not None
        assert len(created.server.messages) == 12
    finally:
        created.server.__exit__(None, None, None)


def test_a_store_without_folders_blocks_deletion(world: World, tmp_path: Path) -> None:
    options = Pop3Options(Pop3DeletePolicy.DELETE_WHEN_DELETED_LOCALLY)
    world.fetch(options)
    empty = Store.create(tmp_path / "empty-store", "acc", generate_data_key(), durable=False)
    world.store = empty
    report = world.fetch(options)
    assert report.deleted_on_server == 0 and report.deletion_blocked is not None and len(world.server.messages) == 3


def test_damaged_folder_blocks_deletion(world: World, tmp_path: Path) -> None:
    options = Pop3Options(Pop3DeletePolicy.DELETE_WHEN_DELETED_LOCALLY)
    world.fetch(options)
    for description in (tmp_path / "store" / "folders").rglob("folder.json"):
        description.write_text("{broken")
    world.store.refresh()
    report = world.fetch(options)
    assert report.deleted_on_server == 0 and report.deletion_blocked is not None


# --- the state file ------------------------------------------------------------------------------


def test_state_ignores_a_line_cut_off_by_a_crash_and_keeps_appending(tmp_path: Path) -> None:
    path = tmp_path / "state.log"
    path.write_bytes(b"D\tuid1\t" + b"a" * 32 + b"\t100\nD\tuid2\t" + b"b" * 32 + b"\t101\nD\tuid3\t" + b"c" * 3)
    state = Pop3State(path, durable=False)
    assert set(state.records) == {"uid1", "uid2"} and state.ignored_lines == 1
    state.record_download("uid4", "d" * 32, 200)
    reloaded = Pop3State(path, durable=False)
    assert set(reloaded.records) == {"uid1", "uid2", "uid4"}


@pytest.mark.parametrize("junk", [b"garbage\n", b"D\n", b"D\tuid\tshort\t1\n", b"D\tuid\t" + b"a" * 32 + b"\tnot-a-number\n", b"X\tunknown\t5\n", b"\x00\xff\xfe\n", b"Z\ta\tb\tc\n"])
def test_state_tolerates_junk_lines(tmp_path: Path, junk: bytes) -> None:
    path = tmp_path / "state.log"
    path.write_bytes(junk + b"D\tgood\t" + b"a" * 32 + b"\t1\n")
    state = Pop3State(path, durable=False)
    assert set(state.records) == {"good"}


def test_state_records_server_deletion_and_replays(tmp_path: Path) -> None:
    state = Pop3State(tmp_path / "state.log", durable=False)
    state.record_download("uid1", "a" * 32, 5)
    state.record_server_deletion("uid1", 6)
    reloaded = Pop3State(tmp_path / "state.log", durable=False)
    assert reloaded.records["uid1"].deleted_on_server and reloaded.records["uid1"].downloaded_at == 5


def test_missing_state_file_means_nothing_downloaded_yet(tmp_path: Path) -> None:
    assert Pop3State(tmp_path / "none.log", durable=False).records == {}
