"""The sync conflicts of the calendars, task lists and address books, and what waits for their servers (D-194): listed, and settled by the person
(Chrono Mail's version, the server's, or a choice per field). Settings > Sync conflicts and the command line use these commands."""

from dataclasses import dataclass, field, replace
from typing import Literal

from chrono_mail.core import LOCAL_ACCOUNT_ID, LOOK_FOLLOWS, LOOK_WAITING, ChronoMailError, CollectionInfo, CollectionLook, MessageRef, ProtocolError, StorageError
from chrono_mail.engine.changes import Change
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_dav import _client, _lock_for, _state_path, keep_looks
from chrono_mail.engine.imap_online import is_imap, waiting_marker
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from chrono_mail.engine.pimaccess import account_name, ensure_local_pim, open_pim, pim_account_ids
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.pim.merge import LOCAL, SERVER
from chrono_mail.pimstore import PimStore
from chrono_mail.protocols.dav import DavClient, DavConflict, canonical_href, passing_failure
from chrono_mail.sync.davconflicts import (
    COLLECTION_CHANGED_ON_SERVER,
    COLLECTION_CREATE_REFUSED,
    COLLECTION_DELETE_REFUSED,
    COLLECTION_DELETED_ON_SERVER,
    COLLECTION_LOOK_CLASH,
    COLLECTION_LOOK_REFUSED,
    COLLECTION_REASONS,
    DELETE_REFUSED,
    LOOK_REASONS,
    REFUSED,
    SyncConflict,
    load_conflicts,
)
from chrono_mail.sync.davcreates import forget as forget_create
from chrono_mail.sync.davcreates import waiting_ids as creates_waiting
from chrono_mail.sync.davdeletes import forget as forget_delete
from chrono_mail.sync.davdeletes import load_waiting
from chrono_mail.sync.davconflicts import forget as forget_conflict
from chrono_mail.sync.davitems import DONE, ItemGateway
from chrono_mail.sync.davsync import PUBLIC_SUFFIX, DavState, digest_of, forget_collection
from chrono_mail.sync.imap_sync import message_flags
from chrono_mail.sync.mailconflicts import (
    CONFLICTS_FILE,
    FOLDER_GONE,
    FOLDER_GONE_CHANGED,
    FOLDER_DELETE_REFUSED,
    FOLDER_DELETED_HERE_CHANGED,
    FOLDER_GONE_WITH_MAIL,
    FOLDER_PENDING,
    FOLDER_REFUSED,
    FOLDER_RENAME_REFUSED,
    KEEP_ONLY,
    MARKS_REFUSED,
    MESSAGE_REFUSED,
    MOVE_REFUSED,
    MailConflict,
)

# --- Version ------------------------------------------------------------
__version__ = "commands_conflicts.py 4.2.453"

# --- Version history ----------------------------------------------------
# v4.2.453: Use Server data for a folder deleted on the server switches off the rules that move mail to it (D-302)
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v2.0.286: A collection's name or colour changed here and on the server (Use Chrono Mail, Use Server data, per field) or a name the server
#           refused (Use Server data, Retry sync): the choice goes into what the account keeps, the synchronisation started at once does it (D-192, F3)
# v1.1.240: A folder renamed or deleted offline: refused (Use Server data undoes it here, Retry sync) or changed on the server meanwhile (Use
#           Chrono Mail deletes it anyway, Use Server data brings it back) (R-31)
# v1.1.239: Mail conflicts and what waits are asked and settled through the mailbox, never its store (R-37)
# v1.1.237: A mail folder deleted on the server with changes here: Use Chrono Mail makes it there again with its mail, Use Server data deletes it
#           here (only when no mail exists only here), Keep here only (only when some does) (R-30)
# v1.1.236: A collection made here offline that the server refused to make: Keep here only moves it with everything in it to the local ones, Retry
#           sync; collections waiting to be made on the server counted with their items (R-29)
# v1.1.235: Use Chrono Mail ties a collection made again to the address the server lists it at (Kerio); a collection deleted here offline that the server changed (Use Chrono Mail deletes it there anyway, Use Server data brings it back) or
#           whose delete it refused (Use Server data brings it back, Retry sync); deletes waiting for the server counted (R-28)
# v1.1.234: A whole calendar, task list or address book deleted on the server with changes here: Use Chrono Mail makes it there again (its items
#           follow at the synchronisation started at once), Use Server data deletes it here too (R-25)
# v1.1.228: Retry sync: a refusal released and sent again (the accounts to synchronise at once are in the answer); refused for a clash (the owner)
# v1.1.227: Mail too: refused marks, moves, messages and folders of IMAP accounts listed and settled (Use Server data, Keep here only); what of an
#           IMAP account waits for its server, for the accounts with changes made offline (D-195)
# v1.1.225: Initial: conflict.list, conflict.settle and sync.waiting (R-23, R-24, D-194)

