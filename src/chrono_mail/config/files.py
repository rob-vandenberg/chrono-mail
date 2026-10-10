import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any  # json.load returns untyped data; every value is checked below

from chrono_mail.config.model import AccountSettings, AppConfig, Pop3DeletePolicy, Pop3Options
from chrono_mail.core import (
    Account,
    AccountKind,
    ConfigurationError,
    ConnectionSecurity,
    ServerSettings,
)

# --- Version ------------------------------------------------------------
__version__ = "files.py 0.0.20"

# --- Version history ----------------------------------------------------
# v0.0.20: Settings window, account editing, signatures
# v0.0.9: Initial settings file

FORMAT_VERSION: int = 1
MAXIMUM_CONFIG_BYTES: int = 4 * 1024 * 1024


def _text(data: dict[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ConfigurationError(f"Setting '{where}.{key}' is missing or not text")
    return value


def _server(data: object, where: str) -> ServerSettings:
    if not isinstance(data, dict):
        raise ConfigurationError(f"Setting '{where}' is missing")
    port = data.get("port")
    if not isinstance(port, int) or isinstance(port, bool):
        raise ConfigurationError(f"Setting '{where}.port' is missing or not a number")
    try:
        security = ConnectionSecurity(_text(data, "security", where))
    except ValueError:
        raise ConfigurationError(f"Setting '{where}.security' has an unknown value") from None
    return ServerSettings(_text(data, "host", where), port, security, _text(data, "username", where))


def _account(data: object, position: int) -> AccountSettings:
    where = f"accounts[{position}]"
    if not isinstance(data, dict):
        raise ConfigurationError(f"Setting '{where}' is not an object")
    try:
        kind = AccountKind(_text(data, "kind", where))
    except ValueError:
        raise ConfigurationError(f"Setting '{where}.kind' has an unknown value") from None
    pop3 = data.get("pop3", {})
    if not isinstance(pop3, dict):
        raise ConfigurationError(f"Setting '{where}.pop3' is not an object")
    try:
        policy = Pop3DeletePolicy(pop3.get("policy", Pop3DeletePolicy.KEEP.value))
    except ValueError:
        raise ConfigurationError(f"Setting '{where}.pop3.policy' has an unknown value") from None
    days = pop3.get("days", Pop3Options().days)
    if not isinstance(days, int) or isinstance(days, bool):
        raise ConfigurationError(f"Setting '{where}.pop3.days' is not a number")
    encrypted = data.get("encrypted", True)
    if not isinstance(encrypted, bool):
        raise ConfigurationError(f"Setting '{where}.encrypted' is not true or false")
    account = Account(
        _text(data, "id", where),
        _text(data, "display_name", where),
        _text(data, "email_address", where),
        kind,
        _server(data.get("incoming"), f"{where}.incoming"),
        _server(data.get("outgoing"), f"{where}.outgoing"),
    )
    signature = data.get("signature", "")
    if not isinstance(signature, str):
        raise ConfigurationError(f"Setting '{where}.signature' is not text")
    try:
        return AccountSettings(account, Pop3Options(policy, days), encrypted, signature)
    except ConfigurationError as error:
        raise ConfigurationError(f"{where}: {error}") from None


def parse_config(text: str) -> AppConfig:
    """Read the settings. Unknown settings are ignored; anything wrong is a ConfigurationError."""
    try:
        data = json.loads(text)
    except ValueError:
        raise ConfigurationError("The settings file is not valid JSON") from None
    if not isinstance(data, dict):
        raise ConfigurationError("The settings file does not hold an object")
    if data.get("version") != FORMAT_VERSION:
        raise ConfigurationError("The settings file has an unknown version")
    accounts = data.get("accounts", [])
    if not isinstance(accounts, list):
        raise ConfigurationError("Setting 'accounts' is not a list")
    key_id = data.get("key_id")
    if key_id is not None and not isinstance(key_id, str):
        raise ConfigurationError("Setting 'key_id' is not text")
    return AppConfig(tuple(_account(item, position) for position, item in enumerate(accounts)), key_id)


def _server_data(server: ServerSettings) -> dict[str, object]:
    return {"host": server.host, "port": server.port, "security": server.security.value, "username": server.username}


def config_to_text(config: AppConfig) -> str:
    return json.dumps(
        {
            "version": FORMAT_VERSION,
            "key_id": config.key_id,
            "accounts": [
                {
                    "id": settings.account.account_id,
                    "display_name": settings.account.display_name,
                    "email_address": settings.account.email_address,
                    "kind": settings.account.kind.value,
                    "encrypted": settings.encrypted,
                    "incoming": _server_data(settings.account.incoming),
                    "outgoing": _server_data(settings.account.outgoing),
                    "pop3": {"policy": settings.pop3.policy.value, "days": settings.pop3.days},
                    "signature": settings.signature,
                }
                for settings in config.accounts
            ],
        },
        indent=2,
        ensure_ascii=False,
    )


def load_config(path: Path) -> AppConfig:
    """The settings from this file; an empty configuration when the file does not exist yet.
    A damaged file is copied to <name>.bad and reported; it is never overwritten silently."""
    try:
        if not path.exists():
            return AppConfig()
        if path.stat().st_size > MAXIMUM_CONFIG_BYTES:
            raise ConfigurationError("The settings file is far too large")
        text = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        raise ConfigurationError("The settings file cannot be read") from None
    try:
        return parse_config(text)
    except ConfigurationError as error:
        backup = path.with_name(path.name + ".bad")
        try:
            shutil.copyfile(path, backup)
        except OSError:
            raise ConfigurationError(f"{error} (and it could not be backed up)") from None
        raise ConfigurationError(f"{error}. The file was copied to {backup.name}.") from None


def save_config(path: Path, config: AppConfig) -> None:
    """Write the settings in one atomic step, readable by the owner only."""
    temporary = path.with_name(f".{path.name}.{os.urandom(6).hex()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            os.write(descriptor, config_to_text(config).encode("utf-8"))
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
        temporary.unlink(missing_ok=True)
        raise ConfigurationError(f"The settings could not be saved: {error.strerror}") from None
