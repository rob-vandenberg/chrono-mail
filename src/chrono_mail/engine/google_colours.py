"""The colours of the events of a Google calendar (D-337). Google's CalDAV does not carry them; its Calendar API does.

Reading: at every synchronisation of a Google Calendar account each calendar's labels (the colours the person made in Google Calendar, D-338) and
its events' colours are read (after the first time only what changed) and kept on this computer, in google-colours.json beside the items; an event
without tags shows Google's colour (D-329: another program's colour). A label the person named is a tag here as well, in its colour, and stands in
the tag list; a label without a name gives its colour only. Nothing is written into the event's text.

Writing, server first (D-193): the colour tag of an event (the first of its tags) is compared with the one that was last in step with Google. When
the person changed it, Google gets the label of that name when the calendar has one, else the nearest of Google's eleven colours (no tag: the
calendar's own colour again), and only then is the new one remembered. A change made offline is the same comparison at the next synchronisation;
nothing waits in a queue (as IMAP, D-050). The first time an event is seen its tag is only remembered, so tags given before never repaint Google's
colours."""

import json
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from chrono_mail.config import AccountSettings, load_tags, save_tags
from chrono_mail.core import AuthenticationError, ChronoMailError, CollectionInfo, CollectionKind, ProtocolError
from chrono_mail.core.colours import GOOGLE_COLOUR_NAMES, GOOGLE_EVENT_COLOURS, NEW_TAG, google_colour_nearest
from chrono_mail.core.jsontext import load_json
from chrono_mail.engine.google_access import google_bearer
from chrono_mail.engine.session import Engine
from chrono_mail.pim.ical import Moment, parse_events
from chrono_mail.pimstore import ItemQuery, PimStore
from chrono_mail.protocols.google import CalendarUnknown, EventGone, GoogleRefused, SyncTokenGone, calendar_id_of, read_colours, read_labels, set_colour, set_label
from chrono_mail.store.atomic import replace_file_atomically

# --- Version ------------------------------------------------------------
__version__ = "google_colours.py 4.4.600"

# --- Version history ----------------------------------------------------
# v4.4.600: Every colour of a Google calendar is a tag here, on the appointments that carry it and in the tag list; taking it off in the window takes the colour off Google (D-339)
# v4.4.599: Google's labels (the colours a person made and named in Google Calendar): a named one is a tag here with its colour; a tag of that name goes back as that label (D-338)
# v4.4.598: Initial: Google's event colours read at every synchronisation and shown on events without tags; a changed colour tag sent to Google first (D-337)

FILE_NAME: str = "google-colours.json"
FORMAT: int = 2  # 1 (4.4.598): events without their label, no labels of their own
_LOCK = threading.Lock()  # one read-change-write of the file at a time (the account's synchronisation lock covers the server; this the file)


@dataclass
class CalendarColours:
    token: str = ""  # where the last read of the calendar ended
    events: dict[str, tuple[str, str, str]] = field(default_factory=dict)  # key -> (Google's id of the event, Google's colour number or "", its label or "")
    labels: dict[str, tuple[str, str]] = field(default_factory=dict)  # Google's label -> (the name the person gave it or "", its colour "#rrggbb")
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


@dataclass
class Kept:
    """What is kept for one account: its calendars, and the colour this program last gave each tag it made from a label (D-338), so a colour the
    person chose themselves is left alone."""

    calendars: dict[str, CalendarColours] = field(default_factory=dict)
    label_tags: dict[str, tuple[str, str]] = field(default_factory=dict)  # the name without regard to case -> (the name, the colour) written here


