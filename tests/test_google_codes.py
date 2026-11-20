import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from chrono_mail.config import ServerRole
from chrono_mail.core import LOCAL_ACCOUNT_ID, AuthenticationError, ConfigurationError
from chrono_mail.engine import Engine, UsageError, commands_google, google_access
from chrono_mail.protocols.google import CODES_REFUSED, GoogleClient, SignedIn, refresh_access
from tests.enginefactory import make_engine
from tests.fakedav import FakeDav
from tests.test_engine_commands import call
from tests.test_google_sign_in import CLIENT, CODES, google_answers, servers, signs_in

# --- Version ------------------------------------------------------------
__version__ = "test_google_codes.py 4.6.646"

# --- Version history ----------------------------------------------------
# v4.6.646: Initial tests: each Google account keeps the client ID and client secret the person typed, the secret only in the credential
#           store; account.edit changes them for both accounts of one Google address; removing an account removes its secret; the one copy
#           of the former file's codes (B-34, D-364)

__all__ = ["servers"]  # the fixture of Google's two servers, used here by its name


def added(engine: Engine, monkeypatch: pytest.MonkeyPatch, address: str = "ann@gmail.com", **codes: object) -> tuple[str, ...]:
    signs_in(monkeypatch, address, "refresh-1")
    return tuple(call(engine, "google.sign-in", **(codes or CODES)).account_ids)


def test_both_accounts_of_a_sign_in_keep_the_codes_typed_the_secret_only_in_the_credential_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: tuple[FakeDav, FakeDav],
) -> None:
    engine = make_engine(tmp_path)
    accounts = added(engine, monkeypatch)
    assert len(accounts) == 2
    for account_id in accounts:
        assert engine.config().find(account_id).google_client_id == CLIENT.client_id
        assert engine.passwords.get(account_id, ServerRole.GOOGLE_SECRET) == CLIENT.client_secret
    assert CLIENT.client_secret not in engine.directories.config_file.read_text("utf-8")  # OR-6: never in the settings file
    saved = [item for item in json.loads(engine.directories.config_file.read_text("utf-8"))["accounts"] if item["google"]]
    assert [item["google_client_id"] for item in saved] == [CLIENT.client_id] * 2


def test_signing_in_again_uses_the_accounts_own_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: tuple[FakeDav, FakeDav]) -> None:
    engine = make_engine(tmp_path)
    accounts = added(engine, monkeypatch)
    used: list[GoogleClient] = []

    class Waiter:
        def __init__(self, client: GoogleClient, hint: str = "") -> None:
            used.append(client)
            self.url = "https://accounts.google.com/"

        def wait(self, seconds: float, stop: object = None) -> SignedIn:
            return SignedIn("refresh-2", "access-1", 3600, "ann@gmail.com", "")

        def check(self) -> None:
            pass

        def cancel(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(commands_google, "SignInWaiter", Waiter)
    call(engine, "google.sign-in", account=accounts[0], client_id="typed-elsewhere", client_secret="not-this-one")
    assert used == [CLIENT]


def test_an_account_without_codes_says_where_to_fill_them_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: tuple[FakeDav, FakeDav]) -> None:
    engine = make_engine(tmp_path)
    account = added(engine, monkeypatch)[0]
    engine.passwords.delete(account, ServerRole.GOOGLE_SECRET)  # a secret the credential store lost
    with pytest.raises(ConfigurationError, match="no client ID and client secret yet: fill them in on the General tab"):
        google_access.google_client(engine, engine.config().find(account))
    with pytest.raises(ConfigurationError, match="no client ID and client secret yet"):
        call(engine, "google.sign-in", account=account)


def test_google_refusing_the_codes_says_where_to_check_them(monkeypatch: pytest.MonkeyPatch) -> None:
    google_answers(monkeypatch, error="invalid_client")
    with pytest.raises(AuthenticationError) as refused:
        refresh_access(CLIENT, "refresh-1")
    assert str(refused.value) == CODES_REFUSED


def test_editing_the_codes_changes_both_accounts_of_the_google_address_and_no_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: tuple[FakeDav, FakeDav],
) -> None:
    engine = make_engine(tmp_path)
    ann = added(engine, monkeypatch)
    calendar, contacts = servers
    monkeypatch.setattr(commands_google, "PARTS", (("Google Calendar", f"http://127.0.0.1:{calendar.port}/dav/bob/"),
                                                   ("Google Contacts", f"http://localhost:{contacts.port}/dav/bob/")))
    bob = added(engine, monkeypatch, "bob@gmail.com")
    call(engine, "account.edit", account=ann[0], google_client_id=" new-id ", google_client_secret="new-secret")
    for account_id in ann:
        assert engine.config().find(account_id).google_client_id == "new-id"
        assert engine.passwords.get(account_id, ServerRole.GOOGLE_SECRET) == "new-secret"
    for account_id in bob:
        assert engine.config().find(account_id).google_client_id == CLIENT.client_id
        assert engine.passwords.get(account_id, ServerRole.GOOGLE_SECRET) == CLIENT.client_secret
    call(engine, "account.edit", account=ann[1], google_client_secret="")  # a secret field left empty keeps the stored one
    assert engine.passwords.get(ann[0], ServerRole.GOOGLE_SECRET) == "new-secret"


