"""Tags for mail: give messages tags, make and delete tags, and the mail side of listing and renaming them.

A tag is a name with a colour. On mail the names live in the tags file of each mailbox (store/tags.py); on contacts,
events and tasks in the items themselves; the colours of all of them in tags.json (D-073, D-128)."""

from dataclasses import dataclass, field
from typing import Literal

from chrono_mail.config import TAG_KINDS, load_tags, save_tags
from chrono_mail.core import ChronoMailError, ConfigurationError, StorageError
from chrono_mail.core.colours import NEW_TAG
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import open_pim, pim_account_ids
from chrono_mail.engine.pim_server import item_writer
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine, quiet_command
from chrono_mail.pim.edit import retag_item
from chrono_mail.store import clean_tags

# --- Version ------------------------------------------------------------
__version__ = "commands_tags.py 1.9.271"

# --- Version history ----------------------------------------------------
# v1.9.271: R-19: typed tag.create, tag.edit, tag.delete
# v1.9.270: R-19: typed message.tag
# v1.1.225: a tag deleted from the items goes through item_writer: on their server first (R-23, D-194)
# v1.1.206: The tag counts come from the messages' properties files (D-176)
# v1.1.172: A new tag's colour from the colour file (D-158)
# v0.5.156: Delete a tag everywhere is a big job: one "mailbox changed" per mailbox at its end (D-146)
# v0.5.136: give_colours is used by the rules too
# v0.5.135: tag.edit: a tag's shortcut key, what it is for, and its place in the list
# v0.5.134: Initial version: message.tag, tag.create, tag.delete; mail counts and renaming for tag.list and tag.rename

DEFAULT_COLOUR: str = NEW_TAG
MAXIMUM_MESSAGES: int = 10_000  # per message.tag


def _name(text: str) -> str:
    name = " ".join(text.split())
    if not name:
        raise UsageError("A tag needs a name")
    if len(name) > 100:
        raise UsageError("A tag has at most 100 characters")
    return name


def mail_places(engine: Engine) -> list[str]:
    """The id of every place where mail is kept (each account with its own folders, and Local Folders once)."""
    return list(engine.storage_groups())


def mail_tag_counts(engine: Engine) -> dict[str, tuple[str, int]]:
    """Folded name -> (name as first written, how many messages carry it), over all mail. A mailbox whose tags file is
    damaged is left out of the count (a message whose properties file is damaged: that message)."""
    config = engine.config()
    counts: dict[str, tuple[str, int]] = {}
    for place in mail_places(engine):
        try:
            store = engine.open_store(config.find(place), config)
            tagged = store.properties.tags()
        except ChronoMailError:
            continue
        for tags in tagged.values():
            for tag in tags:
                shown, count = counts.get(tag.casefold(), (tag, 0))
                counts[tag.casefold()] = (shown, count + 1)
    return counts


def rename_in_mail(engine: Engine, old: str, new: str | None) -> int:
    """Rename a tag in all mail (None removes it). Returns how many messages changed."""
    changed = 0
    for place in mail_places(engine):
        with open_mailbox(engine, place) as mailbox:
            changed += mailbox.rename_tag(old, new)
    return changed


# --- message.tag ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MailTagParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder the messages are in"))
    messages: tuple[str, ...] = field(metadata=option("The message ids (repeat for more)"))
    add: tuple[str, ...] = field(default=(), metadata=option("Tags to add (repeat for more)"))
    remove: tuple[str, ...] = field(default=(), metadata=option("Tags to take away (repeat for more)"))
    replace: bool = field(default=False, metadata=option("Give the messages exactly the tags of add (all others go)"))


@dataclass(frozen=True, slots=True)
class MessageTagsResult:
    message: str
    tags: tuple[str, ...]


def message_tag(engine: Engine, parameters: MailTagParameters) -> tuple[MessageTagsResult, ...]:
    """Add tags to messages, take tags away, or give them exactly these tags. A new tag gets a colour."""
    if not parameters.messages:
        raise UsageError("Name at least one message")
    if len(parameters.messages) > MAXIMUM_MESSAGES:
        raise UsageError(f"At most {MAXIMUM_MESSAGES} messages at once")
    for message in parameters.messages:
        if len(message) != 32 or any(character not in "0123456789abcdef" for character in message):
            raise UsageError("A message id is 32 lower-case hexadecimal characters")
    added = tuple(_name(tag) for tag in parameters.add)
    removed = {_name(tag).casefold() for tag in parameters.remove}
    if not added and not removed and not parameters.replace:
        raise UsageError("Give tags to add or to take away")
    results: list[MessageTagsResult] = []
    try:
        with open_mailbox(engine, parameters.account) as mailbox:
            folder = mailbox.folder(parameters.folder)
            for message in parameters.messages:
                current = () if parameters.replace else mailbox.tags_of(message)
                wanted = clean_tags([*(tag for tag in current if tag.casefold() not in removed), *(tag for tag in added if tag.casefold() not in removed)])
                results.append(MessageTagsResult(message, mailbox.set_tags(mailbox.reference(folder, message), wanted)))
        give_colours(engine, added)
    except StorageError as error:
        raise UsageError(str(error)) from None
    return tuple(results)


def give_colours(engine: Engine, names: tuple[str, ...]) -> None:
    """Tags that are new get the default colour, so that they are listed and shown like the others."""
    try:
        colours = load_tags(engine.directories.tags_file)
        changed = colours
        for name in names:
            if not changed.colour_of(name):
                changed = changed.with_colour(name, DEFAULT_COLOUR)
        if changed != colours:
            save_tags(engine.directories.tags_file, changed)
    except ConfigurationError as error:
        raise UsageError(str(error)) from None


