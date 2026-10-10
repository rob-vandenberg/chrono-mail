from dataclasses import dataclass, field

from chrono_mail.config import ServerRole
from chrono_mail.core import AccountKind, ConfigurationError, FolderRole
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.protocols.imap import ImapClient
from chrono_mail.sync import ImapState, plan_folders

# --- Version ------------------------------------------------------------
__version__ = "commands_imap.py 0.0.44"

# --- Version history ----------------------------------------------------
# v0.0.44: Initial choice of the folders to synchronise and their roles

CHOOSABLE_ROLES: tuple[str, ...] = tuple(role.value for role in (FolderRole.SENT, FolderRole.DRAFTS, FolderRole.TRASH, FolderRole.JUNK, FolderRole.ARCHIVE))


@dataclass(frozen=True, slots=True)
class FolderAccount:
    account: str = field(metadata=option("The IMAP account"))


@dataclass(frozen=True, slots=True)
class FolderChoice:
    server_name: str
    local_path: str  # where the folder is kept here ('' when it is not synchronised)
    shown: bool  # is it synchronised?
    subscribed: bool  # does the server list it as subscribed?
    automatic_role: str  # what the program makes of it by itself: sent, drafts, trash, junk, archive, inbox or other
    chosen_role: str  # what the person said ('' for: automatic)


def _imap_account(engine: Engine, identifier: str) -> tuple[str, ImapState, ImapClient]:
    settings = engine.resolve_account(identifier)
    if settings.account.kind is not AccountKind.IMAP:
        raise UsageError("Only an IMAP account has folders on a server")
    password = engine.passwords.get(settings.account.account_id, ServerRole.INCOMING)
    if password is None:
        raise ConfigurationError("No password is stored for this account")
    account_id = settings.account.account_id
    state = ImapState(engine.directories.imap_state_file(account_id), durable=engine.durable)
    return account_id, state, ImapClient(settings.account.incoming, password, ssl_context=engine.tls_context(settings))


def account_folders(engine: Engine, parameters: FolderAccount) -> tuple[FolderChoice, ...]:
    """The folders on the server of an IMAP account, and which of them are synchronised."""
    _, state, client = _imap_account(engine, parameters.account)
    with client:
        listed = client.list_folders()
        subscribed = frozenset(entry.name for entry in client.list_folders(subscribed_only=True))
    plans, _ = plan_folders(listed, subscribed, frozenset(state.forced), dict(state.roles))
    automatic, _ = plan_folders(listed, subscribed)
    chosen = {plan.server_name: plan for plan in plans if plan.server_name not in state.hidden}
    by_name = {plan.server_name: plan for plan in automatic}
    choices: list[FolderChoice] = []
    for entry in listed:
        if "\\noselect" in entry.attributes or "\\nonexistent" in entry.attributes:
            continue
        plan = chosen.get(entry.name)
        auto = by_name.get(entry.name)
        choices.append(
            FolderChoice(entry.name, "/".join(plan.local_path) if plan else "", plan is not None, entry.name in subscribed or not subscribed, auto.role.value if auto else "other", state.roles.get(entry.name, ""))
        )
    return tuple(choices)


@dataclass(frozen=True, slots=True)
class SetFolders:
    account: str = field(metadata=option("The IMAP account"))
    show: tuple[str, ...] = field(default=(), metadata=option("A server folder to synchronise (repeat for more)"))
    hide: tuple[str, ...] = field(default=(), metadata=option("A server folder not to synchronise (repeat for more); the mail already here stays"))
    role: tuple[str, ...] = field(default=(), metadata=option("name=role: which standard folder a server folder is (sent, drafts, trash, junk, archive); name= for automatic"))


@dataclass(frozen=True, slots=True)
class SetFoldersResult:
    shown: int
    hidden: int
    roles: int


def account_set_folders(engine: Engine, parameters: SetFolders) -> SetFoldersResult:
    """Change which server folders are synchronised and which standard folder each is (takes effect at the next sync)."""
    settings = engine.resolve_account(parameters.account)
    if settings.account.kind is not AccountKind.IMAP:
        raise UsageError("Only an IMAP account has folders on a server")
    wanted: list[tuple[str, str]] = []
    for text in parameters.role:
        name, separator, role = text.rpartition("=")
        if not separator or not name or role not in ("", *CHOOSABLE_ROLES):
            raise UsageError(f"A role is written name=role with a role from {', '.join(CHOOSABLE_ROLES)} (or nothing): '{text}'")
        wanted.append((name, role))
    if set(parameters.show) & set(parameters.hide):
        raise UsageError("A folder cannot be both shown and hidden")
    state = ImapState(engine.directories.imap_state_file(settings.account.account_id), durable=engine.durable)
    for name in parameters.show:
        state.set_hidden(name, False)
    for name in parameters.hide:
        if name.upper() == "INBOX":
            raise UsageError("The Inbox is always synchronised")
        state.set_hidden(name, True)
    for name, role in wanted:
        state.set_role(name, role)
    return SetFoldersResult(len(parameters.show), len(parameters.hide), len(wanted))


def register(registry: CommandRegistry) -> None:
    registry.register("account.folders", "List the folders on the server of an IMAP account and which are synchronised", FolderAccount, account_folders)
    registry.register("account.set-folders", "Choose which folders of an IMAP account are synchronised and which standard folder each is", SetFolders, account_set_folders)
