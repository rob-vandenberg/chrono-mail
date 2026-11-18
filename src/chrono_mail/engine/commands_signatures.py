"""The person's library of templates and signatures (B-24, D-358): all texts for Settings, the whole list kept at its OK, one text
opened from or saved as an HTML file, every text imported from or exported to a file of Chrono Mail's own, an account's choice of
texts, and the first start's move of each account's old plain-text signature into the library.

A template and a signature are the same thing, a named text; which one it is shows only in how an account or the person uses it.
Everything is kept on this computer only (no server holds it, D-201); config/signatures.py keeps the files."""

import base64
import html
import json
import mimetypes
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from chrono_mail.config import AppConfig
from chrono_mail.config.model import AccountTexts
from chrono_mail.config.signatures import (
    MAXIMUM_HTML_BYTES,
    MAXIMUM_ITEMS,
    Item,
    ItemList,
    clean_name,
    load_items,
    read_html,
    remove_html,
    save_items,
    write_atomically,
    write_html,
)
from chrono_mail.core import ConfigurationError
from chrono_mail.core.jsontext import load_json
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "commands_signatures.py 4.5.628"

# --- Version history ----------------------------------------------------
# v4.5.628: A text that cannot be read is given with its reason and never written over; the others still load (D-358)
# v4.5.626: Initial version: snippet.all, .save-all, .open, .save-as, .import, .export, .account-texts, .set-account-texts,
#           .take-old-signatures (B-24, D-358)

EXPORT_FORMAT: int = 1
EXPORT_LIST: str = "templates and signatures"
PICTURE: re.Pattern[str] = re.compile(r"""(<img\b[^>]*?\bsrc\s*=\s*)(["'])(.*?)\2""", re.IGNORECASE | re.DOTALL)
OUTSIDE: tuple[str, ...] = ("http:", "https:", "data:", "cid:", "mailto:", "ftp:")
MAXIMUM_EXPORT_BYTES: int = MAXIMUM_ITEMS * MAXIMUM_HTML_BYTES // 4  # far above a real library; never read without a limit


@dataclass(frozen=True, slots=True)
class SnippetText:
    """A text of the library with its HTML; item_id "" for one not kept yet."""

    item_id: str
    name: str
    html: str
    problem: str = ""  # why its text could not be read ("" it could): it is left out of every message, its file left alone


@dataclass(frozen=True, slots=True)
class SnippetSummary:
    item_id: str
    name: str


def _directory(engine: Engine) -> Path:
    return engine.directories.signatures_directory


def free_name(base: str, taken: Callable[[str], bool]) -> str:
    """base, or base with " (2)", " (3)" and so on when that name is taken."""
    name, number = base, 2
    while taken(name):
        name, number = f"{base} ({number})", number + 1
    return name


def _new_id(*lists: ItemList) -> str:
    item_id = os.urandom(6).hex()
    while any(items.find(item_id) is not None for items in lists):
        item_id = os.urandom(6).hex()
    return item_id


# --- snippet.all and snippet.save-all ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NoParameters:
    pass


def snippet_all(engine: Engine, parameters: NoParameters) -> tuple[SnippetText, ...]:
    """Every text of the library with its HTML, in the person's order."""
    directory = _directory(engine)
    try:
        listed = load_items(directory).entries
    except ConfigurationError as error:
        raise UsageError(str(error)) from None
    found: list[SnippetText] = []
    for entry in listed:  # one text that cannot be read (too large, damaged) never blocks the others
        try:
            found.append(SnippetText(entry.item_id, entry.name, read_html(directory, entry.item_id)))
        except ConfigurationError as error:
            found.append(SnippetText(entry.item_id, entry.name, "", str(error)))
    return tuple(found)


@dataclass(frozen=True, slots=True)
class SaveAllParameters:
    ids: tuple[str, ...] = field(default=(), metadata=option("Each text's id in the new order (empty for a new one; repeat)"))
    names: tuple[str, ...] = field(default=(), metadata=option("Each text's name, in the same order (repeat)"))
    htmls: tuple[str, ...] = field(default=(), metadata=option("Each text's HTML, in the same order (repeat)"))


