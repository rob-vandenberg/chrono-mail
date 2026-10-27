"""Reading the three exports of eM Client: its settings file (.xml, accounts, passwords and rules), its mail export
(a folder tree of .eml files) and its backup (.zip, which adds the read, flagged and replied marks and the contacts).
Every file is hostile input: sizes are capped and anything that cannot be read is reported, never a crash."""

import base64
import os
import re
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ElementTree
import zipfile
from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal

from chrono_mail.config import Action, Condition
from chrono_mail.config.rules import HEADER_NAME
from chrono_mail.core import ChronoMailError, FolderRole, MessageFlag
from chrono_mail.core.colours import NEW_TAG
from chrono_mail.core.folder import roles_by_name

# --- Version ------------------------------------------------------------
__version__ = "emclient_source.py 4.2.387"

# --- Version history ----------------------------------------------------
# v4.2.387: the standard folders by name in ten languages (core roles_by_name) instead of an English table; Outbox still goes to Drafts
# v1.2.254: A rule that moves to a standard folder by its name ("Junk E-mail", "Deleted Items", "Sent Items" …) goes to Chrono Mail's own
#           standard folder, as the mail of that folder does (STANDARD_FOLDERS, one table for both): to Junk is Move to Junk, to Trash is Delete;
#           before, the name was kept and the rule made an ordinary folder of that name
# v1.1.172: A category without a valid colour gets the colour file's colour for a new tag (D-158)
# v0.5.141: The 9th character of the password key corrected (it came from a misremembered password)
# v0.5.137: Categories (as tags, with colour and what they are for), the categories of the messages in the backup, and the category parts of rules
# v0.5.132: Initial version: settings file, mail export and backup of eM Client; every rule as it is (also the blocked-senders rule and rules written as a script)


class ImportSourceError(ChronoMailError):
    """An export cannot be read (missing, too large or not what it should be)."""


MAXIMUM_SETTINGS_BYTES: int = 50 * 1024 * 1024
MAXIMUM_DATABASE_BYTES: int = 8 * 1024 * 1024 * 1024
MAXIMUM_ACCOUNTS: int = 1000
MAXIMUM_RULES: int = 1000
MAXIMUM_CONTACTS: int = 200_000
MAXIMUM_HEADER_BYTES: int = 256 * 1024
MAXIMUM_MESSAGE_BYTES: int = 256 * 1024 * 1024  # the same limit as a download
Security = Literal["tls", "starttls", "none"]
_TICKS_AT_1970: int = 621_355_968_000_000_000  # .NET ticks (100 ns since the year 1) at 1970-01-01 UTC
_READ_BIT, _FLAGGED_BIT = 2, 4
_BACKUP_FILES: tuple[str, ...] = ("Local Folders/folders.dat", "Local Folders/mail_index.dat", "Local Folders/contact_index.dat")

# The passwords in the settings file are XOR-ed with a fixed key of the program. The key is kept here as two rows that
# only mean something together, so that it is not a readable string in the source or in the built program.
_KEY_ROWS: tuple[tuple[int, ...], tuple[int, ...]] = (
    (30, 118, 161, 79, 228, 36, 127, 167, 27, 82, 194, 222),
    (90, 19, 199, 46, 145, 72, 11, 230, 115, 61, 168, 157),
)


def _key() -> bytes:
    return bytes(first ^ second for first, second in zip(*_KEY_ROWS, strict=True))


def decode_password(text: str) -> str | None:
    """The password stored in the settings file, or None when it cannot be decoded (empty, or longer than the 12 known characters of the key)."""
    try:
        data = base64.b64decode(text.strip(), validate=True)
    except ValueError:
        return None
    key = _key()
    if not data or len(data) > len(key):
        return None
    try:
        password = bytes(byte ^ mask for byte, mask in zip(data, key)).decode("utf-8")
    except UnicodeDecodeError:
        return None
    return password if password.isprintable() else None


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next((item for item in element if _local(item.tag) == name), None)


def _text(element: ElementTree.Element | None, name: str) -> str:
    found = None if element is None else _child(element, name)
    return "" if found is None or found.text is None else found.text.strip()


