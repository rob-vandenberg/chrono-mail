from datetime import UTC, datetime

from chrono_mail.core import MessageFlag, MessageRef
from chrono_mail.index import UNREADABLE_SUBJECT, IndexEntry, entry_from_raw, unreadable_entry
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_index_entry.py 0.0.14"

# --- Version history ----------------------------------------------------
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
