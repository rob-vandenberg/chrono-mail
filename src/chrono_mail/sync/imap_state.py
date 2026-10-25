import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from chrono_mail.core import StorageError
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "imap_state.py 3.6.351"

# --- Version history ----------------------------------------------------
# v3.6.351: a checkpoint keeps the messages the server would not send yet ("w"), so one such message never keeps its folder from a checkpoint
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash
# v3.6.349: forget_numbering ("z"): a folder synchronised afresh, the person's choices for it kept (RV-195)
# v1.1.248: Where each folder stood on the server at the end of the last check that left nothing open (checkpoints, event "c": UIDNEXT and the
#           change counter), so the next check asks only what changed since (R-09); forgotten with the folder's UIDs, moved with a rename
# v1.1.241: A folder ticked here that the server's list of folders to show now has follows that list from then on (event "l", R-34)
# v1.1.240: Folder renames and deletes made here while the server could not be reached, kept until the next check does them there (pending,
#           events "pr", "pd", "pf", "pz"; R-31)
# v1.1.237: The server's name of every synchronised folder (server_names, event "n"), so a folder gone from the server's list can be asked about
#           by its own name; moved along with a rename, dropped when the folder is forgotten (R-30)
# v0.5.123: folders renamed ("w") and deleted ("y") on the server by the person (D-118)
# v0.4.120: messages deleted by the person while the server could not be reached ("k")
# v0.0.44: folder choices and roles
# v0.0.31: folders deleted here are hidden
# v0.0.30: uploaded messages
# v0.0.29: Initial IMAP state log

# What is known about an IMAP account is kept in an append-only file, one JSON object per line:
#   {"e": "v", "f": [folder path], "v": UIDVALIDITY}                    the folder's UIDVALIDITY; forgets the folder's messages
#   {"e": "m", "f": [...], "u": UID, "s": store id, "g": [flags]}        UID <-> store id, with the server flags last seen
#   {"e": "g", "f": [...], "u": UID, "g": [flags]}                       the server flags changed
#   {"e": "x", "f": [...], "u": UID}                                     the UID is gone from the server (and from the store)
#   {"e": "c", "f": [...], "u": UIDNEXT, "q": HIGHESTMODSEQ or null}     the folder's sync ended with nothing open: where the server stood (R-09)
#     (with "w": [UID, ...]: messages the server would not send yet, asked again at every check; 3.6.351)
#   {"e": "c", "f": [...]}                                               that is no longer known (the next check lists the folder in full)
#   {"e": "h", "n": server folder name, "f": [local path]}                               the folder is not shown or synchronised here (deleted here; it stays on the server)
#   {"e": "d", "n": server folder name, "v": true|false}                  the person chose not to synchronise the folder (true) or to synchronise it though it is not subscribed (false)
#   {"e": "l", "n": server folder name}                                  the server's list of folders to show has the folder: it follows that list from now on (R-34)
#   {"e": "r", "n": server folder name, "r": role}                       the person says which standard folder (sent, drafts, trash, junk, archive) this server folder is
#   {"e": "a", "s": store id}                                           the message was sent to the server (so that it is not sent twice)
#   {"e": "k", "s": store id}                                           the person deleted the message for good here while the server could not be reached
#   {"e": "w", "f": [old path], "t": [new path], "p": [[old name, new name], ...]}   the person renamed the folder (and those below it) on the server
#   {"e": "y", "f": [path], "n": server folder name}                     the person deleted the folder on the server: everything known about it is forgotten
#   {"e": "z", "f": [path]}                                             how the folder numbers its messages is no longer known (a restore left it out): it is
#                                                                        synchronised from the server afresh; the person's choices for it stay
#   {"e": "n", "f": [path], "n": server folder name}                     the name the server has for the local folder (R-30)
#   {"e": "pr", "f": [old path], "t": [new path], "n": server name}      renamed here offline: to be renamed on the server (R-31)
#   {"e": "pd", "f": [path], "n": server name, "v": validity, "u": [uids]} deleted here offline: to be deleted on the server (R-31)
#   {"e": "pf", "f": [path]}                                             that delete goes even though the server changed the folder (the person chose)
#   {"e": "pz", "f": [path]}                                             the change waiting for that folder is done or given up
# Appending (with fsync) cannot damage earlier lines; a line cut off by a crash is ignored; unknown events are ignored (later
# versions add more, for example the changes waiting to be sent). If the file is lost the next sync downloads again and
# matches by Message-ID: duplicates are avoided where possible, never a loss. The file is rewritten compactly when it has
# grown to many times the size of what it describes.

