import threading
from pathlib import Path

import pytest

from chrono_mail.core import FolderRole, StorageError
from chrono_mail.crypto import generate_data_key
from chrono_mail.store import MessageTags, Store, clean_tags

# --- Version ------------------------------------------------------------
__version__ = "test_store_tags.py 0.5.134"

# --- Version history ----------------------------------------------------
# v0.5.134: Initial tests: the tags of the messages of a mailbox

INBOX = ("Inbox",)
A, B = "a" * 32, "b" * 32


def make_store(tmp_path: Path) -> Store:
    store = Store.create(tmp_path / "store", "account1", generate_data_key(), durable=False)
    store.create_folder(INBOX, FolderRole.INBOX)
    store.create_folder(("Other",))
    return store


def test_tags_are_kept_as_written_once_each_without_regard_to_case(tmp_path: Path) -> None:
    tags = MessageTags(tmp_path, durable=False)
    assert tags.set(A, ("Work", " work ", "Family  matters", "")) == ("Work", "Family matters")
    assert tags.of(A) == ("Work", "Family matters") and tags.of(B) == ()
    assert MessageTags(tmp_path).all() == {A: ("Work", "Family matters")}  # on disk
    tags.set(A, ())
    assert tags.all() == {}


def test_bad_tags_and_ids_are_refused(tmp_path: Path) -> None:
    tags = MessageTags(tmp_path, durable=False)
    with pytest.raises(StorageError):
        tags.set("not-an-id", ("Work",))
    with pytest.raises(StorageError):
        tags.set(A, ("x" * 101,))
    with pytest.raises(StorageError):
        clean_tags([f"t{number}" for number in range(51)])


def test_rename_and_remove_a_tag_everywhere(tmp_path: Path) -> None:
    tags = MessageTags(tmp_path, durable=False)
    tags.set(A, ("Work", "Home"))
    tags.set(B, ("home",))
    assert sorted(tags.rename("HOME", "House")) == [A, B]
    assert tags.all() == {A: ("Work", "House"), B: ("House",)}
    assert sorted(tags.rename("house", None)) == [A, B]
    assert tags.all() == {A: ("Work",)}


def test_a_damaged_tags_file_is_kept_aside_and_reported(tmp_path: Path) -> None:
    (tmp_path / "tags.json").write_text("{ not json", "utf-8")
    tags = MessageTags(tmp_path, durable=False)
    with pytest.raises(StorageError, match="tags.json.bad"):
        tags.all()
    assert (tmp_path / "tags.json.bad").read_text("utf-8") == "{ not json"
    with pytest.raises(StorageError):
        tags.set(A, ("Work",))  # never written over silently
    assert (tmp_path / "tags.json").read_text("utf-8") == "{ not json"


def test_changes_made_at_the_same_time_are_all_kept(tmp_path: Path) -> None:
    ids = [f"{number:032x}" for number in range(40)]

    def tag(store_id: str) -> None:
        MessageTags(tmp_path, durable=False).set(store_id, ("Work",))  # each with its own object, as different commands have

    threads = [threading.Thread(target=tag, args=(store_id,)) for store_id in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert set(MessageTags(tmp_path).all()) == set(ids)


def test_a_moved_message_keeps_its_tags_a_copy_gets_them_a_deleted_one_loses_them(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = store.deliver(INBOX, b"Subject: a\r\n\r\nA")
    store.tags.set(first.store_id, ("Work",))
    moved = store.move(first, ("Other",))
    assert store.tags.of(moved.store_id) == ("Work",)
    copied = store.copy(moved, INBOX)
    assert store.tags.of(copied.store_id) == ("Work",)
    store.delete(moved)
    assert store.tags.all() == {copied.store_id: ("Work",)}
