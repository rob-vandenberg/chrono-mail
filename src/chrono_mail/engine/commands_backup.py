from dataclasses import dataclass, field
from pathlib import Path

from chrono_mail.engine.backup import (
    CALENDARS,
    CONTACTS,
    MESSAGES,
    RULES,
    SETTINGS,
    TASKS,
    BackupChoice,
    backup_entries,
    cancel_backup,
    new_backup_password,
    write_backup,
)
from chrono_mail.config import ServerRole
from chrono_mail.core import AccountKind, ChronoMailError, ConnectionSecurity
from chrono_mail.engine.commands_accounts import AccountCheckParameters, NoParameters, Security, account_check
from chrono_mail.engine.commands_dav import DiscoverParameters, dav_discover
from chrono_mail.engine.newkey import recovery_code_not_stored
from chrono_mail.engine.restore import RestoreChoice, ensure_key, read_contents, restore_backup
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, option
from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "commands_backup.py 3.5.346"

# --- Version history ----------------------------------------------------
# v3.5.346: key.ensure also says whether the recovery code was never confirmed as stored, also for a key made before (RV-03)
# v2.4.334: backup.restore says where the damaged messages and items were
# v2.4.328: backup.restore says how many damaged messages and items it left out (W5)
# v2.4.327: backup.restore says how many messages of the Outbox went to Drafts and how many scheduled ones were paused (D-245)
# v2.4.326: backup.contents per account with its parts and counts; backup.restore of the chosen accounts, parts and passwords (D-244)
# v2.4.324: Restore: key.ensure, backup.contents, backup.restore, backup.check-account; backup.cancel stops a restore too (D-244)
# v2.4.322: Initial: backup.choices, backup.new-password, backup.make, backup.cancel (Menu > File > Back up…, D-244)


@dataclass(frozen=True, slots=True)
class BackupChoiceRow:
    """An account the backup can hold (or Local Folders, or this computer's calendars, contacts and tasks) and the parts it has."""

    entry_id: str
    name: str
    parts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BackupChoices:
    rows: tuple[BackupChoiceRow, ...]


@dataclass(frozen=True, slots=True)
class BackupPassword:
    password: str


@dataclass(frozen=True, slots=True)
class BackupParameters:
    path: str = field(metadata=option("The backup file to write (.cmz is added when missing)"))
    accounts: tuple[str, ...] = field(metadata=option("The accounts to back up (their ids; local-folders and this-computer for what is kept only here)"))
    messages: bool = field(default=True, metadata=option("Back up the messages"))
    rules: bool = field(default=True, metadata=option("Back up the mail rules"))
    calendars: bool = field(default=True, metadata=option("Back up the calendar items"))
    contacts: bool = field(default=True, metadata=option("Back up the contacts"))
    tasks: bool = field(default=True, metadata=option("Back up the tasks"))
    settings: bool = field(default=True, metadata=option("Back up the settings"))
    passwords: bool = field(default=False, metadata=option("Put the account passwords in the backup (only with a backup password)"))
    password: str | None = field(default=None, metadata=option("The backup password (from backup.new-password); none: not protected", secret=True))
    window_settings: str = field(default="", metadata=option("The window's own settings, as the window writes them"))
    overwrite: bool = field(default=False, metadata=option("Replace an existing file"))


@dataclass(frozen=True, slots=True)
class BackupResult:
    path: str
    size: int
    accounts: int
    messages: int
    items: int
    unreadable: int


@dataclass(frozen=True, slots=True)
class CancelResult:
    cancelled: bool


@dataclass(frozen=True, slots=True)
class KeyEnsured:
    made: bool  # a new key was made now
    show_code: bool = False  # the key's recovery code was never confirmed as stored (made now or before): shown before anything else happens (RV-03)


@dataclass(frozen=True, slots=True)
class ReadParameters:
    path: str = field(metadata=option("The backup file (.cmz)"))
    password: str | None = field(default=None, metadata=option("The backup password, when the backup is protected", secret=True))


@dataclass(frozen=True, slots=True)
class ContentsRow:
    entry_id: str  # the account's id; local-folders and this-computer for what was kept only on that computer
    name: str
    parts: tuple[str, ...]
    messages: int
    items: int
    here: bool


@dataclass(frozen=True, slots=True)
class ContentsResult:
    created: str
    version: str
    parts: tuple[str, ...]
    accounts: tuple[ContentsRow, ...]
    messages: int
    items: int
    passwords: bool
    protected: bool