COMPACT_FACTOR: int = 8
COMPACT_MINIMUM_LINES: int = 5000


@dataclass(frozen=True, slots=True)
class PendingFolder:
    """A folder renamed or deleted here while the server could not be reached (R-31), known by its path here (the new one for a rename)."""

    kind: str  # "rename" or "delete"
    path: tuple[str, ...]
    server_name: str  # its name on the server now (before the rename, or the folder to delete)
    old_path: tuple[str, ...] = ()  # a rename: where it was here
    validity: int = 0  # a delete: the folder's UIDVALIDITY as last synchronised
    uids: frozenset[int] = frozenset()  # a delete: the messages the server had then (one more means it changed)
    force: bool = False  # a delete the person chose to do although the server changed the folder


MAXIMUM_WAITING: int = 10_000  # messages a checkpoint keeps to ask again; more: no checkpoint (the folder is listed in full)


def _checkpoint_event(folder: tuple[str, ...], checkpoint: "Checkpoint") -> dict[str, object]:
    event: dict[str, object] = {"e": "c", "f": list(folder), "u": checkpoint.uid_next, "q": checkpoint.modseq}
    if checkpoint.waiting:
        event["w"] = sorted(checkpoint.waiting)
    return event


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Where a server folder stood at the start of the last check of it that left nothing open (R-09): the next check asks only for what
    changed since."""

    uid_next: int  # UIDNEXT: every message that came later has this UID or a higher one
    modseq: int | None  # HIGHESTMODSEQ (RFC 7162); None: the server keeps no change counter for the folder
    waiting: frozenset[int] = frozenset()  # messages below uid_next not here yet (the server would not send them): asked again every check


@dataclass(frozen=True, slots=True)
class ImapEntry:
    store_id: str
    flags: frozenset[str]  # the server flags last seen, limited to the ones we keep


class ImapState:
    def __init__(self, path: Path, durable: bool = True) -> None:
        self.path: Path = path
        self._durable: bool = durable
        self.validity: dict[tuple[str, ...], int] = {}
        self.entries: dict[tuple[str, ...], dict[int, ImapEntry]] = {}
        self.uploaded: set[str] = set()
        self.deleted_here: set[str] = set()  # deleted for good by the person, not yet on the server: never held back by a safety rule
        self.hidden: set[str] = set()
        self.forced: set[str] = set()  # folders to synchronise even though the server does not list them as subscribed
        self.roles: dict[str, str] = {}  # server folder name -> chosen role
        self.server_names: dict[tuple[str, ...], str] = {}  # local folder -> its name on the server, as last synchronised (R-30)
        self.pending: dict[tuple[str, ...], PendingFolder] = {}  # folder renames and deletes made offline, by path here (R-31)
        self.checkpoints: dict[tuple[str, ...], Checkpoint] = {}  # local folder -> where its server folder stood at the last settled check (R-09)
        self.ignored_lines: int = 0
        self._lines: int = 0
        self._needs_newline: bool = False
        self._load()

    def _load(self) -> None:
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as error:
            raise StorageError(f"The IMAP state could not be read: {error.strerror}") from None
        self._needs_newline = bool(data) and not data.endswith(b"\n")
        for raw in data.decode("utf-8", "replace").split("\n"):
            if not raw.strip():
                continue
            self._lines += 1
            try:
                self._apply(load_json(raw))
            except (ValueError, KeyError, TypeError, AttributeError):
                self.ignored_lines += 1

    def _apply(self, event: dict[str, object]) -> None:
        kind = event["e"]
        if kind == "d":
            name, hide = event["n"], event["v"]
            if not isinstance(name, str) or not name or not isinstance(hide, bool):
                raise ValueError("bad folder choice")
            if hide:
                self.hidden.add(name)
                self.forced.discard(name)
            else:
                self.hidden.discard(name)
                self.forced.add(name)
            return
        if kind == "l":
            name = event["n"]
            if not isinstance(name, str) or not name:
                raise ValueError("bad folder name")
            self.forced.discard(name)
            return
        if kind == "r":
            name, role = event["n"], event["r"]
            if not isinstance(name, str) or not name or not isinstance(role, str) or len(role) > 20:
                raise ValueError("bad role")
            if role:
                self.roles[name] = role
            else:
                self.roles.pop(name, None)
            return
        if kind == "h":
            name, where = event["n"], event["f"]
            if not isinstance(name, str) or not name or not isinstance(where, list) or not where:
                raise ValueError("bad folder name")
            self.hidden.add(name)
            self.validity.pop(tuple(str(part) for part in where), None)
            self.entries.pop(tuple(str(part) for part in where), None)
            self.server_names.pop(tuple(str(part) for part in where), None)
            self.checkpoints.pop(tuple(str(part) for part in where), None)
            return
        if kind == "w":
            old, new, pairs = event["f"], event["t"], event["p"]
            if not isinstance(old, list) or not old or not isinstance(new, list) or not new or not isinstance(pairs, list):
                raise ValueError("bad folder rename")
            names: dict[str, str] = {}
            for pair in pairs:
                if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(name, str) and name for name in pair):
                    raise ValueError("bad folder rename")
                names[str(pair[0])] = str(pair[1])
            self._rename(tuple(str(part) for part in old), tuple(str(part) for part in new), names)
            return
        if kind == "z":
            where = event["f"]
            if not isinstance(where, list) or not where:
                raise ValueError("bad folder")
            self._unknown(tuple(str(part) for part in where))
            return
        if kind == "y":
            where, name = event["f"], event["n"]
            if not isinstance(where, list) or not where or not isinstance(name, str) or not name:
                raise ValueError("bad folder")
            self._forget(tuple(str(part) for part in where), name)
            return
        if kind == "n":
            where, name = event["f"], event["n"]
            if not isinstance(where, list) or not where or not isinstance(name, str) or not name:
                raise ValueError("bad server name")
            self.server_names[tuple(str(part) for part in where)] = name
            return
        if kind in ("pr", "pd", "pf", "pz"):
            self._apply_pending(kind, event)
            return
        if kind == "a":
            stored = event["s"]
            if not isinstance(stored, str) or len(stored) != 32:
                raise ValueError("bad store id")
            self.uploaded.add(stored)
            return
        if kind == "k":
            deleted = event["s"]
            if not isinstance(deleted, str) or len(deleted) != 32:
                raise ValueError("bad store id")
            self.deleted_here.add(deleted)
            return
        raw_folder = event["f"]
        if not isinstance(raw_folder, list) or not all(isinstance(part, str) for part in raw_folder) or not raw_folder:
            raise ValueError("bad folder")
        folder = tuple(str(part) for part in raw_folder)
        if kind == "v":
            value = event["v"]
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError("bad validity")
            self.validity[folder] = value
            self.entries[folder] = {}
            self.checkpoints.pop(folder, None)
            return
        if kind == "c":
            self._apply_checkpoint(folder, event)
            return
        uid = event["u"]
        if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0:
            raise ValueError("bad uid")
        if kind == "m":
            store_id, flags = event["s"], event["g"]
            if not isinstance(store_id, str) or len(store_id) != 32 or not isinstance(flags, list):
                raise ValueError("bad entry")
            self.entries.setdefault(folder, {})[uid] = ImapEntry(store_id, frozenset(str(flag) for flag in flags))
        elif kind == "g":
            flags = event["g"]
            current = self.entries.get(folder, {}).get(uid)
            if not isinstance(flags, list):
                raise ValueError("bad flags")
            if current is not None:
                self.entries[folder][uid] = ImapEntry(current.store_id, frozenset(str(flag) for flag in flags))
        elif kind == "x":
            self.entries.get(folder, {}).pop(uid, None)

    def _apply_checkpoint(self, folder: tuple[str, ...], event: dict[str, object]) -> None:
        if "u" not in event:
            self.checkpoints.pop(folder, None)
            return
        uid_next, modseq = event["u"], event.get("q")
        if not isinstance(uid_next, int) or isinstance(uid_next, bool) or uid_next <= 0:
            raise ValueError("bad uidnext")
        if modseq is not None and (not isinstance(modseq, int) or isinstance(modseq, bool) or modseq <= 0):
            raise ValueError("bad modseq")
        waiting = event.get("w", [])
        if not isinstance(waiting, list) or len(waiting) > MAXIMUM_WAITING or not all(isinstance(uid, int) and not isinstance(uid, bool) and 0 < uid < uid_next for uid in waiting):
            raise ValueError("bad waiting")
        self.checkpoints[folder] = Checkpoint(uid_next, modseq, frozenset(waiting))

    def _apply_pending(self, kind: str, event: dict[str, object]) -> None:
        where = event["f"]
        if not isinstance(where, list) or not where or not all(isinstance(part, str) for part in where):
            raise ValueError("bad folder")
        path = tuple(str(part) for part in where)
        if kind == "pz":
            self.pending.pop(path, None)
            return
        if kind == "pf":
            if path in self.pending:
                self.pending[path] = replace(self.pending[path], force=True)
            return
        name = event["n"]
        if not isinstance(name, str) or not name:
            raise ValueError("bad server name")
        if kind == "pr":
            old = event["t"]
            if not isinstance(old, list) or not old:
                raise ValueError("bad folder")
            earlier = self.pending.pop(path, None)  # renamed again: the change keeps the server's name, under the newest path
            new = tuple(str(part) for part in old)
            self.pending[new] = PendingFolder("rename", new, name, old_path=earlier.old_path if earlier is not None else path)
            return
        validity, uids = event["v"], event["u"]
        if not isinstance(validity, int) or not isinstance(uids, list) or not all(isinstance(uid, int) for uid in uids):
            raise ValueError("bad delete")
        self.pending.pop(path, None)
        self.pending[path] = PendingFolder("delete", path, name, validity=validity, uids=frozenset(int(uid) for uid in uids))

    # --- writing ------------------------------------------------------------------------------------------------

    def _append(self, event: dict[str, object]) -> None:
        text = ("\n" if self._needs_newline else "") + json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o600)
            try:
                os.write(descriptor, text.encode("utf-8"))
                if self._durable:
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._needs_newline = False
            self._lines += 1
        except OSError as error:
            raise StorageError(f"The IMAP state could not be saved: {error.strerror}") from None

    def hide_folder(self, server_name: str, folder: tuple[str, ...]) -> None:
        """The folder was deleted here: it is no longer synchronised (its mail on the server stays) and the UIDs we knew are forgotten."""
        self._append({"e": "h", "n": server_name, "f": list(folder)})
        self.hidden.add(server_name)
        self.forced.discard(server_name)
        self.validity.pop(folder, None)
        self.entries.pop(folder, None)
        self.server_names.pop(folder, None)
        self.checkpoints.pop(folder, None)

    def set_hidden(self, server_name: str, hidden: bool) -> None:
        """Do not synchronise this folder (its local mail stays), or synchronise it again (even if the server does not list it as subscribed)."""
        self._append({"e": "d", "n": server_name, "v": hidden})
        if hidden:
            self.hidden.add(server_name)
            self.forced.discard(server_name)
        else:
            self.hidden.discard(server_name)
            self.forced.add(server_name)

    def follow_list(self, server_name: str) -> None:
        """The server's list of folders to show (its subscriptions) has a folder the person ticked here: from now on it follows that list, so
        another program taking it off the list takes it away here too (R-34)."""
        self._append({"e": "l", "n": server_name})
        self.forced.discard(server_name)

    def set_role(self, server_name: str, role: str) -> None:
        """Say which standard folder a server folder is ('' for: decide automatically)."""
        self._append({"e": "r", "n": server_name, "r": role})
        if role:
            self.roles[server_name] = role
        else:
            self.roles.pop(server_name, None)

    def rename_later(self, old: tuple[str, ...], new: tuple[str, ...]) -> None:
        """Renamed here while the server could not be reached: what is known moves to the new path now, and the next check renames it on the
        server (a second rename before then keeps the server's name)."""
        earlier = self.pending.get(old)
        name = earlier.server_name if earlier is not None else self.server_names.get(old, "")
        self.rename_folder(old, new, {})
        if not name:
            return  # never on the server: the next check makes it under its new name
        self._append({"e": "pr", "f": list(old), "t": list(new), "n": name})
        self.pending.pop(old, None)
        self.pending[new] = PendingFolder("rename", new, name, old_path=earlier.old_path if earlier is not None else old)

    def delete_later(self, folder: tuple[str, ...]) -> None:
        """Deleted here while the server could not be reached: forgotten here now, and the next check deletes it on the server unless the server
        changed it meanwhile."""
        earlier = self.pending.get(folder)
        name = earlier.server_name if earlier is not None else self.server_names.get(folder, "")
        validity, uids = self.validity.get(folder, 0), sorted(self.entries.get(folder, {}))
        self.forget_folder(folder, name)
        if not name or not validity:
            self.pending.pop(folder, None)
            return  # never on the server: nothing to delete there
        self._append({"e": "pd", "f": list(folder), "n": name, "v": validity, "u": uids})
        self.pending[folder] = PendingFolder("delete", folder, name, validity=validity, uids=frozenset(uids))

    def force_delete(self, folder: tuple[str, ...]) -> None:
        """The person chose to delete it on the server although the server changed it."""
        if folder in self.pending:
            self._append({"e": "pf", "f": list(folder)})
            self.pending[folder] = replace(self.pending[folder], force=True)

    def pending_done(self, folder: tuple[str, ...]) -> None:
        """The change waiting for this folder was done on the server, or given up."""
        if folder in self.pending:
            self._append({"e": "pz", "f": list(folder)})
            del self.pending[folder]

    def set_server_name(self, folder: tuple[str, ...], name: str) -> None:
        """The name the server has for this local folder (written only when it changed)."""
        if self.server_names.get(folder) == name:
            return
        self._append({"e": "n", "f": list(folder), "n": name})
        self.server_names[folder] = name

    def rename_folder(self, old: tuple[str, ...], new: tuple[str, ...], names: dict[str, str]) -> None:
        """The person renamed a folder on the server: what is known about it and the folders below it moves to the new
        paths, and the choices made for the server folders (by their old name) go to their new names."""
        self._append({"e": "w", "f": list(old), "t": list(new), "p": [[before, after] for before, after in sorted(names.items())]})
        self._rename(old, new, names)

    def _rename(self, old: tuple[str, ...], new: tuple[str, ...], names: dict[str, str]) -> None:
        for path in [path for path in self.validity if path[: len(old)] == old]:
            self.validity[new + path[len(old) :]] = self.validity.pop(path)
        for path in [path for path in self.entries if path[: len(old)] == old]:
            self.entries[new + path[len(old) :]] = self.entries.pop(path)
        for path in [path for path in self.checkpoints if path[: len(old)] == old]:
            self.checkpoints[new + path[len(old) :]] = self.checkpoints.pop(path)
        for path in [path for path in self.server_names if path[: len(old)] == old]:
            name = self.server_names.pop(path)
            self.server_names[new + path[len(old) :]] = next((after + name[len(before) :] for before, after in names.items() if name.startswith(before)), name)
        for before, after in names.items():
            if before in self.roles:
                self.roles[after] = self.roles.pop(before)
            if before in self.forced:
                self.forced.discard(before)
                self.forced.add(after)
            if before in self.hidden:
                self.hidden.discard(before)
                self.hidden.add(after)

    def forget_folder(self, folder: tuple[str, ...], server_name: str) -> None:
        """The person deleted the folder on the server: its UIDs and the choices made for it are forgotten."""
        self._append({"e": "y", "f": list(folder), "n": server_name})
        self._forget(folder, server_name)

    def _forget(self, folder: tuple[str, ...], server_name: str) -> None:
        self.validity.pop(folder, None)
        self.entries.pop(folder, None)
        self.server_names.pop(folder, None)
        self.checkpoints.pop(folder, None)
        self.roles.pop(server_name, None)
        self.forced.discard(server_name)
        self.hidden.discard(server_name)

    def forget_numbering(self, folder: tuple[str, ...]) -> None:
        """How the folder numbers its messages is no longer known (a restore left the folder out, RV-195): the next check synchronises it
        from the server afresh, as a folder new here. Its name on the server and the person's choices (ticked, its role) stay."""
        self._append({"e": "z", "f": list(folder)})
        self._unknown(folder)

    def _unknown(self, folder: tuple[str, ...]) -> None:
        self.validity.pop(folder, None)
        self.entries.pop(folder, None)
        self.checkpoints.pop(folder, None)

    def mark_uploaded(self, store_id: str) -> None:
        """Remember that a local message was sent to the server. Call right after the server accepted it."""
        self._append({"e": "a", "s": store_id})
        self.uploaded.add(store_id)

    def note_deleted(self, store_id: str) -> None:
        """The person deleted this message for good here and the server could not be reached: the next sync deletes it there."""
        self._append({"e": "k", "s": store_id})
        self.deleted_here.add(store_id)

    def set_validity(self, folder: tuple[str, ...], validity: int) -> None:
        """The folder's UIDVALIDITY. A new value forgets every UID of the folder (they mean something else now)."""
        self._append({"e": "v", "f": list(folder), "v": validity})
        self.validity[folder] = validity
        self.entries[folder] = {}
        self.checkpoints.pop(folder, None)

    def set_checkpoint(self, folder: tuple[str, ...], checkpoint: Checkpoint | None) -> None:
        """Where the server folder stood at the start of a check of it that left nothing open (None: no longer known). Written only when it
        changed."""
        if self.checkpoints.get(folder) == checkpoint:
            return
        if checkpoint is None:
            self._append({"e": "c", "f": list(folder)})
            del self.checkpoints[folder]
            return
        self._append(_checkpoint_event(folder, checkpoint))
        self.checkpoints[folder] = checkpoint

    def map(self, folder: tuple[str, ...], uid: int, store_id: str, flags: frozenset[str]) -> None:
        """Remember that this message is safely stored. Call before anything is changed on the server."""
        self._append({"e": "m", "f": list(folder), "u": uid, "s": store_id, "g": sorted(flags)})
        self.entries.setdefault(folder, {})[uid] = ImapEntry(store_id, flags)

    def set_flags(self, folder: tuple[str, ...], uid: int, flags: frozenset[str]) -> None:
        current = self.entries.get(folder, {}).get(uid)
        if current is None:
            return
        self._append({"e": "g", "f": list(folder), "u": uid, "g": sorted(flags)})
        self.entries[folder][uid] = ImapEntry(current.store_id, flags)

    def unmap(self, folder: tuple[str, ...], uid: int) -> None:
        if uid in self.entries.get(folder, {}):
            self._append({"e": "x", "f": list(folder), "u": uid})
            self.deleted_here.discard(self.entries[folder][uid].store_id)
            del self.entries[folder][uid]

    # --- reading ------------------------------------------------------------------------------------------------

    def folder_entries(self, folder: tuple[str, ...]) -> dict[int, ImapEntry]:
        return dict(self.entries.get(folder, {}))

    def find(self, store_id: str) -> tuple[tuple[str, ...], int] | None:
        """Where on the server a stored message is, if it is known."""
        for folder, mapped in self.entries.items():
            for uid, entry in mapped.items():
                if entry.store_id == store_id:
                    return folder, uid
        return None

    def count(self) -> int:
        return sum(len(mapped) for mapped in self.entries.values())

    def compact_if_needed(self) -> bool:
        """Rewrite the file as a snapshot when it holds far more lines than facts. Atomic: a crash keeps the old file."""
        facts = self.count() + len(self.validity)
        if self._lines < COMPACT_MINIMUM_LINES or self._lines < COMPACT_FACTOR * max(facts, 1):
            return False
        lines: list[str] = []
        for folder, validity in self.validity.items():
            lines.append(json.dumps({"e": "v", "f": list(folder), "v": validity}, separators=(",", ":"), ensure_ascii=False))
            for uid, entry in self.entries.get(folder, {}).items():
                lines.append(json.dumps({"e": "m", "f": list(folder), "u": uid, "s": entry.store_id, "g": sorted(entry.flags)}, separators=(",", ":"), ensure_ascii=False))
            if (checkpoint := self.checkpoints.get(folder)) is not None:
                lines.append(json.dumps(_checkpoint_event(folder, checkpoint), separators=(",", ":"), ensure_ascii=False))
        lines.extend(json.dumps({"e": "d", "n": name, "v": True}, separators=(",", ":"), ensure_ascii=False) for name in sorted(self.hidden))
        lines.extend(json.dumps({"e": "d", "n": name, "v": False}, separators=(",", ":"), ensure_ascii=False) for name in sorted(self.forced))
        lines.extend(json.dumps({"e": "r", "n": name, "r": role}, separators=(",", ":"), ensure_ascii=False) for name, role in sorted(self.roles.items()))
        lines.extend(json.dumps({"e": "n", "f": list(folder), "n": name}, separators=(",", ":"), ensure_ascii=False) for folder, name in sorted(self.server_names.items()))
        for change in self.pending.values():
            if change.kind == "rename":
                lines.append(json.dumps({"e": "pr", "f": list(change.old_path), "t": list(change.path), "n": change.server_name}, separators=(",", ":"), ensure_ascii=False))
            else:
                lines.append(json.dumps({"e": "pd", "f": list(change.path), "n": change.server_name, "v": change.validity, "u": sorted(change.uids)}, separators=(",", ":"), ensure_ascii=False))
            if change.force:
                lines.append(json.dumps({"e": "pf", "f": list(change.path)}, separators=(",", ":"), ensure_ascii=False))
        lines.extend(json.dumps({"e": "a", "s": store_id}, separators=(",", ":")) for store_id in sorted(self.uploaded))
        mapped = {entry.store_id for entries in self.entries.values() for entry in entries.values()}
        lines.extend(json.dumps({"e": "k", "s": store_id}, separators=(",", ":")) for store_id in sorted(self.deleted_here & mapped))
        temporary = self.path.with_name(self.path.name + ".new")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
            try:
                os.write(descriptor, ("\n".join(lines) + "\n").encode("utf-8"))
                if self._durable:
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)
            temporary.replace(self.path)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise StorageError(f"The IMAP state could not be compacted: {error.strerror}") from None
        self._lines = len(lines)
        return True

