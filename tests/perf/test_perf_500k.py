import json
import os
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chrono_mail.core import FolderRole, MessageFlag, MessageRef
from chrono_mail.crypto import DataKey, generate_data_key
from chrono_mail.index import Index, SearchFilter, SortField
from chrono_mail.store import Store
from chrono_mail.sync import rebuild_index, reconcile_index
from tests.perf.mailbox_generator import generate_message

# --- Version ------------------------------------------------------------
__version__ = "test_perf_500k.py 0.0.8"

# --- Version history ----------------------------------------------------
# v0.0.8: Initial 500,000 message performance test

pytestmark = pytest.mark.perf

MESSAGES: int = int(os.environ.get("CHRONO_PERF_MESSAGES", "500000"))
DIRECTORY: Path = Path(os.environ.get("CHRONO_PERF_DIR", str(Path.home() / "chrono-mail-perf")))
INBOX = ("Inbox",)
SENT = ("Sent",)
FOLDERS: dict[int, tuple[str, ...]] = {
    0: INBOX, 1: INBOX, 2: INBOX, 3: INBOX, 4: INBOX, 5: INBOX, 6: SENT, 7: SENT, 8: ("Archive", "Old"), 9: ("Archive", "Older"),
}
RESULTS: dict[str, float] = {}

# Responsiveness limits in seconds for the interactive operations: far above what was measured on the
# development machine (see DEVLOG.md), so a failure means a real regression, not a slow moment.
LIMITS_SECONDS: dict[str, float] = {
    "count messages in Inbox": 0.2,
    "count unread in Inbox": 1.5,
    "first page (100) by date, newest first": 0.05,
    "page at offset 150,000 by date": 0.25,
    "last page by date (oldest first, offset 0)": 0.05,
    "first page by subject": 0.05,
    "first page by sender": 0.05,
    "first page by size": 0.05,
    "first page, unread only": 0.05,
    "thread members": 0.05,
    "get one message by id": 0.01,
    "search a rare word (about 500 hits)": 0.05,
    "search a common word, first 100": 1.0,
    "search two words": 1.0,
    "search a prefix": 1.0,
    "search a phrase": 0.5,
    "search from: a sender": 1.0,
    "search with folder and date filter": 1.0,
    "filter only: has attachments, first 100": 0.5,
    "index: update flags of 1,000 messages (one at a time)": 1.0,
    "store: read one message WITH path hint": 0.05,
    "store: read one message WITHOUT hint (search the folder)": 0.5,
    "store: set flags WITH hint (a rename)": 0.05,
}


def measure(label: str, action: Callable[[], object], repeat: int = 1) -> float:
    """Best time in seconds of a few runs; recorded and printed."""
    best = float("inf")
    for _ in range(repeat):
        started = time.perf_counter()
        action()
        best = min(best, time.perf_counter() - started)
    RESULTS[label] = best
    print(f"  {label:<58} {best * 1000:>12.2f} ms")
    return best


def build_store(key_holder: dict[str, DataKey]) -> Store:
    root = DIRECTORY / "store"
    marker = DIRECTORY / "generated.json"
    key = generate_data_key()
    shutil.rmtree(DIRECTORY, ignore_errors=True)
    DIRECTORY.mkdir(parents=True)
    store = Store.create(root, "perf", key, durable=False)
    for path in set(FOLDERS.values()):
        store.create_folder(path, FolderRole.INBOX if path == INBOX else FolderRole.OTHER)
    started = time.perf_counter()
    for number in range(MESSAGES):
        flags = frozenset({MessageFlag.SEEN}) if number % 3 else frozenset()
        store.deliver(FOLDERS[number % 10], generate_message(number), flags=flags, verify=False)
        if number and number % 50_000 == 0:
            print(f"  generated {number} messages in {time.perf_counter() - started:.0f} s")
    RESULTS["generate and store all messages (s)"] = time.perf_counter() - started
    key_holder["key"] = key
    marker.write_text(json.dumps({"messages": MESSAGES}))
    return store


