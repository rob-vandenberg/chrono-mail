from collections.abc import Callable
from dataclasses import dataclass, field

from chrono_mail.config import LOCAL_FOLDER_KINDS, Sidebar, load_sidebar, save_sidebar
from chrono_mail.core import ConfigurationError
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_combined import VIEWS
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "commands_sidebar.py 1.9.270"

# --- Version history ----------------------------------------------------
# v1.9.270: R-19: typed favorite.*, sidebar.show-tree, sidebar.show-local-folder, group.*
# v1.1.252: a change of the sidebar file holds the engine's sidebar lock (a rename moves pins at the same time, R-41)
# v1.1.251: sidebar.show is typed (SIDEBAR_SHOW), and EMPTY_SIDEBAR for a window that has none yet (R-11)
# v1.1.204: sidebar.show-local-folder: hide or show a standard folder of Local Folders; the hidden ones in SidebarInfo
# v1.1.193: sidebar.show forgets the hidden trees and group places of removed accounts too (not only their pins)
# v1.1.191: sidebar.show-tree: hide or show an account's own folder tree; the hidden ones in SidebarInfo
# v1.1.190: group.add needs at least one account (an empty group could never be shown)
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
    hidden_trees: tuple[str, ...] = ()  # the accounts whose own folder tree is not shown
    hidden_local_folders: tuple[str, ...] = ()  # the standard folders of Local Folders that are not shown (LOCAL_FOLDER_KINDS)


EMPTY_SIDEBAR: SidebarInfo = SidebarInfo((), (), ())


def _info(sidebar: Sidebar) -> SidebarInfo:
    return SidebarInfo(
        tuple(PinInfo(item.pin_id, item.account, item.folder, item.name) for item in sidebar.pins),
        tuple(GroupInfo(item.group_id, item.name, item.accounts) for item in sidebar.groups),
        sidebar.hidden,
        sidebar.hidden_trees,
        sidebar.hidden_local_folders,
    )


def _change(engine: Engine, change: Callable[[Sidebar], Sidebar]) -> SidebarInfo:
    try:
        with engine.sidebar_lock:
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
    gone = {item.account for item in sidebar.pins} | set(sidebar.hidden_trees) | {member for group in sidebar.groups for member in group.accounts}
    for account in gone - known:  # what belonged to an account that is gone is not shown: pins, hidden trees, group places
        sidebar = sidebar.without_account(account)
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


@dataclass(frozen=True, slots=True)
class TreeParameters:
    account: str = field(metadata=option("An account with a folder tree of its own"))
    visible: bool = field(default=True, metadata=option("false hides its folder tree in the folder list"))


def sidebar_show_tree(engine: Engine, parameters: TreeParameters) -> SidebarInfo:
    account = engine.resolve_account(parameters.account).account.account_id
    return _change(engine, lambda sidebar: sidebar.with_hidden_tree(account, not parameters.visible))


@dataclass(frozen=True, slots=True)
class LocalFolderParameters:
    folder: str = field(metadata=option("A standard folder of Local Folders: " + ", ".join(LOCAL_FOLDER_KINDS)))
    visible: bool = field(default=True, metadata=option("false hides it (and the folders inside it) in the folder list"))


def sidebar_show_local_folder(engine: Engine, parameters: LocalFolderParameters) -> SidebarInfo:
    if parameters.folder not in LOCAL_FOLDER_KINDS:
        raise UsageError(f"The standard folders of Local Folders are {', '.join(LOCAL_FOLDER_KINDS)}")
    return _change(engine, lambda sidebar: sidebar.with_hidden_local_folder(parameters.folder, not parameters.visible))


def favorite_show(engine: Engine, parameters: VisibleParameters) -> SidebarInfo:
    if parameters.view not in VIEWS:
        raise UsageError(f"The standard favorites are {', '.join(VIEWS)}")
    return _change(engine, lambda sidebar: sidebar.with_hidden(parameters.view, not parameters.visible))


@dataclass(frozen=True, slots=True)
class GroupAddParameters:
    name: str = field(metadata=option("The name of the group"))
    account: tuple[str, ...] = field(default=(), metadata=option("Accounts to put in it (repeat for more; at least one)"))


