from dataclasses import dataclass
from pathlib import Path

import platformdirs

from chrono_mail.config.model import ACCOUNT_ID_PATTERN
from chrono_mail.core import SERVICE_NAME, ConfigurationError

# --- Version ------------------------------------------------------------
__version__ = "paths.py 4.2.419"

# --- Version history ----------------------------------------------------
# v4.2.419: updates_file: the day of the last check for updates and the version the person skipped (D-293)
# v3.6.371: profile_name: a profile's folder name, shown in the labels of its wallet entries (B-23, D-275)
# v3.6.370: credential_service: a profile keeps its passwords and key under a wallet name of its own, never mixed with the person's own
# v2.3.320: profile_directories: everything of one profile in one folder of its own (the demo for screenshots, D-243)
# v2.1.308: junk_filter_file: what the junk filter learned (D-238)
# v2.0.302: opened_directory: copies of attachments opened in another program (D-236)
# v0.0.60: Commands for collections, tags, contacts, events and tasks
# v0.0.47: Pinned favorites, account groups and search in combined views
# v0.0.33: rules file
# v0.0.31: IMAP accounts in the engine and the interface
# v0.0.9: Initial application directories

APPLICATION_NAME: str = "chrono-mail"


@dataclass(frozen=True, slots=True)
class AppDirectories:
    """Where the application keeps its settings and its data (per platform conventions)."""

    config_directory: Path
    data_directory: Path
    credential_service: str = SERVICE_NAME  # the name its passwords and key have in the wallet: a profile's own, never the person's
    profile_name: str = ""  # a profile's folder name, shown in the labels of its wallet entries; "" for the person's own

    @property
    def config_file(self) -> Path:
        return self.config_directory / "config.json"

    @property
    def rules_file(self) -> Path:
        return self.config_directory / "rules.json"

    @property
    def sidebar_file(self) -> Path:
        return self.config_directory / "sidebar.json"

    @property
    def tags_file(self) -> Path:
        return self.config_directory / "tags.json"

    def pim_directory(self, account_id: str) -> Path:
        """Where the calendars, task lists and address books of an account are kept."""
        return self.account_directory(account_id) / "pim"

    def account_directory(self, account_id: str) -> Path:
        if ACCOUNT_ID_PATTERN.fullmatch(account_id) is None:
            raise ConfigurationError("Not a valid account id")
        return self.data_directory / "accounts" / account_id

    def store_directory(self, account_id: str) -> Path:
        return self.account_directory(account_id) / "store"

    def index_file(self, account_id: str) -> Path:
        return self.account_directory(account_id) / "index.sqlite"

    def pop3_state_file(self, account_id: str) -> Path:
        return self.account_directory(account_id) / "pop3-state.log"

    @property
    def junk_filter_file(self) -> Path:
        """What the junk filter learned, for every account together (D-238)."""
        return self.data_directory / "junk-filter.bin"

    @property
    def updates_file(self) -> Path:
        """The day updates were last looked for and the version the person skipped, on this computer only (D-293)."""
        return self.data_directory / "updates.json"

    @property
    def opened_directory(self) -> Path:
        """Copies of attachments opened in another program, readable by the person only; emptied at the next start (D-236)."""
        return self.data_directory / "opened"

    def imap_state_file(self, account_id: str) -> Path:
        return self.account_directory(account_id) / "imap-state.log"


def default_directories() -> AppDirectories:
    return AppDirectories(
        platformdirs.user_config_path(APPLICATION_NAME, appauthor=False),
        platformdirs.user_data_path(APPLICATION_NAME, appauthor=False),
    )


def profile_directories(folder: Path) -> AppDirectories:
    """Everything of a profile in one folder of its own (its settings in config, its mail in data), apart from the person's own (D-243)."""
    return AppDirectories(folder / "config", folder / "data", f"{SERVICE_NAME} ({folder})", folder.name)