# --- tag.create and tag.delete --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateParameters:
    name: str = field(metadata=option("The name of the new tag"))
    colour: str = field(default=DEFAULT_COLOUR, metadata=option("Its colour as #rrggbb"))


@dataclass(frozen=True, slots=True)
class CreatedTag:
    name: str
    colour: str


def tag_create(engine: Engine, parameters: CreateParameters) -> CreatedTag:
    """Make a tag (a name with a colour) before anything carries it. A tag of the same name gets this colour."""
    name = _name(parameters.name)
    try:
        save_tags(engine.directories.tags_file, load_tags(engine.directories.tags_file).with_colour(name, parameters.colour or DEFAULT_COLOUR))
    except ConfigurationError as error:
        raise UsageError(str(error)) from None
    return CreatedTag(name, (parameters.colour or DEFAULT_COLOUR).lower())


@dataclass(frozen=True, slots=True)
class DeleteParameters:
    name: str = field(metadata=option("The tag to delete everywhere"))


@dataclass(frozen=True, slots=True)
class DeletedTag:
    messages: int  # mail that carried it
    items: int  # contacts, events and tasks that carried it


def tag_delete(engine: Engine, parameters: DeleteParameters) -> DeletedTag:
    """Take a tag away from all mail, contacts, events and tasks, and forget its colour. The mail and items stay."""
    name = _name(parameters.name)
    from chrono_mail.pimstore import ItemQuery

    try:
        messages = rename_in_mail(engine, name, None)
    except StorageError as error:
        raise UsageError(str(error)) from None
    items = 0
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store, item_writer(engine, account, store) as writer:
            for item in store.index.query(ItemQuery(tag=name, limit=100_000)):
                text = store.get(item.collection, item.item_id)
                updated = retag_item(item.kind, text, lambda tags: tuple(tag for tag in tags if tag.casefold() != name.casefold()))
                if updated != text:
                    writer.put(store.get_collection(item.collection), updated, item.item_id)
                    items += 1
    try:
        save_tags(engine.directories.tags_file, load_tags(engine.directories.tags_file).with_colour(name, ""))
    except ConfigurationError as error:
        raise UsageError(str(error)) from None
    return DeletedTag(messages, items)


@dataclass(frozen=True, slots=True)
class EditParameters:
    name: str = field(metadata=option("The tag"))
    shortcut: str | None = field(default=None, metadata=option("Its shortcut key (Ctrl+Alt+1 to Ctrl+Alt+9); empty takes it away"))
    use_for: tuple[str, ...] = field(default=(), metadata=option("What it is offered for: mail, events, contacts, tasks (repeat; none given keeps it)"))
    move: Literal["", "up", "down"] = field(default="", metadata=option("One place up or down in the list"))
    position: int | None = field(default=None, metadata=option("Its place in the list (0 is the top)"))


@dataclass(frozen=True, slots=True)
class EditedTag:
    name: str
    colour: str
    shortcut: str
    use_for: tuple[str, ...]


def tag_edit(engine: Engine, parameters: EditParameters) -> EditedTag:
    """A tag's shortcut key, what it is for and its place in the list (Menu > Tags). A tag without a colour gets one first."""
    name = _name(parameters.name)
    try:
        tags = load_tags(engine.directories.tags_file)
        if tags.entry(name) is None:
            tags = tags.with_colour(name, DEFAULT_COLOUR)
        entry = tags.entry(name)
        assert entry is not None
        if parameters.shortcut is not None or parameters.use_for:
            tags = tags.with_details(name, entry.shortcut if parameters.shortcut is None else parameters.shortcut.strip(), parameters.use_for or entry.kinds)
        if parameters.move:
            tags = tags.moved(name, -1 if parameters.move == "up" else 1)
        if parameters.position is not None:
            tags = tags.placed(name, parameters.position)
        save_tags(engine.directories.tags_file, tags)
    except ConfigurationError as error:
        raise UsageError(str(error)) from None
    final = tags.entry(name)
    assert final is not None
    return EditedTag(final.shown, final.colour, final.shortcut, final.kinds)


MESSAGE_TAG: TypedCommand[MailTagParameters, tuple[MessageTagsResult, ...]] = TypedCommand("message.tag", MailTagParameters, tuple)  # every element is a MessageTagsResult


TAG_CREATE: TypedCommand[CreateParameters, CreatedTag] = TypedCommand("tag.create", CreateParameters, CreatedTag)
TAG_EDIT: TypedCommand[EditParameters, EditedTag] = TypedCommand("tag.edit", EditParameters, EditedTag)
TAG_DELETE: TypedCommand[DeleteParameters, DeletedTag] = TypedCommand("tag.delete", DeleteParameters, DeletedTag)


def register(registry: CommandRegistry) -> None:
    registry.register(MESSAGE_TAG.id, "Add tags to messages, take tags away, or give them exactly these tags", MailTagParameters, message_tag)
    registry.register(TAG_CREATE.id, "Make a tag with a colour", CreateParameters, tag_create)
    registry.register(TAG_EDIT.id, "Give a tag a shortcut key, say what it is for, or move it up or down in the list", EditParameters, tag_edit)
    registry.register(TAG_DELETE.id, "Take a tag away from all mail, contacts, events and tasks", DeleteParameters, quiet_command(tag_delete), destructive=True)
