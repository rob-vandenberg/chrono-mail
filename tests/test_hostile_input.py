import dataclasses
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chrono_mail.cli.render import render_text
from chrono_mail.core import FolderRole, MessageRef
from chrono_mail.engine.commands_attachments import is_program
from chrono_mail.engine.compose import suggest_filename
from chrono_mail.index import Index, entry_from_raw
from chrono_mail.index import index as index_module
from chrono_mail.mime import parse_message, send_time
from tests.mailfactory import raw_message
from tests.test_sync_imap import INBOX, World, mail, world  # noqa: F401 - world is a fixture

__all__ = ["world"]

# --- Version ------------------------------------------------------------
__version__ = "test_hostile_input.py 3.4.344"

# --- Version history ----------------------------------------------------
# v3.4.344: Initial: hostile input never stops the program (the code review's batch 4: RV-34, RV-35, RV-36, RV-118, RV-49, RV-51, RV-110,
#           RV-129; D-259)

FAR_DATE: str = "Fri, 31 Dec 9999 23:00:00 -1200"  # in UTC it is past the last moment Python can hold


def _with_headers(*headers: str) -> bytes:
    """A message with these header lines first (its own Date line left out when one is given)."""
    raw = raw_message(subject="Odd", body="x\r\n", message_id="<odd@example.com>")
    if any(header.startswith("Date:") for header in headers):
        raw = re.sub(rb"(?m)^Date:.*\r\n", b"", raw, count=1)
    return "".join(f"{header}\r\n" for header in headers).encode("utf-8") + raw


def _indexed(tmp_path: Path, raw: bytes) -> Index:
    tmp_path.mkdir(parents=True, exist_ok=True)
    index = Index(tmp_path / "index.sqlite")
    index.add(entry_from_raw(MessageRef("acc", INBOX, "a" * 32), f"cur/{'a' * 32}", frozenset(), len(raw), raw))
    return index


def test_a_date_no_time_zone_can_show_is_unreadable_and_the_message_is_indexed(tmp_path: Path) -> None:
    """RV-34, RV-118: year 9999 at -1200 could not be turned into UTC: the check, every rebuild of the index and Print stopped there."""
    raw = _with_headers(f"Received: from mx.example.com; {FAR_DATE}", f"Date: {FAR_DATE}")
    message = parse_message(raw)
    assert message.date is None and message.received is None
    rows = _indexed(tmp_path, raw).page(INBOX, limit=10)
    assert [row.subject for row in rows] == ["Odd"]


def test_a_scheduled_time_with_other_digits_is_no_time(tmp_path: Path) -> None:
    """RV-35: "²" passed isdigit() and int() refused it: one received header stopped the check and every rebuild."""
    for value in ("²", "1²", "99999999999999999999"):
        raw = _with_headers(f"X-Chrono-Mail-Send-At: {value}")
        assert send_time(parse_message(raw)) is None
        _indexed(tmp_path / value.encode().hex(), raw)
    assert send_time(parse_message(_with_headers("X-Chrono-Mail-Send-At: 1800000000"))) == 1_800_000_000


class _WindowsDatetime(datetime):
    """datetime as on Windows: a moment before 1970 cannot be made from seconds."""

    @classmethod
    def fromtimestamp(cls, timestamp: float, tz: object = None) -> "_WindowsDatetime":  # type: ignore[override] - only the refusal matters here
        if timestamp < 0:
            raise OSError(22, "Invalid argument")
        return super().fromtimestamp(timestamp, tz)  # type: ignore[arg-type]


def test_a_message_dated_before_1970_can_be_listed_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-36: on Windows fromtimestamp refuses a negative time: a folder with such a message could not be listed."""
    raw = raw_message(subject="Old", body="x\r\n", date=datetime(1965, 5, 1, 12, tzinfo=UTC))
    index = _indexed(tmp_path, raw)
    monkeypatch.setattr(index_module, "datetime", _WindowsDatetime)
    rows = index.page(INBOX, limit=10)
    assert [(row.subject, row.date) for row in rows] == [("Old", datetime(1965, 5, 1, 12, tzinfo=UTC))]


def test_a_message_the_server_refuses_to_send_holds_back_only_itself(world: World) -> None:
    """RV-49: one message the server refused (NO) stopped the whole folder at every check: no new mail, no upload."""
    world.server.add("INBOX", mail(1))
    refused = world.server.add("INBOX", mail(2))
    world.server.add("INBOX", mail(3))
    world.server.refuse_fetch = {refused}
    report = world.sync()
    assert report.downloaded == 2 and any("refuses to send" in line for line in report.failed)
    world.server.refuse_fetch = set()
    assert world.sync().downloaded == 1 and len(world.messages(INBOX)) == 3


def test_a_body_sent_as_nil_is_fetched_again(world: World) -> None:
    """RV-51: a NIL body was stored as an empty message and never fetched again."""
    world.server.add("INBOX", mail(1))
    empty = world.server.add("INBOX", mail(2))
    world.server.nil_bodies = {empty}
    report = world.sync()
    assert report.downloaded == 1 and all(raw for raw, _ in world.messages(INBOX).values())
    world.server.nil_bodies = set()
    assert world.sync().downloaded == 1 and sorted(world.subjects(INBOX)) == ["Message 1", "Message 2"]


def test_a_long_attachment_name_never_ends_in_a_space_or_a_dot_and_keeps_its_extension() -> None:
    """RV-110: cut after cleaning, "…x.exe " passed as no program and Windows (which drops the space) opened it as x.exe."""
    hidden = suggest_filename("a" * 145 + ".exe " + "b" * 10, "application/octet-stream", 0)
    assert len(hidden) <= 150 and not hidden.endswith((" ", "."))
    cut = suggest_filename("a" * 146 + ".exe" + " " * 20 + "c", "application/octet-stream", 0)
    assert len(cut) <= 150 and not cut.endswith((" ", ".")) and is_program(cut.rstrip(" .")) == is_program(cut)
    assert suggest_filename("report " * 40 + ".pdf", "application/pdf", 0).endswith("report.pdf")


@dataclasses.dataclass(frozen=True)
class _Shown:
    subject: str
    body: str


def test_the_command_line_never_prints_escape_codes_from_a_mail() -> None:
    """RV-129: the subject and text of a hostile mail went to the terminal as they were, with their escape codes."""
    text = render_text(_Shown("\x1b]0;owned\x07Hello\x9b2J", "line one\n\x1b[31mred\x1b[0m\tand a tab"))
    assert "\x1b" not in text and "\x07" not in text and "\x9b" not in text and "\tand a tab" in text and "Hello" in text
