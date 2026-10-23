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
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, option
from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "commands_backup.py 2.4.322"

# --- Version history ----------------------------------------------------
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


def register(registry: CommandRegistry) -> None:
    registry.register(BACKUP_CHOICES.id, "The accounts a backup can hold and what each one has", NoParameters, backup_choices, doing="Reading what can be backed up")
    registry.register(BACKUP_NEW_PASSWORD.id, "Make a new backup password", NoParameters, backup_new_password, doing="Making a backup password")
    registry.register(BACKUP_MAKE.id, "Back up mail, rules, calendars, contacts, tasks and settings to a .cmz file", BackupParameters, backup_make, doing="Backing up")
    registry.register(BACKUP_CANCEL.id, "Stop the backup that is being made", NoParameters, backup_cancel, doing="Stopping the backup")