@dataclass(frozen=True, slots=True)
class RestoreParameters:
    path: str = field(metadata=option("The backup file (.cmz)"))
    password: str | None = field(default=None, metadata=option("The backup password, when the backup is protected", secret=True))
    remove_first: bool = field(default=False, metadata=option("Remove all data on this computer first (never on a server; put back when the restore fails)"))
    replace_existing: bool = field(default=False, metadata=option("Replace items that already exist (otherwise they are skipped)"))
    accounts: tuple[str, ...] = field(default=(), metadata=option("The accounts to restore (as backup.contents names them); none given: all"))
    messages: bool = field(default=True, metadata=option("Restore the messages"))
    rules: bool = field(default=True, metadata=option("Restore the mail rules"))
    calendars: bool = field(default=True, metadata=option("Restore the calendar items"))
    contacts: bool = field(default=True, metadata=option("Restore the contacts"))
    tasks: bool = field(default=True, metadata=option("Restore the tasks"))
    settings: bool = field(default=True, metadata=option("Restore the settings"))
    passwords: bool = field(default=True, metadata=option("Restore the account passwords, when the backup holds them"))


@dataclass(frozen=True, slots=True)
class RestoreResult:
    made: tuple[str, ...]
    filled: tuple[str, ...]
    messages: int
    items: int
    skipped: int
    window_settings: str
    notes: tuple[str, ...]
    to_drafts: int = 0  # messages of the Outbox put in Drafts (D-245)
    paused: int = 0  # scheduled messages paused because their time had passed (D-245)
    damaged: int = 0  # messages and items of the backup that could not be read and were left out (W5)
    damaged_places: tuple[str, ...] = ()  # where they were: "1 message in Inbox of Work"


@dataclass(frozen=True, slots=True)
class CheckAccountParameters:
    account: str = field(metadata=option("The account"))


@dataclass(frozen=True, slots=True)
class CheckAccountResult:
    account_id: str
    connected: bool
    text: str


SECURITY_WORDS: dict[ConnectionSecurity, Security] = {ConnectionSecurity.NONE: "none", ConnectionSecurity.STARTTLS: "starttls", ConnectionSecurity.TLS: "tls"}


def key_ensure(engine: Engine, parameters: NoParameters) -> KeyEnsured:
    """Make this computer's encryption key when it has none yet."""
    made = ensure_key(engine)
    return KeyEnsured(made, recovery_code_not_stored(engine))


def backup_contents(engine: Engine, parameters: ReadParameters) -> ContentsResult:
    """What a backup holds: its date, its accounts, its parts and its counts."""
    contents = read_contents(engine, Path(parameters.path).expanduser(), parameters.password)
    return ContentsResult(
        contents.created, contents.version, contents.parts,
        tuple(ContentsRow(row.entry_id, row.name, row.parts, row.messages, row.items, row.here) for row in contents.accounts),
        contents.messages, contents.items, contents.passwords, contents.protected,
    )


def backup_restore(engine: Engine, parameters: RestoreParameters) -> RestoreResult:
    """Put a backup back on this computer: an account that is not here whole, one that is here with what it lacks (the server's data stays
    the server's to bring)."""
    wanted = {
        MESSAGES: parameters.messages, RULES: parameters.rules, CALENDARS: parameters.calendars, CONTACTS: parameters.contacts,
        TASKS: parameters.tasks, SETTINGS: parameters.settings,
    }
    report = restore_backup(engine, RestoreChoice(
        Path(parameters.path).expanduser(), parameters.password, parameters.remove_first, parameters.replace_existing, parameters.accounts or None,
        frozenset(part for part, chosen in wanted.items() if chosen), parameters.passwords,
    ))
    return RestoreResult(tuple(report.made), tuple(report.filled), report.messages, report.items, report.skipped, report.window_settings, tuple(report.notes), report.to_drafts, report.paused, len(report.damaged), tuple(report.damaged_places))


def backup_check_account(engine: Engine, parameters: CheckAccountParameters) -> CheckAccountResult:
    """Test an account the restore made, as Settings > Accounts > Edit > Test connection does, with its stored passwords and accepted
    certificates. Nothing is changed."""
    settings = engine.resolve_account(parameters.account)
    account = settings.account
    try:
        if account.kind is AccountKind.DAV:
            dav_discover(engine, DiscoverParameters(account.account_id))
            return CheckAccountResult(account.account_id, True, "Connected")
        if engine.passwords.get(account.account_id, ServerRole.INCOMING) is None:
            return CheckAccountResult(account.account_id, False, "No password: the backup did not hold it. Type it in Settings > Accounts > Edit")
        result = account_check(engine, AccountCheckParameters(
            account.incoming.username, account.incoming.host, account.outgoing.host, incoming_port=account.incoming.port,
            incoming_security=SECURITY_WORDS[account.incoming.security], outgoing_port=account.outgoing.port, outgoing_security=SECURITY_WORDS[account.outgoing.security],
            outgoing_username=None if settings.outgoing_same_login else account.outgoing.username, kind="imap" if account.kind is AccountKind.IMAP else "pop3",
            trusted_certificate=settings.trusted_certificates, account=account.account_id,
        ))
    except ChronoMailError as error:
        return CheckAccountResult(account.account_id, False, str(error))
    if result.incoming_ok and result.outgoing_ok:
        return CheckAccountResult(account.account_id, True, "Connected")
    problems = ([f"Incoming: {result.incoming_message}"] if not result.incoming_ok else []) + ([f"Outgoing: {result.outgoing_message}"] if not result.outgoing_ok else [])
    return CheckAccountResult(account.account_id, False, " ".join(problems))


