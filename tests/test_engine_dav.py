from pathlib import Path

import pytest

from chrono_mail.core import ConfigurationError
from chrono_mail.engine import Engine, UsageError
from chrono_mail.protocols.tls import fingerprint
from tests.enginefactory import make_engine
from tests.fakedav import FakeDav
from tests.test_engine_commands import call
from tests.test_sync_dav import card, event, task

# --- Version ------------------------------------------------------------
__version__ = "test_engine_dav.py 0.0.72"

# --- Version history ----------------------------------------------------
# v0.0.72: Accepted certificates belong to one server (host and port)
# v0.0.66: Initial tests of the commands for the calendar and contacts server


def add_account(tmp_path: Path) -> tuple[Engine, str]:
    engine = make_engine(tmp_path)
    created = call(
        engine, "account.add", display_name="Me", email_address="ann@example.com", username="ann", password="secret", incoming_host="127.0.0.1", incoming_port=1,
        incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=1, outgoing_security="none",
    )
    return engine, str(created.account_id)


def test_the_commands_set_discover_and_synchronise(tmp_path: Path) -> None:
    engine, account = add_account(tmp_path)
    with FakeDav() as server:
        server.add("events", "e1.ics", event("e1", "Meeting"))
        server.add("tasks", "t1.ics", task("t1", "Write"))
        server.add("contacts", "c1.vcf", card("c1", "Ann Lee"))
        with pytest.raises(UsageError):
            call(engine, "dav.sync", account=account)  # no server set yet
        summary = call(engine, "account.set-dav", account=account, url=server.url)
        assert summary.dav_url == server.url and summary.dav_username == ""
        assert {(item.name, item.kind) for item in call(engine, "dav.discover", account=account)} == {("Calendar", "events"), ("Tasks", "tasks"), ("Contacts", "contacts")}
        (result,) = call(engine, "dav.sync")
        assert (result.created, result.pulled, result.errors) == (3, 3, ())
        shown = [item for item in call(engine, "collection.list") if item.account == account]
        assert sorted(item.name for item in shown) == ["Calendar", "Contacts", "Tasks"]
        assert [item.name for item in call(engine, "contact.list").contacts] == ["Ann Lee"]
        call(engine, "contact.add", collection=next(item.collection for item in shown if item.kind == "contacts"), name="Bob")
        assert call(engine, "dav.sync", account=account)[0].pushed == 1
        assert len(server.collections["contacts"].items) == 2


def test_a_separate_user_name_and_password_and_taking_the_server_away(tmp_path: Path) -> None:
    engine, account = add_account(tmp_path)
    with FakeDav(user="zed", password="other") as server:
        call(engine, "account.set-dav", account=account, url=server.url, username="zed", password="other")
        assert call(engine, "dav.sync", account=account)[0].errors == ()
        call(engine, "account.set-dav", account=account)
        assert call(engine, "account.list")[0].dav_url == ""
        with pytest.raises(UsageError):
            call(engine, "dav.sync", account=account)


def test_an_address_must_be_a_web_address(tmp_path: Path) -> None:
    engine, account = add_account(tmp_path)
    with pytest.raises(ConfigurationError):
        call(engine, "account.set-dav", account=account, url="ftp://example.com")


def test_a_certificate_that_was_accepted_for_the_account_is_used(tmp_path: Path) -> None:
    engine, account = add_account(tmp_path)
    with FakeDav(tls=True) as server:
        call(engine, "account.set-dav", account=account, url=server.url)
        with pytest.raises(Exception, match="certificate"):
            call(engine, "dav.sync", account=account)
        call(engine, "account.trust-certificate", account=account, host="127.0.0.1", port=int(server.url.rsplit(":", 1)[1].split("/")[0]), fingerprint=fingerprint(server.certificate))
        assert call(engine, "dav.sync", account=account)[0].errors == ()