def snippet_save_all(engine: Engine, parameters: SaveAllParameters) -> tuple[SnippetSummary, ...]:
    """The whole library as Settings shows it at its OK: these texts in this order; any other is deleted, and an account that used a
    deleted one uses none there any more. Every name is checked before anything is written; the texts are written before the list
    names them, and a deleted text's file goes after the list left it out."""
    if not len(parameters.ids) == len(parameters.names) == len(parameters.htmls):
        raise UsageError("Give an id, a name and a text for every template or signature")
    directory = _directory(engine)
    try:
        before = load_items(directory)
        wanted = ItemList()
        for item_id, name in zip(parameters.ids, parameters.names, strict=True):
            if not item_id or before.find(item_id) is None or wanted.find(item_id) is not None:
                item_id = _new_id(before, wanted)
            wanted = wanted.with_new(Item(item_id, name))
        for entry, text in zip(wanted.entries, parameters.htmls, strict=True):
            if before.find(entry.item_id) is None or _changed(directory, entry.item_id, text):
                write_html(directory, entry.item_id, text)
        if wanted != before:
            save_items(directory, wanted)
    except ConfigurationError as error:
        raise UsageError(str(error)) from None
    gone = {entry.item_id for entry in before.entries if wanted.find(entry.item_id) is None}
    if gone:
        engine.change_config(lambda fresh: _without_texts(fresh, gone))
    for item_id in gone:
        remove_html(directory, item_id)
    return tuple(SnippetSummary(entry.item_id, entry.name) for entry in wanted.entries)


def _changed(directory: Path, item_id: str, text: str) -> bool:
    """Whether a kept text differs from this; one that could not be read and comes back empty is left alone (never written over)."""
    try:
        return read_html(directory, item_id) != text
    except ConfigurationError:
        return text != ""


def _without_texts(config: AppConfig, gone: set[str]) -> AppConfig:
    """Every account's choice of a deleted text becomes none."""
    def kept(chosen: str) -> str:
        return "" if chosen in gone else chosen

    for settings in config.accounts:
        texts = settings.texts
        changed = AccountTexts(
            kept(texts.template_new), kept(texts.template_reply), kept(texts.template_forward),
            kept(texts.signature_new), kept(texts.signature_reply), kept(texts.signature_forward), texts.signature_below,
        )
        if changed != texts:
            config = config.with_account(replace(settings, texts=changed))
    return config


# --- snippet.account-texts and snippet.set-account-texts ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccountParameters:
    account: str = field(metadata=option("The account"))


@dataclass(frozen=True, slots=True)
class AccountTextsAnswer:
    """An account's choice: the id of a text of the library for each, "" none."""

    template_new: str
    template_reply: str
    template_forward: str
    signature_new: str
    signature_reply: str
    signature_forward: str
    signature_below: bool


def snippet_account_texts(engine: Engine, parameters: AccountParameters) -> AccountTextsAnswer:
    """The texts an account puts in by itself; one no longer in the library counts as none."""
    try:
        texts = engine.config().find(parameters.account).texts
        known = {entry.item_id for entry in load_items(_directory(engine)).entries}
    except ConfigurationError as error:
        raise UsageError(str(error)) from None

    def shown(chosen: str) -> str:
        return chosen if chosen in known else ""

    return AccountTextsAnswer(
        shown(texts.template_new), shown(texts.template_reply), shown(texts.template_forward),
        shown(texts.signature_new), shown(texts.signature_reply), shown(texts.signature_forward), texts.signature_below,
    )


@dataclass(frozen=True, slots=True)
class SetAccountParameters:
    account: str = field(metadata=option("The account"))
    template_new: str = field(default="", metadata=option("The text it starts new mail with (empty: none)"))
    template_reply: str = field(default="", metadata=option("The text it starts replies with (empty: none)"))
    template_forward: str = field(default="", metadata=option("The text it starts forwards with (empty: none)"))
    signature_new: str = field(default="", metadata=option("The signature of its new mail (empty: none)"))
    signature_reply: str = field(default="", metadata=option("The signature of its replies (empty: none)"))
    signature_forward: str = field(default="", metadata=option("The signature of its forwards (empty: none)"))
    signature_below: bool = field(default=False, metadata=option("Its signature stands after the quoted text of a reply or forward"))


