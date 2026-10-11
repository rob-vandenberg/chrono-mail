from collections.abc import Callable
from dataclasses import replace

from chrono_mail.core import CollectionKind
from chrono_mail.pim.contact import contact_to_text, parse_contacts
from chrono_mail.pim.ical import events_to_text, parse_events, parse_tasks, tasks_to_text

# --- Version ------------------------------------------------------------
__version__ = "edit.py 0.0.63"

# --- Version history ----------------------------------------------------
# v0.0.63: The tasks section
# v0.0.60: Initial changes to the tags of an item

Retag = Callable[[tuple[str, ...]], tuple[str, ...]]


def changed_tags(tags: tuple[str, ...], add: tuple[str, ...], remove: tuple[str, ...]) -> tuple[str, ...]:
    """The tags with some added (a tag that is there already, in any case, is not added twice) and some taken away (without regard to case)."""
    gone = {" ".join(name.split()).casefold() for name in remove}
    kept = [tag for tag in tags if tag.casefold() not in gone]
    for name in add:
        cleaned = " ".join(name.split())
        if cleaned and cleaned.casefold() not in {tag.casefold() for tag in kept}:
            kept.append(cleaned)
    return tuple(kept)


def tags_of(kind: CollectionKind, text: str) -> tuple[str, ...]:
    """The tags (categories) of the first item in a text."""
    if kind is CollectionKind.CONTACTS:
        contacts = parse_contacts(text)
        return contacts[0].categories if contacts else ()
    if kind is CollectionKind.EVENTS:
        series = parse_events(text)
        return series[0].master.categories if series else ()
    tasks = parse_tasks(text)
    return tasks[0].categories if tasks else ()


def retag_item(kind: CollectionKind, text: str, change: Retag) -> str:
    """The item with its tags changed by `change` (given the tags it has, returns the tags it is to have). The text is returned as it was when nothing
    changes, or when it cannot be read."""
    if kind is CollectionKind.CONTACTS:
        contacts = parse_contacts(text)
        if not contacts or change(contacts[0].categories) == contacts[0].categories:
            return text
        return contact_to_text(replace(contacts[0], categories=change(contacts[0].categories)))
    if kind is CollectionKind.EVENTS:
        series = parse_events(text)
        if not series or change(series[0].master.categories) == series[0].master.categories:
            return text
        return events_to_text(replace(series[0], master=replace(series[0].master, categories=change(series[0].master.categories))), text)
    tasks = parse_tasks(text)
    if not tasks or change(tasks[0].categories) == tasks[0].categories:
        return text
    return tasks_to_text(replace(tasks[0], categories=change(tasks[0].categories)), text)
