"""What Outlook keeps on the Windows computer where it runs: the default profile's POP3 and IMAP accounts (with their passwords, which
Windows decrypts for the same Windows user), their signatures, the data files, and through Outlook itself (only while it already runs,
never started: it could send its Outbox) the rules and the junk list.

Importable everywhere; elsewhere than on Windows it finds nothing. The registry, the decryption and Outlook itself sit behind Machine,
which the tests replace. A password is never logged, printed or shown (repr=False)."""

import importlib
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from chrono_mail.config.model import MAXIMUM_SIGNATURE_CHARACTERS
from chrono_mail.engine.outlook_rules import OutlookPart, OutlookRule, OutlookRuleSpec

# --- Version ------------------------------------------------------------
__version__ = "outlook_windows.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial version: the default profile's POP3 and IMAP accounts from the registry, their passwords (DPAPI), signatures and data
#           files; rules and the junk list read and rules made through a running Outlook (never started)

RegistryValue = str | int | bytes
Security = Literal["tls", "starttls", "none"]
OUTLOOK_KEY: str = r"Software\Microsoft\Office\16.0\Outlook"
ACCOUNTS_KEY: str = "9375CFF0413111d3B88A00104B2A6676"  # the key of the profile that holds its internet accounts
JUNK_PROPERTY: str = "http://schemas.microsoft.com/mapi/proptag/0x0E9A0102"  # PR_EXTENDED_RULE_MSG_CONDITION
RULE_NAME_PROPERTY: str = "http://schemas.microsoft.com/mapi/proptag/0x65EC001F"  # PR_RULE_MSG_NAME
JUNK_RULE_NAME: str = "Junk E-mail Rule"
INBOX: int = 6  # olFolderInbox
MAXIMUM_SIGNATURE_BYTES: int = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ProfileAccount:
    kind: Literal["pop3", "imap"]
    name: str
    email: str
    display_name: str
    incoming_host: str
    incoming_port: int
    incoming_security: Security
    incoming_user: str
    outgoing_host: str
    outgoing_port: int
    outgoing_security: Security
    outgoing_user: str = ""  # "" the incoming login
    outgoing_auth: bool = True
    leave_on_server: bool = True  # POP3: a copy stays on the server
    leave_days: int = 0  # the number of days Outlook shows (whether its removal is on is not known for sure)
    leave_other_bits: bool = False  # other options of "Leave on Server" were set (not taken over)
    password: str | None = field(default=None, repr=False)
    outgoing_password: str | None = field(default=None, repr=False)
    signature: str = ""


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    accounts: tuple[ProfileAccount, ...]
    files: tuple[Path, ...]  # the data files (.pst) in Outlook's folders


@dataclass(frozen=True, slots=True)
class Machine:
    """What is read from this computer; the tests give their own."""

    profile_entries: Callable[[], tuple[str, list[dict[str, RegistryValue]]] | None]  # the default profile's name and its account entries
    decrypt: Callable[[bytes], bytes | None]  # CryptUnprotectData for this Windows user
    outlook: Callable[[], object | None]  # the Outlook that runs now (None when it does not run; never started)
    signatures: Path | None
    data_folders: tuple[Path, ...]


# --- the real computer ----------------------------------------------------------------------------------------------------------------


def _registry_entries() -> tuple[str, list[dict[str, RegistryValue]]] | None:
    if sys.platform != "win32":
        return None
    winreg = importlib.import_module("winreg")  # a module of Windows only: typed by hand below

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, OUTLOOK_KEY) as outlook:
            try:
                default = str(winreg.QueryValueEx(outlook, "DefaultProfile")[0])
            except OSError:
                default = ""
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, OUTLOOK_KEY + r"\Profiles") as profiles:
            names = [str(winreg.EnumKey(profiles, number)) for number in range(int(winreg.QueryInfoKey(profiles)[0]))]
        if default not in names:
            if len(names) != 1:
                return None
            default = names[0]
        entries: list[dict[str, RegistryValue]] = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{OUTLOOK_KEY}\Profiles\{default}\{ACCOUNTS_KEY}") as accounts:
            for number in range(winreg.QueryInfoKey(accounts)[0]):
                with winreg.OpenKey(accounts, winreg.EnumKey(accounts, number)) as entry:
                    values: dict[str, RegistryValue] = {}
                    for index in range(winreg.QueryInfoKey(entry)[1]):
                        name, data, _ = winreg.EnumValue(entry, index)
                        if isinstance(data, (str, int, bytes)):
                            values[str(name)] = data
                    entries.append(values)
        return default, entries
    except OSError:
        return None