COLLECTION_KINDS: dict[str, str] = {"events": "calendar", "tasks": "tasklist", "contacts": "addressbook"}  # What a collection's conflict is about

REASON_WORDS: dict[str, str] = {
    "clash": "Changed here and on the server",
    "refused": "The server did not take the change",
    "deleted_on_server": "Changed here, deleted on the server",
    "changed_on_server": "Deleted here, changed on the server",
    "delete_refused": "The server did not take the delete",
    COLLECTION_DELETED_ON_SERVER: "Deleted on the server, with changes here not sent yet",
    COLLECTION_CHANGED_ON_SERVER: "Deleted here, changed on the server",
    COLLECTION_DELETE_REFUSED: "The server did not take the delete",
    COLLECTION_CREATE_REFUSED: "The server did not make it",
    COLLECTION_LOOK_CLASH: "Changed here and on the server",
    COLLECTION_LOOK_REFUSED: "The server did not take the name",
    MARKS_REFUSED: "The server did not take the marks",
    MOVE_REFUSED: "The server did not take the move",
    MESSAGE_REFUSED: "The server did not store the message",
    FOLDER_REFUSED: "The server did not make the folder",
    FOLDER_GONE_CHANGED: "Deleted on the server, with changes here not sent yet",
    FOLDER_GONE_WITH_MAIL: "Deleted on the server, with mail here the server never had",
    FOLDER_RENAME_REFUSED: "The server did not take the rename",
    FOLDER_DELETE_REFUSED: "The server did not take the delete",
    FOLDER_DELETED_HERE_CHANGED: "Deleted here, new mail on the server",
}


@dataclass(frozen=True, slots=True)
class ConflictField:
    key: str  # what a choice names
    label: str
    local: str  # Chrono Mail's value as text ("" = absent)
    server: str


@dataclass(frozen=True, slots=True)
class ConflictSummary:
    conflict: str
    account: str
    account_name: str
    collection: str
    collection_name: str
    item: str  # the item here ("" when it was deleted here)
    kind: str  # events, tasks or contacts
    reason: str
    reason_text: str  # the reason in the person's words
    name: str
    detail: str  # the server's own words for a refusal
    whole: bool  # only a whole side can be chosen
    local_allowed: bool  # "Use Chrono Mail" can work (not for a refusal)
    when: float
    fields: tuple[ConflictField, ...]
    server_allowed: bool = True  # "Use Server data" can work (not where it would delete mail that exists only here)
    keep_allowed: bool = False  # "Keep here only" is the way (mail made here that the server will not take)
    retry_allowed: bool = False  # "Retry sync": a refusal, which the server may take now (a clash needs the person's choice)


@dataclass(frozen=True, slots=True)
class ConflictList:
    conflicts: tuple[ConflictSummary, ...]


