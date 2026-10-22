import json
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from chrono_mail.config import AccountSettings
from chrono_mail.core import ChronoMailError, MessageRef, StorageError
from chrono_mail.engine.recurring import OLD_RECURRING_FILE, Repeat, load_old_repeats, next_due, repeat_text
from chrono_mail.engine.session import Engine
from chrono_mail.index import Index
from chrono_mail.mime import REPEAT_HEADER, parse_message, with_extra_headers
from chrono_mail.store import Properties, Store

# --- Version ------------------------------------------------------------
__version__ = "old_files.py 1.1.206"

# --- Version history ----------------------------------------------------
# v1.1.206: Initial version: the side files of before 1.1.206 (snoozed.json, archived.log, recurring.json) move into the messages'
#           properties files and into the repeating messages themselves, once, when a mailbox is first opened (D-176)

OLD_SNOOZED_FILE: str = "snoozed.json"
OLD_ARCHIVED_FILE: str = "archived.log"
MAXIMUM_OLD_FILE_BYTES: int = 64 * 1024 * 1024

_SETTLED: set[str] = set()  # the mailboxes whose old files were looked at in this run of the program
_SETTLED_LOCK = threading.Lock()  # one mailbox at a time; a command that opens the mailbox meanwhile waits until it is done


def settle_old_files(engine: Engine, place: AccountSettings, store: Store, index: Index) -> None:
    """Before 1.1.206 three files were kept beside a mailbox, and under the account that made the change rather than where the
    mail is (so a repeating message from an account that keeps its mail in Local Folders was never sent). Each entry now goes
    to its message: snooze times and archive origins into its properties file, a repeating message's timetable into the
    message itself. An old file is removed only after all of its entries are written. Every folder looked at belongs to this
    mailbox now (its own, and those of the accounts that keep their mail in it; a change of storage keeps message ids), so an
    entry whose message is not here is about a message that is gone. What is already in a message or its properties file is
    newer and is kept. Looked at once per
    mailbox and run of the program; a failure leaves the old file for the next run and never stops the mailbox from opening."""
    key = str(engine.directories.account_directory(place.account.account_id))
    with _SETTLED_LOCK:
        if key in _SETTLED:
            return
        directories = [engine.directories.account_directory(place.account.account_id)]
        try:
            members = engine.storage_groups().get(place.account.account_id, [])
        except ChronoMailError:
            members: list[AccountSettings] = []
        directories += [engine.directories.account_directory(member.account.account_id) for member in members]
        done = True
        present: dict[str, tuple[tuple[str, ...], str]] | None = None
        for directory in dict.fromkeys(directories):
            for name, settle in ((OLD_SNOOZED_FILE, _settle_snoozed), (OLD_ARCHIVED_FILE, _settle_archived), (OLD_RECURRING_FILE, _settle_repeats)):
                path = directory / name
                if not path.exists():
                    continue
                try:
                    if present is None:
                        present = store.locate_all()  # only when an old file is there: the messages of this mailbox
                    settle(path, store, index, present, engine.clock())
                except (ChronoMailError, OSError):
                    done = False
        if done:
            _SETTLED.add(key)


def _read_json(path: Path) -> dict[str, object]:
    if path.stat().st_size > MAXIMUM_OLD_FILE_BYTES:
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except ValueError:
        return {}  # damaged: nothing can be taken from it
    return {str(key): value for key, value in data.items()} if isinstance(data, dict) else {}




def _unless_set(update: Callable[[Properties], Properties], is_set: Callable[[Properties], bool]) -> Callable[[Properties], Properties]:
    return lambda current: current if is_set(current) else update(current)


def _settle_snoozed(path: Path, store: Store, index: Index, present: dict[str, tuple[tuple[str, ...], str]], now: float) -> None:
    for store_id, entry in _read_json(path).items():
        if store_id not in present or not isinstance(entry, dict) or not isinstance(entry.get("until"), int) or not isinstance(entry.get("folder"), str):
            continue  # damaged: nothing to take over
        until, folder = entry["until"], entry["folder"]
        store.properties.change(
            store_id, _unless_set(lambda current: replace(current, snoozed_until=until, snoozed_from=folder), lambda current: current.snoozed_until is not None)
        )
    path.unlink()


def _settle_archived(path: Path, store: Store, index: Index, present: dict[str, tuple[tuple[str, ...], str]], now: float) -> None:
    if path.stat().st_size > MAXIMUM_OLD_FILE_BYTES:
        return
    origins: dict[str, str] = {}
    for line in path.read_text("utf-8", errors="replace").split("\n"):
        parts = line.split("\t")
        if len(parts) != 2 or not parts[1] or parts[0] not in present:
            continue  # a line cut off by a crash, or a message that is gone
        origins[parts[0]] = parts[1]  # the last line of a message is where it was last archived from
    for store_id, folder in origins.items():
        store.properties.change(
            store_id, _unless_set(lambda current: replace(current, archived_from=folder), lambda current: current.archived_from is not None)
        )
    path.unlink()


def _look_at(repeat: Repeat, now: float) -> int:
    """When the minute check looks at a repeating message: its next send; a series that is over at once (its pattern is removed)."""
    moment = next_due(repeat)
    return moment if moment is not None else int(now)


def _settle_repeats(path: Path, store: Store, index: Index, present: dict[str, tuple[tuple[str, ...], str]], now: float) -> None:
    for store_id, repeat in load_old_repeats(path).items():
        where = present.get(store_id)
        if where is None:
            continue  # its message is gone: nothing is left to send
        reference = MessageRef(store.account_id, where[0], store_id)
        raw = store.read(reference, hint=where[1])
        if any(name == REPEAT_HEADER for name, _ in parse_message(raw).chrono_headers):
            continue  # the message carries its timetable already: it is newer than the file
        moment = _look_at(repeat, now)
        store.replace(reference, with_extra_headers(raw, {"X-Chrono-Mail-Kind": "recurring", "X-Chrono-Mail-Send-At": str(moment), "X-Chrono-Mail-Repeat": repeat_text(repeat)}))
        try:
            index.set_send_times({store_id: moment})  # the minute check finds it in the index from now on
        except StorageError:
            pass  # the index is never the truth: a rebuild reads the time from the message
    path.unlink()
