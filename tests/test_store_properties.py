import json
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from chrono_mail.core import FolderRole, StorageError
from chrono_mail.crypto import generate_data_key
from chrono_mail.store import MessageProperties, Properties, Store, clean_tags

# --- Version ------------------------------------------------------------
__version__ = "test_store_properties.py 1.1.206"

# --- Version history ----------------------------------------------------
# v1.1.206: Initial tests: one properties file per message (it replaces test_store_tags.py and tags.json, D-176)
# v0.5.134: (as test_store_tags.py) the tags of the messages of a mailbox

INBOX = ("Inbox",)
A, B = "a" * 32, "b" * 32


def make_store(tmp_path: Path) -> Store:
    store = Store.create(tmp_path / "store", "account1", generate_data_key(), durable=False)
    store.create_folder(INBOX, FolderRole.INBOX)
    store.create_folder(("Other",))
    return store


def snoozed(until: int, origin: str) -> Callable[[Properties], Properties]:
    return lambda current: replace(current, snoozed_until=until, snoozed_from=origin)


def test_a_message_has_one_file_named_after_it_and_only_while_it_has_properties(tmp_path: Path) -> None:
    properties = MessageProperties(tmp_path, durable=False)
    assert properties.of(A) == Properties() and not (tmp_path / "properties").exists()
    properties.change(A, snoozed(5, "Inbox"))
    properties.set_tags(A, ("Work",))
    assert sorted(path.name for path in (tmp_path / "properties").iterdir()) == [f"{A}.json"]
    assert json.loads((tmp_path / "properties" / f"{A}.json").read_text("utf-8")) == {"tags": ["Work"], "snoozed_until": 5, "snoozed_from": "Inbox"}
    properties.change(A, lambda current: replace(current, snoozed_until=None, snoozed_from=None))
    properties.set_tags(A, ())
    assert list((tmp_path / "properties").iterdir()) == []  # nothing left to keep: no file


def test_tags_are_kept_as_written_once_each_without_regard_to_case(tmp_path: Path) -> None:
    properties = MessageProperties(tmp_path, durable=False)
    assert properties.set_tags(A, ("Work", " work ", "Family  matters", "")) == ("Work", "Family matters")
    assert properties.tags_of(A) == ("Work", "Family matters") and properties.tags_of(B) == ()
    assert MessageProperties(tmp_path).tags() == {A: ("Work", "Family matters")}  # on disk
    properties.set_tags(A, ())
    assert properties.tags() == {}


def test_bad_tags_and_ids_are_refused(tmp_path: Path) -> None:
    properties = MessageProperties(tmp_path, durable=False)
    with pytest.raises(StorageError):
        properties.set_tags("not-an-id", ("Work",))
    with pytest.raises(StorageError):
        properties.set_tags(A, ("x" * 101,))
    with pytest.raises(StorageError):
        clean_tags([f"t{number}" for number in range(51)])


def test_rename_and_remove_a_tag_everywhere(tmp_path: Path) -> None:
    properties = MessageProperties(tmp_path, durable=False)
    properties.set_tags(A, ("Work", "Home"))
    properties.set_tags(B, ("home",))
    properties.change(B, snoozed(9, "Inbox"))
    assert sorted(properties.rename_tag("HOME", "House")) == [A, B]
    assert properties.tags() == {A: ("Work", "House"), B: ("House",)}
    assert sorted(properties.rename_tag("house", None)) == [A, B]
    assert properties.tags() == {A: ("Work",)} and properties.of(B).snoozed_until == 9  # the other properties stay


def test_a_damaged_file_is_kept_aside_and_reported_for_that_message_alone(tmp_path: Path) -> None:
    properties = MessageProperties(tmp_path, durable=False)
    properties.set_tags(B, ("Home",))
    (tmp_path / "properties" / f"{A}.json").write_text("{ not json", "utf-8")
    with pytest.raises(StorageError, match=f"{A}.json.bad"):
        properties.of(A)
    assert (tmp_path / "properties" / f"{A}.json.bad").read_text("utf-8") == "{ not json"
    with pytest.raises(StorageError):
        properties.set_tags(A, ("Work",))  # never written over silently
    assert (tmp_path / "properties" / f"{A}.json").read_text("utf-8") == "{ not json"
    assert properties.tags() == {B: ("Home",)}  # the other messages never suffer from it