def _load(path: Path) -> Kept:
    try:
        data = load_json(path.read_text("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return Kept()  # none yet, or damaged: read again from Google (it is never the truth)
    if not isinstance(data, dict) or data.get("format") not in (1, FORMAT) or not isinstance(data.get("calendars"), dict):
        return Kept()
    older = data.get("format") == 1  # 4.4.598 knew no labels: everything is read again once, or the events it already has would never get theirs
    found: dict[str, CalendarColours] = {}
    for collection, raw in data["calendars"].items():
        if not isinstance(raw, dict):
            continue
        events = raw.get("events", {})
        labels = raw.get("labels", {})
        tags = raw.get("tags", {})
        found[str(collection)] = CalendarColours(
            "" if older else str(raw.get("token", "")),
            {str(key): (str(value[0]), str(value[1]), str(value[2]) if len(value) > 2 else "")  # a file of 4.4.598 (format 1) knows no labels yet
             for key, value in events.items() if isinstance(value, list) and len(value) in (2, 3)} if isinstance(events, dict) else {},
            {str(key): (str(value[0]), str(value[1])) for key, value in labels.items() if isinstance(value, list) and len(value) == 2} if isinstance(labels, dict) else {},
            {str(key): str(value) for key, value in tags.items()} if isinstance(tags, dict) else {},
            raw.get("unknown") is True,
            raw.get("compared") is True,
        )
    written = data.get("label_tags")
    return Kept(found, {str(key): (str(value[0]), str(value[1])) for key, value in written.items() if isinstance(value, list) and len(value) == 2}
                if isinstance(written, dict) else {})


def _save(engine: Engine, path: Path, kept: Kept) -> None:
    calendars = kept.calendars
    data = {"format": FORMAT, "label_tags": {key: list(value) for key, value in kept.label_tags.items()}, "calendars": {
        collection: {"token": kept.token, "events": {key: list(value) for key, value in kept.events.items()},
                     "labels": {key: list(value) for key, value in kept.labels.items()}, "tags": kept.tags, "unknown": kept.unknown, "compared": kept.compared}
        for collection, kept in calendars.items()
    }}
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_file_atomically(path, json.dumps(data, indent=0).encode("utf-8"), engine.durable)


def colour_tag(name: str, colour: str) -> tuple[str, str]:
    """The tag one of Google's colours is here: the name the person gave it, or, for a colour they never named, the name Google itself gives the
    nearest of its eleven (Tomato, Banana, Grape...). Its colour is the colour Google shows (D-339)."""
    if name:
        return name, colour
    standard = google_colour_nearest(colour) if colour else ""
    return (GOOGLE_COLOUR_NAMES.get(standard, ""), colour) if standard else ("", "")


@dataclass(frozen=True, slots=True)
class GoogleShown:
    """What Google gives the events of an account, per calendar: event key -> its colour ("#rrggbb"), and -> the name of its label (D-338)."""

    colours: dict[str, dict[str, str]] = field(default_factory=dict)
    names: dict[str, dict[str, str]] = field(default_factory=dict)


def shown_colours(engine: Engine, settings: AccountSettings) -> GoogleShown:
    """What Google gives this account's events; empty for any other account. Every colour of a Google calendar is a tag here (D-339): the name the
    person gave it, or Google's own name for the nearest of its eleven. A label of the calendar comes first: it is what Google shows."""
    if not settings.google:
        return GoogleShown()
    colours: dict[str, dict[str, str]] = {}
    names: dict[str, dict[str, str]] = {}
    for collection, kept in _load(colours_path(engine, settings.account.account_id)).calendars.items():
        here: dict[str, str] = {}
        named: dict[str, str] = {}
        for key, (_, colour, label) in kept.events.items():
            given, found = kept.labels.get(label, ("", "")) if label else ("", "")
            if not found and colour in GOOGLE_EVENT_COLOURS:
                found = GOOGLE_EVENT_COLOURS[colour]
            name, shade = colour_tag(given, found)
            if shade:
                here[key] = shade
            if name:
                named[key] = name
        colours[collection] = here
        names[collection] = named
    return GoogleShown(colours, names)


def colours_in_use(kept: Kept) -> dict[str, tuple[str, str]]:
    """Every colour an account's events carry, and every colour the person named in Google: the tag it is here, by its name without regard to
    case -> (the name as written, its colour). That is the list the tags of the account follow (D-339)."""
    found: dict[str, tuple[str, str]] = {}
    for calendar in kept.calendars.values():
        for given, colour in calendar.labels.values():
            if given:
                name, shade = colour_tag(given, colour)
                if name:
                    found[name.casefold()] = (name, shade)
        for _, colour, label in calendar.events.values():
            given, shade = calendar.labels.get(label, ("", "")) if label else ("", "")
            if not shade and colour in GOOGLE_EVENT_COLOURS:
                shade = GOOGLE_EVENT_COLOURS[colour]
            name, shade = colour_tag(given, shade)
            if name:
                found[name.casefold()] = (name, shade)
    return found


def google_colour(shown: dict[str, dict[str, str]], collection: str, uid: str, recurrence_id: Moment | None) -> str:
    """The colour Google gives one occurrence: that of its changed date when Google has one, else that of its series; empty: none."""
    colours = shown.get(collection, {})
    if recurrence_id is not None:
        own = colours.get(event_key(uid, recurrence_id), "")
        if own:
            return own
    return colours.get(uid, "")


def google_name(shown: GoogleShown, collection: str, uid: str, recurrence_id: Moment | None) -> str:
    """The name of the label Google gives one occurrence. A changed date that Google colours itself answers for itself, name or none: the name of
    the series belongs to the series' colour, which that date does not have (D-338)."""
    names = shown.names.get(collection, {})
    if recurrence_id is not None:
        own = event_key(uid, recurrence_id)
        if own in shown.colours.get(collection, {}):
            return names.get(own, "")
    return names.get(uid, "")


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


def _read(access: str, calendar: str, kept: CalendarColours, labels_too: bool = True) -> int:
    """Google's labels and colours into what is kept; returns how many events look different here. Right after one save the labels are not asked
    again: they change when the person works in Google Calendar, which the next synchronisation sees."""
    before_labels = dict(kept.labels)
    labels = before_labels
    if labels_too:
        try:
            labels = {label.label_id: (label.name, label.colour) for label in read_labels(access, calendar)}
        except CalendarUnknown:
            raise
        except GoogleRefused:
            labels = before_labels  # a calendar that tells no more than its events (one shared as free/busy): its colours still come
    try:
        read = read_colours(access, calendar, kept.token)
    except SyncTokenGone:
        read = read_colours(access, calendar)
    kept.labels = labels  # only now: a read that failed must leave the labels to be compared again next time
    before: dict[str, tuple[str, str, str]] = dict(kept.events)
    events: dict[str, tuple[str, str, str]] = {} if read.whole else dict(kept.events)
    for event in read.events:
        if event.cancelled:  # deleted on Google: known by Google's id (a deleted entry need not name its UID)
            for gone in [key for key, (event_id, _, _) in events.items() if event_id == event.event_id]:
                del events[gone]
            continue
        key = f"{event.uid} {event.original_start}" if event.original_start else event.uid
        events[key] = (event.event_id, event.colour_id, event.label_id)
    kept.events = events
    kept.token = read.sync_token
    nothing = ("", "", "")
    changed = sum(1 for key in set(before) | set(events)
                  if before.get(key, nothing)[1:] != events.get(key, nothing)[1:])
    if before_labels != kept.labels:  # a label renamed or recoloured: every event that carries it looks different
        moved = {label for label, _ in set(before_labels.items()) ^ set(kept.labels.items())}
        changed += sum(1 for key, (_, _, label) in events.items() if label in moved and before.get(key, nothing)[1:] == events[key][1:])
    return changed


def _send(access: str, calendar: str, kept: CalendarColours, tags: dict[str, str], colour_of_tag: dict[str, str], result: ColourSync, whole: bool) -> bool:
    """Every colour tag changed since it was last in step with Google, sent there first; False when Google could not be reached (the rest waits
    for the next synchronisation). Whole: tags holds every event of the calendar, so one not in it was deleted here."""
    for key, tag in tags.items():
        known = kept.events.get(key)
        if key not in kept.tags:
            if not kept.compared or not tag or known is None or known[1] or known[2]:
                if known is not None or not kept.compared:
                    kept.tags[key] = tag  # from before this version, or coloured on Google already: remembered only, never sent
                continue  # not on Google yet (made offline, sent by CalDAV first): compared again once the next read brings its id
        elif kept.tags[key] == tag:
            continue
        if known is None:
            continue  # not on Google yet: the next read brings its id
        label = _label_of(kept, tag)
        wanted = "" if label or not tag else google_colour_nearest(colour_of_tag.get(" ".join(tag.split()).casefold(), NEW_TAG))
        try:
            if label:
                set_label(access, calendar, known[0], label)
            else:
                set_colour(access, calendar, known[0], wanted)  # Google puts the label of that colour on it itself, or takes the label away (live)
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
        if (known[1], known[2]) != (wanted, label):
            kept.events[key] = (known[0], wanted, label)
            result.recoloured += 1
    if whole:
        for key in [key for key in kept.tags if key not in tags]:
            del kept.tags[key]  # deleted here: nothing left to compare
        kept.compared = True
    return True


def _label_of(kept: CalendarColours, tag: str) -> str:
    """The label of this calendar the person named like this tag (D-338); empty when the calendar has no such label."""
    wanted = " ".join(tag.split()).casefold()
    if not wanted:
        return ""
    return next((label for label, (name, _) in sorted(kept.labels.items()) if name.casefold() == wanted), "")


def _tag_the_items(store: PimStore, kept: Kept, only: tuple[str, ...] = ()) -> None:
    """Give every event of the account the tag of the colour Google gives it, in the index (D-339). The index is never the truth: it is written
    from the items and from this, and a rebuild gets it back at the next synchronisation. Nothing is written into the event itself, so Google
    keeps one place for its colour."""
    for collection, calendar in kept.calendars.items():
        if only and collection not in only:
            continue
        names: dict[str, str] = {}
        for key, (_, colour, label) in calendar.events.items():
            if " " in key:
                continue  # one changed date of a series: the index holds one row for the whole series, so its colour shows in the calendar only
            given, shade = calendar.labels.get(label, ("", "")) if label else ("", "")
            if not shade and colour in GOOGLE_EVENT_COLOURS:
                shade = GOOGLE_EVENT_COLOURS[colour]
            name, _ = colour_tag(given, shade)
            if name:
                names[key] = name
        store.index.keep_other_tags(collection, names)


def _named_labels(calendars: dict[str, CalendarColours]) -> dict[str, tuple[str, str]]:
    """Every label the person named, over all calendars of the account: the name without regard to case -> (the name as written, its colour).
    Two calendars with a label of the same name give one tag; each appointment still shows its own calendar's colour."""
    return {name.casefold(): (name, colour) for kept in calendars.values() for name, colour in kept.labels.values() if name}


def _keep_tag_list(engine: Engine, kept: Kept, after: dict[str, tuple[str, str]], result: ColourSync) -> None:
    """The tag list follows Google's named labels: a label the person named is a tag of its colour here. What the person chose is theirs: a tag
    they made, coloured, deleted or set to other kinds of item themselves is never changed back, and one with a shortcut key stays (D-338, OR-9).
    The colour written here is remembered, so next time it can be told from a colour the person gave."""
    written = kept.label_tags
    if after == written:
        return  # nothing of Google's changed since the last time: the tag list is not even read
    kinds: tuple[str, ...] = ("events",)  # a tag of Google's is for appointments: never in the mail tag menus
    try:
        tags = load_tags(engine.directories.tags_file)
        changed, now = tags, dict(written)  # what is written is kept only once the tag list really went to disk
        for key, (name, colour) in after.items():
            entry = changed.entry(name)
            if entry is None:
                if key in written:
                    continue  # the person deleted this tag: it never comes back by itself
                changed = changed.with_colour(name, colour).with_details(name, "", kinds)
                now[key] = (name, colour)
            elif key in written and entry.colour.lower() == written[key][1].lower():
                if entry.colour.lower() != colour.lower() or entry.shown != name:
                    changed = changed.with_colour(name, colour)  # ours and untouched: it follows the label, also when only its capitals changed
                now[key] = (name, colour)
            # else: the person made that tag, or gave it a colour of their own. It is theirs; Google's colour still colours the appointments.
        for key in [key for key in written if key not in after]:
            entry = changed.entry(key)
            if entry is not None and entry.colour.lower() == written[key][1].lower() and not entry.shortcut and entry.kinds == kinds:
                changed = changed.with_colour(entry.shown, "")  # ours and untouched: it goes with the label
            del now[key]
        if changed != tags:
            save_tags(engine.directories.tags_file, changed)
    except (ChronoMailError, OSError) as error:  # the tag list could not be read or written: the colours still show, the names are tried again
        result.errors.append(f"The tags of Google's colours: {error}")
        return
    kept.label_tags = now


def _tag_colours(engine: Engine) -> dict[str, str]:
    return {entry.key: entry.colour for entry in load_tags(engine.directories.tags_file).entries}


def google_colour_off(engine: Engine, account: str, collection: str, uid: str) -> None:
    """The person took the colour's tag off an appointment in its window: Google loses that colour (D-339). It is remembered here as "no tag", so
    the next synchronisation sends it even when Google cannot be reached now."""
    path = colours_path(engine, account)
    with _LOCK:
        kept = _load(path)
        calendar = kept.calendars.get(collection)
        if calendar is None or event_key(uid) not in calendar.events:
            return
        calendar.tags[event_key(uid)] = "\x00 taken off"  # never a tag's name: the next comparison sees a change and sends the empty colour
        try:
            _save(engine, path, kept)
        except OSError:
            pass  # the colour stays as it is; nothing here is lost


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
                kept = kept_all.calendars.setdefault(info.collection_id, CalendarColours())
                if kept.unknown or (items and not kept.compared):
                    continue  # right after a save only a calendar read before is asked (one small question): the first read is the synchronisation's
                try:
                    result.recoloured += _read(access, calendar, kept, labels_too=not items)
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
            for gone in [key for key in kept_all.calendars if key not in {info.collection_id for info in store.collections()}]:
                del kept_all.calendars[gone]  # a calendar no longer here: its colours go with it
            _keep_tag_list(engine, kept_all, colours_in_use(kept_all), result)
            _tag_the_items(store, kept_all, tuple(info.collection_id for info, _ in calendars))
        finally:
            try:
                _save(engine, path, kept_all)
            except OSError as error:  # a full disk: Google's colours are read again next time, a sent colour compared again
                result.errors.append(f"Event colours could not be kept on this computer: {error}")
    return result

