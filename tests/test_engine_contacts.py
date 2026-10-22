from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.core import ConfigurationError, MessageFlag
from chrono_mail.engine.mailbox import open_mailbox
from tests.mailfactory import raw_message
from tests.test_engine_commands import Setup, add_account, call, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_contacts.py 0.0.23"

# --- Version history ----------------------------------------------------
# v0.0.23: Initial address suggestion tests


def put(setup: Setup, account: str, folder: str, number: int, sender: str, to: str) -> None:
    raw = raw_message(subject=f"S{number}", sender=sender, to=to, message_id=f"<c{number}@x>")
    with open_mailbox(setup.engine, account) as mailbox:
        mailbox.deliver(mailbox.folder(folder), raw, frozenset({MessageFlag.SEEN}))


def suggest(setup: Setup, text: str, **values: object) -> list[Any]:
    return list(call(setup.engine, "contact.suggest", text=text, **values))


def test_suggestions_come_from_mail_received_and_sent(setup: Setup) -> None:
    put(setup, setup.account_id, "Inbox", 1, "Ann Archer <ann@x.org>", "me@example.com")
    put(setup, setup.account_id, "Sent", 2, "me@example.com", "Bob Brown <bob@x.org>")
    put(setup, setup.account_id, "Sent", 3, "me@example.com", "Bob Brown <bob@x.org>")
    found = suggest(setup, "o")
    assert found[0].address == "bob@x.org"
    assert found[0].display == "Bob Brown <bob@x.org>" and found[0].times_written_to == 2
    ann = suggest(setup, "arch")[0]
    assert ann.address == "ann@x.org" and ann.times_received_from == 1 and ann.display == "Ann Archer <ann@x.org>"


def test_own_addresses_are_never_suggested(setup: Setup) -> None:
    put(setup, setup.account_id, "Sent", 1, "me@example.com", "Bob <bob@x.org>")
    assert suggest(setup, "me@") == [] and suggest(setup, "example") == []


def test_names_with_special_characters_are_quoted_in_the_display_form(setup: Setup) -> None:
    put(setup, setup.account_id, "Inbox", 1, '"Archer, Ann" <ann@x.org>', "me@example.com")
    (item,) = suggest(setup, "ann@")
    assert item.name == "Archer, Ann" and item.display == '"Archer, Ann" <ann@x.org>'


def test_all_accounts_are_searched_unless_one_is_named(setup: Setup) -> None:
    second = add_account(setup.engine, setup.pop, setup.smtp, email="second@example.com")
    put(setup, setup.account_id, "Inbox", 1, "Ann <ann@x.org>", "me@example.com")
    put(setup, second, "Inbox", 2, "Ann <ann@x.org>", "second@example.com")
    put(setup, second, "Inbox", 3, "Zed <zed@y.org>", "second@example.com")
    assert {item.address for item in suggest(setup, "@")} == {"ann@x.org", "zed@y.org"}
    merged = [item for item in suggest(setup, "ann")]
    assert len(merged) == 1 and merged[0].times_received_from == 2  # the two accounts add up
    assert [item.address for item in suggest(setup, "@", account=setup.account_id)] == ["ann@x.org"]
    assert {item.address for item in suggest(setup, "@", account="second@example.com")} == {"ann@x.org", "zed@y.org"}
    with pytest.raises(ConfigurationError, match="Unknown account"):
        suggest(setup, "a", account="nobody")


def test_the_limit_is_respected_and_bounded(setup: Setup) -> None:
    for number in range(12):
        put(setup, setup.account_id, "Inbox", number, f"P{number} <p{number}@x.org>", "me@example.com")
    assert len(suggest(setup, "p")) == 8 and len(suggest(setup, "p", limit=3)) == 3 and len(suggest(setup, "p", limit=1000)) == 12 and len(suggest(setup, "p", limit=0)) == 1


def test_fetched_mail_feeds_the_suggestions_and_an_old_index_is_rebuilt(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    assert [item.address for item in suggest(setup, "alice")] == ["alice@example.com"]
    call(setup.engine, "index.rebuild", account=setup.account_id)
    assert [item.address for item in suggest(setup, "alice")] == ["alice@example.com"]
    assert suggest(setup, "alice")[0].times_received_from == 3  # recounted exactly
