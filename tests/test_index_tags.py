import dataclasses
import sqlite3
from pathlib import Path

from chrono_mail.core import FolderRole
from chrono_mail.crypto import generate_data_key
from chrono_mail.index import Index, SearchFilter
from chrono_mail.index.index import split_tag_terms
from chrono_mail.index.schema import FOLDER_NUMBER_STATEMENTS, SEND_AT_INDEX, STATEMENTS, TAG_STATEMENTS
from chrono_mail.store import Store
from chrono_mail.sync.rebuild import rebuild_index, reconcile_index
from tests.test_index_index import INBOX, make_entry, subjects

# --- Version ------------------------------------------------------------
__version__ = "test_index_tags.py 1.9.276"

# --- Version history ----------------------------------------------------
# v1.9.276: The in-place upgrade from format 6 is gone: every older index is rebuilt (D-226)
# v1.1.250: the format 6 index is built without the folder numbers of format 10 and goes on to 10 (R-10)
# v1.1.206: tags are kept in the messages' properties files (D-176)
# v1.1.196: The format 6 index is built without the send times of format 9 and goes on to 9
# v0.5.166: format 6 is upgraded on through 7 to 8
# v0.5.134: Initial tests: tags in the index (kept, found, upgraded in place, rebuilt from the store)


def tagged_index(tmp_path: Path) -> Index:
    index = Index(tmp_path / "index.sqlite")
    index.add_many([
        dataclasses.replace(make_entry(1), tags=("Work",)),
        dataclasses.replace(make_entry(2), tags=("Work", "Family matters")),
        make_entry(3),
    ])
    return index


def test_tags_are_kept_and_given_back_with_each_message(tmp_path: Path) -> None:
    index = tagged_index(tmp_path)
    found = index.get(f"{2:032x}")
    assert found is not None and found.tags == ("Work", "Family matters")
    index.update_tags(f"{2:032x}", ("Home",))
    again = index.get(f"{2:032x}")
    assert again is not None and again.tags == ("Home",)
    assert index.tagged() == {f"{1:032x}", f"{2:032x}"}


def test_messages_are_found_by_tag_without_regard_to_case(tmp_path: Path) -> None:
    index = tagged_index(tmp_path)
    assert sorted(subjects(index.search("", INBOX, SearchFilter(tag="work")))) == ["Subject number 1", "Subject number 2"]
    assert subjects(index.search('tag:"family  MATTERS"', INBOX)) == ["Subject number 2"]
    assert subjects(index.search("tag:work -tag:family", INBOX)) == ["Subject number 2", "Subject number 1"]
    assert subjects(index.search('-tag:work number', INBOX)) == ["Subject number 3"]
    assert subjects(index.search("tag:nothing", INBOX)) == []


def test_tag_terms_are_taken_out_of_the_words(tmp_path: Path) -> None:
    rest, wanted, excluded = split_tag_terms('report tag:Work -tag:"a b" from:ann')
    assert (" ".join(rest.split()), wanted, excluded) == ("report from:ann", ("Work",), ("a b",))
    assert split_tag_terms("vintage:wine") == ("vintage:wine", (), ())  # only a whole word tag: counts


def test_a_removed_message_loses_its_tags_in_the_index(tmp_path: Path) -> None:
    index = tagged_index(tmp_path)
    index.remove(f"{1:032x}")
    index.remove_folder(INBOX)
    assert index.tagged() == set()


def test_a_rebuild_and_a_repair_take_the_tags_from_the_store(tmp_path: Path) -> None:
    store = Store.create(tmp_path / "store", "account1", generate_data_key(), durable=False)
    store.create_folder(INBOX, FolderRole.INBOX)
    first = store.deliver(INBOX, b"Subject: one\r\n\r\nA")
    second = store.deliver(INBOX, b"Subject: two\r\n\r\nB")
    store.properties.set_tags(first.store_id, ("Work",))
    index, _ = rebuild_index(store, tmp_path / "index.sqlite")
    assert subjects(index.search("tag:work", INBOX)) == ["one"]
    store.properties.set_tags(second.store_id, ("Work",))  # changed while the index was not told (a crash in between)
    store.properties.set_tags(first.store_id, ())
    report = reconcile_index(store, index)
    assert report.corrected == 2
    assert subjects(index.search("tag:work", INBOX)) == ["two"]