def snippet_set_account_texts(engine: Engine, parameters: SetAccountParameters) -> AccountTextsAnswer:
    """Set the texts an account puts in by itself; each must be a text of the library, or none."""
    chosen = (parameters.template_new, parameters.template_reply, parameters.template_forward, parameters.signature_new, parameters.signature_reply, parameters.signature_forward)
    try:
        known = {entry.item_id for entry in load_items(_directory(engine)).entries}
        if any(item and item not in known for item in chosen):
            raise UsageError("That template or signature does not exist (any more)")
        texts = AccountTexts(*chosen, parameters.signature_below)
        engine.change_config(lambda fresh: fresh.with_account(replace(fresh.find(parameters.account), texts=texts)))
    except ConfigurationError as error:
        raise UsageError(str(error)) from None
    return snippet_account_texts(engine, AccountParameters(parameters.account))


# --- snippet.open and snippet.save-as: one text as an HTML file ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpenParameters:
    file: str = field(metadata=option("The HTML file to open"))


def snippet_open(engine: Engine, parameters: OpenParameters) -> SnippetText:
    """An HTML file as a new text, not kept yet (no id): named after the file, the pictures beside it taken inside."""
    path = Path(parameters.file)
    try:
        if path.stat().st_size > MAXIMUM_HTML_BYTES:
            raise UsageError(f"The file is larger than {MAXIMUM_HTML_BYTES // (1024 * 1024)} MB")
        text = path.read_bytes().decode("utf-8", "replace")
    except OSError as error:
        raise UsageError(f"The file cannot be read: {error.strerror}") from None
    return SnippetText("", " ".join(path.stem.split())[:90] or "Untitled", with_pictures_inside(text, path.parent))


def with_pictures_inside(text: str, folder: Path) -> str:
    """Pictures the HTML names by a place on this computer, relative to its folder, put inside it as data addresses, so the text
    stands on its own. A picture that is missing, not a picture or too large keeps its place as it was."""
    budget = [MAXIMUM_HTML_BYTES - len(text.encode("utf-8", "surrogatepass"))]

    def inside(match: re.Match[str]) -> str:
        place = html.unescape(match.group(3)).strip()
        if not place or place.lower().startswith(OUTSIDE) or place.startswith(("/", "\\")) or ":" in place:
            return match.group(0)
        picture = folder / place.replace("\\", "/")
        kind = mimetypes.guess_type(picture.name)[0] or ""
        try:
            if not kind.startswith("image/") or not picture.is_file() or picture.stat().st_size * 4 // 3 + 64 > budget[0]:
                return match.group(0)
            data = base64.b64encode(picture.read_bytes()).decode("ascii")
        except OSError:
            return match.group(0)
        budget[0] -= len(data) + 32
        return f"{match.group(1)}{match.group(2)}data:{kind};base64,{data}{match.group(2)}"

    return PICTURE.sub(inside, text)


@dataclass(frozen=True, slots=True)
class SaveAsParameters:
    name: str = field(metadata=option("The text's name (the page's title)"))
    html: str = field(metadata=option("Its HTML"))
    file: str = field(metadata=option("The HTML file to write (replaced when it exists)"))


@dataclass(frozen=True, slots=True)
class WrittenFile:
    file: str


def snippet_save_as(engine: Engine, parameters: SaveAsParameters) -> WrittenFile:
    """One text as an HTML file any program can open, its pictures inside it."""
    text = parameters.html
    page = text if "<html" in text.lower() else f'<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>{html.escape(parameters.name)}</title></head><body>{text}</body></html>\n'
    try:
        write_atomically(Path(parameters.file), page.encode("utf-8", "surrogatepass"), "The file could not be saved")
    except ConfigurationError as error:
        raise UsageError(str(error)) from None
    return WrittenFile(parameters.file)


