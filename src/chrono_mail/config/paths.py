from dataclasses import dataclass
from pathlib import Path

import platformdirs

from chrono_mail.config.model import ACCOUNT_ID_PATTERN
from chrono_mail.core import ConfigurationError

# --- Version ------------------------------------------------------------
__version__ = "paths.py 0.0.9"

# --- Version history ----------------------------------------------------
# v0.0.9: Initial application directories

APPLICATION_NAME: str = "chrono-mail"


@dataclass(frozen=True, slots=True)
class AppDirectories:
    """Where the application keeps its settings and its data (per platform conventions)."""

    config_directory: Path
    data_directory: Path

    @property
    def config_file(self) -> Path:
        return self.config_directory / "config.json"

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


def default_directories() -> AppDirectories:
    return AppDirectories(
        platformdirs.user_config_path(APPLICATION_NAME, appauthor=False),
        platformdirs.user_data_path(APPLICATION_NAME, appauthor=False),
    )
