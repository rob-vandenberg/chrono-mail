from collections.abc import Callable
from dataclasses import dataclass, field

from chrono_mail.config import Sidebar, load_sidebar, save_sidebar
from chrono_mail.core import ConfigurationError
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_combined import VIEWS
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "commands_sidebar.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: Initial commands for pinned favorites and account groups


@dataclass(frozen=True, slots=True)
class PinInfo:
    pin_id: str
    account: str
    folder: str
    name: str  # what the user called it; empty: the name of the folder


@dataclass(frozen=True, slots=True)
class GroupInfo:
    group_id: str
    name: str
    accounts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SidebarInfo:
    pins: tuple[PinInfo, ...]
    groups: tuple[GroupInfo, ...]
    hidden: tuple[str, ...]  # the standard favorites that are switched off


def _info(sidebar: Sidebar) -> SidebarInfo:
    return SidebarInfo(
        tuple(PinInfo(item.pin_id, item.account, item.folder, item.name) for item in sidebar.pins),
        tuple(GroupInfo(item.group_id, item.name, item.accounts) for item in sidebar.groups),
        sidebar.hidden,
    )


def _change(engine: Engine, change: Callable[[Sidebar], Sidebar]) -> SidebarInfo:
    try:
        sidebar = change(load_sidebar(engine.directories.sidebar_file))
        save_sidebar(engine.directories.sidebar_file, sidebar)
    except ConfigurationError as error:
        raise UsageError(str(error)) from None
    return _info(sidebar)


def sidebar_show(engine: Engine, parameters: NoParameters) -> SidebarInfo:
    """The pinned folders, the groups of accounts and the standard favorites that are switched off."""
    try:
        sidebar = load_sidebar(engine.directories.sidebar_file)
    except ConfigurationError as error:
        raise UsageError(str(error)) from None
    known = {item.account.account_id for item in engine.config().accounts}
    for item in sidebar.pins:  # what belonged to an account that is gone is not shown
        if item.account not in known:
            sidebar = sidebar.without_account(item.account)
    return _info(sidebar)


@dataclass(frozen=True, slots=True)
class PinParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder to pin to Favorites"))


def favorite_pin(engine: Engine, parameters: PinParameters) -> SidebarInfo:
    account = engine.resolve_account(parameters.account).account.account_id
    with open_mailbox(engine, account) as mailbox:
        folder = "/".join(mailbox.folder(parameters.folder))
    return _change(engine, lambda sidebar: sidebar.pin(account, folder))


@dataclass(frozen=True, slots=True)
class PinnedParameters:
    pin: str = field(metadata=option("The id of the pinned folder (see sidebar.show)"))


def favorite_unpin(engine: Engine, parameters: PinnedParameters) -> SidebarInfo:
    return _change(engine, lambda sidebar: sidebar.unpin(parameters.pin))


@dataclass(frozen=True, slots=True)
class RenamePinParameters:
    pin: str = field(metadata=option("The id of the pinned folder"))
    name: str = field(metadata=option("The new name (empty: the name of the folder)"))


def favorite_rename(engine: Engine, parameters: RenamePinParameters) -> SidebarInfo:
    return _change(engine, lambda sidebar: sidebar.renamed_pin(parameters.pin, parameters.name))


@dataclass(frozen=True, slots=True)
class ReorderParameters:
    pin: tuple[str, ...] = field(metadata=option("The ids of the pinned folders in the order they are to have (repeat)"))


def favorite_reorder(engine: Engine, parameters: ReorderParameters) -> SidebarInfo:
    return _change(engine, lambda sidebar: sidebar.reordered_pins(parameters.pin))


@dataclass(frozen=True, slots=True)
class VisibleParameters:
    view: str = field(metadata=option("A standard favorite: " + ", ".join(VIEWS)))
    visible: bool = field(default=True, metadata=option("false switches it off"))


def favorite_show(engine: Engine, parameters: VisibleParameters) -> SidebarInfo:
    if parameters.view not in VIEWS:
        raise UsageError(f"The standard favorites are {', '.join(VIEWS)}")
    return _change(engine, lambda sidebar: sidebar.with_hidden(parameters.view, not parameters.visible))


@dataclass(frozen=True, slots=True)
class GroupAddParameters:
    name: str = field(metadata=option("The name of the group"))
    account: tuple[str, ...] = field(default=(), metadata=option("Accounts to put in it (repeat for more)"))


def group_add(engine: Engine, parameters: GroupAddParameters) -> SidebarInfo:
    members = [engine.resolve_account(name).account.account_id for name in parameters.account]

    def change(sidebar: Sidebar) -> Sidebar:
        sidebar, group = sidebar.with_group(parameters.name)
        for member in members:
            sidebar = sidebar.assigned(member, group.group_id)
        return sidebar

    return _change(engine, change)


@dataclass(frozen=True, slots=True)
class GroupParameters:
    group: str = field(metadata=option("The id of the group (see sidebar.show)"))


@dataclass(frozen=True, slots=True)
class GroupRenameParameters:
    group: str = field(metadata=option("The id of the group"))
    name: str = field(metadata=option("The new name"))


def group_rename(engine: Engine, parameters: GroupRenameParameters) -> SidebarInfo:
    return _change(engine, lambda sidebar: sidebar.renamed_group(parameters.group, parameters.name))


def group_remove(engine: Engine, parameters: GroupParameters) -> SidebarInfo:
    """Remove the group; its accounts stay as they are, outside any group."""
    return _change(engine, lambda sidebar: sidebar.without_group(parameters.group))


@dataclass(frozen=True, slots=True)
class AssignParameters:
    account: str = field(metadata=option("The account"))
    group: str | None = field(default=None, metadata=option("The id of the group; leave out to take the account out of its group"))


def group_assign(engine: Engine, parameters: AssignParameters) -> SidebarInfo:
    account = engine.resolve_account(parameters.account).account.account_id
    return _change(engine, lambda sidebar: sidebar.assigned(account, parameters.group))


def register(registry: CommandRegistry) -> None:
    registry.register("sidebar.show", "Show the pinned folders and the groups of accounts", NoParameters, sidebar_show)
    registry.register("favorite.pin", "Pin a folder to Favorites", PinParameters, favorite_pin)
    registry.register("favorite.unpin", "Take a pinned folder out of Favorites", PinnedParameters, favorite_unpin)
    registry.register("favorite.rename", "Give a pinned folder another name in Favorites", RenamePinParameters, favorite_rename)
    registry.register("favorite.reorder", "Put the pinned folders in another order", ReorderParameters, favorite_reorder)
    registry.register("favorite.show", "Switch a standard favorite (All Inboxes, Unread ...) on or off", VisibleParameters, favorite_show)
    registry.register("group.add", "Make a group of accounts", GroupAddParameters, group_add)
    registry.register("group.rename", "Rename a group", GroupRenameParameters, group_rename)
    registry.register("group.remove", "Remove a group (its accounts stay)", GroupParameters, group_remove)
    registry.register("group.assign", "Put an account in a group, or take it out of its group", AssignParameters, group_assign)