def _summary(engine: Engine, account: str, store: PimStore, conflict: SyncConflict) -> ConflictSummary:
    if conflict.reason == COLLECTION_CREATE_REFUSED:  # it exists only here: only Keep here only or Retry sync (anything else would lose it)
        return ConflictSummary(
            conflict.conflict_id, account, account_name(engine, account), conflict.collection, "", "", COLLECTION_KINDS.get(conflict.kind, conflict.kind),
            conflict.reason, REASON_WORDS[conflict.reason], conflict.name, conflict.detail, True, False, conflict.when, (),
            server_allowed=False, keep_allowed=True, retry_allowed=True,
        )
    if conflict.reason in LOOK_REASONS:  # a collection's name or colour (D-192): a choice per field; a refusal only the server's or Retry sync
        refused = conflict.reason == COLLECTION_LOOK_REFUSED
        return ConflictSummary(
            conflict.conflict_id, account, account_name(engine, account), conflict.collection, "", "", COLLECTION_KINDS.get(conflict.kind, conflict.kind),
            conflict.reason, REASON_WORDS[conflict.reason], conflict.name, conflict.detail, False, not refused, conflict.when,
            tuple(ConflictField(c.key, c.label, c.local, c.server) for c in conflict.clashes), retry_allowed=refused,
        )
    if conflict.reason in COLLECTION_REASONS:  # about a whole collection: it is the Name, and only a whole side can be chosen
        refused = conflict.reason == COLLECTION_DELETE_REFUSED
        return ConflictSummary(
            conflict.conflict_id, account, account_name(engine, account), conflict.collection, "", "", COLLECTION_KINDS.get(conflict.kind, conflict.kind),
            conflict.reason, REASON_WORDS[conflict.reason], conflict.name, conflict.detail, True, not refused, conflict.when, (), retry_allowed=refused,
        )
    try:
        collection_name = store.get_collection(conflict.collection).name
    except StorageError:
        collection_name = ""
    return ConflictSummary(
        conflict.conflict_id, account, account_name(engine, account), conflict.collection, collection_name, conflict.item, conflict.kind, conflict.reason,
        REASON_WORDS.get(conflict.reason, conflict.reason), conflict.name, conflict.detail, conflict.whole, conflict.reason not in (REFUSED, DELETE_REFUSED),
        conflict.when, tuple(ConflictField(c.key, c.label, c.local, c.server) for c in conflict.clashes),
        retry_allowed=conflict.reason in (REFUSED, DELETE_REFUSED),
    )


def _mail_summary(engine: Engine, account: str, conflict: MailConflict) -> ConflictSummary:
    where = "/".join(conflict.folder)
    fields: tuple[ConflictField, ...] = ()
    if conflict.reason == MARKS_REFUSED:
        fields = (ConflictField("marks", "Marks", conflict.local_marks, conflict.server_marks),)
    elif conflict.reason == MOVE_REFUSED:
        fields = (ConflictField("folder", "Folder", where, "/".join(conflict.server_folder)),)
    if conflict.reason in FOLDER_PENDING:  # a folder renamed or deleted offline (R-31)
        changed = conflict.reason == FOLDER_DELETED_HERE_CHANGED
        fields = (ConflictField("folder", "Name", where, "/".join(conflict.server_folder)),) if conflict.reason == FOLDER_RENAME_REFUSED else ()
        return ConflictSummary(
            conflict.conflict_id, account, account_name(engine, account), where, "", "", "folder", conflict.reason, REASON_WORDS[conflict.reason],
            conflict.name, conflict.detail, True, changed, conflict.when, fields, retry_allowed=not changed,
        )
    if conflict.reason in FOLDER_GONE:  # a whole folder: made there again, deleted here (never when mail exists only here), or kept here (R-30)
        return ConflictSummary(
            conflict.conflict_id, account, account_name(engine, account), where, "", "", "folder", conflict.reason, REASON_WORDS[conflict.reason],
            conflict.name, "", True, True, conflict.when, (), server_allowed=conflict.reason == FOLDER_GONE_CHANGED,
            keep_allowed=conflict.reason == FOLDER_GONE_WITH_MAIL,
        )
    return ConflictSummary(
        conflict.conflict_id, account, account_name(engine, account), where, where, conflict.store_id, "mail", conflict.reason,
        REASON_WORDS.get(conflict.reason, conflict.reason), conflict.name, conflict.detail, False, False, conflict.when, fields,
        server_allowed=conflict.reason not in KEEP_ONLY, keep_allowed=conflict.reason in KEEP_ONLY, retry_allowed=True,
    )


def _mail_accounts_with_conflicts(engine: Engine) -> list[str]:
    """The IMAP accounts that keep sync conflicts (only those are opened)."""
    return [
        settings.account.account_id for settings in engine.config().accounts
        if is_imap(settings) and (engine.directories.store_directory(settings.account.account_id) / "private" / CONFLICTS_FILE).exists()
    ]


def conflict_list(engine: Engine, parameters: NoParameters) -> ConflictList:
    """Every open sync conflict of every account, oldest first: calendars, task lists and address books, and mail."""
    found: list[ConflictSummary] = []
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            found.extend(_summary(engine, account, store, conflict) for conflict in load_conflicts(store))
    for account in _mail_accounts_with_conflicts(engine):
        with open_mailbox(engine, account) as mailbox:
            found.extend(_mail_summary(engine, account, conflict) for conflict in mailbox.sync_conflicts())
    return ConflictList(tuple(sorted(found, key=lambda item: item.when)))


