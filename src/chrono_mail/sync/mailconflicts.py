"""The sync conflicts of one IMAP mailbox (D-195): an offline change the server refused for good, kept beside the mail (encrypted, on this computer
only) until the person settles it. While a conflict is open its message or folder is not sent again, so a refusal is told once, not at every
check. Marks never clash (a mark is yes or no: both sides changing it from the same agreed value made the same change); only refusals are kept."""

import json
import os
import time
from dataclasses import dataclass, replace

from chrono_mail.core import StorageError
from chrono_mail.store import Store
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "mailconflicts.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash
# v1.1.240: A folder renamed or deleted here offline whose rename or delete the server refused, or that the server changed meanwhile (R-31)
# v1.1.237: A folder the server confirmed deleted while it had changes here not yet sent (FOLDER_GONE_CHANGED), or mail that exists only here
#           (FOLDER_GONE_WITH_MAIL); held_under: a held folder holds the folders inside it too (R-30)
# v1.1.227: Initial: refused marks, moves, folders and messages of an IMAP mailbox kept as sync conflicts (R-24, D-195)

CONFLICTS_FILE: str = "sync-conflicts"
MAXIMUM_CONFLICTS: int = 10_000

MARKS_REFUSED: str = "marks_refused"  # the marks changed here (offline); the server will not take them: settled with the server's marks
MOVE_REFUSED: str = "move_refused"  # moved here (offline) to another folder; the server will not move it: settled by moving it back here
MESSAGE_REFUSED: str = "message_refused"  # a message made here (offline); the server will not store it: kept here only
FOLDER_REFUSED: str = "folder_refused"  # a folder made here (offline); the server will not make it: kept here only
FOLDER_GONE_CHANGED: str = "folder_deleted_on_server"  # deleted on the server; here marks were changed (offline): made again, or deleted here
FOLDER_GONE_WITH_MAIL: str = "folder_deleted_with_mail_here"  # deleted on the server; here it holds mail the server never had: made again, or kept here
FOLDER_GONE: frozenset[str] = frozenset({FOLDER_GONE_CHANGED, FOLDER_GONE_WITH_MAIL})
FOLDER_RENAME_REFUSED: str = "folder_rename_refused"  # renamed here (offline); the server will not rename it: renamed back here, or tried again
FOLDER_DELETE_REFUSED: str = "folder_delete_refused"  # deleted here (offline); the server will not delete it: brought back here, or tried again
FOLDER_DELETED_HERE_CHANGED: str = "folder_deleted_here_changed"  # deleted here (offline); new mail on the server meanwhile: deleted anyway, or brought back
FOLDER_PENDING: frozenset[str] = frozenset({FOLDER_RENAME_REFUSED, FOLDER_DELETE_REFUSED, FOLDER_DELETED_HERE_CHANGED})
KEEP_ONLY: frozenset[str] = frozenset({MESSAGE_REFUSED, FOLDER_REFUSED})  # settling with the server would delete mail that exists only here: never


@dataclass(frozen=True, slots=True)
class MailConflict:
    conflict_id: str
    reason: str
    folder: tuple[str, ...]  # where it is here
    store_id: str  # the message ("" for a folder)
    name: str  # the subject, or the folder's name
    detail: str  # the server's own words
    server_folder: tuple[str, ...]  # a move: where the server keeps it
    local_marks: str  # marks: as they are here, in words
    server_marks: str  # marks: as the server has them, in words
    when: float
    server_flags: tuple[str, ...] = ()  # marks: the server's own names for them (to take them here)
    kept: bool = False  # the person chose "Keep here only": no longer listed, still never sent


def load(store: Store) -> list[MailConflict]:
    text = store.read_private(CONFLICTS_FILE)
    if text is None:
        return []
    try:
        return [
            MailConflict(
                str(entry["id"]), str(entry["reason"]), tuple(str(part) for part in entry["folder"]), str(entry["store_id"]), str(entry["name"]),
                str(entry["detail"]), tuple(str(part) for part in entry["server_folder"]), str(entry["local_marks"]), str(entry["server_marks"]),
                float(entry["when"]), tuple(str(flag) for flag in entry.get("server_flags", [])), bool(entry.get("kept", False)),
            )
            for entry in load_json(text)["conflicts"]
        ]
    except (ValueError, KeyError, TypeError) as error:
        raise StorageError(f"The list of sync conflicts is damaged: {error}") from None


def save(store: Store, conflicts: list[MailConflict]) -> None:
    if not conflicts:
        store.drop_private(CONFLICTS_FILE)
        return
    data = {
        "conflicts": [
            {
                "id": c.conflict_id, "reason": c.reason, "folder": list(c.folder), "store_id": c.store_id, "name": c.name, "detail": c.detail,
                "server_folder": list(c.server_folder), "local_marks": c.local_marks, "server_marks": c.server_marks, "when": c.when,
                "server_flags": list(c.server_flags), "kept": c.kept,
            }
            for c in conflicts[-MAXIMUM_CONFLICTS:]
        ]
    }
    store.write_private(CONFLICTS_FILE, json.dumps(data))


def record(
    store: Store, reason: str, folder: tuple[str, ...], store_id: str, name: str, detail: str, server_folder: tuple[str, ...] = (), local_marks: str = "",
    server_marks: str = "", server_flags: tuple[str, ...] = (),
) -> bool:
    """Keep a conflict; one already open for the same message (or folder) is brought up to date, never doubled. Returns whether it is new."""
    conflicts = load(store)
    for number, known in enumerate(conflicts):
        if known.store_id == store_id and (store_id or known.folder == folder):
            conflicts[number] = MailConflict(known.conflict_id, reason, folder, store_id, name, detail, server_folder, local_marks, server_marks, known.when,
                                             server_flags, known.kept)
            save(store, conflicts)
            return False
    conflicts.append(MailConflict(os.urandom(8).hex(), reason, folder, store_id, name, detail, server_folder, local_marks, server_marks, time.time(), server_flags))
    save(store, conflicts)
    return True


def forget(store: Store, conflict_id: str) -> None:
    save(store, [c for c in load(store) if c.conflict_id != conflict_id])


def keep_here(store: Store, conflict_id: str) -> None:
    """"Keep here only": the message or folder stays on this computer and is never sent; the conflict is no longer listed."""
    save(store, [replace(c, kept=True) if c.conflict_id == conflict_id else c for c in load(store)])


def listed(store: Store) -> list[MailConflict]:
    """The conflicts the person has not settled (kept ones are held, not listed)."""
    return [c for c in load(store) if not c.kept]


def held(store: Store) -> tuple[set[str], set[tuple[str, ...]]]:
    """The messages and folders with an open conflict: not sent again while it is open."""
    conflicts = load(store)
    return {c.store_id for c in conflicts if c.store_id}, {c.folder for c in conflicts if not c.store_id}


def held_under(folders: set[tuple[str, ...]], path: tuple[str, ...]) -> bool:
    """Whether a folder is held: itself or a folder it is inside has an open (or kept) conflict."""
    return any(path[: len(folder)] == folder for folder in folders)