def _number(text: str, default: int) -> int:
    return int(text) if re.fullmatch(r"-?\d{1,9}", text) else default


# --- accounts -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceServer:
    host: str
    port: int
    security: Security  # as account.add takes it
    username: str
    password: str | None  # None: not stored or not decodable


@dataclass(frozen=True, slots=True)
class SourceAccount:
    uid: str
    name: str  # the name of the account in eM Client
    person_name: str  # the name in the From of sent mail
    email_address: str
    kind: str  # pop3, imap or another protocol (only pop3 is imported)
    incoming: SourceServer
    outgoing: SourceServer
    leave_on_server: bool
    delete_after_days: int  # -1 or 0: never
    remove_when_deleted: bool


def _security(encryption: str, port: int) -> Security:
    """Certificates are always checked (AGENTS.md 4.6), so 'if available' is STARTTLS too; a TLS port means implicit TLS."""
    value = encryption.upper()
    if value in ("NONE", "NO", "OFF"):
        return "none"
    if value == "SSL" or port in (465, 993, 995):
        return "tls"
    return "starttls"


def _server(protocol: ElementTree.Element, account: ElementTree.Element, crypted: bool) -> SourceServer:
    port = _number(_text(protocol, "Port"), 0)
    username = _text(protocol, "LoginName") or _text(account, "LoginName")
    stored = _text(protocol, "Password") or _text(account, "Password")
    password = (decode_password(stored) if crypted else stored) if stored else None
    return SourceServer(_text(protocol, "Server"), port, _security(_text(protocol, "Encryption"), port), username, password)


def _account(item: ElementTree.Element, crypted: bool) -> SourceAccount | None:
    account = _child(item, "Account")
    if account is None:
        return None
    protocols = [element for element in account if _local(element.tag) == "Protocol"]
    outgoing = next((element for element in protocols if _text(element, "Type").upper() == "SMTP"), None)
    incoming = next((element for element in protocols if _text(element, "Type").upper() != "SMTP"), None)
    if incoming is None or outgoing is None:
        return None
    addresses = _child(account, "Addresses")
    email = _text(addresses, "Address") if addresses is not None else ""
    return SourceAccount(
        account.get("uid", ""),
        _text(item, "AccountName") or email,
        _text(account, "PersonName"),
        email,
        _text(incoming, "Type").lower(),
        _server(incoming, account, crypted),
        _server(outgoing, account, crypted),
        _text(incoming, "LeaveMessagesOnServer").lower() != "false",
        _number(_text(incoming, "DeleteMessageInDays"), -1),
        _text(incoming, "RemoveWhenDeleted").lower() == "true",
    )


# --- rules ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceRule:
    name: str
    enabled: bool
    account_uid: str | None  # the rule is for this account only
    conditions: tuple[Condition, ...]
    match: str  # all or any
    actions: tuple[Action, ...]
    problem: str | None  # why the rule cannot be imported; None when it can
    left_out: tuple[str, ...] = ()  # parts of the rule that were not imported


# Conditions of the rule editor that have a Chrono Mail counterpart: element -> field of a rule condition.
_CONDITION_FIELDS: dict[str, str] = {"wordsInSubject": "subject", "fromPeople": "from", "sentToPeople": "to", "wordsInBody": "body"}
_SIMPLE_ACTIONS: dict[str, str] = {"markAsRead": "mark_read", "markAsFlagged": "flag", "deleteMail": "delete", "permanentlyDeleteMail": "delete", "moveToJunk": "mark_junk"}
_IGNORED: frozenset[str] = frozenset({"active", "dataEntered", "invocation", "name", "usingAccount", "expandFolder"})
_ACTION_NAMES: dict[str, str] = {
    "forwardToPeople": "forward", "forwardToPeopleAsAttachment": "forward as attachment", "moveCopyToFolder": "copy to a folder",
    "archive": "archive", "addNote": "add a note", "skipNotification": "no notification",
}


# The standard folders a rule names as special://<name>.
_SPECIAL_FOLDERS: dict[str, Action] = {
    "spam": Action("mark_junk"), "junk": Action("mark_junk"), "trash": Action("delete"), "deleted": Action("delete"),
    "inbox": Action("move_to", "Inbox"), "sent": Action("move_to", "Sent"), "drafts": Action("move_to", "Drafts"), "archive": Action("move_to", "Archive"),
}