# --- snippet.export and snippet.import: every text in a file of Chrono Mail's own ----------------------------------


@dataclass(frozen=True, slots=True)
class ExportParameters:
    file: str = field(metadata=option("The file to write (replaced when it exists)"))
    names: tuple[str, ...] = field(default=(), metadata=option("Each text's name (repeat)"))
    htmls: tuple[str, ...] = field(default=(), metadata=option("Each text's HTML, in the same order (repeat)"))


def snippet_export(engine: Engine, parameters: ExportParameters) -> WrittenFile:
    """Every text given (as Settings shows them) in one file of Chrono Mail's own."""
    if len(parameters.names) != len(parameters.htmls):
        raise UsageError("Give a name and a text for every template or signature")
    items = [{"name": name, "html": text} for name, text in zip(parameters.names, parameters.htmls, strict=True)]
    data = json.dumps({"format": EXPORT_FORMAT, "program": "Chrono Mail", EXPORT_LIST: items}, indent=2, ensure_ascii=False).encode("utf-8", "surrogatepass")
    try:
        write_atomically(Path(parameters.file), data, "The file could not be saved")
    except ConfigurationError as error:
        raise UsageError(str(error)) from None
    return WrittenFile(parameters.file)


@dataclass(frozen=True, slots=True)
class ImportParameters:
    file: str = field(metadata=option("The file of templates and signatures to read"))


def snippet_import(engine: Engine, parameters: ImportParameters) -> tuple[SnippetText, ...]:
    """The texts of a file Export made, not kept yet (Settings keeps them at its OK)."""
    path = Path(parameters.file)
    refused = "This is not a file of templates and signatures of Chrono Mail"
    try:
        if path.stat().st_size > MAXIMUM_EXPORT_BYTES:
            raise UsageError(refused)
        data = load_json(path.read_bytes())
    except OSError as error:
        raise UsageError(f"The file cannot be read: {error.strerror}") from None
    except ValueError:
        raise UsageError(refused) from None
    try:
        if data.get("format") != EXPORT_FORMAT or data.get("program") != "Chrono Mail":
            raise UsageError(refused)
        entries = data[EXPORT_LIST]
        if not isinstance(entries, list) or len(entries) > MAXIMUM_ITEMS:
            raise UsageError(refused)
        found: list[SnippetText] = []
        for entry in entries:
            text = str(entry["html"])
            if len(text.encode("utf-8", "surrogatepass")) > MAXIMUM_HTML_BYTES:
                raise UsageError(refused)
            found.append(SnippetText("", clean_name(str(entry["name"])), text))
        return tuple(found)
    except (KeyError, TypeError, AttributeError, ConfigurationError):
        raise UsageError(refused) from None


# --- snippet.take-old-signatures: the first start's move ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TakeOldParameters:
    below_quote: bool = field(default=False, metadata=option("The old signatures stood below the quoted text (Settings > Writing)"))


@dataclass(frozen=True, slots=True)
class TakenSignatures:
    names: tuple[str, ...]  # the texts made, one per account that had an old signature


def snippet_take_old_signatures(engine: Engine, parameters: TakeOldParameters) -> TakenSignatures:
    """Each account's old plain-text signature becomes a text of the library, named after the account, and that account's signature
    for new mail, replies and forwards, before or after the quoted text as before: a new message looks as it did. Done once: an
    account without an old signature is left alone. The library is written before the accounts change, so nothing is lost when it
    stops half-way (the next start makes the rest; a name made twice gets " (2)")."""
    directory = _directory(engine)
    old = [settings for settings in engine.config().accounts if settings.signature.strip()]
    if not old:
        return TakenSignatures(())
    made: dict[str, tuple[str, str]] = {}  # account id -> (text id, its name)
    try:
        items = load_items(directory)
        for settings in old:
            label = " ".join((settings.account_name or settings.account.email_address or "Signature").split())[:90] or "Signature"
            name = free_name(label, lambda candidate: items.named(candidate) is not None)
            item_id = _new_id(items)
            write_html(directory, item_id, "<p>" + html.escape(settings.signature.strip("\n")).replace("\n", "<br>") + "</p>")
            items = items.with_new(Item(item_id, name))
            made[settings.account.account_id] = (item_id, name)
        save_items(directory, items)
    except ConfigurationError as error:
        raise UsageError(str(error)) from None

    def moved(config: AppConfig) -> AppConfig:
        for account_id, (item_id, _) in made.items():
            settings = config.find(account_id)
            texts = replace(settings.texts, signature_new=item_id, signature_reply=item_id, signature_forward=item_id, signature_below=parameters.below_quote)
            config = config.with_account(replace(settings, signature="", texts=texts))
        return config

    engine.change_config(moved)
    return TakenSignatures(tuple(name for _, name in made.values()))


