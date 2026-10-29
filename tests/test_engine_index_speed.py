import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from chrono_mail.core import MessageFlag, MessageRef
from chrono_mail.engine import commands_folders
from chrono_mail.engine.changes import MessageRow
from chrono_mail.index import Index, IndexEntry, entry_from_raw
from chrono_mail.index.entry import header_block
from chrono_mail.index.model import SortField
from chrono_mail.index.schema import FILL_HEADERS_KEY, SENT_FILL_KEY
from tests.mailfactory import raw_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_rebuilds import without_format_16

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_index_speed.py 4.2.429"

# --- Version history ----------------------------------------------------
# v4.2.429: Initial: format 16 (From / To for mail one sent sorts by exactly what it shows), the filling in after an upgrade from format 15
#           reads nothing (from 15) or only the headers (from 14) and steps aside while the person's command runs, the Account sort by the names in SQLite (D-295, D-229)


def _entry(number: int, to: str, sender: str = "Me <me@example.com>") -> IndexEntry:
    raw = raw_message(subject=f"Mail {number}", sender=sender, to=to, message_id=f"<s{number}@x>")
    return entry_from_raw(MessageRef("a" * 32, ("Sent",), f"{number:032d}"), "cur/x", frozenset(), len(raw), raw)


def test_from_to_for_mail_one_sent_sorts_and_groups_by_its_first_recipient_else_the_sender(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    for number, to in ((1, "Zoe <z@example.org>, Abe <a@example.org>"), (2, "Bea <b@example.org>"), (3, "")):
        index.add(_entry(number, to))
    order = [row.subject for row in index.search("", ("Sent",), sort=SortField.TO_SHOWN, descending=False)]
    assert order == ["Mail 2", "Mail 3", "Mail 1"]  # Bea, Me (no recipient: the sender), Zoe: the first recipient only, as the column shows
    index.close()


def test_mail_one_sent_sorted_by_from_to_in_a_folder_and_in_favorites(setup: Setup) -> None:
    for to in (("zoe@example.org", "abe@example.org"), ("bea@example.org",)):
        assert call(setup.engine, "mail.send", account=setup.account_id, to=to, subject=f"To {to[0][:3]}", body="hi").sent
    rows: list[MessageRow] = list(call(setup.engine, "message.list", account=setup.account_id, folder="Sent", sort="to_shown", oldest_first=True).messages)
    assert [row.subject for row in rows] == ["To bea", "To zoe"]  # by the first recipient only: abe would come first by all of them
    combined: tuple[MessageRow, ...] = call(setup.engine, "message.combined", view="sent", sort="to_shown", oldest_first=True)
    assert [row.subject for row in combined] == ["To bea", "To zoe"]


def _upgraded_from(setup: Setup, version: str, monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    """A sent message, its index made of this older format again (attachments marked wrongly on purpose), filled in: what was read."""
    assert call(setup.engine, "mail.send", account=setup.account_id, to="bea@example.org", subject="Sent", body="hi").sent
    connection = sqlite3.connect(setup.engine.directories.index_file(setup.account_id))
    without_format_16(connection)
    if version == "14":
        connection.execute("UPDATE messages SET received_by = ''")
    connection.execute("UPDATE meta SET value = ? WHERE key = 'schema'", (version,))
    connection.execute("UPDATE messages SET has_attachments = 1")  # a mark only the whole message could correct: it must stay
    connection.commit()
    connection.close()
    read: list[bytes] = []
    original = commands_folders.entry_from_raw

    def looked_at(reference: MessageRef, relative: str, flags: frozenset[MessageFlag], size: int, raw: bytes) -> IndexEntry:
        read.append(raw)
        return original(reference, relative, flags, size, raw)

    monkeypatch.setattr(commands_folders, "entry_from_raw", looked_at)
    assert {state.need for state in call(setup.engine, "index.check")} == {"fill"}
    assert call(setup.engine, "index.fill-sent", account=setup.account_id).filled >= 1
    return read


def _shown_and_left(setup: Setup) -> tuple[tuple[str, int], tuple[int]]:
    connection = sqlite3.connect(setup.engine.directories.index_file(setup.account_id))
    shown = connection.execute("SELECT to_shown, has_attachments FROM messages WHERE subject = 'Sent'").fetchone()
    left = connection.execute("SELECT count(*) FROM meta WHERE key IN (?, ?)", (SENT_FILL_KEY, FILL_HEADERS_KEY)).fetchone()
    connection.close()
    return shown, left


def test_an_index_of_format_15_is_filled_in_from_what_it_holds_without_reading_a_message(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _upgraded_from(setup, "15", monkeypatch) == []  # no message read at all
    assert _shown_and_left(setup) == (("bea@example.org", 1), (0,))


def test_an_index_of_format_14_is_filled_in_from_the_headers_only(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    read = _upgraded_from(setup, "14", monkeypatch)
    assert read and all(raw == header_block(raw) for raw in read)  # the headers only, never the whole message
    assert _shown_and_left(setup) == (("bea@example.org", 1), (0,))


def test_the_filling_in_steps_aside_while_a_command_of_the_person_runs(setup: Setup) -> None:
    engine = setup.engine
    stepped: list[str] = []
    with engine.person_work():
        waiting = threading.Thread(target=lambda: (engine.step_aside(lambda: False), stepped.append("went on")))
        waiting.start()
        waiting.join(0.3)  # a guard only: it must still be waiting while the person's command runs
        assert waiting.is_alive() and stepped == []
    waiting.join(5)
    assert stepped == ["went on"]


def test_the_account_sort_reads_the_names_in_sqlite_with_no_call_per_message(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.account_names = {"b" * 32: "Bea", "a" * 32: "Ann"}
    for number, account in ((1, "b" * 32), (2, "a" * 32), (3, "")):
        raw = raw_message(subject=f"Mail {number}", message_id=f"<a{number}@x>")
        entry = entry_from_raw(MessageRef("c" * 32, ("Inbox",), f"{number:032d}"), "cur/x", frozenset(), len(raw), raw)
        index.add(replace(entry, received_by=account))
    assert [row.subject for row in index.search("", ("Inbox",), sort=SortField.ACCOUNT, descending=False)] == ["Mail 3", "Mail 2", "Mail 1"]
    assert {row.subject: row.received_by for row in index.search("", ("Inbox",))} == {"Mail 1": "Bea", "Mail 2": "Ann", "Mail 3": ""}
    index.close()
