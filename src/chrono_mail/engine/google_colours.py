"""The colours of the events of a Google calendar (D-337). Google's CalDAV does not carry them; its Calendar API does.

Reading: at every synchronisation of a Google Calendar account each calendar's labels (the colours the person made in Google Calendar, D-338) and
its events' colours are read (after the first time only what changed) and kept on this computer, in google-colours.json beside the items; an event
without tags shows Google's colour (D-329: another program's colour). Every colour of a calendar is a tag of that calendar (D-341): its name, a
vertical bar and the calendar's Google id, so two calendars' colours of one name stay two tags, shown as "Rood (Esther)". They are never in the
person's tag list and never change a tag the person made; they are renamed and recoloured in Google Calendar. Nothing is written into the event.

Writing, server first (D-193): the colour tag of an event (the first of its tags) is compared with the one that was last in step with Google. When
the person changed it, Google gets the label of that name when the calendar has one, else the nearest of Google's eleven colours (no tag: the
calendar's own colour again), and only then is the new one remembered. A change made offline is the same comparison at the next synchronisation;
nothing waits in a queue (as IMAP, D-050). The first time an event is seen its tag is only remembered, so tags given before never repaint Google's
colours. A calendar tag chosen here for an event goes to Google first in the same way (D-341)."""

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from chrono_mail.config import AccountSettings, load_tags
from chrono_mail.core import AuthenticationError, ChronoMailError, CollectionInfo, CollectionKind, ProtocolError
from chrono_mail.core.colours import GOOGLE_COLOUR_NAMES, GOOGLE_EVENT_COLOURS, NEW_TAG, google_colour_nearest
from chrono_mail.core.jsontext import load_json
from chrono_mail.core.tagnames import calendar_tag, shown_tag, tag_parts
from chrono_mail.engine.google_access import google_bearer
from chrono_mail.engine.session import Engine
from chrono_mail.pim.ical import Moment, parse_events
from chrono_mail.pimstore import ItemQuery, PimStore
from chrono_mail.protocols.google import CalendarUnknown, EventGone, GoogleRefused, SyncTokenGone, calendar_id_of, read_colours, read_labels, set_colour, set_label
from chrono_mail.store.atomic import replace_file_atomically

# --- Version ------------------------------------------------------------
__version__ = "google_colours.py 4.4.602"

# --- Version history ----------------------------------------------------
# v4.4.602: A Google colour is a tag of its own calendar (name|calendar id), never in the person's tag list; a calendar tag chosen here goes to Google first (D-341)
# v4.4.601: What is in Google counts: a tag whose name is a Google colour always has that colour, and two colours of one name are one tag of one colour (D-340)
# v4.4.600: Every colour of a Google calendar is a tag here, on the appointments that carry it and in the tag list; taking it off in the window takes the colour off Google (D-339)
# v4.4.599: Google's labels (the colours a person made and named in Google Calendar): a named one is a tag here with its colour; a tag of that name goes back as that label (D-338)
# v4.4.598: Initial: Google's event colours read at every synchronisation and shown on events without tags; a changed colour tag sent to Google first (D-337)

FILE_NAME: str = "google-colours.json"
FORMAT: int = 3  # 1 (4.4.598): events without their label, no labels of their own; 2 (4.4.599): no calendar id, nothing chosen here
_LOCK = threading.Lock()  # one read-change-write of the file at a time (the account's synchronisation lock covers the server; this the file)


@dataclass
class CalendarColours:
    token: str = ""  # where the last read of the calendar ended
    events: dict[str, tuple[str, str, str]] = field(default_factory=dict)  # key -> (Google's id of the event, Google's colour number or "", its label or "")
    labels: dict[str, tuple[str, str]] = field(default_factory=dict)  # Google's label -> (the name the person gave it or "", its colour "#rrggbb")
    tags: dict[str, str] = field(default_factory=dict)  # key -> the colour tag last in step with Google ("": none)
    unknown: bool = False  # the Calendar API does not know this calendar (Google's holiday calendars): never asked again
    compared: bool = False  # its events were compared once: what is first seen after that is new, not from before this version
    calendar: str = ""  # the calendar's Google id: the part of its tags after the bar (D-341)
    wanted: dict[str, str] = field(default_factory=dict)  # key -> a tag of this calendar chosen here for the event, not on Google yet
    leads: dict[str, str] = field(default_factory=dict)  # key -> the event's first own tag when its calendar tag was made its colour tag here


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
    """What is kept for one account: its calendars."""

    calendars: dict[str, CalendarColours] = field(default_factory=dict)


