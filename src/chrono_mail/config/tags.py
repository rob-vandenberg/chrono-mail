import json
import os
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from chrono_mail.core import ConfigurationError

# --- Version ------------------------------------------------------------
__version__ = "tags.py 0.0.60"

# --- Version history ----------------------------------------------------
# v0.0.60: Initial colours of tags

FORMAT_VERSION: int = 1
MAXIMUM_TAGS: int = 2000
MAXIMUM_NAME: int = 100
MAXIMUM_FILE_BYTES: int = 1024 * 1024
HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True, slots=True)
class TagColours:
    """The colours of tags. A tag is a name (kept in the items themselves); this file only says what colour a name has. Names compare without regard to case."""

    colours: tuple[tuple[str, str, str], ...] = ()  # (name in lower case, name as written, #rrggbb)

    def colour_of(self, name: str) -> str:
        wanted = name.casefold()
        for key, _, colour in self.colours:
            if key == wanted:
                return colour
        return ""

    def display_name(self, name: str) -> str:
        wanted = name.casefold()
        for key, shown, _ in self.colours:
            if key == wanted:
                return shown
        return name

    def with_colour(self, name: str, colour: str) -> "TagColours":
        cleaned = " ".join(name.split())
        if not cleaned or len(cleaned) > MAXIMUM_NAME:
            raise ConfigurationError("A tag needs a name of up to 100 characters")
        if colour and (len(colour) != 7 or colour[0] != "#" or any(character not in HEX for character in colour[1:].lower())):
            raise ConfigurationError("A colour is written #rrggbb")
        rest = tuple(entry for entry in self.colours if entry[0] != cleaned.casefold())
        if len(rest) >= MAXIMUM_TAGS:
            raise ConfigurationError("Too many tags with a colour")
        return replace(self, colours=(*rest, (cleaned.casefold(), cleaned, colour.lower()))) if colour else replace(self, colours=rest)

    def renamed(self, old: str, new: str) -> "TagColours":
        colour = self.colour_of(old)
        return self.with_colour(old, "").with_colour(new, colour) if colour else self


def parse_tags(text: str) -> TagColours:
    try:
        data = json.loads(text)
        if data.get("format") != FORMAT_VERSION:
            raise ConfigurationError("The tags file has an unknown format")
        entries = data.get("tags", [])
        if not isinstance(entries, list) or len(entries) > MAXIMUM_TAGS:
            raise ConfigurationError("The tags file is not valid")
        result = TagColours()
        for entry in entries:
            result = result.with_colour(str(entry["name"]), str(entry["colour"]))
        return result
    except ConfigurationError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ConfigurationError("The tags file is not valid") from None


def tags_to_text(tags: TagColours) -> str:
    return json.dumps({"format": FORMAT_VERSION, "tags": [{"name": shown, "colour": colour} for _, shown, colour in sorted(tags.colours)]}, indent=2, ensure_ascii=False) + "\n"


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
