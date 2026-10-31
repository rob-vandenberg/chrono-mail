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
    CollectionLook,
    ConfigurationError,
    ConnectionSecurity,
    PaletteColour,
    ServerSettings,
)
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "files.py 4.2.438"

# --- Version history ----------------------------------------------------
# v4.2.438: calendar_timed_background read and saved with the account (UR-02, D-297)
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash
# v2.1.308: junk_filter per account (D-238)
# v1.1.221: calendar_palette, calendar_own_colour_name, collection_looks
# v1.1.213: outgoing_same_login (missing: the same user name on both servers)
# v1.1.211: account_name
# v1.1.210: dav_left_out
# v0.5.132: skip_known_mail
# v0.0.66: address and user name of the calendar and contacts server
# v0.0.45: storage of an account
# v0.0.44: Folder choices, roles and check interval per account
# v0.0.43: accepted certificates
# v0.0.33: Rules
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
    pins = data.get("trusted_certificates", [])
    if not isinstance(pins, list) or not all(isinstance(item, str) for item in pins):
        raise ConfigurationError(f"Setting '{where}.trusted_certificates' is not a list of text")
    try:
        check = data.get("check_minutes", 0)
        if not isinstance(check, int) or isinstance(check, bool):
            raise ConfigurationError(f"Setting '{where}.check_minutes' is not a number")
        storage = data.get("storage", "own")
        if not isinstance(storage, str):
            raise ConfigurationError(f"Setting '{where}.storage' is not text")
        dav_url, dav_username = data.get("dav_url", ""), data.get("dav_username", "")
        if not isinstance(dav_url, str) or not isinstance(dav_username, str):
            raise ConfigurationError(f"Setting '{where}.dav_url' is not text")
        left_out = data.get("dav_left_out", [])
        if not isinstance(left_out, list) or not all(isinstance(item, str) for item in left_out):
            raise ConfigurationError(f"Setting '{where}.dav_left_out' is not a list of text")
        same_login = data.get("outgoing_same_login", account.outgoing.username == account.incoming.username)
        if not isinstance(same_login, bool):
            raise ConfigurationError(f"Setting '{where}.outgoing_same_login' is not true or false")
        account_name = data.get("account_name", "")
        if not isinstance(account_name, str):
            raise ConfigurationError(f"Setting '{where}.account_name' is not text")
        skip_known = data.get("skip_known_mail", False)
        if not isinstance(skip_known, bool):
            raise ConfigurationError(f"Setting '{where}.skip_known_mail' is not true or false")
        junk_filter = data.get("junk_filter", True)
        if not isinstance(junk_filter, bool):
            raise ConfigurationError(f"Setting '{where}.junk_filter' is not true or false")
        own_colour_name = data.get("calendar_own_colour_name", "")
        if not isinstance(own_colour_name, str):
            raise ConfigurationError(f"Setting '{where}.calendar_own_colour_name' is not text")
        timed_background = data.get("calendar_timed_background", False)
        if not isinstance(timed_background, bool):
            raise ConfigurationError(f"Setting '{where}.calendar_timed_background' is not true or false")
        return AccountSettings(
            account, Pop3Options(policy, days), encrypted, signature, tuple(pins), check, storage=storage, dav_url=dav_url, dav_username=dav_username,
            dav_left_out=tuple(left_out), outgoing_same_login=same_login, account_name=account_name, skip_known_mail=skip_known,
            calendar_palette=_palette(data.get("calendar_palette"), where), calendar_own_colour_name=own_colour_name,
            calendar_timed_background=timed_background,
            collection_looks=_looks(data.get("collection_looks", []), where), junk_filter=junk_filter,
        )
    except ConfigurationError as error:
        raise ConfigurationError(f"{where}: {error}") from None


def _palette(data: Any, where: str) -> tuple[PaletteColour, ...] | None:
    """An account's appointment colours: absent or null means never changed (the suggestions)."""
    if data is None:
        return None
    if not isinstance(data, list) or not all(isinstance(entry, list) and len(entry) == 3 and all(isinstance(part, str) for part in entry) for entry in data):
        raise ConfigurationError(f"Setting '{where}.calendar_palette' is not a list of [id, name, colour]")
    return tuple(PaletteColour(entry[0], entry[1], entry[2]) for entry in data)


LOOK_FIELDS: tuple[str, ...] = ("key", "name", "name_state", "base_name", "colour", "colour_state", "base_colour")


def _looks(data: Any, where: str) -> tuple[CollectionLook, ...]:
    if not isinstance(data, list) or not all(isinstance(entry, dict) and all(isinstance(entry.get(name, ""), str) for name in LOOK_FIELDS) for entry in data):
        raise ConfigurationError(f"Setting '{where}.collection_looks' is not a list of objects with text")
    return tuple(CollectionLook(*(str(entry.get(name, "")) for name in LOOK_FIELDS)) for entry in data)


def parse_config(text: str) -> AppConfig:
    """Read the settings. Unknown settings are ignored; anything wrong is a ConfigurationError."""
    try:
        data = load_json(text)
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
                    "trusted_certificates": list(settings.trusted_certificates),
                    "check_minutes": settings.check_minutes,
                    "storage": settings.storage,
                    "dav_url": settings.dav_url,
                    "dav_username": settings.dav_username,
                    "dav_left_out": list(settings.dav_left_out),
                    "account_name": settings.account_name,
                    "outgoing_same_login": settings.outgoing_same_login,
                    "skip_known_mail": settings.skip_known_mail,
                    "calendar_palette": None if settings.calendar_palette is None else [[entry.colour_id, entry.name, entry.colour] for entry in settings.calendar_palette],
                    "calendar_own_colour_name": settings.calendar_own_colour_name,
                    "calendar_timed_background": settings.calendar_timed_background,
                    "collection_looks": [{name: getattr(look, name) for name in LOOK_FIELDS} for look in settings.collection_looks],
                    "junk_filter": settings.junk_filter,
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
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass  # nothing was written there
        raise ConfigurationError(f"The settings could not be saved: {error.strerror}") from None
