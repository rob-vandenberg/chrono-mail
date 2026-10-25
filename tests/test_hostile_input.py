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
__version__ = "test_hostile_input.py 3.6.351"

# --- Version history ----------------------------------------------------
# v3.6.351: a number from a server is only the digits 0 to 9 (IMAP UID sets, DAV status); a refused message never keeps its folder
#           from a checkpoint; the --json output never carries a C1 control character
# v3.6.349: a server that refuses to send every message is not asked once per message (RV-202); the command line's error lines print
#           server text safely too (RV-203)
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


def test_a_server_that_refuses_every_message_is_not_asked_once_per_message(world: World) -> None:
    """RV-202: a server refusing everything (throttled) got one command per message on top of each group's, at every check."""
    uids = {world.server.add("INBOX", mail(number)) for number in range(1, 41)}
    world.server.refuse_fetch = set(uids)
    report = world.sync()
    asked = [command for command in world.server.commands if "BODY.PEEK[]" in command.upper()]
    assert report.downloaded == 0 and len(asked) <= 8 and report.failed
    world.server.refuse_fetch = set()
    assert world.sync().downloaded == 40  # nothing skipped for good: all of it comes at the next check


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


def test_the_command_line_error_lines_never_print_escape_codes(tmp_path: Path) -> None:
    """RV-203: only the results were made safe: an error line (which can hold a server's reply) went to the terminal as it was."""
    import io

    from chrono_mail.cli.main import main
    from chrono_mail.core import ChronoMailError
    from chrono_mail.engine import Engine

    def hostile(directories: object) -> Engine:
        raise ChronoMailError("The server said: \x1b]0;owned\x07no\x9b2J")

    err = io.StringIO()
    main(["account.list"], engine_factory=hostile, stdout=io.StringIO(), stderr=err, program_running=lambda: False)
    assert "The server said" in err.getvalue() and "\x1b" not in err.getvalue() and "\x07" not in err.getvalue() and "\x9b" not in err.getvalue()

    def made_up_lines(directories: object) -> Engine:
        raise ChronoMailError("no\nError: your password expired, type it at evil.example\u202e")

    err = io.StringIO()
    main(["account.list"], engine_factory=made_up_lines, stdout=io.StringIO(), stderr=err, program_running=lambda: False)
    assert err.getvalue().count("\n") == 1 and "\u202e" not in err.getvalue()  # one line: a server cannot make up a line of its own



def test_a_number_from_a_server_is_only_the_digits_0_to_9() -> None:
    """The three reviewers of 2026-10-26 (RV-35 elsewhere): "²" passed isdigit() and int() refused it with a ValueError, which got past
    the handlers of a server's errors: one VANISHED or COPYUID set stopped every check of the account, a DAV status the calendar sync."""
    from chrono_mail.core.errors import ProtocolError
    from chrono_mail.core.words import digits
    from chrono_mail.protocols import dav
    from chrono_mail.protocols.imap import uid_set

    assert digits("123") and not digits("²") and not digits("1²") and not digits("") and not digits("٣")
    for text in ("1:²", "²", "4,²:6"):
        with pytest.raises(ProtocolError):
            uid_set(text)
    assert dav._status_of("HTTP/1.1 ²00 OK") == 0 and dav._status_of("HTTP/1.1 207 Multi-Status") == 207  # noqa: SLF001


def test_a_message_the_server_refuses_never_keeps_its_folder_from_a_checkpoint(world: World) -> None:
    """The three reviewers of 2026-10-26 (RV-49): one message the server always refused kept its folder from ever getting a checkpoint, so
    every check asked the server for the marks and sizes of the whole folder. The checkpoint keeps it, and asks only it again."""
    from chrono_mail.sync.imap_state import ImapState

    world.server.add("INBOX", mail(1))
    refused = world.server.add("INBOX", mail(2))
    world.server.refuse_fetch = {refused}
    world.sync()
    checkpoint = world.state.checkpoints.get(INBOX)
    assert checkpoint is not None and checkpoint.waiting == frozenset({refused})
    again = ImapState(world.path / "state.log", durable=False)  # read back from the file
    assert again.checkpoints[INBOX].waiting == frozenset({refused})
    world.server.refuse_fetch = set()
    assert world.sync().downloaded == 1 and sorted(world.subjects(INBOX)) == ["Message 1", "Message 2"]
    after = world.state.checkpoints.get(INBOX)
    assert after is not None and after.waiting == frozenset()


def test_the_json_output_never_prints_c1_control_characters(tmp_path: Path) -> None:
    """The three reviewers of 2026-10-26 (RV-129): the results were written with ensure_ascii=False, which escapes only U+0000 to U+001F,
    so an 8-bit CSI (U+009B) in a folder name reached the terminal raw with --json. It is written as \\u009b; a program reading the JSON
    gets the same text."""
    import io
    import json

    from chrono_mail.cli.main import main
    from chrono_mail.engine import Engine
    from tests.enginefactory import make_engine

    engine = make_engine(tmp_path)
    main(["local.enable"], engine_factory=lambda directories: engine, stdout=io.StringIO(), stderr=io.StringIO(), program_running=lambda: False)
    main(["folder.create", "--account", "0" * 31 + "1", "--folder", "Offers\x9b2J"], engine_factory=lambda directories: engine,
         stdout=io.StringIO(), stderr=io.StringIO(), program_running=lambda: False)

    def same(directories: object) -> Engine:
        return engine

    out = io.StringIO()
    main(["--json", "folder.list", "--account", "0" * 31 + "1"], engine_factory=same, stdout=out, stderr=io.StringIO(), program_running=lambda: False)
    assert "\x9b" not in out.getvalue() and "Offers\x9b2J" in json.dumps(json.loads(out.getvalue()), ensure_ascii=False)


def test_a_number_given_on_the_command_line_has_one_minus_at_most(tmp_path: Path) -> None:
    """The review of 3.6.351: "--5" passed the check and int() refused it with a ValueError instead of a plain refusal."""
    from chrono_mail.engine.registry import UsageError

    from tests.test_engine_registry import run

    with pytest.raises(UsageError, match="whole number"):
        run(tmp_path, {"name": "x", "count": "--5"})
    assert run(tmp_path, {"name": "x", "count": "-5"}).count == -5
    with pytest.raises(UsageError, match="whole number"):
        run(tmp_path, {"name": "x", "count": "9" * 5000})  # int() refuses over 4,300 digits with a ValueError