def _load(path: Path) -> Kept:
    try:
        data = load_json(path.read_text("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return Kept()  # none yet, or damaged: read again from Google (it is never the truth)
    if not isinstance(data, dict) or data.get("format") not in (1, 2, FORMAT) or not isinstance(data.get("calendars"), dict):
        return Kept()
    older = data.get("format") == 1  # 4.4.598 knew no labels: everything is read again once, or the events it already has would never get theirs
    found: dict[str, CalendarColours] = {}
    for collection, raw in data["calendars"].items():
        if not isinstance(raw, dict):
            continue
        events = raw.get("events", {})
        labels = raw.get("labels", {})
        tags = raw.get("tags", {})
        wanted = raw.get("wanted", {})
        leads = raw.get("leads", {})
        found[str(collection)] = CalendarColours(
            "" if older else str(raw.get("token", "")),
            {str(key): (str(value[0]), str(value[1]), str(value[2]) if len(value) > 2 else "")  # a file of 4.4.598 (format 1) knows no labels yet
             for key, value in events.items() if isinstance(value, list) and len(value) in (2, 3)} if isinstance(events, dict) else {},
            {str(key): (str(value[0]), str(value[1])) for key, value in labels.items() if isinstance(value, list) and len(value) == 2} if isinstance(labels, dict) else {},
            {str(key): str(value) for key, value in tags.items()} if isinstance(tags, dict) else {},
            raw.get("unknown") is True,
            raw.get("compared") is True,
            str(raw.get("calendar", "")),
            {str(key): str(value) for key, value in wanted.items()} if isinstance(wanted, dict) else {},
            {str(key): str(value) for key, value in leads.items()} if isinstance(leads, dict) else {},
        )
    return Kept(found)


def _save(engine: Engine, path: Path, kept: Kept) -> None:
    calendars = kept.calendars
    data = {"format": FORMAT, "calendars": {
        collection: {"token": kept.token, "events": {key: list(value) for key, value in kept.events.items()},
                     "labels": {key: list(value) for key, value in kept.labels.items()}, "tags": kept.tags, "unknown": kept.unknown, "compared": kept.compared,
                     "calendar": kept.calendar, "wanted": kept.wanted, "leads": kept.leads}
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
    """What Google gives the events of an account, per calendar: event key -> its colour ("#rrggbb"), -> its tag (the identifier of a tag of
    that calendar, D-341), and the keys whose calendar tag was made their colour tag here (it leads their own tags)."""

    colours: dict[str, dict[str, str]] = field(default_factory=dict)
    names: dict[str, dict[str, str]] = field(default_factory=dict)
    leads: dict[str, frozenset[str]] = field(default_factory=dict)


def in_use(calendar: CalendarColours) -> dict[str, tuple[str, str]]:
    """Every colour a calendar's events carry and every colour the person named in Google: the tag it is, by its name without regard to case
    -> (the name as written, its colour). Two colours of one name are one tag of one colour within a calendar (D-340); another calendar's
    colour of that name is another tag (D-341)."""
    found: dict[str, tuple[str, str]] = {}
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
            found.setdefault(name.casefold(), (name, shade))
    return found


def _event_name(calendar: CalendarColours, colour: str, label: str) -> tuple[str, str]:
    """The name of the colour one event carries and the colour Google shows for it."""
    given, found = calendar.labels.get(label, ("", "")) if label else ("", "")
    if not found and colour in GOOGLE_EVENT_COLOURS:
        found = GOOGLE_EVENT_COLOURS[colour]
    return colour_tag(given, found)


def shown_colours(engine: Engine, settings: AccountSettings) -> GoogleShown:
    """What Google gives this account's events; empty for any other account. Every colour of a Google calendar is a tag of that calendar
    (D-339, D-341), and that tag has one colour (D-340). A calendar tag chosen here and not on Google yet shows at once."""
    if not settings.google:
        return GoogleShown()
    kept_all = _load(colours_path(engine, settings.account.account_id))
    colours: dict[str, dict[str, str]] = {}
    names: dict[str, dict[str, str]] = {}
    leads: dict[str, frozenset[str]] = {}
    for collection, kept in kept_all.calendars.items():
        of_tag = in_use(kept)
        here: dict[str, str] = {}
        named: dict[str, str] = {}
        for key, (_, colour, label) in kept.events.items():
            name, shade = _event_name(kept, colour, label)
            if name and kept.calendar:
                named[key] = calendar_tag(name, kept.calendar)
                shade = of_tag.get(name.casefold(), ("", shade))[1]  # the tag's own colour: one tag, one colour
            if shade:
                here[key] = shade
        for key, tag in kept.wanted.items():  # chosen here, on its way to Google: it shows at once (D-325)
            name, calendar = tag_parts(tag)
            if calendar == kept.calendar and name.casefold() in of_tag:
                named[key] = tag
                here[key] = of_tag[name.casefold()][1]
        colours[collection] = here
        names[collection] = named
        leads[collection] = frozenset(key for key in (*kept.leads, *kept.wanted) if key in named)
    return GoogleShown(colours, names, leads)


@dataclass(frozen=True, slots=True)
class CalendarTag:
    """A tag of a Google calendar, for the tag list: its identifier, its name, its colour, how it is shown and the calendar it belongs to."""

    tag: str
    name: str
    colour: str
    shown: str
    collection: str


def calendar_tags(engine: Engine, settings: AccountSettings, store: PimStore) -> list[CalendarTag]:
    """Every tag of this account's Google calendars, calendar by calendar in the order the sidebar has them, each by name (D-341)."""
    if not settings.google:
        return []
    kept_all = _load(colours_path(engine, settings.account.account_id))
    found: list[CalendarTag] = []
    for info in store.collections(CollectionKind.EVENTS):
        kept = kept_all.calendars.get(info.collection_id)
        if kept is None or not kept.calendar:
            continue
        for name, colour in sorted(in_use(kept).values(), key=lambda pair: pair[0].casefold()):
            found.append(CalendarTag(calendar_tag(name, kept.calendar), name, colour, shown_tag(name, info.name), info.collection_id))
    return found


def calendar_has(engine: Engine, account: str, collection: str, tag: str) -> bool:
    """Whether this tag is one of the calendar's now (its colour is still in Google)."""
    kept = _load(colours_path(engine, account)).calendars.get(collection)
    name, owner = tag_parts(tag)
    return kept is not None and bool(kept.calendar) and owner == kept.calendar and name.casefold() in in_use(kept)


def calendar_id(engine: Engine, account: str, collection: str) -> str:
    """The Google id of one of an account's calendars, as its tags carry it; empty before its first synchronisation or for any other calendar."""
    kept = _load(colours_path(engine, account)).calendars.get(collection)
    return kept.calendar if kept is not None else ""


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


def google_leads(shown: GoogleShown, collection: str, uid: str, recurrence_id: Moment | None) -> bool:
    """Whether the calendar tag of this occurrence was made its colour tag here, before its own tags (D-341)."""
    leads = shown.leads.get(collection, frozenset())
    if recurrence_id is not None and event_key(uid, recurrence_id) in shown.colours.get(collection, {}):
        return event_key(uid, recurrence_id) in leads
    return uid in leads


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
    if not _send_chosen(access, calendar, kept, result):
        return False
    for key, tag in tags.items():
        known = kept.events.get(key)
        if key in kept.leads:
            if kept.leads[key] == tag:
                continue  # its calendar tag was made its colour tag here: its own tags do not colour it on Google
            del kept.leads[key]  # its first own tag changed since (on another computer): that tag colours it again
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
        for chosen in (kept.wanted, kept.leads):
            for key in [key for key in chosen if key not in tags]:
                del chosen[key]
        kept.compared = True
    return True


def _send_chosen(access: str, calendar: str, kept: CalendarColours, result: ColourSync) -> bool:
    """Every calendar tag chosen here for an event, sent to Google first: its label, or for a colour the person never named the one of Google's
    eleven it is named after; False when Google could not be reached (it waits for the next synchronisation, D-341)."""
    for key, tag in list(kept.wanted.items()):
        known = kept.events.get(key)
        if known is None:
            continue  # not on Google yet (made here a moment ago): the next read brings its id
        name, owner = tag_parts(tag)
        label = _label_of(kept, name)
        colour = "" if label else next((number for number, standard in GOOGLE_COLOUR_NAMES.items() if standard.casefold() == name.casefold()), "")
        if owner != kept.calendar or (not label and not colour):
            del kept.wanted[key]  # the colour is gone from Google meanwhile: nothing to send, the event keeps what Google gives it
            kept.leads.pop(key, None)
            continue
        try:
            if label:
                set_label(access, calendar, known[0], label)
            else:
                set_colour(access, calendar, known[0], colour)
        except EventGone:
            del kept.wanted[key]
            continue
        except GoogleRefused as error:
            del kept.wanted[key]  # asking again gives the same answer: Google keeps its colour
            kept.leads.pop(key, None)
            result.errors.append(f"Google kept its colour of an event ({error})")
            continue
        except AuthenticationError:
            raise
        except ProtocolError:
            return False
        del kept.wanted[key]
        kept.events[key] = (known[0], colour, label)
        result.recoloured += 1
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
        if calendar.calendar:  # its id comes with its first synchronisation: until then its colours show without a tag
            for key, (_, colour, label) in calendar.events.items():
                if " " in key:
                    continue  # one changed date of a series: the index holds one row for the whole series, so its colour shows in the calendar only
                name, _ = _event_name(calendar, colour, label)
                if name:
                    names[key] = calendar_tag(name, calendar.calendar)
            for key, tag in calendar.wanted.items():
                if " " not in key and tag_parts(tag)[1] == calendar.calendar:
                    names[key] = tag  # chosen here, on its way to Google: counted and found at once
        store.index.keep_other_tags(collection, names)


def _tag_colours(engine: Engine) -> dict[str, str]:
    return {entry.key: entry.colour for entry in load_tags(engine.directories.tags_file).entries}


def _remember(engine: Engine, account: str, collection: str, change: Callable[[CalendarColours], bool]) -> bool:
    """One change of what is kept for one calendar, written to disk; False when the calendar has no Google colours here or nothing changed."""
    path = colours_path(engine, account)
    with _LOCK:
        kept = _load(path)
        calendar = kept.calendars.get(collection)
        if calendar is None or not change(calendar):
            return False
        try:
            _save(engine, path, kept)
        except OSError:
            return False  # the colour stays as it is; nothing here is lost
        return True


def google_colour_off(engine: Engine, account: str, collection: str, uid: str, recurrence_id: Moment | None = None) -> None:
    """The person took the colour's tag off an appointment in its window: Google loses that colour (D-339). It is remembered here as "no tag", so
    the next synchronisation sends it even when Google cannot be reached now."""
    key = event_key(uid, recurrence_id)

    def off(calendar: CalendarColours) -> bool:
        if key not in calendar.events:
            return False
        calendar.tags[key] = "\x00 taken off"  # never a tag's name: the next comparison sees a change and sends the empty colour
        calendar.wanted.pop(key, None)
        calendar.leads.pop(key, None)
        return True

    _remember(engine, account, collection, off)


def google_colour_choose(engine: Engine, account: str, collection: str, uid: str, recurrence_id: Moment | None, tag: str, first_own: str, send: bool = True) -> bool:
    """The person made a tag of this calendar the colour tag of an appointment (D-341): it goes to Google first, at the save or at the next
    synchronisation, and leads the appointment's own tags (first_own, the first of them, or "") until another one is made its colour tag.
    send=False: the tag it carries already (a date that follows its series): it only leads, nothing goes to Google. False when the tag is not
    one of this calendar's."""
    key = event_key(uid, recurrence_id)
    name, owner = tag_parts(tag)

    def choose(calendar: CalendarColours) -> bool:
        known = in_use(calendar)
        if not calendar.calendar or owner != calendar.calendar or name.casefold() not in known:
            return False
        current = calendar.events.get(key)
        now = _event_name(calendar, current[1], current[2])[0] if current is not None else ""
        if now.casefold() == name.casefold():
            calendar.wanted.pop(key, None)  # Google has it on the event already
        elif send:
            calendar.wanted[key] = calendar_tag(known[name.casefold()][0], owner)  # (unchanged: a choice still on its way to Google stays so)
        calendar.leads[key] = first_own
        calendar.tags[key] = first_own  # its own tags are in step: they do not colour it on Google
        return True

    return _remember(engine, account, collection, choose)


def google_colour_own_tag_leads(engine: Engine, account: str, collection: str, uid: str, recurrence_id: Moment | None) -> None:
    """The person made one of the appointment's own tags its colour tag again, after a calendar tag led it: that tag goes to Google (D-341)."""
    key = event_key(uid, recurrence_id)

    def own(calendar: CalendarColours) -> bool:
        if key not in calendar.leads and key not in calendar.wanted:
            return False
        calendar.leads.pop(key, None)
        calendar.wanted.pop(key, None)
        calendar.tags[key] = "\x00 changed"  # never a tag's name: the next comparison sends the own colour tag
        return True

    _remember(engine, account, collection, own)


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
                kept.calendar = calendar  # the part of its tags after the bar (D-341)
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
            _tag_the_items(store, kept_all, tuple(info.collection_id for info, _ in calendars))
        finally:
            try:
                _save(engine, path, kept_all)
            except OSError as error:  # a full disk: Google's colours are read again next time, a sent colour compared again
                result.errors.append(f"Event colours could not be kept on this computer: {error}")
    return result

