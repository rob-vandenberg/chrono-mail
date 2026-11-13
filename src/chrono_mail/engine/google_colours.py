"""The colours of the events of a Google calendar (D-337). Google's CalDAV does not carry them; its Calendar API does.

Reading: at every synchronisation of a Google Calendar account each calendar's colours are read (after the first time only what changed) and kept
on this computer, in google-colours.json beside the items; an event without tags shows Google's colour (D-329: another program's colour). Nothing
is written into the event's text.

Writing, server first (D-193): the colour tag of an event (the first of its tags) is compared with the one that was last in step with Google. When
the person changed it, Google gets the nearest of its eleven colours (no tag: the calendar's own colour again), and only then is the new one
remembered. A change made offline is the same comparison at the next synchronisation; nothing waits in a queue (as IMAP, D-050). The first time an
event is seen its tag is only remembered, so tags given before never repaint Google's colours."""

import json
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from chrono_mail.config import AccountSettings, load_tags
from chrono_mail.core import AuthenticationError, ChronoMailError, CollectionInfo, CollectionKind, ProtocolError
from chrono_mail.core.colours import GOOGLE_EVENT_COLOURS, NEW_TAG, google_colour_nearest
from chrono_mail.core.jsontext import load_json
from chrono_mail.engine.google_access import google_bearer
from chrono_mail.engine.session import Engine
from chrono_mail.pim.ical import Moment, parse_events
from chrono_mail.pimstore import ItemQuery, PimStore
from chrono_mail.protocols.google import CalendarUnknown, EventGone, GoogleRefused, SyncTokenGone, calendar_id_of, read_colours, set_colour
from chrono_mail.store.atomic import replace_file_atomically

# --- Version ------------------------------------------------------------
__version__ = "google_colours.py 4.4.598"

# --- Version history ----------------------------------------------------
# v4.4.598: Initial: Google's event colours read at every synchronisation and shown on events without tags; a changed colour tag sent to Google first (D-337)

FILE_NAME: str = "google-colours.json"
FORMAT: int = 1
_LOCK = threading.Lock()  # one read-change-write of the file at a time (the account's synchronisation lock covers the server; this the file)


@dataclass
class CalendarColours:
    token: str = ""  # where the last read of the calendar ended
    events: dict[str, tuple[str, str]] = field(default_factory=dict)  # key -> (Google's id of the event, Google's colour number or "")
    tags: dict[str, str] = field(default_factory=dict)  # key -> the colour tag last in step with Google ("": none)
    unknown: bool = False  # the Calendar API does not know this calendar (Google's holiday calendars): never asked again
    compared: bool = False  # its events were compared once: what is first seen after that is new, not from before this version


@dataclass
class ColourSync:
    recoloured: int = 0  # events whose colour here changed (Google's colour, or one sent there)
    errors: list[str] = field(default_factory=list)


def colours_path(engine: Engine, account_id: str) -> Path:
    return engine.directories.pim_directory(account_id) / FILE_NAME


def moment_key(moment: Moment | None) -> str:
    """The key part of one changed date of a series, as Google's originalStartTime gives it (protocols.google.utc_key)."""
    if moment is None:
        return ""
    if isinstance(moment, datetime):
        if moment.tzinfo is None:
            return ""  # a floating time is no moment Google can name
        return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if isinstance(moment, date):
        return moment.strftime("%Y%m%d")
    return ""


def event_key(uid: str, recurrence_id: Moment | None = None) -> str:
    """The key of a series (its UID) or of one changed date of it (UID, a space, the date it had)."""
    part = moment_key(recurrence_id)
    return f"{uid} {part}" if part else uid