def standard_roles(names: Sequence[str]) -> dict[int, FolderRole]:
    """The standard folders of the export by their names (core roles_by_name: the language of all of them), for its mail and for its
    rules alike: each goes to Chrono Mail's own. Its Outbox goes to Drafts: never sent by importing it."""
    return {index: FolderRole.DRAFTS if role is FolderRole.OUTBOX else role for index, role in roles_by_name(names).items()}


# The name of each standard folder in Local Folders, for a rule that moves into a folder inside one ("Deleted Items/Old" -> "Trash/Old").
_STANDARD_NAMES: dict[FolderRole, str] = {
    FolderRole.INBOX: "Inbox", FolderRole.SENT: "Sent", FolderRole.DRAFTS: "Drafts", FolderRole.TRASH: "Trash", FolderRole.JUNK: "Junk",
    FolderRole.ARCHIVE: "Archive",
}


def _move(text: str) -> Action | None:
    """The action for "move to this folder": a standard folder (special://spam, or named like "Junk E-mail") or a folder of Local Folders."""
    if text.lower().startswith("special://"):
        return _SPECIAL_FOLDERS.get(text[len("special://"):].strip("/").lower())
    folder = _folder(text)
    if folder is None:
        return None
    role = standard_roles([folder[0]]).get(0)  # one name: its own language
    if role is FolderRole.JUNK and len(folder) == 1:
        return Action("mark_junk")  # the same as special://spam: Move to Junk
    if role is FolderRole.TRASH and len(folder) == 1:
        return Action("delete")  # the same as special://trash: to Trash
    if role is not None:
        folder = (_STANDARD_NAMES[role], *folder[1:])
    return Action("move_to", "/".join(folder))


def _folder(text: str) -> tuple[str, ...] | None:
    """'/Local Folders/Clients/Rob' -> ('Clients', 'Rob'): the first part names the mailbox."""
    if "://" in text or not text.startswith("/"):
        return None
    parts = tuple(part.strip() for part in text.strip("/").split("/"))
    return parts[1:] if len(parts) > 1 and all(parts) else None


def _words(element: ElementTree.Element, name: str) -> tuple[str, ...]:
    data = _child(element, name + "Data")
    if data is None:
        return ()
    return tuple(value for value in ((item.text or "").strip() for item in data) if value)


def _composed(name: str, enabled: bool, account_uid: str | None, element: ElementTree.Element) -> SourceRule:
    def refused(problem: str) -> SourceRule:
        return SourceRule(name, enabled, account_uid, (), "all", (), problem)

    switched_on = {_local(item.tag) for item in element if (item.text or "").strip().lower() == "true"} - _IGNORED
    groups: list[tuple[Condition, ...]] = []
    actions: list[Action] = []
    left_out: list[str] = []
    stop = False
    for tag in sorted(switched_on):
        if tag in _CONDITION_FIELDS:
            words = _words(element, tag)
            if not words:
                return refused("a condition without words")
            groups.append(tuple(Condition(_CONDITION_FIELDS[tag], "contains", word[:500]) for word in words))
        elif tag == "hasAttachment":
            groups.append((Condition("attachment", "is", "yes"),))
        elif tag == "hasCategory":
            names = _words(element, tag)
            if not names:
                return refused("a category condition without a category")
            groups.append(tuple(Condition("tag", "is", name[:100]) for name in names))
        elif tag in ("addCategories", "removeCategories"):
            names = _words(element, tag)
            if not names:
                return refused("a category action without a category")
            actions.extend(Action("add_tag" if tag == "addCategories" else "remove_tag", name[:100]) for name in names)
        elif tag == "moveToFolder":
            move = _move(_text(element, "moveToFolderData"))
            if move is None:
                return refused("it moves to a folder outside Local Folders")
            actions.append(move)
        elif tag in _SIMPLE_ACTIONS:
            actions.append(Action(_SIMPLE_ACTIONS[tag]))
            if tag == "permanentlyDeleteMail":
                left_out.append("delete for good (it moves to Trash instead)")
        elif tag == "terminateProcessing":
            stop = True
        elif tag in _ACTION_NAMES:
            left_out.append(_ACTION_NAMES[tag])
        else:
            return refused(f"a condition Chrono Mail does not have ({tag})")
    several = [group for group in groups if len(group) > 1]
    if several and len(groups) > 1:
        return refused("several words in more than one condition")
    match = "any" if several else "all"
    conditions = tuple(condition for group in groups for condition in group)
    if not actions:
        return refused("no action that Chrono Mail has")
    if not conditions and account_uid is None:
        return refused("no condition and no account: it would act on all mail")
    if stop:
        actions.append(Action("stop"))
    return SourceRule(name, enabled, account_uid, conditions, match, tuple(actions[:10]), None, tuple(left_out))