def _unprotect(blob: bytes) -> bytes | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    buffer = ctypes.create_string_buffer(blob, len(blob))
    source = Blob(len(blob), ctypes.cast(buffer, ctypes.c_void_p))
    target = Blob()
    windll = getattr(ctypes, "windll")  # noqa: B009 - exists on Windows only, so the type checker cannot know it
    if not windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)):
        return None
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        windll.kernel32.LocalFree(ctypes.c_void_p(target.pbData))


def _running_outlook() -> object | None:
    """The Outlook that runs now, or None: never started (a start could send what waits in its Outbox)."""
    if sys.platform != "win32":
        return None
    try:
        pythoncom = importlib.import_module("pythoncom")
        client = importlib.import_module("win32com.client")
        getattr(pythoncom, "CoInitialize")()  # the commands run in a thread of their own
        return getattr(client, "GetActiveObject")("Outlook.Application")
    except Exception:  # noqa: BLE001 - Outlook does not run (or pywin32 is missing): no rules
        return None


def _real_machine() -> Machine:
    appdata = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    folders = [Path(local) / "Microsoft" / "Outlook"] if local else []
    folders.append(Path.home() / "Documents" / "Outlook Files")
    return Machine(_registry_entries, _unprotect, _running_outlook, Path(appdata) / "Microsoft" / "Signatures" if appdata else None, tuple(folders))


def machine() -> Machine:
    """This computer (the tests put their own in its place)."""
    return _real_machine()


# --- the profile ----------------------------------------------------------------------------------------------------------------------


def _text(values: dict[str, RegistryValue], name: str) -> str:
    value = values.get(name)
    if isinstance(value, bytes):
        return value.decode("utf-16-le", "replace").split("\x00", 1)[0].strip()
    return value.strip() if isinstance(value, str) else ""


def _number(values: dict[str, RegistryValue], name: str) -> int | None:
    value = values.get(name)
    return value & 0xFFFFFFFF if isinstance(value, int) else None


def _security(values: dict[str, RegistryValue], prefix: str, port: int) -> Security:
    """SSL/TLS (1), STARTTLS (2) or none (0) as Outlook keeps it; Auto (3) or unknown by the port."""
    found = _number(values, f"{prefix} Secure Connection")
    if found is None:
        found = _number(values, f"{prefix} Use SSL")
    if found == 0 or found is None:
        return "none"
    if found == 1:
        return "tls"
    if found == 2:
        return "starttls"
    return "tls" if port in (993, 995, 465) else "starttls"


def _password(data: Machine, values: dict[str, RegistryValue], name: str) -> str | None:
    value = values.get(name)
    if not isinstance(value, bytes) or len(value) < 2 or value[0] != 2:
        return None
    clear = data.decrypt(value[1:])
    if clear is None:
        return None
    return clear.decode("utf-16-le", "replace").split("\x00", 1)[0] or None


def _read_signature(data: Machine, name: str) -> str:
    if not name or data.signatures is None or any(part in name for part in ("/", "\\", "..")):
        return ""
    path = data.signatures / f"{name}.txt"
    try:
        raw = path.read_bytes()[:MAXIMUM_SIGNATURE_BYTES]
    except OSError:
        return ""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16", "replace")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", "replace")
    return text.replace("\r\n", "\n").strip()[:MAXIMUM_SIGNATURE_CHARACTERS]


def _account(data: Machine, values: dict[str, RegistryValue]) -> ProfileAccount | None:
    kind: Literal["pop3", "imap"]
    if _text(values, "POP3 Server"):
        kind, prefix = "pop3", "POP3"
    elif _text(values, "IMAP Server"):
        kind, prefix = "imap", "IMAP"
    else:
        return None  # an Exchange or other service: its mail is on its server, read by Outlook only
    incoming_port = _number(values, f"{prefix} Port") or (995 if kind == "pop3" else 993)
    outgoing_port = _number(values, "SMTP Port") or 587
    leave = _number(values, "Leave on Server") or 0
    incoming_user = _text(values, f"{prefix} User")
    smtp_user = _text(values, "SMTP User")
    email = _text(values, "Email")
    return ProfileAccount(
        kind, _text(values, "Account Name") or email, email, _text(values, "Display Name"), _text(values, f"{prefix} Server"), incoming_port,
        _security(values, prefix, incoming_port), incoming_user, _text(values, "SMTP Server"), outgoing_port, _security(values, "SMTP", outgoing_port),
        smtp_user if smtp_user and smtp_user != incoming_user else "", _number(values, "SMTP Use Auth") != 0,
        bool(leave & 1) if kind == "pop3" else True, leave >> 16, bool(leave & 0xFFFE), _password(data, values, f"{prefix} Password"),
        _password(data, values, "SMTP Password") if smtp_user and smtp_user != incoming_user else None, _read_signature(data, _text(values, "New Signature")),
    )


