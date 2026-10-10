import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any  # json.load returns untyped data; every value is checked below

from chrono_mail.config.model import ACCOUNT_ID_PATTERN
from chrono_mail.core import ConfigurationError

# --- Version ------------------------------------------------------------
__version__ = "sidebar.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: Initial pinned favorites and account groups

FORMAT_VERSION: int = 1
MAXIMUM_ITEMS: int = 500
MAXIMUM_TEXT: int = 200
MAXIMUM_FILE_BYTES: int = 1024 * 1024
ITEM_ID: re.Pattern[str] = re.compile(r"[0-9a-f]{16}")


def new_item_id() -> str:
    return os.urandom(8).hex()


@dataclass(frozen=True, slots=True)
class Pin:
    """A folder pinned to Favorites."""

    pin_id: str
    account: str
    folder: str
    name: str = ""  # empty: the name of the folder


@dataclass(frozen=True, slots=True)
class Group:
    """A named set of accounts that the folder list shows together, with one combined inbox."""

    group_id: str
    name: str
    accounts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Sidebar:
    pins: tuple[Pin, ...] = ()
    groups: tuple[Group, ...] = ()
    hidden: tuple[str, ...] = ()  # standard favorites (view ids) that are switched off

    def pin(self, account: str, folder: str) -> "Sidebar":
        """Pin a folder (a folder that is pinned already stays where it is)."""
        if any(item.account == account and item.folder == folder for item in self.pins):
            return self
        if len(self.pins) >= MAXIMUM_ITEMS:
            raise ConfigurationError("Too many pinned folders")
        return replace(self, pins=self.pins + (Pin(new_item_id(), account, folder),))

    def unpin(self, pin_id: str) -> "Sidebar":
        self._find_pin(pin_id)
        return replace(self, pins=tuple(item for item in self.pins if item.pin_id != pin_id))

    def renamed_pin(self, pin_id: str, name: str) -> "Sidebar":
        self._find_pin(pin_id)
        return replace(self, pins=tuple(replace(item, name=_text(name)) if item.pin_id == pin_id else item for item in self.pins))

    def reordered_pins(self, order: tuple[str, ...]) -> "Sidebar":
        """The pins in this order; pins that are not named keep their relative order after the named ones."""
        by_id = {item.pin_id: item for item in self.pins}
        for pin_id in order:
            if pin_id not in by_id:
                raise ConfigurationError(f"There is no pinned folder {pin_id}")
        named = tuple(by_id[pin_id] for pin_id in dict.fromkeys(order))
        return replace(self, pins=named + tuple(item for item in self.pins if item.pin_id not in order))

    def with_hidden(self, view: str, hidden: bool) -> "Sidebar":
        rest = tuple(item for item in self.hidden if item != view)
        return replace(self, hidden=rest + ((view,) if hidden else ()))

    def with_group(self, name: str) -> tuple["Sidebar", Group]:
        name = _text(name)
        if not name:
            raise ConfigurationError("A group needs a name")
        if len(self.groups) >= MAXIMUM_ITEMS:
            raise ConfigurationError("Too many groups")
        group = Group(new_item_id(), name)
        return replace(self, groups=self.groups + (group,)), group

    def renamed_group(self, group_id: str, name: str) -> "Sidebar":
        self._find_group(group_id)
        name = _text(name)
        if not name:
            raise ConfigurationError("A group needs a name")
        return replace(self, groups=tuple(replace(item, name=name) if item.group_id == group_id else item for item in self.groups))

    def without_group(self, group_id: str) -> "Sidebar":
        self._find_group(group_id)
        return replace(self, groups=tuple(item for item in self.groups if item.group_id != group_id))

    def assigned(self, account: str, group_id: str | None) -> "Sidebar":
        """Put the account in this group (an account is in one group at most), or in none."""
        if group_id is not None:
            self._find_group(group_id)
        groups = tuple(
            replace(item, accounts=tuple(a for a in item.accounts if a != account) + ((account,) if item.group_id == group_id else ()))
            for item in self.groups
        )
        return replace(self, groups=groups)

    def without_account(self, account: str) -> "Sidebar":
        """Forget an account that was removed."""
        return replace(
            self,
            pins=tuple(item for item in self.pins if item.account != account),
            groups=tuple(replace(item, accounts=tuple(a for a in item.accounts if a != account)) for item in self.groups),
        )

    def _find_pin(self, pin_id: str) -> Pin:
        for item in self.pins:
            if item.pin_id == pin_id:
                return item
        raise ConfigurationError(f"There is no pinned folder {pin_id}")

    def _find_group(self, group_id: str) -> Group:
        for item in self.groups:
            if item.group_id == group_id:
                return item
        raise ConfigurationError(f"There is no group {group_id}")


