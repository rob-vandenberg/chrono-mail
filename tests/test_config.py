import dataclasses
import json
from pathlib import Path

import pytest
import sys

from chrono_mail.config import (
    AccountSettings,
    AppConfig,
    AppDirectories,
    InMemoryPasswordStore,
    KeyringPasswordStore,
    Pop3DeletePolicy,
    Pop3Options,
    ServerRole,
    config_to_text,
    default_directories,
    load_config,
    new_account_id,
    parse_config,
    save_config,
)
from chrono_mail.core import Account, AccountKind, ConfigurationError, ConnectionSecurity, ServerSettings
from tests.doubles import BrokenBackend, MemoryBackend

# --- Version ------------------------------------------------------------
__version__ = "test_config.py 2.0.287"

# --- Version history ----------------------------------------------------
# v2.0.287: the permission check not on Windows; the XDG folders only on Linux (the Windows computer's report)
# v1.9.266: R-45: the wrong values in a good settings file are cases with fixed names (their random account ids made new names each run)
# v1.1.221: the account's palette and the kept names and colours of its server's collections
# v1.1.213: outgoing_same_login, and its value for files from before
# v1.1.211: the account name
# v1.1.210: the folders of the calendar server left out
# v0.5.133: an error of the desktop's password service is a normal error with its reason
# v0.0.20: signature tests
# v0.0.9: Initial configuration tests


def make_account(identifier: str | None = None, name: str = "Test") -> Account:
    return Account(
        new_account_id() if identifier is None else identifier,
        name,
        "user@example.com",
        AccountKind.POP3,
        ServerSettings("pop.example.com", 995, ConnectionSecurity.TLS, "user@example.com"),
        ServerSettings("smtp.example.com", 587, ConnectionSecurity.STARTTLS, "user@example.com"),
    )


def make_config() -> AppConfig:
    first = AccountSettings(make_account(name="Café ☕"), Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DAYS, 14), True)
    second = AccountSettings(make_account(name="Second"), Pop3Options(), False)
    return AppConfig((first, second), "0123456789abcdef")


def test_account_ids_are_random_hex() -> None:
    ids = {new_account_id() for _ in range(500)}
    assert len(ids) == 500 and all(len(identifier) == 32 for identifier in ids)


def test_round_trip_through_text() -> None:
    config = make_config()
    assert parse_config(config_to_text(config)) == config


def test_the_same_login_goes_through_the_file_and_older_files_get_it_from_the_user_names() -> None:
    config = AppConfig((AccountSettings(make_account(), outgoing_same_login=False),), None)
    assert not parse_config(config_to_text(config)).accounts[0].outgoing_same_login
    data = json.loads(config_to_text(config))
    del data["accounts"][0]["outgoing_same_login"]
    assert parse_config(json.dumps(data)).accounts[0].outgoing_same_login  # both servers have user@example.com
    data["accounts"][0]["outgoing"]["username"] = "other"
    assert not parse_config(json.dumps(data)).accounts[0].outgoing_same_login


def test_the_account_name_goes_through_the_file() -> None:
    config = AppConfig((AccountSettings(make_account(), account_name="Kerio test"),), None)
    assert parse_config(config_to_text(config)).accounts[0].account_name == "Kerio test"
    assert parse_config(config_to_text(AppConfig((AccountSettings(make_account()),), None))).accounts[0].account_name == ""
    with pytest.raises(ConfigurationError):
        AccountSettings(make_account(), account_name="x" * 201)
    data = json.loads(config_to_text(config))
    data["accounts"][0]["account_name"] = 5
    with pytest.raises(ConfigurationError):
        parse_config(json.dumps(data))