def read_profile(data: Machine | None = None) -> Profile | None:
    """The default profile of Outlook on this computer, or None (not Windows, or no profile that can be read)."""
    data = data or machine()
    found = data.profile_entries()
    if found is None:
        return None
    name, entries = found
    accounts = tuple(account for account in (_account(data, values) for values in entries) if account is not None)
    files: list[Path] = []
    for folder in data.data_folders:
        try:
            files.extend(sorted(path for path in folder.glob("*.pst") if path.is_file()))
        except OSError:
            continue
    return Profile(name, accounts, tuple(dict.fromkeys(files)))


# --- Outlook itself (COM) -------------------------------------------------------------------------------------------------------------


def _get(target: object, name: str) -> object:
    return getattr(target, name)


def _call(target: object, name: str, *arguments: object) -> object:
    method = getattr(target, name)
    if not callable(method):
        raise TypeError(name)
    return method(*arguments)


def _items(collection: object) -> list[object]:
    count = _get(collection, "Count")
    return [_call(collection, "Item", number) for number in range(1, (count if isinstance(count, int) else 0) + 1)]


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (tuple, list)):
        return tuple(str(item) for item in value if str(item))
    return ()


CONDITION_KINDS: tuple[str, ...] = (
    "Account", "AnyCategory", "Body", "BodyOrSubject", "Category", "CC", "FormName", "From", "FromAnyRSSFeed", "FromRssFeed", "HasAttachment", "Importance",
    "MeetingInviteOrUpdate", "MessageHeader", "NotTo", "OnLocalMachine", "OnlyToMe", "OnOtherMachine", "RecipientAddress", "SenderAddress",
    "SenderInSpecifiedAddressList", "SentTo", "Sensitivity", "Subject", "ToMe", "ToOrCc",
)
ACTION_KINDS: tuple[str, ...] = (
    "AssignToCategory", "CC", "ClearCategories", "CopyToFolder", "Delete", "DeletePermanently", "DesktopAlert", "Forward", "ForwardAsAttachment", "MarkAsTask",
    "MoveToFolder", "NewItemAlert", "NotifyDelivery", "NotifyRead", "PlaySound", "Redirect", "Stop",
)


def _folder_path(folder: object) -> str:
    """Outlook's \\\\store\\Inbox\\Sub as Inbox/Sub."""
    parts = [part for part in str(_get(folder, "FolderPath")).split("\\") if part]
    return "/".join(parts[1:])


def _values(kind: str, part: object) -> tuple[str, ...]:
    if kind in ("Body", "BodyOrSubject", "Subject", "MessageHeader"):
        return _strings(_get(part, "Text"))
    if kind in ("SenderAddress", "RecipientAddress"):
        return _strings(_get(part, "Address"))
    if kind in ("From", "SentTo"):
        return tuple(str(_get(recipient, "Address")) for recipient in _items(_get(part, "Recipients")))
    if kind in ("Category", "AssignToCategory"):
        return _strings(_get(part, "Categories"))
    if kind == "Account":
        return (str(_get(_get(part, "Account"), "SmtpAddress")),)
    if kind in ("MoveToFolder", "CopyToFolder"):
        return (_folder_path(_get(part, "Folder")),)
    return ()


def _parts(holder: object, kinds: tuple[str, ...]) -> tuple[OutlookPart, ...]:
    found: list[OutlookPart] = []
    for kind in kinds:
        try:
            part = _get(holder, kind)
            if _get(part, "Enabled") is not True:
                continue
        except Exception:  # noqa: BLE001 - a property this Outlook does not have
            continue
        try:
            found.append(OutlookPart(kind, _values(kind, part)))
        except Exception:  # noqa: BLE001 - its values cannot be read: the rule is not imported
            found.append(OutlookPart(kind + "?"))
    return tuple(found)


