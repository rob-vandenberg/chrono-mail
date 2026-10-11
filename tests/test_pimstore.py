from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chrono_mail.core import CollectionKind, CryptoError, StorageError
from chrono_mail.crypto import generate_data_key
from chrono_mail.pim.contact import Contact, Labeled, contact_to_text
from chrono_mail.pim.ical import Event, EventSeries, Reminder, Task, complete_task, events_to_text, tasks_to_text
from chrono_mail.pimstore import ItemQuery, PimStore
from chrono_mail.pimstore.extract import epoch

# --- Version ------------------------------------------------------------
__version__ = "test_pimstore.py 0.0.59"

# --- Version history ----------------------------------------------------
# v0.0.59: Initial tests of the store and the index for calendars, tasks and contacts


def ts(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def contact(name: str, email: str = "", categories: tuple[str, ...] = (), organization: str = "") -> str:
    family, _, given = name.partition(", ")
    return contact_to_text(Contact(uid=f"c-{name}", family_name=family, given_name=given, emails=(Labeled(email),) if email else (), categories=categories, organization=organization))


def event(uid: str, summary: str, start: datetime, hours: int = 1, rrule: str = "") -> str:
    from datetime import timedelta

    return events_to_text(EventSeries(Event(uid=uid, summary=summary, start=start, end=start + timedelta(hours=hours), rrule=rrule)))


@pytest.fixture(params=["encrypted", "plain"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> PimStore:
    key = generate_data_key() if request.param == "encrypted" else None
    return PimStore.create(tmp_path / "pim", key, durable=False)


def test_collections_are_made_listed_changed_and_taken_away(store: PimStore) -> None:
    work = store.create_collection(CollectionKind.EVENTS, "  Work   calendar ", "#AABBCC", server={"href": "/cal/work/", "ctag": "1"})
    people = store.create_collection(CollectionKind.CONTACTS, "People")
    assert work.name == "Work calendar" and work.colour == "#aabbcc" and work.server_value("href") == "/cal/work/" and work.server_value("missing", "x") == "x"
    assert [(c.name, c.kind) for c in store.collections()] == [("People", CollectionKind.CONTACTS), ("Work calendar", CollectionKind.EVENTS)]
    assert [c.name for c in store.collections(CollectionKind.EVENTS)] == ["Work calendar"]
    changed = store.update_collection(work.collection_id, name="Job", colour="#000000", read_only=True, server={"href": "/cal/job/"})
    assert (changed.name, changed.colour, changed.read_only, changed.server_value("href")) == ("Job", "#000000", True, "/cal/job/") and store.get_collection(work.collection_id) == changed
    for bad in ("", "x" * 201):
        with pytest.raises(StorageError):
            store.create_collection(CollectionKind.TASKS, bad)
    with pytest.raises(StorageError, match="#rrggbb"):
        store.create_collection(CollectionKind.TASKS, "Tasks", "red")
    with pytest.raises(StorageError):
        store.get_collection("0" * 32)
    with pytest.raises(StorageError, match="32"):
        store.get_collection("../x")
    item = store.put(people.collection_id, contact("Lee, Ann"))
    store.delete_collection(people.collection_id)
    assert [c.name for c in store.collections()] == ["Job"] and store.index.item_ids() == set()
    assert any((store.root / ".deleted").glob(f"{people.collection_id}.*")) and item


def test_items_are_stored_read_replaced_and_deleted_without_being_erased(store: PimStore) -> None:
    book = store.create_collection(CollectionKind.CONTACTS, "People")
    first = store.put(book.collection_id, contact("Reynolds, John", "john@example.com"))
    assert "Reynolds" in store.get(book.collection_id, first) and store.item_ids(book.collection_id) == [first]
    on_disk = (store.root / "collections" / book.collection_id / "items" / first).read_bytes()
    assert (b"Reynolds" not in on_disk) is store.encrypted  # an encrypted store never holds the text in the clear
    store.put(book.collection_id, contact("Reynolds, John", "new@example.com"), first)  # replaced under the same id
    assert "new@example.com" in store.get(book.collection_id, first) and store.item_ids(book.collection_id) == [first]
    assert list((store.root / "collections" / book.collection_id / "tmp").iterdir()) == []
    store.delete(book.collection_id, first)
    assert store.item_ids(book.collection_id) == [] and store.index.item_ids() == set()
    kept = list((store.root / ".deleted" / book.collection_id).iterdir())
    assert len(kept) == 1 and kept[0].name.startswith(first)  # the file is still there
    with pytest.raises(StorageError, match="no such item"):
        store.get(book.collection_id, first)
    with pytest.raises(StorageError, match="no such item"):
        store.delete(book.collection_id, first)
    assert store.purge_deleted(30) == 0 and kept[0].exists()
    assert store.purge_deleted(-1) == 1 and not kept[0].exists()  # "older than minus one day": everything


def test_the_store_needs_its_own_key(tmp_path: Path) -> None:
    key = generate_data_key()
    store = PimStore.create(tmp_path / "pim", key, durable=False)
    store.close()
    with pytest.raises(StorageError, match="needs its key"):
        PimStore.open(tmp_path / "pim", None)
    with pytest.raises(StorageError, match="not the key"):
        PimStore.open(tmp_path / "pim", generate_data_key())
    with pytest.raises(StorageError, match="already exists"):
        PimStore.create(tmp_path / "pim", key)
    reopened = PimStore.open(tmp_path / "pim", key, durable=False)
    assert reopened.encrypted
    plain = PimStore.create(tmp_path / "plain", None, durable=False)
    plain.close()
    with pytest.raises(StorageError, match="not encrypted"):
        PimStore.open(tmp_path / "plain", key)
    with pytest.raises(StorageError, match="missing or damaged"):
        PimStore.open(tmp_path / "nothing", None)
    assert PimStore.open_or_create(tmp_path / "third", None, durable=False).collections() == []


def test_contacts_can_be_searched_by_part_of_a_word_sorted_and_found_by_tag(store: PimStore) -> None:
    book = store.create_collection(CollectionKind.CONTACTS, "People")
    ids = {name: store.put(book.collection_id, contact(name, f"{name.split(',')[0].lower()}@example.com", categories=tags, organization=org))
           for name, tags, org in (("Reynolds, John", ("Important", "Key Customer"), "Reynolds Inc"), ("Hoffman, Rebecca", ("Business",), "Hoffman & Co"), ("Åberg, Zoë", (), "Café Nord"))}
    everyone = store.index.query(ItemQuery(kind=CollectionKind.CONTACTS))
    assert [item.title for item in everyone] == ["Zoë Åberg", "Rebecca Hoffman", "John Reynolds"]  # sorted by family name, without regard to case or accents
    assert [item.title for item in store.index.query(ItemQuery(text="rey"))] == ["John Reynolds"]  # a part of a word is enough
    assert [item.title for item in store.index.query(ItemQuery(text="cafe"))] == ["Zoë Åberg"] and [i.title for i in store.index.query(ItemQuery(text="zoe aberg"))] == ["Zoë Åberg"]  # accents do not matter
    assert [i.title for i in store.index.query(ItemQuery(text="hoffman@example.com"))] == ["Rebecca Hoffman"]
    assert [i.item_id for i in store.index.query(ItemQuery(tag="important"))] == [ids["Reynolds, John"]] and store.index.query(ItemQuery(tag="impor")) == []  # a whole tag
    assert store.index.tags() == {"important": 1, "key customer": 1, "business": 1} and store.index.count(ItemQuery(kind=CollectionKind.CONTACTS)) == 3
    assert [i.title for i in store.index.query(ItemQuery(limit=1, offset=1, kind=CollectionKind.CONTACTS))] == ["Rebecca Hoffman"]


def test_events_in_a_range_including_repeating_ones(store: PimStore) -> None:
    calendar = store.create_collection(CollectionKind.EVENTS, "Calendar")
    other = store.create_collection(CollectionKind.EVENTS, "Other")
    once = store.put(calendar.collection_id, event("once", "Lunch", ts(2026, 11, 3, 12)))
    before = store.put(calendar.collection_id, event("before", "Old", ts(2026, 9, 1, 12)))
    weekly = store.put(calendar.collection_id, event("weekly", "Standup", ts(2026, 9, 7, 9), rrule="FREQ=WEEKLY"))
    ended = store.put(calendar.collection_id, event("ended", "Course", ts(2026, 9, 7, 18), rrule="FREQ=WEEKLY;UNTIL=20261001T000000Z"))
    counted = store.put(other.collection_id, event("counted", "Three times", ts(2026, 10, 1, 8), rrule="FREQ=DAILY;COUNT=3"))
    start, end = int(ts(2026, 11, 1).timestamp()), int(ts(2026, 12, 1).timestamp())
    found = {item.item_id for item in store.index.query(ItemQuery(kind=CollectionKind.EVENTS, start=start, end=end))}
    assert found == {once, weekly, counted}  # the old one and the ended course are out; the endless and the counted rules are candidates (expansion decides)
    only_other = store.index.query(ItemQuery(kind=CollectionKind.EVENTS, collections=(other.collection_id,), start=start, end=end))
    assert [i.item_id for i in only_other] == [counted] and before and ended
    assert [i.title for i in store.index.query(ItemQuery(kind=CollectionKind.EVENTS, sort="start"))] == ["Old", "Standup", "Course", "Three times", "Lunch"]
    assert epoch(ts(2026, 11, 3, 12)) == (int(ts(2026, 11, 3, 12).timestamp()), False)


def test_tasks_open_done_due_and_ordered_by_due_date(store: PimStore) -> None:
    tasks = store.create_collection(CollectionKind.TASKS, "Tasks")
    soon = store.put(tasks.collection_id, tasks_to_text(Task(uid="t1", summary="Soon", due=ts(2026, 10, 15, 17), priority=1, categories=("Home",))))
    later = store.put(tasks.collection_id, tasks_to_text(Task(uid="t2", summary="Later", due=ts(2026, 12, 1, 17))))
    undated = store.put(tasks.collection_id, tasks_to_text(Task(uid="t3", summary="Whenever")))
    finished = store.put(tasks.collection_id, tasks_to_text(complete_task(Task(uid="t4", summary="Done", due=ts(2026, 10, 1, 17)), ts(2026, 10, 2))))
    assert [i.item_id for i in store.index.query(ItemQuery(kind=CollectionKind.TASKS, only_open=True, sort="due"))] == [soon, later, undated]  # no due date goes last
    assert [i.item_id for i in store.index.query(ItemQuery(kind=CollectionKind.TASKS, only_done=True))] == [finished]
    assert [i.item_id for i in store.index.query(ItemQuery(kind=CollectionKind.TASKS, only_open=True, due_before=int(ts(2026, 11, 1).timestamp())))] == [soon]
    item = store.index.get(soon)
    assert item is not None and (item.priority, item.tags, item.due) == (1, ("home",), int(ts(2026, 10, 15, 17).timestamp())) and not item.done
    assert [i.title for i in store.index.query(ItemQuery(kind=CollectionKind.TASKS, sort="due", descending=True))][0] == "Later"  # a task without a due date is last either way


def test_floating_and_all_day_times_are_found_with_a_margin(store: PimStore) -> None:
    calendar = store.create_collection(CollectionKind.EVENTS, "Calendar")
    from datetime import date

    all_day = events_to_text(EventSeries(Event(uid="d", summary="Holiday", start=date(2026, 12, 25), end=date(2026, 12, 26))))
    item = store.put(calendar.collection_id, all_day)
    indexed = store.index.get(item)
    assert indexed is not None and indexed.floating
    start, end = int(ts(2026, 12, 25, 22).timestamp()), int(ts(2026, 12, 26, 22).timestamp())
    assert [i.item_id for i in store.index.query(ItemQuery(kind=CollectionKind.EVENTS, start=start, end=end))] == [item]


def test_the_index_follows_the_files_and_is_made_again_when_lost_or_damaged(store: PimStore) -> None:
    book = store.create_collection(CollectionKind.CONTACTS, "People")
    first = store.put(book.collection_id, contact("Lee, Ann"))
    second = store.put(book.collection_id, contact("Kim, Bo"))
    store.index.remove(first)  # the index forgets one
    assert store.reconcile() == (1, 0) and store.index.item_ids() == {first, second}
    (store.root / "collections" / book.collection_id / "items" / second).unlink()  # a file is gone
    assert store.reconcile() == (0, 1) and store.index.item_ids() == {first}
    assert store.rebuild_index() == 1 and store.index.check()
    store.close()
    (store.root / "pim.sqlite").write_bytes(b"this is not a database at all" * 100)
    again = PimStore.open(store.root, store._key, durable=False)  # noqa: SLF001
    assert again.index.item_ids() == {first} and again.index.check()  # damaged: made again from the items
    again.close()
    for suffix in ("", "-wal", "-shm"):
        Path(str(store.root / "pim.sqlite") + suffix).unlink(missing_ok=True)
    assert PimStore.open(store.root, store._key, durable=False).index.item_ids() == {first}  # noqa: SLF001


def test_a_damaged_item_is_reported_and_leaves_the_others_alone(tmp_path: Path) -> None:
    store = PimStore.create(tmp_path / "pim", generate_data_key(), durable=False)
    book = store.create_collection(CollectionKind.CONTACTS, "People")
    good = store.put(book.collection_id, contact("Lee, Ann"))
    bad = store.put(book.collection_id, contact("Kim, Bo"))
    path = store.root / "collections" / book.collection_id / "items" / bad
    data = bytearray(path.read_bytes())
    data[-5] ^= 0xFF
    path.write_bytes(bytes(data))
    assert store.verify() == [(book.collection_id, bad)] and store.verify(book.collection_id) == [(book.collection_id, bad)]
    with pytest.raises(CryptoError):
        store.get(book.collection_id, bad)
    store.index.clear()
    assert store.reconcile() == (1, 0) and store.index.item_ids() == {good}  # the damaged one is left out of the index and stays on disk
    assert path.exists() and store.get(book.collection_id, good)


def test_items_move_between_collections_of_the_same_kind_only(store: PimStore) -> None:
    one = store.create_collection(CollectionKind.CONTACTS, "One")
    two = store.create_collection(CollectionKind.CONTACTS, "Two")
    events = store.create_collection(CollectionKind.EVENTS, "Events")
    item = store.put(one.collection_id, contact("Lee, Ann"))
    moved = store.move(one.collection_id, item, two.collection_id)
    assert store.item_ids(one.collection_id) == [] and store.item_ids(two.collection_id) == [moved] and (indexed := store.index.get(moved)) is not None and indexed.collection == two.collection_id
    assert store.index.get(item) is None and store.index.item_ids() == {moved}
    with pytest.raises(StorageError, match="same kind"):
        store.move(two.collection_id, moved, events.collection_id)
    assert store.item_ids(two.collection_id) == [moved]


class Crash(BaseException):
    pass


def crashing_at(point: str) -> Callable[[str], None]:
    def hook(name: str) -> None:
        if name == point:
            raise Crash

    return hook


@pytest.mark.parametrize("point", ["put:before-temporary", "put:after-temporary", "put:after-rename", "put:done"])
def test_a_crash_while_replacing_an_item_leaves_the_old_or_the_new_item_and_loses_nothing(tmp_path: Path, point: str) -> None:
    key = generate_data_key()
    store = PimStore.create(tmp_path / "pim", key, durable=False)
    book = store.create_collection(CollectionKind.CONTACTS, "People")
    item = store.put(book.collection_id, contact("Lee, Ann", "old@example.com"))
    store.close()
    crashing = PimStore.open(tmp_path / "pim", key, durable=False, checkpoint=crashing_at(point))
    with pytest.raises(Crash):
        crashing.put(book.collection_id, contact("Lee, Ann", "new@example.com"), item)
    crashing.close()
    after = PimStore.open(tmp_path / "pim", key, durable=False)
    text = after.get(book.collection_id, item)
    assert "old@example.com" in text or "new@example.com" in text  # never a mix, never nothing
    assert after.verify() == [] and after.index.item_ids() == {item}
    after.put(book.collection_id, contact("Lee, Ann", "final@example.com"), item)  # and the next write works, whatever was left in tmp
    assert "final@example.com" in after.get(book.collection_id, item)


@pytest.mark.parametrize("point", ["delete:after-rename", "move:after-copy"])
def test_a_crash_while_deleting_or_moving_loses_nothing_and_repeating_finishes_it(tmp_path: Path, point: str) -> None:
    key = generate_data_key()
    store = PimStore.create(tmp_path / "pim", key, durable=False)
    one = store.create_collection(CollectionKind.CONTACTS, "One")
    two = store.create_collection(CollectionKind.CONTACTS, "Two")
    item = store.put(one.collection_id, contact("Lee, Ann"))
    store.close()
    crashing = PimStore.open(tmp_path / "pim", key, durable=False, checkpoint=crashing_at(point))
    with pytest.raises(Crash):
        crashing.move(one.collection_id, item, two.collection_id) if point.startswith("move") else crashing.delete(one.collection_id, item)
    crashing.close()
    after = PimStore.open(tmp_path / "pim", key, durable=False)  # reconcile on opening repairs the index
    where = [(c.name, i) for c in after.collections() for i in after.item_ids(c.collection_id)]
    deleted_somewhere = any((after.root / ".deleted").rglob(f"{item}*"))
    assert where or deleted_somewhere  # the item is somewhere
    assert after.index.item_ids() == {i for _, i in where}
    if point.startswith("move"):
        assert [name for name, _ in where] == ["One", "Two"]  # in both: a copy is never lost
        after.move(one.collection_id, item, two.collection_id)  # again: finds the copy, adds no second one, takes the original away
        assert [name for c in after.collections() for name in [c.name] for _ in after.item_ids(c.collection_id)] == ["Two"]