CONFLICT_LIST: TypedCommand[NoParameters, ConflictList] = TypedCommand("conflict.list", NoParameters, ConflictList)


@dataclass(frozen=True, slots=True)
class SettleParameters:
    conflicts: tuple[str, ...] = field(default=(), metadata=option("The conflicts to settle, all with one side (repeat)"))
    side: Literal["local", "server", "keep", "retry", ""] = field(
        default="", metadata=option(
            "local: Chrono Mail's version goes to the server; server: the server's version here; keep: mail made here stays here only; "
            "retry: a refusal is sent again at the next synchronisation"
        ),
    )
    choices: tuple[str, ...] = field(default=(), metadata=option("Per field instead: conflict<TAB>field key<TAB>local or server (repeat); every field of a conflict"))


@dataclass(frozen=True, slots=True)
class SettleResult:
    settled: tuple[str, ...]
    open: tuple[str, ...]  # not settled: changed on the server again, a field not chosen, or the server not reached
    problems: tuple[str, ...]  # in the person's words, one per conflict not settled
    retried: tuple[str, ...] = ()  # the accounts to synchronise at once: their refusals were released (Retry sync)


def conflict_settle(engine: Engine, parameters: SettleParameters) -> SettleResult:
    """Settle sync conflicts: with one side for all those named, or per field. Chrono Mail's version goes to the server first; the server's is
    written here. A conflict whose item the server changed again is weighed again and stays open."""
    wanted: dict[str, dict[str, str]] = {conflict: {} for conflict in parameters.conflicts}
    for text in parameters.choices:
        parts = text.split("\t")
        if len(parts) != 3 or parts[2] not in (LOCAL, SERVER):
            raise UsageError("A choice is conflict<TAB>field key<TAB>local or server")
        wanted.setdefault(parts[0], {})[parts[1]] = parts[2]
    if not wanted:
        raise UsageError("Name the conflicts to settle")
    if parameters.choices and parameters.side:
        raise UsageError("Choose one side for all, or per field, not both")
    if not parameters.choices and parameters.side not in (LOCAL, SERVER, "keep", "retry"):
        raise UsageError("Choose a side: local, server, keep or retry")
    retried: set[str] = set()
    settled: list[str] = []
    still_open: list[str] = []
    problems: list[str] = []
    touched: set[str] = set()
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            mine = [c for c in load_conflicts(store) if c.conflict_id in wanted]
            if not mine:
                continue
            settings = engine.config().find(account)
            if not settings.dav_url:
                problems.extend(f"{c.name}: this account has no calendar server any more" for c in mine)
                still_open.extend(c.conflict_id for c in mine)
                continue
            with _client(engine, settings) as client, _lock_for(account):
                gateway = ItemGateway(client, store, _state_path(engine, account), engine.durable)
                for conflict in mine:
                    touched.add(account)
                    side = parameters.side
                    if conflict.reason in LOOK_REASONS:
                        problem = _settle_look(engine, account, store, conflict, side, wanted[conflict.conflict_id])
                        if problem:
                            problems.append(f"{conflict.name}: {problem}")
                            still_open.append(conflict.conflict_id)
                        else:
                            settled.append(conflict.conflict_id)
                            retried.add(account)  # the synchronisation started at once sends Chrono Mail's or takes the server's
                        continue
                    if conflict.reason in COLLECTION_REASONS:
                        problem = _settle_collection(engine, account, store, client, conflict, side)
                        if problem:
                            problems.append(f"{conflict.name}: {problem}")
                            still_open.append(conflict.conflict_id)
                        else:
                            settled.append(conflict.conflict_id)
                            if side != SERVER or conflict.reason != COLLECTION_DELETED_ON_SERVER:
                                retried.add(account)  # the synchronisation started at once sends its items, brings it back or tries again
                        continue
                    if side == "retry":
                        if conflict.reason not in (REFUSED, DELETE_REFUSED):
                            problems.append(f"{conflict.name}: it was changed on both sides; trying again would only clash again, so choose a side")
                            still_open.append(conflict.conflict_id)
                        else:
                            forget_conflict(store, conflict.conflict_id)  # released: the next synchronisation sends it again (D-194)
                            settled.append(conflict.conflict_id)
                            retried.add(account)
                        continue
                    if side == LOCAL and conflict.reason in (REFUSED, DELETE_REFUSED):
                        problems.append(f"{conflict.name}: the server does not take Chrono Mail's version; only the server's can be used")
                        still_open.append(conflict.conflict_id)
                        continue
                    try:
                        info: CollectionInfo = store.get_collection(conflict.collection)
                        outcome = gateway.settle(info, conflict, side, wanted[conflict.conflict_id] or None)
                    except ProtocolError as error:
                        words = "the server could not be reached" if passing_failure(error) else f"the server did not take it ({error})"
                        problems.append(f"{conflict.name}: {words}; it stays open")
                        still_open.append(conflict.conflict_id)
                        continue
                    except ChronoMailError as error:
                        problems.append(f"{conflict.name}: {error}")
                        still_open.append(conflict.conflict_id)
                        continue
                    if outcome.status == DONE:
                        settled.append(conflict.conflict_id)
                    else:
                        still_open.append(conflict.conflict_id)
                        problems.append(f"{conflict.name}: changed on the server again, or a field was not chosen; it stays open")
    for account in _mail_accounts_with_conflicts(engine):
        with open_mailbox(engine, account) as mailbox:
            for conflict in [c for c in mailbox.sync_conflicts() if c.conflict_id in wanted]:
                touched.add(account)
                problem = _settle_mail(mailbox, conflict, parameters.side)
                if not problem and (parameters.side in ("retry", LOCAL) or conflict.reason in FOLDER_PENDING):
                    retried.add(account)
                if problem:
                    problems.append(f"{conflict.name}: {problem}")
                    still_open.append(conflict.conflict_id)
                else:
                    settled.append(conflict.conflict_id)
    for account in touched:
        engine.report(Change("conflicts_changed", account))
    unknown = [conflict for conflict in wanted if conflict not in settled and conflict not in still_open]
    problems.extend(f"No open conflict {conflict}" for conflict in unknown)
    return SettleResult(tuple(settled), tuple(still_open), tuple(problems), tuple(sorted(retried)))