def _rule(item: ElementTree.Element, account_uids: set[str]) -> SourceRule:
    name = (_text(item, "name") or "Imported rule")[:100]
    enabled = _text(item, "active").lower() != "false"
    account = _child(item, "account")
    uid = account.get("uid") if account is not None else None
    for_account = _text(item, "scope") == "SpecifiedAccount"
    if for_account and uid not in account_uids:
        return SourceRule(name, enabled, None, (), "all", (), "its account is not in the settings file")
    account_uid = uid if for_account else None
    xml = _text(item, "composeXml")
    if not xml:  # a rule written as a script
        try:
            return _scripted(name, enabled, account_uid, _text(item, "sieve"))
        except ChronoMailError as error:
            return SourceRule(name, enabled, account_uid, (), "all", (), str(error))
    try:
        element = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return SourceRule(name, enabled, account_uid, (), "all", (), "it cannot be read")
    try:
        return _composed(name, enabled, account_uid, element)
    except ChronoMailError as error:  # a value Chrono Mail does not accept, such as a folder name
        return SourceRule(name, enabled, account_uid, (), "all", (), str(error))


# --- rules written as a script (Sieve, RFC 5228) ----------------------------------------------------

MAXIMUM_SCRIPT_CHARACTERS: int = 20_000
_TOKEN = re.compile(r'\s+|#[^\n]*|/\*.*?\*/|"((?:[^"\\]|\\.)*)"|(:?[A-Za-z_][A-Za-z0-9_]*)|([()\[\],;{}])', re.DOTALL)
_FIELDS: dict[str, str] = {"from": "from", "to": "to", "cc": "cc", "subject": "subject"}
_FLAGS: dict[str, str] = {"\\seen": "mark_read", "\\flagged": "flag"}


class _ScriptError(ChronoMailError):
    pass


def _tokens(script: str) -> list[tuple[str, str]]:
    """(kind, text): kind is "string", "word", "tag" (:matches) or the punctuation itself."""
    found: list[tuple[str, str]] = []
    position = 0
    while position < len(script):
        match = _TOKEN.match(script, position)
        if match is None:
            raise _ScriptError("the script cannot be read")
        position = match.end()
        if match.group(1) is not None:
            found.append(("string", re.sub(r"\\(.)", r"\1", match.group(1))))
        elif match.group(2) is not None:
            word = match.group(2).lower()
            found.append(("tag" if word.startswith(":") else "word", word))
        elif match.group(3) is not None:
            found.append((match.group(3), match.group(3)))
    return found


