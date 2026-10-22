from datetime import UTC, datetime, timedelta
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.core import MessageFlag
from chrono_mail.engine import UsageError
from chrono_mail.engine.mailbox import open_mailbox
from tests.mailfactory import raw_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_conversations.py 0.0.18"

# --- Version history ----------------------------------------------------
# v0.0.18: Initial conversation tests

START = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)


def put(setup: Setup, folder: str, number: int, subject: str, references: str | None = None, minutes: int = 0) -> str:
    raw = raw_message(subject=subject, body=f"body {number}\n", message_id=f"<c{number}@x>", references=references, date=START + timedelta(minutes=minutes))
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        return mailbox.deliver(mailbox.folder(folder), raw, frozenset({MessageFlag.SEEN})).store_id


def build(setup: Setup) -> dict[str, str]:
    ids = {
        "root": put(setup, "Inbox", 1, "Plans", None, 0),
        "answer": put(setup, "Inbox", 2, "Re: Plans", "<c1@x>", 10),
        "mine": put(setup, "Sent", 3, "Re: Plans", "<c1@x>", 20),
        "later": put(setup, "Inbox", 4, "Re: Plans", "<c1@x> <c2@x>", 30),
        "other": put(setup, "Inbox", 5, "Lunch", None, 40),
    }
    return ids


def search(setup: Setup, **values: object) -> list[Any]:
    return list(call(setup.engine, "message.search", account=setup.account_id, **values))


def test_list_and_search_rows_tell_how_big_their_conversation_is(setup: Setup) -> None:
    ids = build(setup)
    rows = {row.message_id: row for row in call(setup.engine, "message.list", account=setup.account_id, folder="Inbox").messages}
    assert rows[ids["root"]].thread_size == rows[ids["later"]].thread_size == 4  # the reply in Sent belongs to it
    assert rows[ids["other"]].thread_size == 1


def test_the_conversation_of_a_message_lists_all_folders_oldest_first(setup: Setup) -> None:
    ids = build(setup)
    thread = call(setup.engine, "message.thread", account=setup.account_id, folder="Inbox", message=ids["later"])
    assert [row.message_id for row in thread] == [ids["root"], ids["answer"], ids["mine"], ids["later"]]
    assert [row.folder for row in thread] == ["Inbox", "Inbox", "Sent", "Inbox"]
    alone = call(setup.engine, "message.thread", account=setup.account_id, folder="Inbox", message=ids["other"])
    assert [row.message_id for row in alone] == [ids["other"]]
    with pytest.raises(UsageError):
        call(setup.engine, "message.thread", account=setup.account_id, folder="Inbox", message="0" * 32)


def test_conversation_mode_shows_the_newest_message_of_each_conversation_in_its_folder(setup: Setup) -> None:
    ids = build(setup)
    inbox = search(setup, folder="Inbox", conversations=True)
    assert [row.message_id for row in inbox] == [ids["other"], ids["later"]]
    assert inbox[1].thread_size == 4
    assert [row.message_id for row in search(setup, folder="Sent", conversations=True)] == [ids["mine"]]  # newest in the folder it is in
    assert len(search(setup, folder="Inbox")) == 4  # without the mode every message is listed


def test_conversation_mode_pages_and_ignores_text_queries(setup: Setup) -> None:
    ids = build(setup)
    first = search(setup, folder="Inbox", conversations=True, limit=1)
    second = search(setup, folder="Inbox", conversations=True, limit=1, offset=1)
    assert [row.message_id for row in first + second] == [ids["other"], ids["later"]]
    hits = search(setup, folder="Inbox", conversations=True, query="Plans")
    assert {row.message_id for row in hits} == {ids["root"], ids["answer"], ids["later"]}  # a search lists every hit


def test_messages_with_equal_dates_still_get_exactly_one_newest(setup: Setup) -> None:
    first = put(setup, "Inbox", 10, "Same", None, 0)
    second = put(setup, "Inbox", 11, "Re: Same", "<c10@x>", 0)
    rows = search(setup, folder="Inbox", conversations=True)
    assert len(rows) == 1 and rows[0].message_id in (first, second)


def test_a_message_without_a_date_is_handled(setup: Setup) -> None:
    raw1 = raw_message(subject="Undated", body="a\n", message_id="<d1@x>")
    raw2 = raw_message(subject="Re: Undated", body="b\n", message_id="<d2@x>", references="<d1@x>")
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        mailbox.deliver(mailbox.folder("Inbox"), raw1, frozenset())
        mailbox.deliver(mailbox.folder("Inbox"), raw2, frozenset())
    assert len(search(setup, folder="Inbox", conversations=True)) == 1
