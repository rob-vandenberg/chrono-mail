from typing import Any

import pytest

from chrono_mail.config import ServerRole
from chrono_mail.core import ConfigurationError
from chrono_mail.engine import UsageError
from tests.test_engine_commands import Setup, add_account, call, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_account_edit.py 0.0.69"

# --- Version history ----------------------------------------------------
# v0.0.69: Accounts are told apart by user name, incoming server and protocol
# v0.0.20: Initial tests of account.edit and the signature


def summary(setup: Setup) -> Any:  # noqa: ANN401 - an AccountSummary result
    (item,) = [item for item in call(setup.engine, "account.list") if item.account_id == setup.account_id]
    return item


def test_the_summary_tells_every_setting(setup: Setup) -> None:
    item = summary(setup)
    assert item.incoming_host == "127.0.0.1" and item.incoming_port == setup.pop.port and item.incoming_username
    assert item.outgoing_host == "127.0.0.1" and item.outgoing_port == setup.smtp.port and item.signature == "" and item.delete_days == 30


def test_editing_changes_only_what_is_given(setup: Setup) -> None:
    before = summary(setup)
    after = call(setup.engine, "account.edit", account=setup.account_id, display_name="Rob V", signature="Rob\r\nChrono Mail  \n")
    assert after.display_name == "Rob V" and after.signature == "Rob\nChrono Mail"
    assert after.email_address == before.email_address and after.incoming_host == before.incoming_host and after.outgoing_port == before.outgoing_port
    assert summary(setup).signature == "Rob\nChrono Mail"  # saved, not only returned
    cleared = call(setup.engine, "account.edit", account=setup.account_id, signature="")
    assert cleared.signature == "" and cleared.display_name == "Rob V"


def test_servers_ports_security_and_policy_can_be_changed(setup: Setup) -> None:
    after = call(
        setup.engine, "account.edit", account=setup.account_id, incoming_host="pop.example.org", incoming_port=110, incoming_security="starttls",
        outgoing_host="smtp.example.org", outgoing_port=25, outgoing_security="none", username="new-user", outgoing_username="new-out",
        delete_policy="delete_after_days", delete_days=7,
    )
    assert (after.incoming_host, after.incoming_port, after.incoming_security, after.incoming_username) == ("pop.example.org", 110, "starttls", "new-user")
    assert (after.outgoing_host, after.outgoing_port, after.outgoing_security, after.outgoing_username) == ("smtp.example.org", 25, "none", "new-out")
    assert after.delete_policy == "delete_after_days" and after.delete_days == 7
    assert call(setup.engine, "account.policy", account=setup.account_id, policy="keep").delete_days == 30


def test_passwords_are_stored_only_when_given(setup: Setup) -> None:
    old_in = setup.engine.passwords.get(setup.account_id, ServerRole.INCOMING)
    old_out = setup.engine.passwords.get(setup.account_id, ServerRole.OUTGOING)
    call(setup.engine, "account.edit", account=setup.account_id, display_name="Same passwords", password="", outgoing_password="")
    assert setup.engine.passwords.get(setup.account_id, ServerRole.INCOMING) == old_in
    assert setup.engine.passwords.get(setup.account_id, ServerRole.OUTGOING) == old_out
    call(setup.engine, "account.edit", account=setup.account_id, password="new-in", outgoing_password="new-out")
    assert setup.engine.passwords.get(setup.account_id, ServerRole.INCOMING) == "new-in"
    assert setup.engine.passwords.get(setup.account_id, ServerRole.OUTGOING) == "new-out"


def test_bad_values_change_nothing(setup: Setup) -> None:
    before = summary(setup)
    for values in ({"email_address": "nobody"}, {"email_address": "a@"}, {"username": "  "}, {"incoming_port": 0}, {"outgoing_port": 70000}, {"incoming_host": " "}, {"delete_days": 0, "delete_policy": "delete_after_days"}):
        with pytest.raises((UsageError, ConfigurationError)):
            call(setup.engine, "account.edit", account=setup.account_id, password="must-not-be-kept", **values)
    with pytest.raises(ConfigurationError):
        call(setup.engine, "account.edit", account=setup.account_id, signature="x" * 4001)
    assert summary(setup) == before
    assert setup.engine.passwords.get(setup.account_id, ServerRole.INCOMING) != "must-not-be-kept"


def test_accounts_may_share_an_address_but_not_a_mailbox_and_order_is_kept(setup: Setup) -> None:
    second = add_account(setup.engine, setup.pop, setup.smtp, email="second@example.com", username="second", incoming_host="127.0.0.1")
    changed = call(setup.engine, "account.edit", account=second, email_address="ME@example.com")  # the same address on another login is fine
    assert changed.email_address == "ME@example.com"
    with pytest.raises(ConfigurationError, match="exists already"):
        call(setup.engine, "account.edit", account=second, username="USER")  # the login of the first account on the same server and protocol
    call(setup.engine, "account.edit", account=setup.account_id, display_name="First again")
    assert [item.account_id for item in call(setup.engine, "account.list")] == [setup.account_id, second]
    changed = call(setup.engine, "account.edit", account=second, email_address="renamed@example.com")
    assert changed.email_address == "renamed@example.com"
    assert call(setup.engine, "account.edit", account=second, email_address="renamed@example.com").email_address == "renamed@example.com"  # its own address is fine
