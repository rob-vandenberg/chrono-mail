import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from chrono_mail.config import AppConfig
from chrono_mail.core import ConfigurationError, MessageFlag
from chrono_mail.engine import session
from chrono_mail.engine.mailbox import open_mailbox
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message
from tests.test_engine_commands import add_account, call

# --- Version ------------------------------------------------------------
__version__ = "test_config_changes.py 0.5.144"

# --- Version history ----------------------------------------------------
# v0.5.144: Initial tests: changes of the settings made at the same moment are all kept; the rules run when the imported-account flag cannot be saved


def test_changes_of_the_settings_made_at_the_same_moment_are_all_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path)
    accounts = [
        call(engine, "account.add", display_name=f"A{number}", email_address=f"a{number}@example.com", username=f"user{number}", password="secret",
             incoming_host="pop.example.com", outgoing_host="smtp.example.com").account_id
        for number in range(8)
    ]
    real_load = session.load_config

    def slow_load(path: Path) -> AppConfig:  # widens the moment between reading and saving, as a busy computer does
        config = real_load(path)
        time.sleep(0.05)
        return config

    monkeypatch.setattr(session, "load_config", slow_load)
    errors: list[BaseException] = []

    def change(account: str) -> None:
        try:
            call(engine, "account.policy", account=account, policy="delete_after_days", days=7)
        except BaseException as error:  # noqa: BLE001 - reported by the assertion below
            errors.append(error)

    threads = [threading.Thread(target=change, args=(account,)) for account in accounts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    config = engine.config()
    assert [config.find(account).pop3.days for account in accounts] == [7] * 8  # not only the account that saved last


def test_the_rules_run_when_the_flag_of_an_imported_account_cannot_be_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({"u1": raw_message(subject="New invoice", body="new\r\n", message_id="<new@x>")}) as pop, FakeSmtpServer() as smtp:
        account = add_account(engine, pop, smtp)
        config = engine.config()
        engine.save_config(config.with_account(replace(config.find(account), skip_known_mail=True)))
        call(engine, "rule.add", name="Invoices", condition=["subject:contains:invoice"], action=["flag"])

        def refuse(path: Path, config: AppConfig) -> None:
            raise ConfigurationError("The settings could not be saved: Access is denied")

        monkeypatch.setattr(session, "save_config", refuse)
        result = call(engine, "mail.fetch", account=account)
    assert result.downloaded == 1
    assert result.rule_actions == 1
    assert any("Access is denied" in line for line in result.failed)
    with open_mailbox(engine, account) as mailbox:
        (row,) = mailbox.index.page(("Inbox",))
    assert MessageFlag.FLAGGED in row.flags
    assert engine.config().find(account).skip_known_mail is True  # still on: the next check recognises the mail once more
