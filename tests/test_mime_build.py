import pytest

from chrono_mail.core import MessageFormatError
from chrono_mail.mime import (
    Address,
    Attachment,
    OutgoingMessage,
    build_message,
    envelope_recipients,
    parse_message,
)

# --- Version ------------------------------------------------------------
__version__ = "test_mime_build.py 0.0.86"

# --- Version history ----------------------------------------------------
# v0.0.86: Bcc is taken out of a stored message without touching anything else
# v0.0.16: Drafts
# v0.0.5: Initial builder tests

SENDER = Address("Jörg Müller", "joerg@example.com")
RECIPIENT = Address("Alice", "alice@example.com")


def test_round_trip_of_a_simple_message() -> None:
    outgoing = OutgoingMessage(
        from_address=SENDER,
        to_addresses=(RECIPIENT, Address("", "bob@example.com")),
        cc_addresses=(Address("Carol", "carol@example.com"),),
        subject="Héllo wörld ☕",
        text_body="Body with ünïcode\nsecond line\n",
        in_reply_to="<parent@example.com>",
        references=("<root@example.com>", "<parent@example.com>"),
    )
    parsed = parse_message(build_message(outgoing))
    assert parsed.defects == ()
    assert parsed.subject == "Héllo wörld ☕"
    assert parsed.from_addresses == (SENDER,)
    assert parsed.to_addresses == outgoing.to_addresses
    assert parsed.cc_addresses == outgoing.cc_addresses
    assert parsed.text_body == "Body with ünïcode\nsecond line\n"
    assert parsed.in_reply_to == "<parent@example.com>"
    assert parsed.references == outgoing.references
    assert parsed.date is not None and parsed.date.tzinfo is not None
    assert parsed.message_id is not None and parsed.message_id.endswith("@example.com>")


def test_output_uses_crlf_line_endings() -> None:
    raw = build_message(OutgoingMessage(from_address=SENDER, to_addresses=(RECIPIENT,), text_body="a\nb\n"))
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_html_and_attachments_round_trip() -> None:
    attachments = (
        Attachment("logo.png", "image/png", "<logo@example.com>", True, b"\x89PNG\r\n\x1a\n"),
        Attachment("report.pdf", "application/pdf", None, False, bytes(range(256))),
        Attachment(None, "not-a-valid-type", None, False, b"data"),
    )
    outgoing = OutgoingMessage(
        from_address=SENDER,
        to_addresses=(RECIPIENT,),
        subject="Files",
        text_body="plain",
        html_body="<p>html <img src='cid:logo@example.com'></p>",
        attachments=attachments,
    )
    parsed = parse_message(build_message(outgoing))
    assert parsed.text_body == "plain\n"
    assert parsed.html_body is not None and "html" in parsed.html_body
    assert [(a.filename, a.content_type, a.content_id, a.inline, a.data) for a in parsed.attachments] == [
        ("logo.png", "image/png", "<logo@example.com>", True, b"\x89PNG\r\n\x1a\n"),
        ("report.pdf", "application/pdf", None, False, bytes(range(256))),
        (None, "application/octet-stream", None, False, b"data"),
    ]


def test_bcc_is_not_written_but_is_in_the_envelope() -> None:
    outgoing = OutgoingMessage(
        from_address=SENDER,
        to_addresses=(RECIPIENT,),
        cc_addresses=(Address("", "cc@example.com"),),
        bcc_addresses=(Address("", "hidden@example.com"),),
        text_body="x",
    )
    raw = build_message(outgoing)
    assert b"hidden@example.com" not in raw
    assert envelope_recipients(outgoing) == ("alice@example.com", "cc@example.com", "hidden@example.com")


def test_envelope_removes_duplicates_ignoring_case() -> None:
    outgoing = OutgoingMessage(
        from_address=SENDER,
        to_addresses=(RECIPIENT,),
        cc_addresses=(Address("", "ALICE@example.com"),),
        bcc_addresses=(Address("", "alice@example.com"),),
    )
    assert envelope_recipients(outgoing) == ("alice@example.com",)


def test_draft_without_recipients_can_be_built() -> None:
    raw = build_message(OutgoingMessage(from_address=SENDER, subject="draft", text_body="later"))
    parsed = parse_message(raw)
    assert parsed.to_addresses == ()
    assert parsed.subject == "draft"