class _Script:
    """Reads the one shape a rule editor writes: require …; if <test> { <actions> }."""

    def __init__(self, script: str) -> None:
        self.tokens = _tokens(script)
        self.position = 0

    def peek(self) -> tuple[str, str]:
        return self.tokens[self.position] if self.position < len(self.tokens) else ("end", "")

    def take(self, kind: str) -> str:
        found = self.peek()
        if found[0] != kind:
            raise _ScriptError("the script has a form Chrono Mail does not know")
        self.position += 1
        return found[1]

    def strings(self) -> list[str]:
        if self.peek()[0] == "string":
            return [self.take("string")]
        self.take("[")
        values = [self.take("string")]
        while self.peek()[0] == ",":
            self.take(",")
            values.append(self.take("string"))
        self.take("]")
        return values

    def test(self) -> tuple[str, list[Condition]]:
        """("one", "all" or "any", the conditions)."""
        word = self.take("word")
        if word in ("allof", "anyof"):
            self.take("(")
            parts = [self.test()]
            while self.peek()[0] == ",":
                self.take(",")
                parts.append(self.test())
            self.take(")")
            wanted = "all" if word == "allof" else "any"
            if len(parts) == 1:
                return parts[0]
            if any(kind not in ("one", wanted) for kind, _ in parts):
                raise _ScriptError("its conditions are nested in a way Chrono Mail cannot hold")
            return wanted, [condition for _, conditions in parts for condition in conditions]
        if word not in ("header", "address"):
            raise _ScriptError(f"a test Chrono Mail does not have ({word})")
        match = ":is"
        while self.peek()[0] == "tag":
            tag = self.take("tag")
            if tag in (":is", ":contains", ":matches"):
                match = tag
            elif tag == ":comparator":
                self.take("string")
            elif tag not in (":all", ":localpart", ":domain"):
                raise _ScriptError(f"a test Chrono Mail does not have ({tag})")
        names, values = self.strings(), self.strings()
        conditions = [_condition(name, match, value) for name in names for value in values]
        return ("one" if len(conditions) == 1 else "any"), conditions

    def actions(self) -> list[Action]:
        self.take("{")
        found: list[Action] = []
        while self.peek()[0] != "}":
            word = self.take("word")
            if word == "fileinto":
                move = _move(self.take("string"))
                if move is None:
                    raise _ScriptError("it moves to a folder outside Local Folders")
                found.append(move)
            elif word in ("addflag", "setflag"):
                for flag in self.strings():
                    if flag.lower() not in _FLAGS:
                        raise _ScriptError(f"a mark Chrono Mail does not have ({flag})")
                    found.append(Action(_FLAGS[flag.lower()]))
            elif word == "discard":
                found.append(Action("delete"))
            elif word == "stop":
                found.append(Action("stop"))
            elif word != "keep":
                raise _ScriptError(f"an action Chrono Mail does not have ({word})")
            self.take(";")
        self.take("}")
        return found


def _condition(name: str, match: str, value: str) -> Condition:
    lowered = name.strip().lower()
    field, header = (_FIELDS[lowered], "") if lowered in _FIELDS else ("header", name.strip())
    if field == "header" and HEADER_NAME.fullmatch(header) is None:
        raise _ScriptError(f"a header name Chrono Mail does not accept ({name})")
    operator, text = ("contains", value) if match == ":contains" else ("equals", value)
    if match == ":matches":
        if "\\" in value or "?" in value or "*" in value.strip("*"):
            raise _ScriptError("a pattern Chrono Mail cannot hold")
        starts, ends = value.startswith("*"), value.endswith("*")
        operator = "contains" if starts and ends else "ends_with" if starts else "starts_with" if ends else "equals"
        text = value.strip("*")
    if not text:
        raise _ScriptError("a condition without text")
    return Condition(field, operator, text[:500], header)


def _scripted(name: str, enabled: bool, account_uid: str | None, script: str) -> SourceRule:
    if not script.strip():
        raise _ScriptError("it is empty")
    if len(script) > MAXIMUM_SCRIPT_CHARACTERS:
        raise _ScriptError("the script is far too long")
    reader = _Script(script)
    while reader.peek() == ("word", "require"):
        reader.take("word")
        reader.strings()
        reader.take(";")
    if reader.take("word") != "if":
        raise _ScriptError("the script has a form Chrono Mail does not know")
    kind, conditions = reader.test()
    actions = reader.actions()
    if reader.peek()[0] != "end":
        raise _ScriptError("the script has more than one part")
    if not actions:
        raise _ScriptError("no action that Chrono Mail has")
    stop = [action for action in actions if action.type == "stop"]
    ordered = [action for action in actions if action.type != "stop"] + stop[:1]
    return SourceRule(name, enabled, account_uid, tuple(conditions), "any" if kind == "any" else "all", tuple(ordered[:10]), None)