def test_an_empty_client_id_and_codes_for_another_kind_of_account_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: tuple[FakeDav, FakeDav],
) -> None:
    engine = make_engine(tmp_path)
    account = added(engine, monkeypatch)[0]
    with pytest.raises(UsageError, match="must not be empty"):
        call(engine, "account.edit", account=account, google_client_id=" ")
    assert engine.config().find(account).google_client_id == CLIENT.client_id
    with pytest.raises(UsageError, match="Only a Google account"):
        call(engine, "account.edit", account=LOCAL_ACCOUNT_ID, google_client_id="an-id")  # Local Folders, made with the Google accounts


def test_removing_a_google_account_removes_its_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: tuple[FakeDav, FakeDav]) -> None:
    engine = make_engine(tmp_path)
    calendar, contacts = added(engine, monkeypatch)
    call(engine, "account.remove", account=calendar)
    assert engine.passwords.get(calendar, ServerRole.GOOGLE_SECRET) is None
    assert engine.passwords.get(contacts, ServerRole.GOOGLE_SECRET) == CLIENT.client_secret


@pytest.fixture
def former_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "former" / "google-client.json"
    monkeypatch.setattr(google_access, "FORMER_CODES_FILE", path)
    yield path


def without_codes(engine: Engine, account_ids: tuple[str, ...]) -> None:
    """As the accounts were before 4.6.646: no client ID, no secret."""
    import dataclasses

    for account_id in account_ids:
        engine.passwords.delete(account_id, ServerRole.GOOGLE_SECRET)
        engine.change_config(lambda fresh, each=account_id: fresh.with_account(dataclasses.replace(fresh.find(each), google_client_id="")))


def test_the_first_start_gives_the_google_accounts_the_former_files_codes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: tuple[FakeDav, FakeDav], former_file: Path,
) -> None:
    engine = make_engine(tmp_path)
    accounts = added(engine, monkeypatch)
    without_codes(engine, accounts)
    former_file.parent.mkdir()
    former_file.write_text(json.dumps({"client_id": "file-id", "client_secret": "file-secret"}))
    copied = call(engine, "google.copy-codes")
    assert len(copied.names) == 2
    for account_id in accounts:
        assert engine.config().find(account_id).google_client_id == "file-id"
        assert engine.passwords.get(account_id, ServerRole.GOOGLE_SECRET) == "file-secret"
    without_codes(engine, accounts[:1])
    assert call(engine, "google.copy-codes").names == ()  # done: the file is not read again
    assert engine.config().find(accounts[0]).google_client_id == ""


def test_without_the_former_file_the_first_start_copies_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: tuple[FakeDav, FakeDav], former_file: Path,
) -> None:
    engine = make_engine(tmp_path)
    accounts = added(engine, monkeypatch)
    without_codes(engine, accounts)
    assert call(engine, "google.copy-codes").names == ()
    assert all(engine.config().find(each).google_client_id == "" for each in accounts)
    assert all(engine.passwords.get(each, ServerRole.GOOGLE_SECRET) is None for each in accounts)


def test_a_credential_store_that_refuses_leaves_the_copy_for_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: tuple[FakeDav, FakeDav], former_file: Path,
) -> None:
    engine = make_engine(tmp_path)
    accounts = added(engine, monkeypatch)
    without_codes(engine, accounts)
    former_file.parent.mkdir()
    former_file.write_text(json.dumps({"client_id": "file-id", "client_secret": "file-secret"}))

    def refuses(account_id: str, role: ServerRole, password: str) -> None:
        raise ConfigurationError("The credential store refused the password: locked")

    store = engine.passwords
    kept = store.set
    monkeypatch.setattr(store, "set", refuses)
    with pytest.raises(ConfigurationError, match="refused"):
        call(engine, "google.copy-codes")
    assert all(engine.config().find(each).google_client_id == "" for each in accounts)  # never a client ID without its secret
    monkeypatch.setattr(store, "set", kept)
    assert len(call(engine, "google.copy-codes").names) == 2