def group_add(engine: Engine, parameters: GroupAddParameters) -> SidebarInfo:
    members = [engine.resolve_account(name).account.account_id for name in parameters.account]
    if not members:
        raise UsageError("A group needs at least one account")  # an empty group could never be shown

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


SIDEBAR_SHOW: TypedCommand[NoParameters, SidebarInfo] = TypedCommand("sidebar.show", NoParameters, SidebarInfo)


FAVORITE_PIN: TypedCommand[PinParameters, SidebarInfo] = TypedCommand("favorite.pin", PinParameters, SidebarInfo)
FAVORITE_UNPIN: TypedCommand[PinnedParameters, SidebarInfo] = TypedCommand("favorite.unpin", PinnedParameters, SidebarInfo)
FAVORITE_RENAME: TypedCommand[RenamePinParameters, SidebarInfo] = TypedCommand("favorite.rename", RenamePinParameters, SidebarInfo)
FAVORITE_REORDER: TypedCommand[ReorderParameters, SidebarInfo] = TypedCommand("favorite.reorder", ReorderParameters, SidebarInfo)
SIDEBAR_SHOW_TREE: TypedCommand[TreeParameters, SidebarInfo] = TypedCommand("sidebar.show-tree", TreeParameters, SidebarInfo)
SIDEBAR_SHOW_LOCAL_FOLDER: TypedCommand[LocalFolderParameters, SidebarInfo] = TypedCommand("sidebar.show-local-folder", LocalFolderParameters, SidebarInfo)
FAVORITE_SHOW: TypedCommand[VisibleParameters, SidebarInfo] = TypedCommand("favorite.show", VisibleParameters, SidebarInfo)
GROUP_ADD: TypedCommand[GroupAddParameters, SidebarInfo] = TypedCommand("group.add", GroupAddParameters, SidebarInfo)
GROUP_RENAME: TypedCommand[GroupRenameParameters, SidebarInfo] = TypedCommand("group.rename", GroupRenameParameters, SidebarInfo)
GROUP_REMOVE: TypedCommand[GroupParameters, SidebarInfo] = TypedCommand("group.remove", GroupParameters, SidebarInfo)
GROUP_ASSIGN: TypedCommand[AssignParameters, SidebarInfo] = TypedCommand("group.assign", AssignParameters, SidebarInfo)


def register(registry: CommandRegistry) -> None:
    registry.register(SIDEBAR_SHOW.id, "Show the pinned folders and the groups of accounts", NoParameters, sidebar_show)
    registry.register(FAVORITE_PIN.id, "Pin a folder to Favorites", PinParameters, favorite_pin)
    registry.register(FAVORITE_UNPIN.id, "Take a pinned folder out of Favorites", PinnedParameters, favorite_unpin)
    registry.register(FAVORITE_RENAME.id, "Give a pinned folder another name in Favorites", RenamePinParameters, favorite_rename)
    registry.register(FAVORITE_REORDER.id, "Put the pinned folders in another order", ReorderParameters, favorite_reorder)
    registry.register(SIDEBAR_SHOW_TREE.id, "Show or hide the folder tree of an account in the folder list", TreeParameters, sidebar_show_tree)
    registry.register(SIDEBAR_SHOW_LOCAL_FOLDER.id, "Show or hide a standard folder of Local Folders in the folder list", LocalFolderParameters, sidebar_show_local_folder)
    registry.register(FAVORITE_SHOW.id, "Switch a standard favorite (All Inboxes, Unread ...) on or off", VisibleParameters, favorite_show)
    registry.register(GROUP_ADD.id, "Make a group of accounts", GroupAddParameters, group_add)
    registry.register(GROUP_RENAME.id, "Rename a group", GroupRenameParameters, group_rename)
    registry.register(GROUP_REMOVE.id, "Remove a group (its accounts stay)", GroupParameters, group_remove)
    registry.register(GROUP_ASSIGN.id, "Put an account in a group, or take it out of its group", AssignParameters, group_assign)
