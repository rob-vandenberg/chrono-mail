"""The person's library of templates and signatures: kept on this computer only, each text as an HTML file of its own (B-24, D-358).

A template and a signature are the same thing, a named text; which one a text is shows only in how an account or the person uses it.
The folder `templates-and-signatures/` beside the settings holds `items.json` (format, and each text's id and name, in the person's
order: the first seven have Ctrl+1 to Ctrl+7) and one `<id>.html` per text, its pictures inside it as data addresses. Nothing is kept on a server: no server holds them (D-201). Every file
is written in one atomic step; an item's HTML is written before the list names it, and a deleted one leaves the list before its
HTML goes, so an interruption leaves at most an HTML file that nothing names, never a name without its text."""

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from chrono_mail.core import ConfigurationError
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "signatures.py 4.5.626"

# --- Version history ----------------------------------------------------
# v4.5.626: Initial version: the library of templates and signatures, one list of named texts and an HTML file each (B-24, D-358)

FORMAT_VERSION: int = 1
LIST_FILE: str = "items.json"
MAXIMUM_ITEMS: int = 400
MAXIMUM_NAME: int = 100
MAXIMUM_LIST_BYTES: int = 1024 * 1024
MAXIMUM_HTML_BYTES: int = 5 * 1024 * 1024  # a signature with a logo or two; its pictures are inside it
ID_PATTERN: re.Pattern[str] = re.compile(r"[0-9a-f]{12}")


@dataclass(frozen=True, slots=True)
class Item:
    item_id: str  # 12 lower-case hexadecimal characters, also the name of its HTML file
    name: str


@dataclass(frozen=True, slots=True)
class ItemList:
    """The texts in the person's order. A name is unique, compared without regard to capitals."""

    entries: tuple[Item, ...] = ()

    def find(self, item_id: str) -> Item | None:
        return next((entry for entry in self.entries if entry.item_id == item_id), None)

    def named(self, name: str) -> Item | None:
        folded = name.casefold()
        return next((entry for entry in self.entries if entry.name.casefold() == folded), None)

    def with_new(self, item: Item) -> "ItemList":
        if len(self.entries) >= MAXIMUM_ITEMS:
            raise ConfigurationError(f"There can be at most {MAXIMUM_ITEMS} templates and signatures")
        cleaned = replace(item, name=clean_name(item.name))
        if ID_PATTERN.fullmatch(cleaned.item_id) is None or self.find(cleaned.item_id) is not None:
            raise ConfigurationError("Not a valid id for a template or signature")
        if self.named(cleaned.name) is not None:
            raise ConfigurationError(f"A template or signature called {cleaned.name} already exists")
        return replace(self, entries=(*self.entries, cleaned))


def clean_name(text: str) -> str:
    name = " ".join(text.split())
    if not name:
        raise ConfigurationError("A template or signature needs a name")
    if len(name) > MAXIMUM_NAME:
        raise ConfigurationError(f"A name has at most {MAXIMUM_NAME} characters")
    return name


def parse_items(text: str) -> ItemList:
    try:
        data = load_json(text)
        known = data.get("format")
    except (ValueError, TypeError, AttributeError):
        raise ConfigurationError("The templates and signatures file is not valid") from None
    if known != FORMAT_VERSION:
        raise ConfigurationError("The templates and signatures file has an unknown format")
    try:
        entries = data.get("items", [])
        if not isinstance(entries, list) or len(entries) > MAXIMUM_ITEMS:
            raise ValueError("not a list")
        result = ItemList()
        for entry in entries:
            result = result.with_new(Item(str(entry["id"]), str(entry["name"])))
        return result
    except (ConfigurationError, ValueError, KeyError, TypeError, AttributeError):
        raise ConfigurationError("The templates and signatures file is not valid") from None


def items_to_text(items: ItemList) -> str:
    return json.dumps(
        {"format": FORMAT_VERSION, "items": [{"id": entry.item_id, "name": entry.name} for entry in items.entries]},
        indent=2, ensure_ascii=False,
    ) + "\n"


def load_items(directory: Path) -> ItemList:
    """The list in the folder `directory` (none yet: an empty list). A damaged list is copied aside to .bad and refused."""
    path = directory / LIST_FILE
    try:
        if not path.exists():
            return ItemList()
        if path.stat().st_size > MAXIMUM_LIST_BYTES:
            raise ConfigurationError("The templates and signatures file is far too large")
        text = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        raise ConfigurationError("The templates and signatures file cannot be read") from None
    try:
        return parse_items(text)
    except ConfigurationError as error:
        try:
            shutil.copyfile(path, path.with_name(path.name + ".bad"))
        except OSError:
            raise ConfigurationError(f"{error} (and it could not be backed up)") from None
        raise ConfigurationError(f"{error}. The file was copied to {path.name}.bad.") from None


def save_items(directory: Path, items: ItemList) -> None:
    write_atomically(directory / LIST_FILE, items_to_text(items).encode("utf-8"), "The templates and signatures could not be saved")


def html_file(directory: Path, item_id: str) -> Path:
    if ID_PATTERN.fullmatch(item_id) is None:
        raise ConfigurationError("Not a valid id for a template or signature")
    return directory / f"{item_id}.html"


def read_html(directory: Path, item_id: str) -> str:
    """An item's HTML. A missing file is an empty text, never a failure of the whole list."""
    path = html_file(directory, item_id)
    try:
        if not path.exists():
            return ""
        if path.stat().st_size > MAXIMUM_HTML_BYTES:
            raise ConfigurationError("The template or signature is far too large")
        return path.read_bytes().decode("utf-8", "replace")
    except OSError:
        raise ConfigurationError("The template or signature cannot be read") from None


def write_html(directory: Path, item_id: str, html: str) -> None:
    data = html.encode("utf-8", "surrogatepass")
    if len(data) > MAXIMUM_HTML_BYTES:
        raise ConfigurationError(f"A template or signature may be at most {MAXIMUM_HTML_BYTES // (1024 * 1024)} MB, its pictures included")
    write_atomically(html_file(directory, item_id), data, "The template or signature could not be saved")


def remove_html(directory: Path, item_id: str) -> None:
    """The HTML of an item no longer listed. One that cannot be removed stays behind unnamed: nothing shows it."""
    try:
        html_file(directory, item_id).unlink(missing_ok=True)
    except OSError:
        pass


def write_atomically(path: Path, data: bytes, failure: str) -> None:
    """Write to a temporary file beside it, flush it to disk, then move it into place in one rename; readable by the owner only."""
    temporary = path.with_name(f".{path.name}.{os.urandom(6).hex()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            written = 0
            while written < len(data):
                written += os.write(descriptor, data[written:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        if sys.platform != "win32":
            folder = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(folder)
            finally:
                os.close(folder)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ConfigurationError(f"{failure}: {error.strerror}") from None
