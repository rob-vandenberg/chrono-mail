import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any  # tomllib returns untyped dictionaries

# --- Version ------------------------------------------------------------
__version__ = "account.py 2.0.283"

# --- Version history ----------------------------------------------------
# v2.0.283: the login files are read from outside the project folder (SETTINGS_DIRECTORY, D-230)
# v0.5.123: A second account file (the Kerio server); an accepted certificate; the server names of Sent, Drafts and Trash
# v0.0.28: IMAP ports
# v0.0.3: Initial live account loader

# The logins live outside the project folder, never in it (D-230): ~/.claude/chrono-mail/live-tests, or where CHRONO_MAIL_LIVE_SETTINGS says
SETTINGS_DIRECTORY: Path = Path(os.environ.get("CHRONO_MAIL_LIVE_SETTINGS") or Path.home() / ".claude" / "chrono-mail" / "live-tests")
ACCOUNT_FILE: Path = SETTINGS_DIRECTORY / "account.local.toml"
EXAMPLE_FILE: Path = Path(__file__).resolve().parent / "account.example.toml"
KERIO_FILE: Path = ACCOUNT_FILE.with_name("kerio.local.toml")  # the same kind of file, for the account on the Kerio server


@dataclass(frozen=True)
class LiveAccount:
    host: str
    user: str
    password: str
    wrong_password_attempts: int
    smtp_plain_port: int
    smtp_starttls_port: int
    smtp_ssl_port: int
    pop3_port: int
    pop3_ssl_port: int
    imap_port: int = 143
    imap_ssl_port: int = 993
    fingerprint: str = ""  # the SHA-256 fingerprint of a certificate accepted for this server ('' for the usual checks)
    sent_folder: str = "Sent"  # the names of the standard folders on this server
    drafts_folder: str = "Drafts"
    trash_folder: str = "Trash"


def _text(settings: dict[str, Any], key: str, variable: str) -> str:
    value: object = os.environ.get(variable, settings.get(key))
    if not isinstance(value, str) or not value:
        raise RuntimeError(
            f"Live account setting '{key}' is missing. Set {variable} or create "
            f"{ACCOUNT_FILE} from {EXAMPLE_FILE}."
        )
    return value


def _number(settings: dict[str, Any], key: str, default: int, variable: str | None = None) -> int:
    value: object = settings.get(key, default)
    if variable is not None and variable in os.environ:
        value = os.environ[variable]
    return int(str(value))


def load_live_account(file: Path = ACCOUNT_FILE) -> LiveAccount:
    settings: dict[str, Any] = {}
    if file.exists():
        with file.open("rb") as handle:
            settings = tomllib.load(handle)
    return LiveAccount(
        host=_text(settings, "host", "CHRONOMAIL_LIVE_HOST"),
        user=_text(settings, "user", "CHRONOMAIL_LIVE_USER"),
        password=_text(settings, "password", "CHRONOMAIL_LIVE_PASSWORD"),
        wrong_password_attempts=_number(
            settings, "wrong_password_attempts", 1, "CHRONOMAIL_LIVE_WRONG_PASSWORD_ATTEMPTS"
        ),
        smtp_plain_port=_number(settings, "smtp_plain_port", 25),
        smtp_starttls_port=_number(settings, "smtp_starttls_port", 587),
        smtp_ssl_port=_number(settings, "smtp_ssl_port", 465),
        pop3_port=_number(settings, "pop3_port", 110),
        pop3_ssl_port=_number(settings, "pop3_ssl_port", 995),
        imap_port=_number(settings, "imap_port", 143),
        imap_ssl_port=_number(settings, "imap_ssl_port", 993),
        fingerprint=str(settings.get("fingerprint", "")),
        sent_folder=str(settings.get("sent_folder", "Sent")),
        drafts_folder=str(settings.get("drafts_folder", "Drafts")),
        trash_folder=str(settings.get("trash_folder", "Trash")),
    )
