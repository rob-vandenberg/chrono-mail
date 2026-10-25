import json
import os
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from chrono_mail.core import ConfigurationError
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "tags.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash
# v0.5.135: The person's order of the tags, a shortcut key and what each tag is for (Tags window); older files still load
# v0.0.60: Initial colours of tags

FORMAT_VERSION: int = 1
MAXIMUM_TAGS: int = 2000
MAXIMUM_NAME: int = 100
MAXIMUM_FILE_BYTES: int = 1024 * 1024
HEX = frozenset("0123456789abcdef")


KINDS: tuple[str, ...] = ("mail", "events", "contacts", "tasks")  # what a tag may be used for
SHORTCUTS: tuple[str, ...] = tuple(f"Ctrl+Alt+{number}" for number in range(1, 10))  # the keys a tag can have, as the reference's list of shortcuts


@dataclass(frozen=True, slots=True)
class TagEntry:
    key: str  # the name in lower case (casefold)
    shown: str  # the name as written
    colour: str  # #rrggbb
    shortcut: str = ""  # one of SHORTCUTS, or none
    kinds: tuple[str, ...] = KINDS  # what the tag is offered for


@dataclass(frozen=True, slots=True)
class TagColours:
    """The tags a person made: their colours, their order, their shortcut keys and what they are for. A tag is a name
    (kept in the mail and items themselves); this file says what colour a name has. Names compare without regard to case."""

    entries: tuple[TagEntry, ...] = ()  # in the person's order

    @property
    def colours(self) -> tuple[tuple[str, str, str], ...]:
        """(name in lower case, name as written, #rrggbb) of every tag with a colour, in order."""
        return tuple((entry.key, entry.shown, entry.colour) for entry in self.entries)

    def entry(self, name: str) -> TagEntry | None:
        wanted = name.casefold()
        return next((entry for entry in self.entries if entry.key == wanted), None)

    def colour_of(self, name: str) -> str:
        found = self.entry(name)
        return found.colour if found is not None else ""

    def display_name(self, name: str) -> str:
        found = self.entry(name)
        return found.shown if found is not None else name

    def with_colour(self, name: str, colour: str) -> "TagColours":
        """Give a tag a colour (a new tag goes at the end of the list); an empty colour forgets the tag here."""
        cleaned = " ".join(name.split())
        if not cleaned or len(cleaned) > MAXIMUM_NAME:
            raise ConfigurationError("A tag needs a name of up to 100 characters")
        if colour and (len(colour) != 7 or colour[0] != "#" or any(character not in HEX for character in colour[1:].lower())):
            raise ConfigurationError("A colour is written #rrggbb")
        key = cleaned.casefold()
        current = self.entry(cleaned)
        if not colour:
            return replace(self, entries=tuple(entry for entry in self.entries if entry.key != key))
        if current is None:
            if len(self.entries) >= MAXIMUM_TAGS:
                raise ConfigurationError("Too many tags with a colour")
            return replace(self, entries=(*self.entries, TagEntry(key, cleaned, colour.lower())))
        return replace(self, entries=tuple(replace(entry, shown=cleaned, colour=colour.lower()) if entry.key == key else entry for entry in self.entries))

    def with_details(self, name: str, shortcut: str, kinds: tuple[str, ...]) -> "TagColours":
        """The shortcut key and what a tag is for. A shortcut another tag had moves to this one."""
        current = self.entry(name)
        if current is None:
            raise ConfigurationError("Give the tag a colour first")
        if shortcut and shortcut not in SHORTCUTS:
            raise ConfigurationError(f"A tag's shortcut is one of {', '.join(SHORTCUTS)}")
        wanted = tuple(kind for kind in KINDS if kind in kinds)
        if not wanted:
            raise ConfigurationError("A tag is used for at least one kind of item")
        if any(kind not in KINDS for kind in kinds):
            raise ConfigurationError(f"A tag is used for {', '.join(KINDS)}")
        return replace(self, entries=tuple(
            replace(entry, shortcut=shortcut, kinds=wanted) if entry.key == current.key else (replace(entry, shortcut="") if shortcut and entry.shortcut == shortcut else entry)
            for entry in self.entries
        ))

    def moved(self, name: str, step: int) -> "TagColours":
        """A tag one place up (-1) or down (+1) in the list; at the end it stays."""
        current = self.entry(name)
        if current is None:
            return self
        entries = list(self.entries)
        position = entries.index(current)
        target = position + step
        if 0 <= target < len(entries):
            entries[position], entries[target] = entries[target], entries[position]
        return replace(self, entries=tuple(entries))

    def placed(self, name: str, position: int) -> "TagColours":
        """A tag at this place in the list (0 is the top; beyond the end: last)."""
        current = self.entry(name)
        if current is None:
            return self
        entries = [entry for entry in self.entries if entry.key != current.key]
        entries.insert(max(0, min(position, len(entries))), current)
        return replace(self, entries=tuple(entries))

    def renamed(self, old: str, new: str) -> "TagColours":
        """Another name, in the same place, with the same colour, shortcut and kinds (joined with a tag of the new name)."""
        current = self.entry(old)
        if current is None:
            return self
        cleaned = " ".join(new.split())
        rest = tuple(entry for entry in self.entries if entry.key != cleaned.casefold() or entry.key == current.key)
        return replace(self, entries=tuple(replace(entry, key=cleaned.casefold(), shown=cleaned) if entry.key == current.key else entry for entry in rest))


def parse_tags(text: str) -> TagColours:
    try:
        data = load_json(text)
        if data.get("format") != FORMAT_VERSION:
            raise ConfigurationError("The tags file has an unknown format")
        entries = data.get("tags", [])
        if not isinstance(entries, list) or len(entries) > MAXIMUM_TAGS:
            raise ConfigurationError("The tags file is not valid")
        result = TagColours()
        for entry in entries:
            result = result.with_colour(str(entry["name"]), str(entry["colour"]))
            shortcut, kinds = entry.get("shortcut", ""), entry.get("use_for", list(KINDS))  # both optional: files written before 0.5.135 lack them
            if isinstance(shortcut, str) and isinstance(kinds, list) and (shortcut or tuple(kinds) != KINDS):
                result = result.with_details(str(entry["name"]), shortcut, tuple(str(kind) for kind in kinds))
        return result
    except ConfigurationError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ConfigurationError("The tags file is not valid") from None


def tags_to_text(tags: TagColours) -> str:
    return json.dumps(
        {"format": FORMAT_VERSION, "tags": [{"name": entry.shown, "colour": entry.colour, "shortcut": entry.shortcut, "use_for": list(entry.kinds)} for entry in tags.entries]},
        indent=2, ensure_ascii=False,
    ) + "\n"


def load_tags(path: Path) -> TagColours:
    try:
        if not path.exists():
            return TagColours()
        if path.stat().st_size > MAXIMUM_FILE_BYTES:
            raise ConfigurationError("The tags file is far too large")
        text = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        raise ConfigurationError("The tags file cannot be read") from None
    try:
        return parse_tags(text)
    except ConfigurationError as error:
        try:
            shutil.copyfile(path, path.with_name(path.name + ".bad"))
        except OSError:
            raise ConfigurationError(f"{error} (and it could not be backed up)") from None
        raise ConfigurationError(f"{error}. The file was copied to {path.name}.bad.") from None


def save_tags(path: Path, tags: TagColours) -> None:
    temporary = path.with_name(f".{path.name}.{os.urandom(6).hex()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            os.write(descriptor, tags_to_text(tags).encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        if sys.platform != "win32":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ConfigurationError(f"The tags could not be saved: {error.strerror}") from None