@dataclass(frozen=True, slots=True)
class SourceCategory:
    """A category of eM Client: a tag in Chrono Mail."""

    name: str
    colour: str  # #rrggbb
    kinds: tuple[str, ...]  # mail, events, contacts, tasks


_KIND_OF_TYPE: dict[str, str] = {"mail": "mail", "event": "events", "task": "tasks", "contact": "contacts"}
ALL_KINDS: tuple[str, ...] = ("mail", "events", "contacts", "tasks")
MAXIMUM_CATEGORIES: int = 2000


def _kinds(text: str) -> tuple[str, ...]:
    """'Contact, Event, Task, Note' -> the kinds Chrono Mail has; All, None, or only Notes (Chrono Mail has no notes) -> all."""
    found = {_KIND_OF_TYPE[part.strip().lower()] for part in text.split(",") if part.strip().lower() in _KIND_OF_TYPE}
    return tuple(kind for kind in ALL_KINDS if kind in found) or ALL_KINDS


def _categories(root: ElementTree.Element) -> tuple[SourceCategory, ...]:
    element = _child(root, "categories")
    found: list[SourceCategory] = []
    for item in [] if element is None else [child for child in element if _local(child.tag) == "category"][:MAXIMUM_CATEGORIES]:
        name = " ".join(_text(item, "name").split())[:100]
        colour = _text(item, "color").lower()
        if not name:
            continue
        if re.fullmatch(r"#[0-9a-f]{6}", colour) is None:
            colour = NEW_TAG
        found.append(SourceCategory(name, colour, _kinds(_text(item, "parentTypes"))))
    return tuple(found)


@dataclass(frozen=True, slots=True)
class SettingsExport:
    accounts: tuple[SourceAccount, ...]
    rules: tuple[SourceRule, ...]
    passwords_encrypted: bool
    categories: tuple[SourceCategory, ...] = ()


def read_settings(path: Path) -> SettingsExport:
    """Accounts, passwords and rules from the settings file (Menu > File > Export > Settings, with passwords)."""
    try:
        if path.stat().st_size > MAXIMUM_SETTINGS_BYTES:
            raise ImportSourceError("The settings file is far too large")
        root = ElementTree.parse(path).getroot()
    except OSError as error:
        raise ImportSourceError(f"The settings file cannot be read: {error.strerror}") from None
    except ElementTree.ParseError:
        raise ImportSourceError("The settings file is not a settings export (it is not valid XML)") from None
    if _local(root.tag) != "settings":
        raise ImportSourceError("The file is not a settings export")
    accounts_element = _child(root, "accounts")
    crypted = _text(accounts_element, "crypted").lower() == "yes"
    items: list[ElementTree.Element] = [] if accounts_element is None else [item for item in accounts_element if _local(item.tag) == "account"][:MAXIMUM_ACCOUNTS]
    accounts = tuple(account for account in (_account(item, crypted) for item in items) if account is not None)
    rules_element = _child(root, "rules")
    uids = {account.uid for account in accounts}
    rules = () if rules_element is None else tuple(_rule(item, uids) for item in list(rules_element)[:MAXIMUM_RULES] if _local(item.tag) == "rule")
    return SettingsExport(accounts, rules, crypted, _categories(root))


# --- the mail export ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MessageFile:
    relative: str  # the path below the export folder, with / between the parts
    folder: tuple[str, ...]


def message_files(root: Path) -> Iterator[MessageFile]:
    """Every .eml file below the export folder, always in the same order (an import that was stopped continues by this order)."""
    if not root.is_dir():
        raise ImportSourceError("The mail export folder does not exist")
    for directory, folders, files in os.walk(root):
        folders.sort()
        here = Path(directory).relative_to(root)
        for name in sorted(files):
            if name.lower().endswith(".eml"):
                relative = (here / name).as_posix()
                yield MessageFile(relative, here.parts)


def folder_key(path: tuple[str, ...]) -> str:
    return "/".join(part.strip().lower() for part in path)