def test_names_this_version_does_not_know_are_kept(tmp_path: Path) -> None:
    (tmp_path / "properties").mkdir()
    (tmp_path / "properties" / f"{A}.json").write_text('{"tags": ["Work"], "colour_of_tomorrow": 7}', "utf-8")
    properties = MessageProperties(tmp_path, durable=False)
    properties.set_tags(A, ("Home",))
    assert json.loads((tmp_path / "properties" / f"{A}.json").read_text("utf-8")) == {"colour_of_tomorrow": 7, "tags": ["Home"]}


def test_changes_made_at_the_same_time_are_all_kept(tmp_path: Path) -> None:
    ids = [f"{number:032x}" for number in range(40)]

    def tag(store_id: str) -> None:
        MessageProperties(tmp_path, durable=False).set_tags(store_id, ("Work",))  # each with its own object, as different commands have
        MessageProperties(tmp_path, durable=False).change(store_id, snoozed(1, "Inbox"))

    threads = [threading.Thread(target=tag, args=(store_id,)) for store_id in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    found = MessageProperties(tmp_path).readable()
    assert set(found) == set(ids) and all(item == Properties(("Work",), 1, "Inbox") for item in found.values())


def test_a_move_never_touches_the_file_a_copy_gets_the_tags_a_deleted_message_loses_it(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = store.deliver(INBOX, b"Subject: a\r\n\r\nA")
    store.properties.set_tags(first.store_id, ("Work",))
    store.properties.change(first.store_id, lambda current: replace(current, archived_from="Inbox"))
    file = store.root / "properties" / f"{first.store_id}.json"
    written = file.stat().st_mtime_ns
    moved = store.move(first, ("Other",))
    assert file.stat().st_mtime_ns == written and store.properties.of(moved.store_id) == Properties(("Work",), archived_from="Inbox")
    copied = store.copy(moved, INBOX)
    assert store.properties.of(copied.store_id) == Properties(("Work",))  # neither snoozed nor archived by being copied
    store.delete(moved)
    assert not file.exists() and store.properties.tags() == {copied.store_id: ("Work",)}


def test_properties_come_along_to_another_mailbox_and_never_replace_what_is_there(tmp_path: Path) -> None:
    source, target = MessageProperties(tmp_path / "one", durable=False), MessageProperties(tmp_path / "two", durable=False)
    source.set_tags(A, ("Work",))
    source.change(A, snoozed(5, "Inbox"))
    source.set_tags(B, ("Old",))
    target.set_tags(B, ("New",))
    target.take_from(source, A)
    target.take_from(source, B)
    target.take_from(source, "c" * 32)  # nothing to bring
    assert target.of(A) == Properties(("Work",), 5, "Inbox") and target.tags_of(B) == ("New",) and target.stored_ids() == {A, B}


def test_the_tags_file_of_before_is_taken_over_once_and_newer_tags_win(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.properties.set_tags(B, ("Newer",))
    (store.root / "tags.json").write_text(json.dumps({"format": 1, "messages": {A: ["Work", "work"], B: ["Older"], "bad": ["x"]}}), "utf-8")
    again = Store.open(store.root, store._key, durable=False)  # noqa: SLF001
    assert again.properties.tags() == {A: ("Work",), B: ("Newer",)} and not (store.root / "tags.json").exists()


def test_a_damaged_tags_file_of_before_is_kept_as_bad_never_removed(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    (store.root / "tags.json").write_text("{ not json", "utf-8")
    Store.open(store.root, store._key, durable=False)  # noqa: SLF001
    assert (store.root / "tags.json.bad").read_text("utf-8") == "{ not json" and not (store.root / "tags.json").exists()


def test_an_interrupted_take_over_of_the_tags_file_loses_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = make_store(tmp_path)
    ids = [f"{number:032x}" for number in range(5)]
    (store.root / "tags.json").write_text(json.dumps({"format": 1, "messages": {store_id: [f"T{index}"] for index, store_id in enumerate(ids)}}), "utf-8")
    real = MessageProperties.change
    calls = {"count": 0}

    def failing(self: MessageProperties, store_id: str, update: Callable[[Properties], Properties]) -> Properties:
        calls["count"] += 1
        if calls["count"] == 3:
            raise StorageError("the disk is full")
        return real(self, store_id, update)

    monkeypatch.setattr(MessageProperties, "change", failing)
    Store.open(store.root, store._key, durable=False)  # noqa: SLF001
    assert (store.root / "tags.json").exists()  # kept: not all of it was written
    monkeypatch.setattr(MessageProperties, "change", real)
    again = Store.open(store.root, store._key, durable=False)  # noqa: SLF001
    assert again.properties.tags() == {store_id: (f"T{index}",) for index, store_id in enumerate(ids)} and not (store.root / "tags.json").exists()