def test_mailbox_of_500000_messages() -> None:
    print(f"\n=== {MESSAGES:,} messages in {DIRECTORY} ===")
    holder: dict[str, DataKey] = {}
    store = build_store(holder)
    size_bytes = sum(path.stat().st_size for path in (DIRECTORY / "store").rglob("*") if path.is_file())
    RESULTS["store size on disk (GB)"] = size_bytes / 1e9
    print(f"  store size on disk: {size_bytes / 1e9:.2f} GB")

    # --- full rebuild of the index from the store ---------------------------------------------
    index_path = DIRECTORY / "index.sqlite"
    started = time.perf_counter()
    index, report = rebuild_index(store, index_path)
    RESULTS["full index rebuild (s)"] = time.perf_counter() - started
    print(f"  full index rebuild: {RESULTS['full index rebuild (s)']:.0f} s ({report.indexed:,} indexed, {report.unreadable} unreadable)")
    RESULTS["index size on disk (GB)"] = sum(p.stat().st_size for p in DIRECTORY.glob("index.sqlite*")) / 1e9
    print(f"  index size on disk: {RESULTS['index size on disk (GB)']:.2f} GB")
    assert report.indexed == MESSAGES and report.unreadable == 0

    # --- what the user sees: lists -----------------------------------------------------------
    print("  --- folder lists (best of 3)")
    measure("count messages in Inbox", lambda: index.count(INBOX), 3)
    measure("count unread in Inbox", lambda: index.count(INBOX, unread_only=True), 3)
    measure("first page (100) by date, newest first", lambda: index.page(INBOX, SortField.DATE, True, 0, 100), 3)
    measure("page at offset 150,000 by date", lambda: index.page(INBOX, SortField.DATE, True, 150_000, 100), 3)
    measure("last page by date (oldest first, offset 0)", lambda: index.page(INBOX, SortField.DATE, False, 0, 100), 3)
    measure("first page by subject", lambda: index.page(INBOX, SortField.SUBJECT, False, 0, 100), 3)
    measure("first page by sender", lambda: index.page(INBOX, SortField.SENDER, False, 0, 100), 3)
    measure("first page by size", lambda: index.page(INBOX, SortField.SIZE, True, 0, 100), 3)
    measure("first page, unread only", lambda: index.page(INBOX, SortField.DATE, True, 0, 100, True), 3)
    measure("thread members", lambda: index.thread_members("<m100000@perf.example.com>"), 3)
    sample_id = index.page(INBOX, limit=1)[0].store_id
    measure("get one message by id", lambda: index.get(sample_id), 3)

    # --- search -------------------------------------------------------------------------------
    print("  --- search (best of 3)")
    measure("search a rare word (about 500 hits)", lambda: index.search("rarityword3", limit=100), 3)
    measure("search a common word, first 100", lambda: index.search("kalo", limit=100), 3)
    measure("search two words", lambda: index.search("kalo mira", limit=100), 3)
    measure("search a prefix", lambda: index.search("kal", limit=100), 3)
    measure("search a phrase", lambda: index.search('"kalo mira"', limit=100), 3)
    measure("search from: a sender", lambda: index.search("from:Ka", limit=100), 3)
    measure("search with folder and date filter", lambda: index.search("kalo", folder=INBOX, filters=SearchFilter(date_from=datetime(2024, 1, 1, tzinfo=UTC)), limit=100), 3)
    measure("filter only: has attachments, first 100", lambda: index.search("", filters=SearchFilter(has_attachments=True), limit=100), 3)

    # --- changes -------------------------------------------------------------------------------
    print("  --- single changes")
    ids = [m.store_id for m in index.page(INBOX, limit=1000)]
    measure("index: update flags of 1,000 messages (one at a time)", lambda: [index.update_flags(i, frozenset({MessageFlag.SEEN}), f"cur/{i}!2,S") for i in ids])
    newcomer = generate_message(MESSAGES + 1)
    measure("store: deliver one message, durable, verified", lambda: Store.open(DIRECTORY / "store", holder["key"], durable=True).deliver(INBOX, newcomer))
    listed = store.list_messages(INBOX)
    sample = listed[len(listed) // 2]
    measure("store: list all 300,000 messages of the Inbox", lambda: store.list_messages(INBOX))
    measure("store: read one message WITH path hint", lambda: store.read(sample.ref, hint=sample.relative_path), 3)
    measure("store: read one message WITHOUT hint (search the folder)", lambda: store.read(sample.ref), 3)
    measure("store: set flags WITH hint (a rename)", lambda: store.set_flags(sample.ref, frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED}), hint=sample.relative_path))
    measure("store: ids of all messages in all folders", lambda: store.all_store_ids())
    measure("reconcile index with store (nothing to change)", lambda: reconcile_index(store, index))
    index.close()

    (DIRECTORY / "results.json").write_text(json.dumps(RESULTS, indent=2))
    too_slow = {label: RESULTS[label] for label, limit in LIMITS_SECONDS.items() if RESULTS[label] > limit}
    assert not too_slow, f"slower than the responsiveness limit: {too_slow}"
    print(f"\n  results written to {DIRECTORY / 'results.json'}")