def _settle_collection(engine: Engine, account: str, store: PimStore, client: DavClient, conflict: SyncConflict, side: str) -> str:
    """Settle a collection the server deleted while it had changes here (R-25); returns what went wrong ("" when settled). The server's side: it goes
    here too (to the deleted items, 30 days). Chrono Mail's: made again at its old place on the server with its name and colour, its tie given back
    and forgotten what was synchronised, so the next synchronisation sends every item as new."""
    if conflict.reason == COLLECTION_CREATE_REFUSED:
        return _settle_create(engine, store, conflict, side)
    if conflict.reason != COLLECTION_DELETED_ON_SERVER:
        return _settle_delete(store, client, conflict, side)
    if side not in (LOCAL, SERVER):
        return "choose Use Chrono Mail or Use Server data"
    try:
        info = store.get_collection(conflict.collection)
    except StorageError:
        forget_conflict(store, conflict.conflict_id)  # it is gone here already: nothing is left to settle
        return ""
    if side == SERVER:
        store.delete_collection(info.collection_id)
        forget_conflict(store, conflict.conflict_id)
        return ""
    tie = dict(conflict.tie)
    try:
        made = client.make_collection(tie.get("href", conflict.href), conflict.kind, conflict.name, info.colour if conflict.kind != "contacts" else "")
    except DavConflict:
        return "the server has something at its place again; it stays open (Use Server data deletes it here)"
    except ProtocolError as error:
        return ("the server could not be reached" if passing_failure(error) else f"the server did not make it again ({error})") + "; it stays open"
    forget_collection(_state_path(engine, account), info.collection_id, engine.durable)
    # tied to the place the server lists it at (Kerio gives a new calendar an address of its own)
    store.update_collection(info.collection_id, server={**tie, "href": canonical_href(made.href), "component": made.component})
    forget_conflict(store, conflict.conflict_id)
    key = f"{tie.get('component', '')} {canonical_href(tie.get('href', conflict.href))}"
    keep_looks(engine, account, {key: next((look for look in engine.config().find(account).collection_looks if look.key == key), None)}, {key: None})
    return ""