def _load(path: Path) -> dict[str, CalendarColours]:
    try:
        data = load_json(path.read_text("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}  # none yet, or damaged: read again from Google (it is never the truth)
    if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("calendars"), dict):
        return {}
    found: dict[str, CalendarColours] = {}
    for collection, raw in data["calendars"].items():
        if not isinstance(raw, dict):
            continue
        events = raw.get("events", {})
        tags = raw.get("tags", {})
        found[str(collection)] = CalendarColours(
            str(raw.get("token", "")),
            {str(key): (str(value[0]), str(value[1])) for key, value in events.items() if isinstance(value, list) and len(value) == 2} if isinstance(events, dict) else {},
            {str(key): str(value) for key, value in tags.items()} if isinstance(tags, dict) else {},
            raw.get("unknown") is True,
            raw.get("compared") is True,
        )
    return found


def _save(engine: Engine, path: Path, calendars: dict[str, CalendarColours]) -> None:
    data = {"format": FORMAT, "calendars": {
        collection: {"token": kept.token, "events": {key: list(value) for key, value in kept.events.items()}, "tags": kept.tags, "unknown": kept.unknown, "compared": kept.compared}
        for collection, kept in calendars.items()
    }}
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_file_atomically(path, json.dumps(data, indent=0).encode("utf-8"), engine.durable)


def shown_colours(engine: Engine, settings: AccountSettings) -> dict[str, dict[str, str]]:
    """Per calendar of a Google account: event key -> the colour Google gives it ("#rrggbb"); empty for any other account."""
    if not settings.google:
        return {}
    shown: dict[str, dict[str, str]] = {}
    for collection, kept in _load(colours_path(engine, settings.account.account_id)).items():
        shown[collection] = {key: GOOGLE_EVENT_COLOURS[colour] for key, (_, colour) in kept.events.items() if colour in GOOGLE_EVENT_COLOURS}
    return shown


def google_colour(shown: dict[str, dict[str, str]], collection: str, uid: str, recurrence_id: Moment | None) -> str:
    """The colour Google gives one occurrence: that of its changed date when Google has one, else that of its series; empty: none."""
    colours = shown.get(collection, {})
    if recurrence_id is not None:
        own = colours.get(event_key(uid, recurrence_id), "")
        if own:
            return own
    return colours.get(uid, "")


def _google_calendars(store: PimStore) -> list[tuple[CollectionInfo, str]]:
    return [(info, calendar) for info in store.collections() if info.kind is CollectionKind.EVENTS and (calendar := calendar_id_of(info.server_value("href")))]


def _tag_keys(store: PimStore, collection: str, items: tuple[str, ...] = ()) -> dict[str, str]:
    """Event key -> its colour tag (the first of its tags, "" for none), of every event of a calendar (or only these items)."""
    keys: dict[str, str] = {}
    for item in store.index.query(ItemQuery(CollectionKind.EVENTS, (collection,), items=items, limit=1_000_000)):
        if not item.repeats:  # the index has its tags in their order: nothing is read or decrypted (every synchronisation does this)
            keys[event_key(item.uid)] = item.tags[0] if item.tags else ""
            continue
        try:
            text = store.get(collection, item.item_id)
        except Exception:  # noqa: BLE001 - a damaged item is one item left out (AGENTS.md 4.2), never a stalled synchronisation
            continue
        for series in parse_events(text):
            keys[event_key(series.master.uid, series.master.recurrence_id)] = series.master.categories[0] if series.master.categories else ""  # an item of only changed dates: the first one
            for changed in series.overrides:
                keys[event_key(changed.uid or series.master.uid, changed.recurrence_id)] = changed.categories[0] if changed.categories else ""
    return keys


def _read(access: str, calendar: str, kept: CalendarColours) -> int:
    """Google's colours into what is kept; returns how many events changed colour."""
    try:
        read = read_colours(access, calendar, kept.token)
    except SyncTokenGone:
        read = read_colours(access, calendar)
    before: dict[str, tuple[str, str]] = dict(kept.events)
    events: dict[str, tuple[str, str]] = {} if read.whole else dict(kept.events)
    for event in read.events:
        if event.cancelled:  # deleted on Google: known by Google's id (a deleted entry need not name its UID)
            for gone in [key for key, (event_id, _) in events.items() if event_id == event.event_id]:
                del events[gone]
            continue
        key = f"{event.uid} {event.original_start}" if event.original_start else event.uid
        events[key] = (event.event_id, event.colour_id)
    kept.events = events
    kept.token = read.sync_token
    nothing = ("", "")
    return sum(1 for key in set(before) | set(events) if before.get(key, nothing)[1] != events.get(key, nothing)[1])


def _send(access: str, calendar: str, kept: CalendarColours, tags: dict[str, str], colour_of_tag: dict[str, str], result: ColourSync, whole: bool) -> bool:
    """Every colour tag changed since it was last in step with Google, sent there first; False when Google could not be reached (the rest waits
    for the next synchronisation). Whole: tags holds every event of the calendar, so one not in it was deleted here."""
    for key, tag in tags.items():
        known = kept.events.get(key)
        if key not in kept.tags:
            if not kept.compared or not tag or known is None or known[1]:
                if known is not None or not kept.compared:
                    kept.tags[key] = tag  # from before this version, or coloured on Google already: remembered only, never sent
                continue  # not on Google yet (made offline, sent by CalDAV first): compared again once the next read brings its id
        elif kept.tags[key] == tag:
            continue
        if known is None:
            continue  # not on Google yet: the next read brings its id
        wanted = google_colour_nearest(colour_of_tag.get(" ".join(tag.split()).casefold(), NEW_TAG)) if tag else ""
        try:
            set_colour(access, calendar, known[0], wanted)
        except EventGone:
            kept.tags[key] = tag  # gone from Google: the synchronisation takes it away here too
            continue
        except GoogleRefused as error:
            kept.tags[key] = tag  # asking again gives the same answer: Google keeps its colour, the tag still colours it here
            result.errors.append(f"Google kept its colour of an event ({error})")
            continue
        except AuthenticationError:
            raise  # the sign-in ended: said as such by the caller, never as "could not be reached"
        except ProtocolError:
            return False
        kept.tags[key] = tag
        if known[1] != wanted:
            kept.events[key] = (known[0], wanted)
            result.recoloured += 1
    if whole:
        for key in [key for key in kept.tags if key not in tags]:
            del kept.tags[key]  # deleted here: nothing left to compare
        kept.compared = True
    return True


def _tag_colours(engine: Engine) -> dict[str, str]:
    return {entry.key: entry.colour for entry in load_tags(engine.directories.tags_file).entries}


def sync_colours(engine: Engine, settings: AccountSettings, store: PimStore, items: tuple[str, ...] = (), collection: str = "") -> ColourSync:
    """Read Google's colours of every calendar of a Google account (or only this one) and send the colour tags the person changed. Called inside the
    account's synchronisation lock, after the calendars were synchronised or an item was saved: it never raises, so neither fails because of a
    colour; what failed is in errors and waits for the next synchronisation."""
    try:
        return _sync_colours(engine, settings, store, items, collection)
    except (ChronoMailError, OSError) as error:  # a damaged tags file or index here: the colours wait, the save or the synchronisation stands
        return ColourSync(errors=[f"Event colours: {error}"])


def _sync_colours(engine: Engine, settings: AccountSettings, store: PimStore, items: tuple[str, ...], collection: str) -> ColourSync:
    result = ColourSync()
    if not settings.google:
        return result
    calendars = [(info, calendar) for info, calendar in _google_calendars(store) if not collection or info.collection_id == collection]
    if not calendars:
        return result
    try:
        access = google_bearer(engine, settings)
    except (AuthenticationError, ProtocolError) as error:
        result.errors.append(f"Event colours: {error}")
        return result
    except Exception as error:  # noqa: BLE001 - a missing sign-in file or wallet: the colours wait, the calendars were synchronised
        result.errors.append(f"Event colours: {error}")
        return result
    path = colours_path(engine, settings.account.account_id)
    colour_of_tag = _tag_colours(engine)
    with _LOCK:
        kept_all = _load(path)
        try:
            for info, calendar in calendars:
                kept = kept_all.setdefault(info.collection_id, CalendarColours())
                if kept.unknown or (items and not kept.compared):
                    continue  # right after a save only a calendar read before is asked (one small question): the first read is the synchronisation's
                try:
                    result.recoloured += _read(access, calendar, kept)
                    reached = _send(access, calendar, kept, _tag_keys(store, info.collection_id, items), colour_of_tag, result, whole=not items)
                except CalendarUnknown:
                    kept.unknown = True  # a holiday calendar: its events have no colours of their own, and it cannot be changed
                    continue
                except AuthenticationError as error:
                    result.errors.append(f"Event colours: {error}")
                    break
                except ProtocolError as error:
                    if not items:  # after one save a passing failure is said by nothing: the synchronisation tries again
                        result.errors.append(f"Event colours: {error}")
                    continue
                except (ChronoMailError, OSError) as error:  # the index or an item here could not be read: this calendar waits, the others go on
                    result.errors.append(f"Event colours: {error}")
                    continue
                if not reached and not items:
                    result.errors.append("Event colours: Google could not be reached; tried again at the next synchronisation")
            for gone in [key for key in kept_all if key not in {info.collection_id for info in store.collections()}]:
                del kept_all[gone]  # a calendar no longer here: its colours go with it
        finally:
            try:
                _save(engine, path, kept_all)
            except OSError as error:  # a full disk: Google's colours are read again next time, a sent colour compared again
                result.errors.append(f"Event colours could not be kept on this computer: {error}")
    return result