def _default_store(outlook: object) -> object:
    return _get(_call(outlook, "GetNamespace", "MAPI"), "DefaultStore")


def outlook_rules(outlook: object) -> tuple[OutlookRule, ...]:
    """The rules of Outlook's default store (where Outlook keeps them, not per account)."""
    rules: list[OutlookRule] = []
    for rule in _items(_call(_default_store(outlook), "GetRules")):
        rules.append(OutlookRule(
            str(_get(rule, "Name")), _get(rule, "Enabled") is True, _get(rule, "RuleType") == 0, _parts(_get(rule, "Conditions"), CONDITION_KINDS),
            _parts(_get(rule, "Exceptions"), CONDITION_KINDS), _parts(_get(rule, "Actions"), ACTION_KINDS),
        ))
    return tuple(rules)


def junk_list(outlook: object) -> bytes | None:
    """The junk list's data: the hidden extended rule of the default store's Inbox, when it is the junk rule."""
    try:
        inbox = _call(_call(outlook, "GetNamespace", "MAPI"), "GetDefaultFolder", INBOX)
        item = _call(inbox, "GetStorage", "IPM.ExtendedRule.Message", 2)  # olIdentifyByMessageClass
        accessor = _get(item, "PropertyAccessor")
        if str(_call(accessor, "GetProperty", RULE_NAME_PROPERTY)) != JUNK_RULE_NAME:
            return None
        data = _call(accessor, "GetProperty", JUNK_PROPERTY)
    except Exception:  # noqa: BLE001 - no junk list in this store
        return None
    return bytes(data) if isinstance(data, (bytes, bytearray, memoryview)) else None


def _folder(store: object, path: str) -> object:
    folder = _call(store, "GetRootFolder")
    for name in path.split("/"):
        folder = _call(_get(folder, "Folders"), "Item", name)
    return folder


def outlook_rule_names(outlook: object) -> set[str]:
    return {str(_get(rule, "Name")).casefold() for rule in _items(_call(_default_store(outlook), "GetRules"))}


def make_rules(outlook: object, specs: list[OutlookRuleSpec]) -> list[str]:
    """Make the rules in Outlook's default store and save them; returns the names of those Outlook refused, with why."""
    namespace = _call(outlook, "GetNamespace", "MAPI")
    store = _get(namespace, "DefaultStore")
    rules = _call(store, "GetRules")
    refused: list[str] = []
    for spec in specs:
        try:
            rule = _call(rules, "Create", spec.name, 0)  # olRuleReceive
            for holder, parts in ((_get(rule, "Conditions"), spec.conditions), (_get(rule, "Exceptions"), spec.exceptions)):
                for part in parts:
                    target = _get(holder, part.kind)
                    setattr(target, "Enabled", True)
                    if part.kind in ("Subject", "Body"):
                        setattr(target, "Text", part.values)
                    elif part.kind == "SenderAddress":
                        setattr(target, "Address", part.values)
                    elif part.kind == "Category":
                        setattr(target, "Categories", part.values)
            if spec.account:
                account = next(item for item in _items(_get(namespace, "Accounts")) if str(_get(item, "SmtpAddress")).lower() == spec.account.lower())
                condition = _get(_get(rule, "Conditions"), "Account")
                setattr(condition, "Enabled", True)
                setattr(condition, "Account", account)
            for part in spec.actions:
                target = _get(_get(rule, "Actions"), part.kind)
                setattr(target, "Enabled", True)
                if part.kind == "MoveToFolder":
                    setattr(target, "Folder", _folder(store, part.values[0]))
                elif part.kind == "AssignToCategory":
                    setattr(target, "Categories", part.values)
                elif part.kind == "MarkAsTask":
                    setattr(target, "MarkInterval", 4)  # olMarkNoDate
            setattr(rule, "Enabled", spec.enabled)
        except StopIteration:
            refused.append(f"{spec.name}: its account is not in Outlook")
            _remove(rules, spec.name)
        except Exception:  # noqa: BLE001 - Outlook refused a part: the rule is not made at all
            refused.append(f"{spec.name}: Outlook could not hold it (its folder or category may not be there)")
            _remove(rules, spec.name)
    _call(rules, "Save", False)
    return refused


def _remove(rules: object, name: str) -> None:
    try:
        _call(rules, "Remove", name)
    except Exception:  # noqa: BLE001 - it was not made
        pass