def test_the_calendar_folders_left_out_go_through_the_file() -> None:
    first = AccountSettings(make_account(), dav_url="https://dav.example.com/", dav_left_out=("VTODO /cal/tasks/", "VCARD /book/"))
    config = AppConfig((first,), None)
    assert parse_config(config_to_text(config)).accounts[0].dav_left_out == ("VTODO /cal/tasks/", "VCARD /book/")
    data = json.loads(config_to_text(config))
    data["accounts"][0]["dav_left_out"] = "VTODO /cal/tasks/"
    with pytest.raises(ConfigurationError):
        parse_config(json.dumps(data))
    with pytest.raises(ConfigurationError):
        AccountSettings(make_account(), dav_left_out=("",))


def test_round_trip_through_a_file(tmp_path: Path) -> None:
    config = make_config()
    path = tmp_path / "config" / "config.json"
    save_config(path, config)
    assert load_config(path) == config
    assert sys.platform == "win32" or oct(path.stat().st_mode & 0o777) == "0o600"  # Windows has no such permissions
    assert [item.name for item in path.parent.iterdir()] == ["config.json"]  # no temporary file left


def test_saving_twice_replaces_the_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    save_config(path, make_config())
    save_config(path, AppConfig())
    assert load_config(path) == AppConfig()


def test_missing_file_gives_an_empty_configuration(tmp_path: Path) -> None:
    assert load_config(tmp_path / "nothing.json") == AppConfig()


def test_the_default_policy_never_deletes_from_the_server() -> None:
    assert Pop3Options().policy is Pop3DeletePolicy.KEEP


def test_no_password_is_ever_written_to_the_file() -> None:
    text = config_to_text(make_config())
    assert "password" not in text.replace('"policy"', "").lower()


def test_unknown_settings_are_ignored() -> None:
    config = make_config()
    data = json.loads(config_to_text(config))
    data["future_setting"] = {"a": 1}
    data["accounts"][0]["future"] = True
    assert parse_config(json.dumps(data)) == config


def test_account_lookup_add_replace_remove() -> None:
    config = make_config()
    first = config.accounts[0]
    assert config.find(first.account.account_id) == first
    renamed = dataclasses.replace(first, account=dataclasses.replace(first.account, display_name="Renamed"))
    replaced = config.with_account(renamed)
    assert len(replaced.accounts) == 2 and replaced.find(first.account.account_id).account.display_name == "Renamed"
    assert replaced.accounts[0].account.account_id == first.account.account_id  # replacing keeps the place in the list
    removed = config.without_account(first.account.account_id)
    assert len(removed.accounts) == 1
    with pytest.raises(ConfigurationError):
        removed.find(first.account.account_id)
    with pytest.raises(ConfigurationError):
        config.without_account("0" * 32)
    assert config.with_key_id(None).key_id is None


@pytest.mark.parametrize("identifier", ["", "../../etc", "a" * 31, "A" * 32, "g" * 32, "0" * 32 + "/x", "0" * 31 + "\n", "x/" * 16])
def test_unsafe_account_ids_are_rejected(identifier: str) -> None:
    with pytest.raises(ConfigurationError):
        AppConfig((AccountSettings(make_account(identifier)),))
    with pytest.raises(ConfigurationError):
        AppDirectories(Path("/c"), Path("/d")).store_directory(identifier)


def test_duplicate_account_ids_are_rejected() -> None:
    account = make_account()
    with pytest.raises(ConfigurationError):
        AppConfig((AccountSettings(account), AccountSettings(account)))


@pytest.mark.parametrize("key_id", ["", "xyz", "0123456789ABCDEF", "0123"])
def test_bad_key_ids_are_rejected(key_id: str) -> None:
    with pytest.raises(ConfigurationError):
        AppConfig(key_id=key_id)


@pytest.mark.parametrize("days", [0, -1, 3651, 10**9])
def test_days_out_of_range_are_rejected(days: int) -> None:
    with pytest.raises(ConfigurationError):
        Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DAYS, days)


def broken(change: str) -> str:
    data = json.loads(config_to_text(make_config()))
    exec(change, {"data": data})  # noqa: S102 - test-only edit of a parsed dictionary
    return json.dumps(data)


