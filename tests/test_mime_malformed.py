from pathlib import Path

import pytest

from chrono_mail.mime import ParsedMessage, parse_message
from chrono_mail.mime.limits import MAXIMUM_MIME_DEPTH, MAXIMUM_PARTS

# --- Version ------------------------------------------------------------
__version__ = "test_mime_malformed.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial malformed-input tests

CORPUS: Path = Path(__file__).resolve().parent / "data" / "mime"
CORPUS_FILES: list[Path] = sorted(CORPUS.glob("*.eml"))


def load(name: str) -> ParsedMessage:
    return parse_message((CORPUS / name).read_bytes())


@pytest.mark.parametrize("path", CORPUS_FILES, ids=lambda path: path.name)
def test_every_corpus_file_parses_without_raising(path: Path) -> None:
    message = parse_message(path.read_bytes())
    assert isinstance(message, ParsedMessage)
    assert all(isinstance(defect, str) for defect in message.defects)


def test_corpus_is_not_empty() -> None:
    assert len(CORPUS_FILES) >= 15


def test_empty_input() -> None:
    assert load("empty.eml").subject == ""


def test_headers_without_body() -> None:
    message = load("headers_only.eml")
    assert message.subject == "no body at all"
    assert message.text_body == ""


def test_header_line_without_colon_does_not_hide_the_rest() -> None:
    message = load("no_colon_header.eml")
    assert "missing-header-body-separator" in message.defects
    assert message.text_body is not None and message.text_body.startswith("this line has no colon")
    assert message.from_addresses[0].address == "a@example.com"


def test_nul_bytes() -> None:
    message = load("nul_bytes.eml")
    assert message.text_body == "bo\x00dy\n"


def test_unknown_charset_falls_back_to_utf8_with_replacement() -> None:
    message = load("bad_charset.eml")
    assert message.text_body == "h\ufffdllo\n"
    assert "unknown-charset" in message.defects
    assert "invalid-text-encoding" in message.defects


def test_codec_that_is_not_a_text_encoding() -> None:
    message = load("rot13_charset.eml")
    assert message.text_body == "hello\n"
    assert "unknown-charset" in message.defects


def test_wrongly_declared_charset_uses_replacement_characters() -> None:
    message = load("latin1_declared_utf8.eml")
    assert message.text_body == "caf\ufffd\n"
    assert "invalid-text-encoding" in message.defects


def test_undecodable_subject_falls_back_to_the_raw_text() -> None:
    message = load("bad_base64_subject.eml")
    assert message.subject == "=?utf-8?b?!!!!?="
    assert "header-undecodable-subject" in message.defects
    assert message.text_body == "body\n"


def test_invalid_date_is_none_and_reported() -> None:
    message = load("invalid_date.eml")
    assert message.date is None
    assert "invalid-date" in message.defects
    assert message.subject == "date"


def test_garbled_address_header() -> None:
    message = load("garbled_to.eml")
    assert message.to_addresses == ()
    assert ("to", "<<<@@@") in message.raw_address_headers
    assert message.subject == "garbled"


def test_truncated_multipart_keeps_what_arrived() -> None:
    message = load("truncated_multipart.eml")
    assert message.text_body == "part one"
    assert "close-boundary-not-found" in message.defects


def test_multipart_without_boundary() -> None:
    message = load("missing_boundary.eml")
    assert message.subject == "boundary"
    assert "no-boundary-in-multipart" in message.defects
    assert [len(attachment.data) for attachment in message.attachments] == [34]


def test_unknown_transfer_encoding() -> None:
    message = load("unknown_transfer_encoding.eml")
    assert message.subject == "cte"
    assert message.text_body == "body\n"


def test_bad_base64_attachment_is_still_delivered() -> None:
    # Python decodes invalid base64 leniently and reports nothing; the part must not disappear.
    message = load("bad_base64_attachment.eml")
    assert [attachment.filename for attachment in message.attachments] == ["x.bin"]


def test_lone_carriage_returns() -> None:
    message = load("lone_cr_line_endings.eml")
    assert message.text_body is not None and "body line" in message.text_body


def test_duplicate_headers_use_the_first() -> None:
    message = load("duplicate_headers.eml")
    assert message.from_addresses[0].address == "a@example.com"
    assert message.date is not None and message.date.day == 8


def test_date_with_unknown_zone_is_taken_as_utc() -> None:
    message = load("naive_date.eml")
    assert message.date is not None
    assert message.date.utcoffset() is not None and message.date.hour == 10


def nested_multipart(levels: int) -> bytes:
    raw = b"From: a@example.com\r\nSubject: nested\r\nContent-Type: multipart/mixed; boundary=b0\r\n\r\n"
    for level in range(levels):
        raw += b"--b%d\r\nContent-Type: multipart/mixed; boundary=b%d\r\n\r\n" % (level, level + 1)
    return raw + b"--b%d\r\nContent-Type: text/plain\r\n\r\ndeep text\r\n" % levels


def test_nesting_beyond_the_limit_is_reported() -> None:
    message = parse_message(nested_multipart(MAXIMUM_MIME_DEPTH + 10))
    assert "nesting-too-deep" in message.defects
    assert message.subject == "nested"
    assert message.text_body is None


def test_nesting_within_the_limit_is_read() -> None:
    message = parse_message(nested_multipart(MAXIMUM_MIME_DEPTH - 3))
    assert message.text_body == "deep text"
    assert "nesting-too-deep" not in message.defects


def test_nesting_bomb_does_not_crash() -> None:
    message = parse_message(nested_multipart(5000))
    assert message.subject == "nested"
    assert "nesting-too-deep" in message.defects


def test_too_many_parts_are_reported() -> None:
    parts = b"".join(b"--B\r\nContent-Type: application/octet-stream\r\n\r\nx\r\n" for _ in range(MAXIMUM_PARTS + 50))
    raw = b"From: a@example.com\r\nContent-Type: multipart/mixed; boundary=B\r\n\r\n" + parts + b"--B--\r\n"
    message = parse_message(raw)
    assert "too-many-parts" in message.defects
    assert len(message.attachments) <= MAXIMUM_PARTS


def test_huge_header_section_is_cut() -> None:
    raw = b"From: a@example.com\r\nSubject: big\r\nX-Filler: " + b"x" * (3 * 1024 * 1024) + b"\r\n\r\nbody\r\n"
    message = parse_message(raw)
    assert "header-section-too-large" in message.defects
    assert message.subject == "big"


def test_endless_headers_without_blank_line() -> None:
    raw = b"".join(b"X-Header-%d: value\r\n" % number for number in range(200_000))
    message = parse_message(raw)
    assert isinstance(message, ParsedMessage)


def test_one_gigantic_line() -> None:
    message = parse_message(b"Subject: " + b"A" * (5 * 1024 * 1024))
    assert isinstance(message, ParsedMessage)
