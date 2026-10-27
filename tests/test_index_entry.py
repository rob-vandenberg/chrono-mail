from datetime import UTC, datetime

from chrono_mail.core import MessageFlag, MessageRef
from chrono_mail.index import UNREADABLE_SUBJECT, IndexEntry, entry_from_raw, unreadable_entry
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_index_entry.py 4.1.384"

# --- Version history ----------------------------------------------------
# v4.1.384: no other mail program named in a docstring (OR-10)
# v1.9.276: The date is when the mail server received the message: Received, Delivery-date, Date, stored (D-226)
# v1.1.196: The send time of a scheduled message; a message that cannot be read gets 0 (R-01)
# v0.0.14: Changes after the first real use (see DEVLOG.md)
# v0.0.13: Changes for the user interface (see DEVLOG.md)
# v0.0.8: Initial index entry tests

REF = MessageRef("account1", ("Inbox",), "a" * 32)
FLAGS = frozenset({MessageFlag.SEEN})
WHEN = datetime(2026, 10, 8, 10, 30, tzinfo=UTC)


def entry(raw: bytes) -> IndexEntry:
    return entry_from_raw(REF, "cur/x", FLAGS, len(raw), raw)


def test_fields_come_from_the_message() -> None:
    raw = raw_message(subject="Plan", sender="Alice <alice@example.com>", to="bob@example.com, Carol <carol@example.com>", body="the quarterly plan\n", date=WHEN, message_id="<m1@example.com>")
    result = entry(raw)
    assert result.subject == "Plan"
    assert result.sender == "Alice <alice@example.com>"
    assert result.recipients == "bob@example.com, Carol <carol@example.com>"
    assert result.date == WHEN and result.size == len(raw) and result.flags == FLAGS
    assert result.message_id == "<m1@example.com>"
    assert result.body == "the quarterly plan"
    assert result.has_attachments is False
    assert (result.store_id, result.folder, result.path) == (REF.store_id, REF.folder_path, "cur/x")


def test_preview_is_the_start_of_the_body() -> None:
    long_body = ("word " * 100).strip()
    result = entry(raw_message(body=long_body + "\n"))
    assert result.preview == long_body[:160] and len(result.preview) == 160
    assert entry(raw_message(body="short\n")).preview == "short"
    assert entry(raw_message(body="", html="<p>only <b>html</b></p>")).preview == "only html"
    assert entry(b"").preview == ""
    assert entry(raw_message(body="[<https://tracker.example/x?id=1>\n------ HELLO Rob\n")).preview == "HELLO Rob"
    assert unreadable_entry(REF, "cur/x", FLAGS, 1).preview == ""


def test_attachment_flag() -> None:
    assert entry(raw_message(attachment=b"\x00\x01")).has_attachments is True
    assert entry(raw_message(html="<p>x</p>")).has_attachments is False


def test_html_only_body_is_searchable() -> None:
    assert "important" in entry(raw_message(body="", html="<p>an <b>important</b> note</p>")).body


def test_thread_key_prefers_the_root_reference() -> None:
    assert entry(raw_message(message_id="<c@x>", references="<a@x> <b@x>", in_reply_to="<b@x>")).thread_key == "<a@x>"
    assert entry(raw_message(message_id="<c@x>", in_reply_to="<b@x>")).thread_key == "<b@x>"
    assert entry(raw_message(message_id="<c@x>")).thread_key == "<c@x>"
    assert entry(raw_message()).thread_key == f"id:{REF.store_id}"


def test_garbage_still_gets_an_entry() -> None:
    for raw in (b"", b"\x00\xff" * 100, b"Subject: =?bogus?q?x?=\r\n\r\n", bytes(range(256))):
        result = entry(raw)
        assert isinstance(result.subject, str) and result.thread_key


def test_control_characters_are_removed_from_list_fields() -> None:
    assert "\x00" not in entry(b"Subject: a\x00b\r\n\r\nx").subject


def test_unreadable_placeholder() -> None:
    placeholder = unreadable_entry(REF, "cur/x", FLAGS, 123)
    assert placeholder.subject == UNREADABLE_SUBJECT and placeholder.size == 123 and placeholder.date is None


def test_messages_with_raw_utf8_headers_can_be_indexed(tmp_path: object) -> None:
    from pathlib import Path

    from chrono_mail.index import Index

    raw = "From: Ünï Cödé <u@x.org>\r\nTo: 日本 <j@x.jp>\r\nSubject: 🙂 emoji ☕\r\n\r\n𝔘𝔫𝔦𝔠𝔬𝔡𝔢 body\r\n".encode()
    result = entry(raw)
    assert result.subject == "🙂 emoji ☕" and result.sender == "Ünï Cödé <u@x.org>"
    index = Index(Path(str(tmp_path)) / "index.sqlite")
    index.add(result)  # raised UnicodeEncodeError before the parser cleaned its output
    assert index.search("emoji")[0].subject == "🙂 emoji ☕" and index.search("Ünï")[0].sender.startswith("Ünï")
    bad = entry(b"Subject: caf\xe9 \xff\r\nFrom: a@x.org\r\n\r\nx")
    index.add(bad)


def test_a_scheduled_message_carries_its_send_time() -> None:
    raw = raw_message(subject="Later").replace(b"Subject: Later", b"X-Chrono-Mail-Send-At: 1800000000\r\nSubject: Later", 1)
    assert entry(raw).send_at == 1_800_000_000
    assert entry(raw_message()).send_at is None
    assert unreadable_entry(REF, "cur/x", FLAGS, 10).send_at == 0  # not known: looked at by the minute check


def with_lines(raw: bytes, *lines: str) -> bytes:
    """The message with these header lines put on top, as mail servers add them (the last server's line first)."""
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    return b"".join(line.encode("ascii") + newline for line in lines) + raw


def test_received_is_when_the_mail_server_received_it() -> None:
    """D-226, as a widely used open-source mail program does: the topmost Received line (the receiver's own server, which adds its line above the others), for
    IMAP and POP3 alike; not the sender's Date and not the moment Chrono Mail fetched it."""
    raw = with_lines(
        raw_message(date=WHEN),
        "Received: from relay.example.org by mx.example.net with ESMTPS id 1; Thu, 08 Oct 2026 12:31:05 +0200",  # the receiver's server
        "Received: from sender.example.com by relay.example.org; Thu, 08 Oct 2026 12:30:59 +0200",  # a server on the way
    )
    assert entry(raw).date == datetime(2026, 10, 8, 10, 31, 5, tzinfo=UTC)


def test_without_a_received_line_the_delivery_date_counts() -> None:
    raw = with_lines(raw_message(date=WHEN), "Delivery-date: Thu, 08 Oct 2026 12:40:00 +0200")
    assert entry(raw).date == datetime(2026, 10, 8, 10, 40, tzinfo=UTC)


def test_own_mail_that_no_server_received_keeps_its_date() -> None:
    assert entry(raw_message(date=WHEN)).date == WHEN  # a draft, a sent copy, a scheduled message: no Received line


def test_a_message_without_any_time_shows_when_it_was_stored_here() -> None:
    raw = raw_message(date=None)  # the shop's message had Received lines; this one has nothing at all
    stored = datetime(2026, 10, 22, 7, 0, tzinfo=UTC)
    assert entry_from_raw(REF, "cur/x", FLAGS, len(raw), raw, stored).date == stored


def test_an_unreadable_received_line_falls_back_without_failing() -> None:
    raw = with_lines(raw_message(date=WHEN), "Received: from somewhere by nowhere; not a date at all", "Delivery-date: also nonsense")
    assert entry(raw).date == WHEN
