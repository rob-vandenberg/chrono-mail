import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, MessageFlag
from chrono_mail.engine import build_registry
from chrono_mail.engine.local import ensure_local_folders
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.store import store as store_module
from tests.enginefactory import make_engine
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_engine_places.py 2.5.336"

# --- Version history ----------------------------------------------------
# v2.5.336: Initial: a move, copy or delivery costs the same into a full folder as into an empty one (W13, D-248)

REGISTRY = build_registry()
FULL: int = 300  # messages already in Trash
SEEN: frozenset[MessageFlag] = frozenset({MessageFlag.SEEN})  # read mail is kept in cur/, where only a look through the folder finds it


def folders_looked_through(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every folder the store lists to find a message (it reads every name in it; how soon it stops depends on the file system's order)."""
    listed: list[str] = []
    real: Callable[[str], Iterator[os.DirEntry[str]]] = os.scandir

    def counting(path: str) -> Iterator[os.DirEntry[str]]:
        listed.append(path)
        return real(path)

    monkeypatch.setattr(store_module.os, "scandir", counting)
    return listed


def test_a_move_copy_or_delivery_never_looks_through_the_full_target_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path)
    REGISTRY.run(engine, "key.ensure", {})
    ensure_local_folders(engine)
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        inbox, trash = mailbox.folder("Inbox"), mailbox.folder("Trash")
        for number in range(FULL):
            mailbox.deliver(trash, raw_message(subject=f"Old {number}", message_id=f"<old{number}@x>"), SEEN)
        first = mailbox.deliver(inbox, raw_message(subject="First", message_id="<first@x>"), SEEN)
        second = mailbox.deliver(inbox, raw_message(subject="Second", message_id="<second@x>"), SEEN)
        listed = folders_looked_through(monkeypatch)
        moved = mailbox.move(first, trash)  # Delete: before, every name in Trash was read again after the move (W13)
        copied = mailbox.copy(second, trash)
        delivered = mailbox.deliver(trash, raw_message(subject="Third", message_id="<third@x>"), SEEN)
        assert listed == [], f"looked through {listed}"  # the cost no longer grows with the folder (Trash of 6,000: 118 per s, W13)
        for reference in (moved, copied, delivered):  # and the index knows exactly where each one is
            row = mailbox.index.get(reference.store_id)
            assert row is not None and row.folder == trash and row.path == mailbox.store.locate(reference)