WRONG_TEXTS: list[str] = ["", "not json", "[]", "null", "{}", '{"version": 2}', '{"version": "1"}', '{"version": 1, "accounts": {}}', '{"version": 1, "accounts": [1]}', '{"version": 1, "key_id": 5}']
# Each wrong setting is an edit of a good settings file; the edit names the case, so a case has the same name in every run
# (the good file has new account ids each time it is made: as the name of a case they made it a different test each time).
WRONG_EDITS: list[str] = [
    'data["accounts"][0]["kind"] = "carrier pigeon"',
    'data["accounts"][0]["incoming"]["port"] = "995"',
    'data["accounts"][0]["incoming"]["port"] = True',
    'data["accounts"][0]["incoming"]["port"] = 99999',
    'data["accounts"][0]["incoming"]["security"] = "maybe"',
    'data["accounts"][0]["incoming"]["host"] = ""',
    'del data["accounts"][0]["incoming"]',
    'data["accounts"][0]["pop3"] = []',
    'data["accounts"][0]["pop3"]["policy"] = "burn"',
    'data["accounts"][0]["pop3"]["days"] = "30"',
    'data["accounts"][0]["encrypted"] = "yes"',
    'data["accounts"][0]["id"] = "../x"',
    'del data["accounts"][0]["display_name"]',
]


@pytest.mark.parametrize("text", WRONG_TEXTS)
def test_wrong_settings_are_a_configuration_error(text: str) -> None:
    with pytest.raises(ConfigurationError):
        parse_config(text)


@pytest.mark.parametrize("edit", WRONG_EDITS)
def test_a_wrong_value_in_a_good_settings_file_is_a_configuration_error(edit: str) -> None:
    with pytest.raises(ConfigurationError):
        parse_config(broken(edit))


