import random
import time

import pytest

from chrono_mail.ui import messageorder
from chrono_mail.ui.messageorder import MessageOrder, message_key, split_key

# --- Version ------------------------------------------------------------
__version__ = "test_ui_messageorder.py 1.6.260"

# --- Version history ----------------------------------------------------
# v1.6.260: Initial: the order of every message of a view, checked against a plain list through many changes, and its speed with half a million (R-15)


def test_places_keys_and_folders_follow_every_change_as_a_plain_list_does(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(messageorder, "BLOCK", 4)  # small blocks: splits and emptied blocks happen often
    chance = random.Random(15)
    keys = [f"m{number}" for number in range(40)]
    folders = ["Inbox" if number % 3 else "Archive" for number in range(40)]
    order = MessageOrder(keys, folders)
    plain = list(zip(keys, folders, strict=True))
    made = 40
    for _ in range(600):
        if plain and chance.random() < 0.45:
            key, _ = plain.pop(chance.randrange(len(plain)))
            position = order.remove(key)
            assert position >= 0
        else:
            place = chance.randrange(len(plain) + 1)
            key, folder = f"m{made}", chance.choice(("Inbox", "Archive", "Work"))
            made += 1
            plain.insert(place, (key, folder))
            order.insert(place, key, folder)
        assert len(order) == len(plain) and list(order) == [key for key, _ in plain]
        probe = chance.randrange(len(plain)) if plain else 0
        if plain:
            assert order.key_at(probe) == plain[probe][0] and order.folder_at(probe) == plain[probe][1] and order.position(plain[probe][0]) == probe
            assert order.keys(probe, probe + 7) == [key for key, _ in plain[probe:probe + 7]]
    assert order.keys_in("Work") == [key for key, folder in plain if folder == "Work"]
    assert order.position("nothing") == -1 and order.remove("nothing") == -1 and order.key_at(len(plain)) is None


def test_a_folder_renamed_and_many_messages_taken_out_at_once() -> None:
    order = MessageOrder(["a", "b", "c", "d"], ["Projects", "Projects/2026", "Inbox", "Projects"])
    order.rename_folder("Projects", "Work")
    assert [order.folder_of(key) for key in "abcd"] == ["Work", "Work/2026", "Inbox", "Work"]
    smaller = order.without(["a", "c"])
    assert list(smaller) == ["b", "d"] and smaller.position("d") == 1 and smaller.folder_of("d") == "Work"


def test_a_favorites_view_knows_a_message_by_its_mailbox_and_its_id() -> None:
    first, second = message_key("box1", "m1"), message_key("box2", "m1")  # the same id in two mailboxes (a storage move copies with the same ids)
    order = MessageOrder([first, second], ["Inbox", "Inbox"])
    assert order.position(second) == 1 and split_key(second) == ("box2", "m1") and split_key("m1") == ("", "m1")
    assert order.keys_in("Inbox", "box2") == [second]


def test_half_a_million_messages_are_found_put_in_and_taken_out_quickly() -> None:
    keys = [f"{number:032x}" for number in range(500_000)]
    started = time.perf_counter()
    order = MessageOrder(keys, ["Inbox"] * len(keys))
    built = time.perf_counter() - started
    started = time.perf_counter()
    for number in range(1_000):  # a delete of 1,000 selected messages: one fact each
        order.remove(keys[number * 400])
    taken = time.perf_counter() - started
    started = time.perf_counter()
    for number in range(1_000):
        assert order.position(keys[number * 400 + 1]) >= 0
    found = time.perf_counter() - started
    assert len(order) == 499_000 and built < 1.0 and taken < 1.0 and found < 0.5, (built, taken, found)