def message_keys(raw: bytes) -> tuple[str | None, int | None]:
    """The Message-ID (lower case, without < >) and the Date (UTC seconds) of a raw message, from its header only."""
    header = raw[:MAXIMUM_HEADER_BYTES]
    header = re.split(rb"\r?\n\r?\n", header, maxsplit=1)[0]
    header = re.sub(rb"\r?\n[ \t]+", b" ", header)
    found = re.search(rb"(?im)^message-id:[ \t]*<?([^>\r\n]*)>?", header)
    message_id = found.group(1).decode("latin-1").strip().lower() if found else ""
    found = re.search(rb"(?im)^date:[ \t]*([^\r\n]*)", header)
    moment: int | None = None
    if found:
        try:
            moment = int(parsedate_to_datetime(found.group(1).decode("latin-1")).timestamp())
        except (ValueError, TypeError, OverflowError):
            moment = None
    return message_id or None, moment


# --- the backup -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceContact:
    name: str
    given: str
    additional: str
    family: str
    nickname: str
    organization: str
    department: str
    title: str
    note: str
    emails: tuple[str, ...]
    phones: tuple[str, ...]
    birthday: str  # 1980-04-15, or empty


@dataclass
class BackupData:
    """What the backup adds to the mail export: the marks of each message and the contacts."""

    marks: dict[tuple[str, str], list[tuple[frozenset[MessageFlag], tuple[str, ...]]]] = field(default_factory=dict)  # (folder key, id:… or date:…) -> marks and tags
    contacts: tuple[SourceContact, ...] = ()
    tagged: tuple[tuple[str | None, int | None, tuple[str, ...]], ...] = ()  # (Message-ID, date, tags) of every message with categories

    def take(self, folder: tuple[str, ...], message_id: str | None, moment: int | None) -> tuple[frozenset[MessageFlag], tuple[str, ...]] | None:
        """The marks and tags of this message in the backup (each found entry is used once), or None when it is not there."""
        keys = ([f"id:{message_id}"] if message_id else []) + ([f"date:{moment}"] if moment is not None else [])
        for key in keys:
            waiting = self.marks.get((folder_key(folder), key))
            if waiting:
                return waiting.pop(0)
        return None

    def take_marks(self, folder: tuple[str, ...], message_id: str | None, moment: int | None) -> frozenset[MessageFlag] | None:
        found = self.take(folder, message_id, moment)
        return found[0] if found is not None else None


def _seconds(ticks: object) -> int | None:
    return (ticks - _TICKS_AT_1970) // 10_000_000 if isinstance(ticks, int) and ticks > _TICKS_AT_1970 else None


def _message_tags(connection: sqlite3.Connection) -> dict[int, tuple[str, ...]]:
    """message id in the backup -> the names of its categories (none when the backup has no such table)."""
    found: dict[int, list[str]] = {}
    try:
        rows = connection.execute("SELECT id, categoryName FROM MailCategoryNames ORDER BY rowid").fetchall()
    except sqlite3.Error:
        return {}
    for identifier, name in rows:
        if isinstance(identifier, int) and isinstance(name, str) and " ".join(name.split()):
            found.setdefault(identifier, []).append(" ".join(name.split())[:100])
    return {identifier: tuple(dict.fromkeys(names)) for identifier, names in found.items()}


def _marks(connection: sqlite3.Connection, folders: dict[int, str]) -> tuple[dict[tuple[str, str], list[tuple[frozenset[MessageFlag], tuple[str, ...]]]], tuple[tuple[str | None, int | None, tuple[str, ...]], ...]]:
    found: dict[tuple[str, str], list[tuple[frozenset[MessageFlag], tuple[str, ...]]]] = {}
    tags_of = _message_tags(connection)
    tagged: list[tuple[str | None, int | None, tuple[str, ...]]] = []
    rows = connection.execute("SELECT id, folder, messageId, date, flags, replyDate FROM MailItems ORDER BY id")
    for row_id, folder, message_id, date, flags, replied in rows:
        path = folders.get(folder) if isinstance(folder, int) else None
        if path is None or not isinstance(flags, int):
            continue
        marks = {MessageFlag.SEEN} if flags & _READ_BIT else set()
        if flags & _FLAGGED_BIT:
            marks.add(MessageFlag.FLAGGED)
        if isinstance(replied, int) and replied > 0:
            marks.add(MessageFlag.REPLIED)
        identifier = message_id.strip().strip("<>").lower() if isinstance(message_id, str) else ""
        moment = _seconds(date)
        key = f"id:{identifier}" if identifier else (f"date:{moment}" if moment is not None else None)  # without a Message-ID: by its date
        tags = tags_of.get(row_id, ()) if isinstance(row_id, int) else ()
        if tags:
            tagged.append((identifier or None, moment, tags))
        if key is not None:
            found.setdefault((path, key), []).append((frozenset(marks), tags))
    return found, tuple(tagged)