SNIPPET_ALL: TypedCommand[NoParameters, tuple[SnippetText, ...]] = TypedCommand("snippet.all", NoParameters, tuple)  # every element is a SnippetText
SNIPPET_SAVE_ALL: TypedCommand[SaveAllParameters, tuple[SnippetSummary, ...]] = TypedCommand("snippet.save-all", SaveAllParameters, tuple)  # every element is a SnippetSummary
SNIPPET_ACCOUNT_TEXTS: TypedCommand[AccountParameters, AccountTextsAnswer] = TypedCommand("snippet.account-texts", AccountParameters, AccountTextsAnswer)
SNIPPET_SET_ACCOUNT_TEXTS: TypedCommand[SetAccountParameters, AccountTextsAnswer] = TypedCommand("snippet.set-account-texts", SetAccountParameters, AccountTextsAnswer)
SNIPPET_OPEN: TypedCommand[OpenParameters, SnippetText] = TypedCommand("snippet.open", OpenParameters, SnippetText)
SNIPPET_SAVE_AS: TypedCommand[SaveAsParameters, WrittenFile] = TypedCommand("snippet.save-as", SaveAsParameters, WrittenFile)
SNIPPET_EXPORT: TypedCommand[ExportParameters, WrittenFile] = TypedCommand("snippet.export", ExportParameters, WrittenFile)
SNIPPET_IMPORT: TypedCommand[ImportParameters, tuple[SnippetText, ...]] = TypedCommand("snippet.import", ImportParameters, tuple)  # every element is a SnippetText
SNIPPET_TAKE_OLD: TypedCommand[TakeOldParameters, TakenSignatures] = TypedCommand("snippet.take-old-signatures", TakeOldParameters, TakenSignatures)


def register(registry: CommandRegistry) -> None:
    registry.register(SNIPPET_ALL.id, "List the templates and signatures with their texts", NoParameters, snippet_all, doing="Reading the templates and signatures")
    registry.register(SNIPPET_SAVE_ALL.id, "Keep the whole list of templates and signatures as given; any other is deleted", SaveAllParameters, snippet_save_all, destructive=True, doing="Saving the templates and signatures")
    registry.register(SNIPPET_ACCOUNT_TEXTS.id, "Show the templates and signatures an account puts in by itself", AccountParameters, snippet_account_texts, doing="Reading an account's templates and signatures")
    registry.register(SNIPPET_SET_ACCOUNT_TEXTS.id, "Set the templates and signatures an account puts in by itself", SetAccountParameters, snippet_set_account_texts, doing="Saving an account's templates and signatures")
    registry.register(SNIPPET_OPEN.id, "Read an HTML file as a new template or signature", OpenParameters, snippet_open, doing="Opening a file")
    registry.register(SNIPPET_SAVE_AS.id, "Save a template or signature as an HTML file", SaveAsParameters, snippet_save_as, doing="Saving a file")
    registry.register(SNIPPET_EXPORT.id, "Save templates and signatures in one file of Chrono Mail's own", ExportParameters, snippet_export, doing="Exporting the templates and signatures")
    registry.register(SNIPPET_IMPORT.id, "Read the templates and signatures of a file Export made", ImportParameters, snippet_import, doing="Importing templates and signatures")
    registry.register(SNIPPET_TAKE_OLD.id, "Move each account's old plain-text signature into the library", TakeOldParameters, snippet_take_old_signatures, doing="Moving the old signatures")