def _settle_look(engine: Engine, account: str, store: PimStore, conflict: SyncConflict, side: str, choices: dict[str, str]) -> str:
    """Settle a collection's name or colour conflict (D-192); returns what went wrong ("" when settled). The choice goes into what the account keeps:
    Chrono Mail's waits to be sent (the server's value now is what it must still have then, else it clashes again), the server's is followed and
    shown here at once; Retry sync (a refusal) sends the name again. The synchronisation started at once does the rest."""
    refused = conflict.reason == COLLECTION_LOOK_REFUSED
    if side == "retry" and not refused:
        return "it was changed on both sides; trying again would only clash again, so choose a side"
    if (side == LOCAL or LOCAL in choices.values()) and refused:
        return "the server does not take Chrono Mail's name; only the server's can be used, or Retry sync"
    if side not in (LOCAL, SERVER, "retry") and not choices:
        return "choose Use Chrono Mail or Use Server data" if not refused else "choose Use Server data or Retry sync"
    try:
        info = store.get_collection(conflict.collection)
    except StorageError:
        forget_conflict(store, conflict.conflict_id)  # it is gone here already: nothing is left to settle
        return ""
    key = f"{info.server_value('component')} {canonical_href(info.server_value('href'))}"
    before = next((look for look in engine.config().find(account).collection_looks if look.key == key), None)
    look = before if before is not None else CollectionLook(key)
    name, colour = info.name, info.colour
    for clash in conflict.clashes:
        choice = choices.get(clash.key, side) if choices else side
        if choice not in (LOCAL, SERVER, "retry"):
            return "choose a side for every field"
        theirs = choice == SERVER
        if clash.key == "name":
            look = replace(look, name="", name_state=LOOK_FOLLOWS, base_name="") if theirs else replace(look, name=clash.local, name_state=LOOK_WAITING, base_name=clash.server)
            if theirs:
                name = clash.server + (PUBLIC_SUFFIX if info.server_value("public") == "1" else "")
        else:
            look = replace(look, colour="", colour_state=LOOK_FOLLOWS, base_colour="") if theirs else replace(look, colour=clash.local, colour_state=LOOK_WAITING, base_colour=clash.server)
            if theirs:
                colour = clash.server
    keep_looks(engine, account, {key: before}, {key: None if look.empty else look})
    if (name, colour) != (info.name, info.colour):
        store.update_collection(info.collection_id, name=name, colour=colour)  # told by the store as collection_edited (D-146)
    forget_conflict(store, conflict.conflict_id)
    return ""


def _settle_create(engine: Engine, store: PimStore, conflict: SyncConflict, side: str) -> str:
    """Settle a collection made here (offline) that the server refused to make (R-29). It exists only here, so it is never deleted: Keep here only
    moves it with everything in it to the local ones on this computer (copied and read back first, then taken away from the account); Retry sync
    asks the server again at the synchronisation started at once."""
    if side == "retry":
        forget_conflict(store, conflict.conflict_id)
        return ""
    if side != "keep":
        return "it exists only here: only Keep here only or Retry sync can settle it (anything else would lose it)"
    try:
        info = store.get_collection(conflict.collection)
    except StorageError:
        forget_conflict(store, conflict.conflict_id)
        forget_create(store, conflict.collection)
        return ""
    ensure_local_pim(engine)
    with open_pim(engine, LOCAL_ACCOUNT_ID) as local:
        copy = local.create_collection(info.kind, info.name, info.colour)
        for item in store.index.item_ids(info.collection_id):
            local.put(copy.collection_id, store.get(info.collection_id, item))  # written and read back before anything is taken away
    forget_create(store, conflict.collection)
    store.delete_collection(info.collection_id)  # to the deleted items (30 days), like every removal
    forget_conflict(store, conflict.conflict_id)
    return ""


