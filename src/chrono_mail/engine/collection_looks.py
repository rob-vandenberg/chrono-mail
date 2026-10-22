"""The name and colour of a calendar, task list or address book of a server, changed by the person: on the server first, here after it took them
(D-192). Offline it is done here, kept with the account and sent at the next synchronisation; a refusal keeps Chrono Mail's and is told."""

from dataclasses import replace

from chrono_mail.core import LOOK_FOLLOWS, LOOK_KEPT, LOOK_WAITING, ChronoMailError, CollectionInfo, CollectionKind, CollectionLook, is_colour
from chrono_mail.engine.commands_dav import _client, _lock_for, keep_looks
from chrono_mail.engine.pimaccess import account_name
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import Engine
from chrono_mail.pimstore import PimStore
from chrono_mail.protocols.dav import canonical_href
from chrono_mail.sync.davsync import DONE, LATER, PUBLIC_SUFFIX, REFUSED, send_look, server_name

# --- Version ------------------------------------------------------------
__version__ = "collection_looks.py 1.1.223"

# --- Version history ----------------------------------------------------
# v1.1.223: The client is closed when the change is sent (R-36)
# v1.1.221: Initial: a server collection's name and colour on the server first; offline kept and sent later; a refusal kept and told (D-192)

KIND_WORDS: dict[CollectionKind, str] = {CollectionKind.EVENTS: "calendar", CollectionKind.TASKS: "task list", CollectionKind.CONTACTS: "address book"}


def key_of(info: CollectionInfo) -> str:
    """Where a collection is on its server, as the account remembers it (the folder_key form); empty for a collection of this computer only."""
    href = info.server_value("href")
    return f"{info.server_value('component')} {canonical_href(href)}" if href else ""


def _part(new: str, kept_value: str, kept_state: str, kept_base: str, now: str, outcome: str) -> tuple[str, str, str]:
    """What the account keeps of one part after it was sent (or could not be): (value, state, base). The base is what the server had before the
    first change here that it has not got yet."""
    base = kept_base if kept_state != LOOK_FOLLOWS else now
    if outcome == DONE or new == base:
        return "", LOOK_FOLLOWS, ""  # the server has it (or it is back to what the server has)
    return new, LOOK_WAITING if outcome == LATER else LOOK_KEPT, base


def change_look(engine: Engine, account_id: str, store: PimStore, info: CollectionInfo, name: str | None, colour: str | None) -> CollectionInfo:
    """Give a collection another name and/or colour. One of this computer only: here, it is the truth. One of a server: on the server first, then
    here; offline (or the server failing for now) here, kept with the account and sent at the next synchronisation; a lasting refusal is kept here
    and told. An address book's colour has no place on the server: it is kept with the account."""
    key = key_of(info)
    settings = engine.config().find(account_id)
    if not key or not settings.dav_url:
        return store.update_collection(info.collection_id, name, colour)
    if info.read_only:
        raise UsageError(f"This {KIND_WORDS[info.kind]} is read only on its server: its name and colour are the server's")
    new_name = " ".join(name.split()) if name is not None else None
    new_colour = colour.strip().lower() if colour is not None else None
    if new_name is not None and (not new_name or len(new_name) > 200):
        raise UsageError("A name of up to 200 characters is needed")
    if new_colour is not None and new_colour and not is_colour(new_colour):
        raise UsageError("A colour is written #rrggbb")
    with _lock_for(account_id):  # one at a time with the synchronisation of this account
        settings = engine.config().find(account_id)
        current = store.get_collection(info.collection_id)
        kept = settings.look(key)
        look = kept if kept is not None else CollectionLook(key)
        public = current.server_value("public") == "1"
        if new_name is not None and public and new_name.endswith(PUBLIC_SUFFIX):
            new_name = new_name[: -len(PUBLIC_SUFFIX)]
        shown_name = server_name(current)
        send_name = new_name if new_name is not None and new_name != shown_name else None
        send_colour = new_colour if new_colour is not None and new_colour != current.colour else None
        if send_colour == "":
            send_colour = None  # a colour is never taken away on the server
        outcomes: dict[str, str] = {}
        reason = ""
        to_server_colour = send_colour if current.kind is not CollectionKind.CONTACTS else None
        if send_name is not None or to_server_colour is not None:
            try:
                with _client(engine, settings) as client:
                    outcomes, reason = send_look(client, current.server_value("href"), send_name, to_server_colour)
            except ChronoMailError as error:  # no password stored, for one: tried again at the next synchronisation
                outcomes, reason = {part: LATER for part, value in (("name", send_name), ("colour", to_server_colour)) if value is not None}, str(error)
        if send_name is not None:
            value, state, base = _part(send_name, look.name, look.name_state, look.base_name, shown_name, outcomes["name"])
            look = replace(look, name=value, name_state=state, base_name=base)
        if send_colour is not None:
            outcome = outcomes.get("colour", REFUSED)  # an address book's: kept here, without a word (the server has no place for it)
            value, state, base = _part(send_colour, look.colour, look.colour_state, look.base_colour, current.colour, outcome)
            look = replace(look, colour=value, colour_state=state, base_colour=base)
        changed = store.update_collection(
            current.collection_id, (send_name + (PUBLIC_SUFFIX if public else "")) if send_name is not None else None, send_colour,
        )
        now: CollectionLook | None = None if look.empty else look
        if now != kept:
            keep_looks(engine, account_id, {key: kept}, {key: now})
    _tell(engine, account_id, changed, outcomes, reason)
    return changed


def _tell(engine: Engine, account_id: str, info: CollectionInfo, outcomes: dict[str, str], reason: str) -> None:
    words = {"name": "the new name", "colour": "the new colour"}
    waiting = [words[part] for part, outcome in outcomes.items() if outcome == LATER]
    refused = [words[part] for part, outcome in outcomes.items() if outcome == REFUSED]
    if waiting:
        engine.notice(f"{info.name}: the server of {account_name(engine, account_id)} could not be reached ({reason}): {' and '.join(waiting)} go there at the next synchronisation")
    if refused:
        engine.notice(f"{info.name}: the server did not take {' and '.join(refused)} ({reason}); Chrono Mail keeps it on this computer")