def backup_choices(engine: Engine, parameters: NoParameters) -> BackupChoices:
    """The accounts a backup can hold and what each one has."""
    return BackupChoices(tuple(BackupChoiceRow(entry.entry_id, entry.name, entry.parts) for entry in backup_entries(engine)))


def backup_new_password(engine: Engine, parameters: NoParameters) -> BackupPassword:
    """A new backup password: 24 random capitals, lower-case letters and digits."""
    return BackupPassword(new_backup_password())


def backup_make(engine: Engine, parameters: BackupParameters) -> BackupResult:
    """Write a backup of the chosen parts of the chosen accounts (a .cmz file). Only reads: nothing changes here or on a server."""
    chosen = {
        MESSAGES: parameters.messages, RULES: parameters.rules, CALENDARS: parameters.calendars, CONTACTS: parameters.contacts,
        TASKS: parameters.tasks, SETTINGS: parameters.settings,
    }
    report = write_backup(
        engine,
        BackupChoice(
            Path(parameters.path).expanduser(), parameters.accounts, frozenset(part for part, wanted in chosen.items() if wanted), parameters.passwords,
            parameters.password, parameters.window_settings, parameters.overwrite,
        ),
    )
    return BackupResult(str(report.path), report.size, report.accounts, report.messages, report.items, report.unreadable)


def backup_cancel(engine: Engine, parameters: NoParameters) -> CancelResult:
    """Stop the backup that is being made; its unfinished file is removed."""
    return CancelResult(cancel_backup())


BACKUP_CHOICES: TypedCommand[NoParameters, BackupChoices] = TypedCommand("backup.choices", NoParameters, BackupChoices)
BACKUP_NEW_PASSWORD: TypedCommand[NoParameters, BackupPassword] = TypedCommand("backup.new-password", NoParameters, BackupPassword)
BACKUP_MAKE: TypedCommand[BackupParameters, BackupResult] = TypedCommand("backup.make", BackupParameters, BackupResult)
BACKUP_CANCEL: TypedCommand[NoParameters, CancelResult] = TypedCommand("backup.cancel", NoParameters, CancelResult)


KEY_ENSURE: TypedCommand[NoParameters, KeyEnsured] = TypedCommand("key.ensure", NoParameters, KeyEnsured)
BACKUP_CONTENTS: TypedCommand[ReadParameters, ContentsResult] = TypedCommand("backup.contents", ReadParameters, ContentsResult)
BACKUP_RESTORE: TypedCommand[RestoreParameters, RestoreResult] = TypedCommand("backup.restore", RestoreParameters, RestoreResult)
BACKUP_CHECK_ACCOUNT: TypedCommand[CheckAccountParameters, CheckAccountResult] = TypedCommand("backup.check-account", CheckAccountParameters, CheckAccountResult)


def register(registry: CommandRegistry) -> None:
    registry.register(KEY_ENSURE.id, "Make this computer's encryption key when it has none yet", NoParameters, key_ensure, doing="Making the encryption key")
    registry.register(BACKUP_CONTENTS.id, "What a backup holds", ReadParameters, backup_contents, doing="Reading a backup")
    registry.register(BACKUP_RESTORE.id, "Put a backup back on this computer", RestoreParameters, backup_restore, doing="Restoring a backup")
    registry.register(BACKUP_CHECK_ACCOUNT.id, "Test the connection of an account a restore made", CheckAccountParameters, backup_check_account, doing="Testing a restored account")
    registry.register(BACKUP_CHOICES.id, "The accounts a backup can hold and what each one has", NoParameters, backup_choices, doing="Reading what can be backed up")
    registry.register(BACKUP_NEW_PASSWORD.id, "Make a new backup password", NoParameters, backup_new_password, doing="Making a backup password")
    registry.register(BACKUP_MAKE.id, "Back up mail, rules, calendars, contacts, tasks and settings to a .cmz file", BackupParameters, backup_make, doing="Backing up")
    registry.register(BACKUP_CANCEL.id, "Stop the backup or restore that runs", NoParameters, backup_cancel, doing="Stopping the backup")