def _settle_delete(store: PimStore, client: DavClient, conflict: SyncConflict, side: str) -> str:
    """Settle a collection deleted here (offline) that the server changed, or whose delete it refused (R-28). The server's side: the delete is given
    up and the next synchronisation brings it back here. Chrono Mail's (only when it was changed there): deleted on the server now, also what was
    changed there. Retry sync (only for a refusal): sent again at the next synchronisation."""
    if side == SERVER:
        forget_delete(store, conflict.href)
    elif side == LOCAL and conflict.reason == COLLECTION_CHANGED_ON_SERVER:
        try:
            client.delete(conflict.href)
        except ProtocolError as error:
            return ("the server could not be reached" if passing_failure(error) else f"the server did not delete it ({error})") + "; it stays open"
        forget_delete(store, conflict.href)
    elif side == "retry" and conflict.reason == COLLECTION_DELETE_REFUSED:
        pass  # the delete still waits: released, it is sent at the synchronisation started at once
    elif side == LOCAL:
        return "the server does not take the delete; only the server's can be used, or Retry sync"
    else:
        return "choose Use Chrono Mail or Use Server data" if conflict.reason == COLLECTION_CHANGED_ON_SERVER else "choose Use Server data or Retry sync"
    forget_conflict(store, conflict.conflict_id)
    return ""


def _settle_mail(mailbox: Mailbox, conflict: MailConflict, side: str) -> str:
    """Settle a mail conflict (D-195); returns what went wrong ("" when settled). Chrono Mail's side is never offered (the server refused it). The
    server's: its marks here, or the message moved back here. Keep here only: mail made here that the server will not take stays here, never sent."""
    if conflict.reason in FOLDER_GONE:
        return _settle_gone_folder(mailbox, conflict, side)
    if conflict.reason in FOLDER_PENDING:
        return _settle_pending_folder(mailbox, conflict, side)
    if side == "retry":  # every mail conflict is a refusal: released, the next check sends it again (D-195)
        mailbox.forget_sync_conflict(conflict.conflict_id)
        return ""
    if conflict.reason in KEEP_ONLY:
        if side != "keep":
            return "it exists only here: only Keep here only can settle it (anything else would delete mail)"
        mailbox.keep_sync_conflict_here(conflict.conflict_id)
        return ""
    if side != SERVER:
        return "the server does not take Chrono Mail's version; only the server's can be used"
    ref = MessageRef(mailbox.account_id, conflict.folder, conflict.store_id)
    try:
        if conflict.reason == MARKS_REFUSED:
            mailbox.take_server_marks(ref, message_flags(frozenset(conflict.server_flags)))
        elif conflict.reason == MOVE_REFUSED:
            mailbox.move_back(ref, conflict.server_folder)
    except ChronoMailError as error:
        return str(error)
    mailbox.forget_sync_conflict(conflict.conflict_id)
    return ""


def _settle_pending_folder(mailbox: Mailbox, conflict: MailConflict, side: str) -> str:
    """Settle a folder renamed or deleted offline (R-31). A refusal: Use Server data undoes it here (renamed back, or the folder comes back at the
    check started at once), Retry sync asks the server again. New mail on the server: Use Chrono Mail deletes it there anyway, Use Server data
    brings it back."""
    changed = conflict.reason == FOLDER_DELETED_HERE_CHANGED
    try:
        if side == "retry" and not changed:
            pass  # the change still waits: released, it is sent at the check started at once
        elif side == SERVER and conflict.reason == FOLDER_RENAME_REFUSED:
            mailbox.undo_folder_rename(conflict.folder, conflict.server_folder)
        elif side == SERVER:
            mailbox.give_up_folder_change(conflict.folder)
        elif side == LOCAL and changed:
            mailbox.delete_folder_anyway(conflict.folder)
        else:
            return "choose Use Chrono Mail or Use Server data" if changed else "the server does not take it; choose Use Server data or Retry sync"
    except ChronoMailError as error:
        return str(error)
    mailbox.forget_sync_conflict(conflict.conflict_id)
    return ""


def _settle_gone_folder(mailbox: Mailbox, conflict: MailConflict, side: str) -> str:
    """Settle a folder the server deleted while it had changes here (R-30). Use Chrono Mail: released, the check started at once makes it again on
    the server and uploads its mail. Use Server data (only when no mail exists only here): taken away here with the folders inside it (their files
    kept in the store's .deleted). Keep here only (only when some mail exists only here): it stays on this computer, never sent."""
    if side == LOCAL:
        mailbox.forget_sync_conflict(conflict.conflict_id)
        return ""
    if side == SERVER and conflict.reason == FOLDER_GONE_CHANGED:
        inside = sorted((folder.path for folder in mailbox.folders() if folder.path[: len(conflict.folder)] == conflict.folder), key=len, reverse=True)
        try:
            for path in inside:
                mailbox.drop_unsynchronised_folder(path, gone=True)  # deleted on the server: its rules are switched off (D-302)
        except ChronoMailError as error:
            return str(error)
        mailbox.forget_sync_conflict(conflict.conflict_id)
        return ""
    if side == "keep" and conflict.reason == FOLDER_GONE_WITH_MAIL:
        mailbox.keep_sync_conflict_here(conflict.conflict_id)
        return ""
    if conflict.reason == FOLDER_GONE_WITH_MAIL:
        return "it holds mail that exists only here: Use Chrono Mail or Keep here only (anything else would lose it)"
    return "choose Use Chrono Mail or Use Server data"