def _texts(connection: sqlite3.Connection, sql: str) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    for parent, value in connection.execute(sql):
        if isinstance(parent, int) and isinstance(value, str) and value.strip():
            found.setdefault(parent, []).append(value.strip())
    return found


def _contacts(connection: sqlite3.Connection) -> tuple[SourceContact, ...]:
    emails = _texts(connection, "SELECT parentId, email FROM ContactEmails ORDER BY isPrimary DESC, id")
    phones = _texts(connection, "SELECT parentId, telephone FROM ContactTelephones ORDER BY isPrimary DESC, id")
    columns = "id, displayName, firstName, middleName, lastName, nickName, organization, department, title, comment, birthday"
    found: list[SourceContact] = []
    for row in connection.execute(f"SELECT {columns} FROM ContactItems ORDER BY id LIMIT {MAXIMUM_CONTACTS}"):
        identifier = row[0]
        name, given, additional, family, nickname, organization, department, title, note = (value.strip() if isinstance(value, str) else "" for value in row[1:10])
        seconds = _seconds(row[10])
        birthday = (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)).strftime("%Y-%m-%d") if seconds is not None else ""
        contact = SourceContact(
            name, given, additional, family, nickname, organization, department, title, note,
            tuple(emails.get(identifier, ())), tuple(phones.get(identifier, ())), birthday,
        )
        if contact.name or contact.given or contact.family or contact.organization or contact.emails:
            found.append(contact)
    return tuple(found)


@contextmanager
def _unpacked(backup: Path, work: Path) -> Generator[Path]:
    """The three small database files of the backup, unpacked into a folder of their own that is always removed afterwards."""
    work.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="import-", dir=work))
    try:
        try:
            with zipfile.ZipFile(backup) as archive:
                names = {item.filename: item for item in archive.infolist()}
                for number, name in enumerate(_BACKUP_FILES):
                    item = names.get(name)
                    if item is None:
                        if number < 2:
                            raise ImportSourceError(f"The backup has no {name}; it is not a backup of eM Client")
                        continue
                    if item.file_size > MAXIMUM_DATABASE_BYTES:
                        raise ImportSourceError(f"{name} in the backup is far too large")
                    with archive.open(item) as source, open(folder / f"{number}.dat", "wb") as target:
                        shutil.copyfileobj(source, target, 1024 * 1024)
        except (OSError, zipfile.BadZipFile) as error:
            raise ImportSourceError(f"The backup cannot be read: {error}") from None
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _open_database(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)


def read_backup(backup: Path, work: Path) -> BackupData:
    """The marks of the messages and the contacts of Local Folders from a backup (Menu > Backup); work is where it is unpacked for a moment."""
    with _unpacked(backup, work) as folder:
        try:
            connection = _open_database(folder / "0.dat")
            try:
                folders = {identifier: folder_key(tuple(path.strip("/").split("/"))) for identifier, path in connection.execute("SELECT id, path FROM Folders") if isinstance(identifier, int) and isinstance(path, str)}
            finally:
                connection.close()
            connection = _open_database(folder / "1.dat")
            try:
                marks, tagged = _marks(connection, folders)
            finally:
                connection.close()
            contacts: tuple[SourceContact, ...] = ()
            if (folder / "2.dat").exists():
                connection = _open_database(folder / "2.dat")
                try:
                    contacts = _contacts(connection)
                finally:
                    connection.close()
        except sqlite3.Error as error:
            raise ImportSourceError(f"The backup's database cannot be read: {error}") from None
    return BackupData(marks, contacts, tagged)