def _text(value: str) -> str:
    return " ".join(value.split())[:MAXIMUM_TEXT]


def _object(value: Any, name: str) -> dict[str, Any]:  # noqa: ANN401
    if not isinstance(value, dict):
        raise ConfigurationError(f"{name} must be an object")
    return value


def _string(data: dict[str, Any], key: str, name: str, default: str | None = None) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or len(value) > MAXIMUM_TEXT * 2:
        raise ConfigurationError(f"{name}: {key} must be text")
    return value


def parse_sidebar(text: str) -> Sidebar:
    try:
        data = _object(json.loads(text), "The sidebar file")
    except ValueError:
        raise ConfigurationError("The sidebar file is not valid JSON") from None
    if data.get("format") != FORMAT_VERSION:
        raise ConfigurationError("The sidebar file has an unknown format")
    pins: list[Pin] = []
    for raw in _list(data, "pins"):
        item = _object(raw, "A pinned folder")
        pin = Pin(_string(item, "id", "A pinned folder"), _string(item, "account", "A pinned folder"), _string(item, "folder", "A pinned folder"), _string(item, "name", "A pinned folder", ""))
        if ITEM_ID.fullmatch(pin.pin_id) is None or ACCOUNT_ID_PATTERN.fullmatch(pin.account) is None or not pin.folder:
            raise ConfigurationError("A pinned folder is not valid")
        pins.append(pin)
    groups: list[Group] = []
    for raw in _list(data, "groups"):
        item = _object(raw, "A group")
        members = _list(item, "accounts")
        group = Group(_string(item, "id", "A group"), _string(item, "name", "A group"), tuple(str(member) for member in members))
        if ITEM_ID.fullmatch(group.group_id) is None or not group.name or any(ACCOUNT_ID_PATTERN.fullmatch(member) is None for member in group.accounts):
            raise ConfigurationError("A group is not valid")
        groups.append(group)
    hidden = tuple(str(view) for view in _list(data, "hidden"))
    return Sidebar(tuple(pins), tuple(groups), hidden)


def _list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key, [])
    if not isinstance(value, list) or len(value) > MAXIMUM_ITEMS:
        raise ConfigurationError(f"{key} must be a list of at most {MAXIMUM_ITEMS} items")
    return value


def sidebar_to_text(sidebar: Sidebar) -> str:
    return json.dumps(
        {
            "format": FORMAT_VERSION,
            "pins": [{"id": item.pin_id, "account": item.account, "folder": item.folder, "name": item.name} for item in sidebar.pins],
            "groups": [{"id": item.group_id, "name": item.name, "accounts": list(item.accounts)} for item in sidebar.groups],
            "hidden": list(sidebar.hidden),
        },
        indent=2,
    ) + "\n"


def load_sidebar(path: Path) -> Sidebar:
    """The pinned folders and groups in this file; none when it does not exist yet. A damaged file is copied to <name>.bad and reported."""
    try:
        if not path.exists():
            return Sidebar()
        if path.stat().st_size > MAXIMUM_FILE_BYTES:
            raise ConfigurationError("The sidebar file is far too large")
        text = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        raise ConfigurationError("The sidebar file cannot be read") from None
    try:
        return parse_sidebar(text)
    except ConfigurationError as error:
        backup = path.with_name(path.name + ".bad")
        try:
            shutil.copyfile(path, backup)
        except OSError:
            raise ConfigurationError(f"{error} (and it could not be backed up)") from None
        raise ConfigurationError(f"{error}. The file was copied to {backup.name}.") from None


def save_sidebar(path: Path, sidebar: Sidebar) -> None:
    """Write the file in one atomic step, readable by the owner only."""
    temporary = path.with_name(f".{path.name}.{os.urandom(6).hex()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            os.write(descriptor, sidebar_to_text(sidebar).encode("utf-8"))
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
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass  # nothing was written there
        raise ConfigurationError(f"The sidebar could not be saved: {error.strerror}") from None