CONFLICT_SETTLE: TypedCommand[SettleParameters, SettleResult] = TypedCommand("conflict.settle", SettleParameters, SettleResult)


@dataclass(frozen=True, slots=True)
class AccountWaiting:
    account: str
    account_name: str
    changes: int  # items changed or deleted here that the server does not have yet (open conflicts not counted)
    items: tuple[str, ...]  # the items here among them (deleted ones are only counted)


@dataclass(frozen=True, slots=True)
class WaitingList:
    accounts: tuple[AccountWaiting, ...]


def sync_waiting(engine: Engine, parameters: NoParameters) -> WaitingList:
    """What waits for each account's calendar server: changes made here that the next synchronisation sends (for the count and the mark, D-194)."""
    found: list[AccountWaiting] = []
    for account in pim_account_ids(engine):
        settings = engine.config().find(account)
        if not settings.dav_url:
            continue
        with open_pim(engine, account) as store:
            state = DavState(_state_path(engine, account), durable=False)
            held = {(c.collection, c.item) for c in load_conflicts(store)} | {(c.collection, c.href) for c in load_conflicts(store)}
            items: list[str] = []
            deleted = 0
            for info in store.collections():
                if not info.server_value("href") or info.read_only:
                    continue
                links = state.get(info.collection_id).links
                linked = {link.item_id: (href, link) for href, link in links.items()}
                for item in store.index.item_ids(info.collection_id):
                    if (info.collection_id, item) in held:
                        continue
                    known = linked.get(item)
                    if known is None or digest_of(store.get(info.collection_id, item)) != known[1].digest:
                        items.append(item)
                existing = set(store.index.item_ids(info.collection_id))
                deleted += sum(1 for href, link in links.items() if link.item_id not in existing and (info.collection_id, href) not in held)
            for collection in creates_waiting(store) - {c.collection for c in load_conflicts(store)}:  # made here, to be made there (R-29)
                deleted += 1
                items.extend(store.index.item_ids(collection))
            held_hrefs = {c.href for c in load_conflicts(store)}
            deleted += sum(1 for waiting in load_waiting(store) if canonical_href(waiting.href) not in held_hrefs)  # whole collections (R-28)
            if items or deleted:
                found.append(AccountWaiting(account, account_name(engine, account), len(items) + deleted, tuple(items)))
    for settings in engine.config().accounts:  # mail: only the IMAP accounts that had changes made offline are counted (D-195)
        account = settings.account.account_id
        marker = waiting_marker(engine, account)
        if not is_imap(settings) or not marker.exists():
            continue
        with open_mailbox(engine, account) as mailbox:
            messages, deleted = mailbox.waiting_for_server()
        if messages or deleted:
            found.append(AccountWaiting(account, account_name(engine, account), len(messages) + deleted, tuple(messages)))
        else:
            marker.unlink(missing_ok=True)  # nothing waits any more
    return WaitingList(tuple(found))


SYNC_WAITING: TypedCommand[NoParameters, WaitingList] = TypedCommand("sync.waiting", NoParameters, WaitingList)


def register(registry: CommandRegistry) -> None:
    registry.register(CONFLICT_LIST.id, "List the sync conflicts of the calendars, task lists and address books", NoParameters, conflict_list, doing="Reading the sync conflicts")
    registry.register(CONFLICT_SETTLE.id, "Settle sync conflicts: Chrono Mail's version, the server's, or per field", SettleParameters, conflict_settle, doing="Settling sync conflicts")
    registry.register(SYNC_WAITING.id, "List the changes made here that wait for the calendar servers", NoParameters, sync_waiting, doing="Reading what waits for the servers")