def test_damaged_file_is_backed_up_and_reported(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{ this is damaged", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="config.json.bad"):
        load_config(path)
    assert (tmp_path / "config.json.bad").read_text("utf-8") == "{ this is damaged"
    assert path.read_text("utf-8") == "{ this is damaged"  # never overwritten silently


@pytest.mark.parametrize("content", [b"\xff\xfe\x00bad bytes", b"\x00" * 50])
def test_binary_garbage_is_a_configuration_error(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "config.json"
    path.write_bytes(content)
    with pytest.raises(ConfigurationError):
        load_config(path)


def test_huge_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_bytes(b" " * (5 * 1024 * 1024))
    with pytest.raises(ConfigurationError, match="large"):
        load_config(path)


@pytest.mark.skipif(sys.platform != "linux", reason="the XDG folders are the Linux convention")
def test_directories_follow_the_platform_conventions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    directories = default_directories()
    identifier = new_account_id()
    assert directories.config_file == tmp_path / "cfg" / "chrono-mail" / "config.json"
    assert directories.store_directory(identifier) == tmp_path / "data" / "chrono-mail" / "accounts" / identifier / "store"
    assert directories.index_file(identifier).name == "index.sqlite"
    assert directories.pop3_state_file(identifier).name == "pop3-state.log"


def test_password_stores_keep_passwords_per_account_and_direction() -> None:
    backend = MemoryBackend()
    stores = [InMemoryPasswordStore(), KeyringPasswordStore("chrono-mail-test", backend)]
    for store in stores:
        store.set("a" * 32, ServerRole.INCOMING, "secret one")
        store.set("a" * 32, ServerRole.OUTGOING, "secret two")
        store.set("b" * 32, ServerRole.INCOMING, "secret three")
        assert store.get("a" * 32, ServerRole.INCOMING) == "secret one"
        assert store.get("a" * 32, ServerRole.OUTGOING) == "secret two"
        assert store.get("c" * 32, ServerRole.INCOMING) is None
        store.delete("a" * 32, ServerRole.INCOMING)
        store.delete("a" * 32, ServerRole.INCOMING)
        assert store.get("a" * 32, ServerRole.INCOMING) is None and store.get("b" * 32, ServerRole.INCOMING) == "secret three"
    assert {name for (_, name) in backend.entries} == {f"password-{'b' * 32}-incoming", f"password-{'a' * 32}-outgoing"}
    assert {service for (service, _) in backend.entries} == {"chrono-mail-test"}


def test_unavailable_credential_store_is_a_configuration_error_without_the_password() -> None:
    store = KeyringPasswordStore("chrono-mail-test", BrokenBackend())
    for action in (lambda: store.get("a" * 32, ServerRole.INCOMING), lambda: store.set("a" * 32, ServerRole.INCOMING, "hunter2"), lambda: store.delete("a" * 32, ServerRole.INCOMING)):
        with pytest.raises(ConfigurationError) as raised:
            action()
        assert "hunter2" not in str(raised.value)


def test_a_signature_is_saved_and_old_files_without_one_still_load() -> None:
    config = make_config()
    signed = config.with_account(dataclasses.replace(config.accounts[0], signature="Rob\nChrono Mail"))
    assert parse_config(config_to_text(signed)) == signed
    data = json.loads(config_to_text(signed))
    del data["accounts"][0]["signature"]
    assert parse_config(json.dumps(data)).accounts[0].signature == ""


@pytest.mark.parametrize("signature", [5, ["a"], None])
def test_a_signature_must_be_text(signature: object) -> None:
    data = json.loads(config_to_text(make_config()))
    data["accounts"][0]["signature"] = signature
    with pytest.raises(ConfigurationError, match="signature"):
        parse_config(json.dumps(data))


def test_a_huge_signature_is_refused() -> None:
    data = json.loads(config_to_text(make_config()))
    data["accounts"][0]["signature"] = "x" * 5000
    with pytest.raises(ConfigurationError, match="at most"):
        parse_config(json.dumps(data))
    with pytest.raises(ConfigurationError):
        dataclasses.replace(make_config().accounts[0], signature="x" * 4001)


def test_an_error_of_the_desktop_password_service_is_a_normal_error_with_its_reason() -> None:
    from tests.doubles import SessionLostBackend

    store = KeyringPasswordStore("chrono-mail-test", SessionLostBackend())
    for action in (lambda: store.get("a" * 32, ServerRole.INCOMING), lambda: store.set("a" * 32, ServerRole.INCOMING, "hunter2"), lambda: store.delete("a" * 32, ServerRole.INCOMING)):
        with pytest.raises(ConfigurationError) as raised:
            action()
        assert "Can't find session" in str(raised.value) and "Try again" in str(raised.value) and "hunter2" not in str(raised.value)



def test_the_palette_and_the_kept_looks_go_through_text_and_are_checked() -> None:
    from dataclasses import replace

    from chrono_mail.core import CollectionLook, PaletteColour

    config = make_config()
    first = config.accounts[0]
    look = CollectionLook("VEVENT /dav/calendars/ann/events/", "Home", "waiting", "Calendar", "#123456", "kept", "")
    changed = AppConfig((replace(first, calendar_palette=(PaletteColour("g11", "Rood", "#ff0000"),), calendar_own_colour_name="Mine", collection_looks=(look,)),))
    again = parse_config(config_to_text(changed))
    assert again == changed and again.accounts[0].look(look.key) == look
    assert parse_config(config_to_text(config)).accounts[0].calendar_palette is None  # never changed: the suggestions
    with pytest.raises(ConfigurationError):
        replace(first, calendar_palette=(PaletteColour("g11", "Rood", "red"),))
    with pytest.raises(ConfigurationError):
        replace(first, collection_looks=(replace(look, name_state="maybe"),))
    with pytest.raises(ConfigurationError):
        replace(first, collection_looks=(look, look))