@pytest.mark.parametrize(
    "outgoing",
    [
        OutgoingMessage(from_address=SENDER, to_addresses=(RECIPIENT,), subject="a\r\nBcc: evil@example.com"),
        OutgoingMessage(from_address=SENDER, to_addresses=(Address("Eve\r\nBcc: evil@example.com", "eve@example.com"),)),
        OutgoingMessage(from_address=SENDER, to_addresses=(RECIPIENT,), in_reply_to="<a@b>\r\nBcc: evil@example.com"),
        OutgoingMessage(from_address=SENDER, to_addresses=(RECIPIENT,), references=("<a@b>\nBcc: evil@example.com",)),
        OutgoingMessage(from_address=Address("", "bad@example.com\r\nBcc: evil@example.com")),
        OutgoingMessage(from_address=SENDER, to_addresses=(RECIPIENT,), attachments=(Attachment("a\r\nBcc: evil@example.com", "text/plain", None, False, b"x"),)),
    ],
    ids=["subject", "display-name", "in-reply-to", "references", "address", "filename"],
)
def test_header_injection_is_rejected(outgoing: OutgoingMessage) -> None:
    with pytest.raises(MessageFormatError):
        build_message(outgoing)


@pytest.mark.parametrize("address", ["", "   ", "jörg@example.com", "a@exämple.com"])
def test_invalid_or_non_ascii_addresses_are_rejected(address: str) -> None:
    with pytest.raises(MessageFormatError):
        build_message(OutgoingMessage(from_address=SENDER, to_addresses=(Address("", address),)))


def test_error_message_does_not_contain_the_private_text() -> None:
    secret = "very-private-subject"
    with pytest.raises(MessageFormatError) as raised:
        build_message(OutgoingMessage(from_address=SENDER, subject=secret + "\r\nBcc: x@example.com"))
    assert secret not in str(raised.value)


# --- drafts (version 0.0.16) ---------------------------------------------------------------------------------------------


def test_a_draft_keeps_bcc_and_the_programs_own_notes_and_a_sent_message_does_not() -> None:
    outgoing = OutgoingMessage(
        from_address=SENDER, to_addresses=(RECIPIENT,), bcc_addresses=(Address("Hidden", "hidden@example.com"),), subject="Draft", text_body="unfinished",
        extra_headers=(("X-Chrono-Mail-Kind", "reply"), ("X-Chrono-Mail-Source", "Inbox/" + "a" * 32)),
    )
    draft = parse_message(build_message(outgoing, draft=True))
    assert draft.bcc_addresses == (Address("Hidden", "hidden@example.com"),)
    assert draft.chrono_headers == (("x-chrono-mail-kind", "reply"), ("x-chrono-mail-source", "Inbox/" + "a" * 32))
    sent = build_message(outgoing)
    assert b"hidden@example.com" not in sent and parse_message(sent).bcc_addresses == ()
    assert parse_message(sent).chrono_headers == (("x-chrono-mail-kind", "reply"), ("x-chrono-mail-source", "Inbox/" + "a" * 32))


@pytest.mark.parametrize(
    "header",
    [("X-Other", "x"), ("Subject", "x"), ("X-Chrono-Mail-", "x"), ("X-Chrono-Mail-A B", "x"), ("X-Chrono-Mail-Kind", "a\r\nBcc: evil@example.com"), ("X-Chrono-Mail-Kind", "a\nb"),
     ("X-Chrono-Mail-Kind", "caf\u00e9"), ("X-Chrono-Mail-Kind", "x" * 501), ("X-Chrono-Mail-Kind", "\x00"), ("x-chrono-mail-kind\r\nBcc: e@x", "v")],
)
def test_only_the_programs_own_headers_with_plain_values_can_be_added(header: tuple[str, str]) -> None:
    with pytest.raises(MessageFormatError):
        build_message(OutgoingMessage(from_address=SENDER, to_addresses=(RECIPIENT,), extra_headers=(header,)))


def test_a_draft_may_be_empty() -> None:
    parsed = parse_message(build_message(OutgoingMessage(from_address=SENDER), draft=True))
    assert parsed.to_addresses == () and parsed.bcc_addresses == () and parsed.subject == "" and parsed.text_body is not None


def test_without_bcc_removes_only_the_bcc_header_and_its_continuation_lines() -> None:
    from chrono_mail.mime import without_bcc

    raw = b"From: a@example.com\r\nBcc: one@example.com,\r\n two@example.com\r\nSubject: Bcc: in the subject\r\nX-Other: kept\r\n folded\r\n\r\nBcc: in the body stays\r\n"
    assert without_bcc(raw) == b"From: a@example.com\r\nSubject: Bcc: in the subject\r\nX-Other: kept\r\n folded\r\n\r\nBcc: in the body stays\r\n"
    assert without_bcc(b"no header end") == b"no header end"
    assert without_bcc(b"BCC: x@example.com\r\nTo: y@example.com\r\n\r\nbody") == b"To: y@example.com\r\n\r\nbody"
