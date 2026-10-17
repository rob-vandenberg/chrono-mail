import os
import sys
from pathlib import Path

import pytest

from chrono_mail.core import MessageFlag
from chrono_mail.engine import UsageError
from chrono_mail.engine.mailbox import open_mailbox
from tests.mailfactory import raw_message
from tests.test_engine_commands import Setup, call, first_id, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_export.py 0.5.164"

# --- Version history ----------------------------------------------------
# v0.5.164: the mode of the exported file is checked only where files have mode bits; the read-only folder test is skipped on Windows; the root check in a function the type check for Windows understands
# v0.5.161: the root check asks for the user id only where the system has one (the file failed to load on Windows)
# v0.0.22: Initial export, source and plain text tests


def stored(setup: Setup, raw: bytes) -> str:
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        return mailbox.deliver(mailbox.folder("Inbox"), raw, frozenset({MessageFlag.SEEN})).store_id


def test_export_writes_the_message_exactly_as_received(setup: Setup, tmp_path: Path) -> None:
    raw = raw_message(subject="Keep me", body="Hello\n", message_id="<e1@x>", attachment=b"\x00\x01binary\xff" * 100)
    message = stored(setup, raw)
    target = tmp_path / "out" / "keep.eml"
    target.parent.mkdir()
    result = call(setup.engine, "message.export", account=setup.account_id, folder="Inbox", message=message, path=str(target))
    assert result.size == len(raw) and target.read_bytes() == raw
    if sys.platform != "win32":  # Windows has no such mode bits: the file is private through the rights of the user's folder
        assert oct(target.stat().st_mode & 0o777) == "0o600"  # a copy of private mail is private
    assert [path.name for path in target.parent.iterdir()] == ["keep.eml"]  # no leftovers


def test_export_does_not_overwrite_unless_asked(setup: Setup, tmp_path: Path) -> None:
    message = stored(setup, raw_message(subject="New", message_id="<e2@x>"))
    target = tmp_path / "x.eml"
    target.write_bytes(b"precious")
    with pytest.raises(UsageError, match="exists"):
        call(setup.engine, "message.export", account=setup.account_id, folder="Inbox", message=message, path=str(target))
    assert target.read_bytes() == b"precious"
    call(setup.engine, "message.export", account=setup.account_id, folder="Inbox", message=message, path=str(target), overwrite=True)
    assert target.read_bytes() != b"precious"


def test_export_to_an_impossible_place_fails_cleanly(setup: Setup, tmp_path: Path) -> None:
    message = stored(setup, raw_message(subject="New", message_id="<e3@x>"))
    with pytest.raises(UsageError, match="cannot be written"):
        call(setup.engine, "message.export", account=setup.account_id, folder="Inbox", message=message, path=str(tmp_path / "missing" / "x.eml"))
    with pytest.raises(UsageError):
        call(setup.engine, "message.export", account=setup.account_id, folder="Inbox", message="../../etc/passwd", path=str(tmp_path / "x.eml"))
    assert not [path for path in tmp_path.rglob('*') if path.name.endswith(('.part', '.eml'))]


def running_as_root() -> bool:
    if sys.platform != "win32":  # Windows has no geteuid; inside this block the type check for Windows skips it
        return os.geteuid() == 0
    return False


@pytest.mark.skipif(sys.platform == "win32" or running_as_root(), reason="Windows cannot make a folder read-only this way; root can write anywhere")
def test_export_into_a_read_only_folder_fails_cleanly(setup: Setup, tmp_path: Path) -> None:
    message = stored(setup, raw_message(subject="New", message_id="<e4@x>"))
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(UsageError, match="cannot be written"):
            call(setup.engine, "message.export", account=setup.account_id, folder="Inbox", message=message, path=str(locked / "x.eml"))
    finally:
        locked.chmod(0o700)


def test_source_shows_headers_and_body_and_survives_bad_bytes(setup: Setup) -> None:
    message = stored(setup, raw_message(subject="Source", body="Line one\n", message_id="<s1@x>"))
    source = call(setup.engine, "message.source", account=setup.account_id, folder="Inbox", message=message)
    assert "Subject: Source" in source.text and "Message-ID: <s1@x>" in source.text and "Line one" in source.text
    assert "\r" not in source.text and not source.truncated and source.size > 50
    bad = b"From: a@b.org\nSubject: Caf\xe9 \x00 nul\n\nbody \xff\xfe\n"
    broken = call(setup.engine, "message.source", account=setup.account_id, folder="Inbox", message=stored(setup, bad))
    assert "\x00" not in broken.text and "body" in broken.text


def test_source_of_a_huge_message_is_cut(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.engine import commands_export

    monkeypatch.setattr(commands_export, "MAXIMUM_SOURCE_CHARACTERS", 500)
    message = stored(setup, raw_message(subject="Big", body="word " * 1000 + "\n", message_id="<s2@x>"))
    source = call(setup.engine, "message.source", account=setup.account_id, folder="Inbox", message=message)
    assert source.truncated and len(source.text) == 500


def test_text_gives_the_plain_text_of_text_and_html_messages(setup: Setup) -> None:
    plain = call(setup.engine, "message.text", account=setup.account_id, folder="Inbox", message=stored(setup, raw_message(subject="Plain", body="Just text\n", message_id="<t1@x>")))
    assert plain.subject == "Plain" and plain.text.strip() == "Just text" and plain.sender == ("Alice <alice@example.com>",) and plain.attachments == ()
    html = call(
        setup.engine, "message.text", account=setup.account_id, folder="Inbox",
        message=stored(setup, b"From: a@b.org\nSubject: Web\nContent-Type: text/html\n\n<p>Hello <b>world</b></p><script>bad()</script><img src='http://tracker.example/x.gif'>"),
    )
    assert "Hello" in html.text and "world" in html.text and "tracker" not in html.text and "<b>" not in html.text
    with_file = call(setup.engine, "message.text", account=setup.account_id, folder="Inbox", message=stored(setup, raw_message(subject="File", message_id="<t3@x>", attachment=b"data")))
    assert len(with_file.attachments) == 1


def test_the_commands_work_for_fetched_mail_too(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    message = first_id(setup)
    assert call(setup.engine, "message.source", account=setup.account_id, folder="Inbox", message=message).size > 0
    assert call(setup.engine, "message.text", account=setup.account_id, folder="Inbox", message=message).text
