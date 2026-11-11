from dataclasses import dataclass

from chrono_mail.config import load_tags, save_tags
from chrono_mail.core import ChronoMailError, CollectionKind, ConfigurationError
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_calendar import EditParameters, event_edit
from chrono_mail.engine.pimaccess import open_pim, palette_for, pim_account_ids
from chrono_mail.engine.registry import CommandRegistry, TypedCommand
from chrono_mail.engine.session import Engine
from chrono_mail.pim.ical import parse_events
from chrono_mail.pim.times import format_moment
from chrono_mail.pimstore import ItemQuery
from chrono_mail.store.atomic import replace_file_atomically

# --- Version ------------------------------------------------------------
__version__ = "commands_colourtags.py 4.4.573"

# --- Version history ----------------------------------------------------
# v4.4.573: Initial event.colours-to-tags: every appointment colour in use becomes a tag of the same name and colour, given to the appointment as its colour tag (D-329)

DONE_FILE: str = "colours-to-tags.done"  # in an account's calendar store: every appointment of it has its colour as a tag
EVERY: int = 10**6


@dataclass(frozen=True, slots=True)
class ColoursToTags:
    appointments: int  # appointments that got their colour as a tag
    failed: int  # appointments that could not be changed now (tried again at the next start)


def _unique(name: str, colour: str, taken: dict[str, str]) -> str:
    """The tag for a colour: its own name, unless a tag of that name has another colour; then the name with a number (Work 2)."""
    number = 1
    candidate = name
    while candidate.casefold() in taken and taken[candidate.casefold()].lower() != colour.lower():
        number += 1
        candidate = f"{name} {number}"
    return candidate


def colours_to_tags(engine: Engine, parameters: NoParameters) -> ColoursToTags:
    """Every appointment with a colour of its account's palette gets a tag of that colour's name and colour, first among its tags so it gives
    the colour, and loses the colour (D-329). Each appointment is changed as an edit is: on the server first, offline here and sent later. An
    account is marked done once every appointment of it was changed; one that failed is tried again at the next start."""
    changed = failed = 0
    for account in pim_account_ids(engine):
        try:
            with open_pim(engine, account) as store:
                marker = store.root / DONE_FILE
                if marker.exists():
                    continue
                settings = engine.config().find(account)
                wanted: list[tuple[str, str, str, str, tuple[str, ...]]] = []  # item, changed date ('' for the whole), tag name, colour, its tags now
                for info in store.collections(CollectionKind.EVENTS):
                    if info.read_only:
                        continue
                    palette = {entry.colour_id: entry for entry in palette_for(settings, info)[1:]}  # the calendar's own colour is no colour of the appointment
                    for item in store.index.query(ItemQuery(CollectionKind.EVENTS, (info.collection_id,), limit=EVERY)):
                        try:
                            series = parse_events(store.get(item.collection, item.item_id))
                        except ChronoMailError:
                            continue  # a damaged item fails only itself
                        for one in series[:1]:
                            for event, occurrence in ((one.master, ""), *((change, format_moment(change.recurrence_id)) for change in one.overrides if change.recurrence_id is not None)):
                                if event.colour_id and event.colour_id in palette:  # a changed date with a colour of its own too
                                    entry = palette[event.colour_id]
                                    wanted.append((item.item_id, occurrence, entry.name.strip() or "Colour", entry.colour, event.categories))
        except ChronoMailError:
            failed += 1
            continue
        missed = 0
        for item, occurrence, name, colour, tags in wanted:
            try:
                colours = load_tags(engine.directories.tags_file)
                taken = {key: shown_colour for key, _, shown_colour in colours.colours}
                tag = _unique(name, colour, taken)
                if tag.casefold() not in taken:
                    save_tags(engine.directories.tags_file, colours.with_colour(tag, colour))
            except ConfigurationError:
                missed += 1  # the tags file cannot take it now: tried again at the next start, its colour still shows meanwhile
                continue
            ordered = (tag, *(other for other in tags if other.casefold() != tag.casefold()))
            try:
                event_edit(engine, EditParameters(item=item, occurrence=occurrence, tag=ordered, colour=""))
                changed += 1
            except ChronoMailError:
                missed += 1
        failed += missed
        if not missed:
            try:
                with open_pim(engine, account) as store:
                    replace_file_atomically(store.root / DONE_FILE, b"done\n", durable=True)
            except ChronoMailError:
                pass  # looked at again at the next start: an appointment without a colour is skipped then
    return ColoursToTags(changed, failed)


EVENT_COLOURS_TO_TAGS: TypedCommand[NoParameters, ColoursToTags] = TypedCommand("event.colours-to-tags", NoParameters, ColoursToTags)


def register(registry: CommandRegistry) -> None:
    registry.register(EVENT_COLOURS_TO_TAGS.id, "Turn every appointment colour in use into a tag of the same name and colour (once per account)", NoParameters, colours_to_tags, doing="Turning appointment colours into tags")
