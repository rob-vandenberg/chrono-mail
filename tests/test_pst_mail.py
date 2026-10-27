import struct
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

import pytest

from chrono_mail.core.flags import MessageFlag
from chrono_mail.pst.headers import categories, flags
from chrono_mail.pst.mail import MailError, build_mail
from chrono_mail.pst.properties import Attachment, Message
from tests.test_pst_helpers import KEYWORDS, Plain, props

# --- Version ------------------------------------------------------------
__version__ = "test_pst_mail.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial tests: messages rebuilt from the data file's properties

HEADERS = (
    "Received: from a.example.org by b.example.org;\r\n\tMon, 2 Mar 2026 09:00:00 +0100\r\n"
    "From: Ann Example <ann@example.org>\r\nTo: bob@example.net\r\nSubject: Kept subject\r\n"
    "Message-ID: <kept@example.org>\r\nMIME-Version: 1.0\r\nContent-Type: multipart/alternative;\r\n\tboundary=\"old\"\r\n"
    "Content-Transfer-Encoding: 7bit\r\nX-Kept: yes\r\n\r\n"
)


def _parse(raw: bytes) -> EmailMessage:
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(parsed, EmailMessage)
    return parsed


def _message(values: dict[int, Plain], recipients: tuple[dict[int, Plain], ...] = (), attachments: tuple[Attachment, ...] = ()) -> Message:
    return Message(props(values), tuple(props(row) for row in recipients), attachments, node=0x200024)


def _text(part: EmailMessage) -> str:
    content = part.get_content()
    assert isinstance(content, str)
    return content


def test_kept_headers_with_a_new_body() -> None:
    raw = build_mail(_message({0x007D: HEADERS, 0x1000: "Hello\r\nthere", 0x0037: "Ignored subject"}))
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    parsed = _parse(raw)
    assert parsed["Subject"] == "Kept subject"
    assert parsed["X-Kept"] == "yes"
    assert len(parsed.get_all("Received") or []) == 1
    assert "Mon, 2 Mar 2026" in str(parsed["Received"])  # the folded line stays with its header
    assert len(parsed.get_all("Content-Type") or []) == 1
    assert parsed.get_content_type() == "text/plain"
    assert "boundary=\"old\"" not in raw.decode("ascii")
    assert _text(parsed).replace("\r\n", "\n").strip() == "Hello\nthere"


def test_headers_built_from_properties() -> None:
    sent = datetime(2026, 3, 2, 8, 30, tzinfo=UTC)
    message = _message(
        {
            0x0037: "Grüße", 0x0039: sent, 0x0042: "Ann Example", 0x5D02: "ann@example.org", 0x1035: "<id1@example.org>",
            0x1042: "<id0@example.org>", 0x1039: "<id0@example.org>", 0x1000: "Body", 0x0017: 2,
        },
        recipients=(
            {0x0C15: 1, 0x3001: "Bob", 0x39FE: "bob@example.net"},
            {0x0C15: 2, 0x3001: "Carol", 0x3003: "carol@example.com", 0x3002: "SMTP"},
            {0x0C15: 3, 0x3001: "Dave", 0x3003: "/o=Org/ou=Group/cn=Recipients/cn=dave", 0x3002: "EX"},
        ),
    )
    parsed = _parse(build_mail(message))
    assert parsed["Subject"] == "Grüße"
    assert parsed["From"].addresses[0].addr_spec == "ann@example.org"
    assert parsed["To"].addresses[0].addr_spec == "bob@example.net"
    assert parsed["Cc"].addresses[0].addr_spec == "carol@example.com"
    assert "Dave" in str(parsed["Bcc"])  # no internet address: the name alone
    assert parsed["Date"].datetime == sent
    assert parsed["Message-ID"] == "<id1@example.org>"
    assert parsed["In-Reply-To"] == "<id0@example.org>"
    assert parsed["Importance"] == "high"


def test_plain_and_html_make_an_alternative() -> None:
    html = "<html><body><p>Hi</p></body></html>"
    message = _message({0x1000: "Hi", 0x1013: html.encode("utf-8"), 0x3FDE: 65001})
    parsed = _parse(build_mail(message))
    assert parsed.get_content_type() == "multipart/alternative"
    kinds = [part.get_content_type() for part in parsed.iter_parts()]
    assert kinds == ["text/plain", "text/html"]


def _compressed(rtf: bytes) -> bytes:
    return struct.pack("<IIII", len(rtf) + 12, len(rtf), 0x414C454D, 0) + rtf


def test_html_from_compressed_rtf() -> None:
    rtf = b"{\\rtf1\\ansi\\fromhtml1 {\\*\\htmltag <p>}Hi{\\*\\htmltag </p>}}"
    parsed = _parse(build_mail(_message({0x1009: _compressed(rtf)})))
    assert parsed.get_content_type() == "text/html"
    assert _text(parsed).strip() == "<p>Hi</p>"


def test_plain_text_from_compressed_rtf() -> None:
    parsed = _parse(build_mail(_message({0x1009: _compressed(b"{\\rtf1\\ansi Only rtf\\par}")})))
    assert parsed.get_content_type() == "text/plain"
    assert _text(parsed).strip() == "Only rtf"


def test_inline_picture_and_attachment() -> None:
    picture = Attachment(props({0x3705: 1, 0x3707: "logo.png", 0x370E: "image/png", 0x3712: "logo@1"}), b"\x89PNG-data")
    document = Attachment(props({0x3705: 1, 0x3707: "report.pdf", 0x370E: "application/pdf"}), b"%PDF-1.4 data")
    message = _message({0x1000: "See", 0x1013: b'<img src="cid:logo@1">', 0x3FDE: 65001}, attachments=(picture, document))
    parsed = _parse(build_mail(message))
    assert parsed.get_content_type() == "multipart/mixed"
    kinds = [part.get_content_type() for part in parsed.walk()]
    assert kinds == ["multipart/mixed", "multipart/alternative", "text/plain", "multipart/related", "text/html", "image/png", "application/pdf"]
    parts = {part.get_content_type(): part for part in parsed.walk()}
    assert parts["image/png"]["Content-ID"] == "<logo@1>"
    assert parts["image/png"].get_content() == b"\x89PNG-data"
    assert parts["application/pdf"].get_filename() == "report.pdf"
    assert parts["application/pdf"].get_content() == b"%PDF-1.4 data"


def test_picture_not_shown_by_the_html_is_an_attachment() -> None:
    picture = Attachment(props({0x3705: 1, 0x3707: "photo.jpg", 0x370E: "image/jpeg", 0x3712: "unused@1"}), b"jpeg")
    parsed = _parse(build_mail(_message({0x1000: "Text"}, attachments=(picture,))))
    assert [part.get_content_type() for part in parsed.walk()] == ["multipart/mixed", "text/plain", "image/jpeg"]


def test_embedded_message() -> None:
    inner = _message({0x0037: "Inner", 0x1000: "Inner body", 0x0042: "Eve", 0x5D02: "eve@example.org"})
    holder = Attachment(props({0x3705: 5, 0x3001: "Inner"}), None, inner)
    parsed = _parse(build_mail(_message({0x0037: "Outer", 0x1000: "Outer body"}, attachments=(holder,))))
    kinds = [part.get_content_type() for part in parsed.walk()]
    assert "message/rfc822" in kinds
    attached = next(part for part in parsed.walk() if part.get_content_type() == "message/rfc822")
    inner_message = attached.get_content()
    assert isinstance(inner_message, EmailMessage)
    assert inner_message["Subject"] == "Inner"


def test_embedding_depth_is_capped() -> None:
    message = _message({0x1000: "Deepest"})
    for _ in range(10):
        message = _message({0x1000: "Level"}, attachments=(Attachment(props({0x3705: 5}), None, message),))
    raw = build_mail(message)
    assert raw.count(b"message/rfc822") == 5


def test_a_message_over_the_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.pst import mail

    monkeypatch.setattr(mail, "MAXIMUM_MESSAGE_BYTES", 100)
    big = Attachment(props({0x3705: 1, 0x3707: "big.bin"}), b"x" * 1000)
    with pytest.raises(MailError):
        build_mail(_message({0x1000: "Body"}, attachments=(big,)))


def test_flags() -> None:
    assert flags(props({0x0E07: 0x0001})) == {MessageFlag.SEEN}
    assert flags(props({0x0E07: 0x0008})) == {MessageFlag.DRAFT}
    assert flags(props({0x1090: 2, 0x1081: 103})) == {MessageFlag.FLAGGED, MessageFlag.REPLIED}
    assert flags(props({0x1090: 1, 0x1081: 104})) == frozenset()  # completed flag, forwarded: no flag here


def test_categories() -> None:
    assert categories(props({KEYWORDS: ("Work", "Blue", "Work", " ")})) == ("Work", "Blue")
    assert categories(props({})) == ()


# --- faults found by the review --------------------------------------------------------------------------------------------------------


def test_a_body_that_cannot_be_read_costs_only_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review fault 1: a failure in the RTF conversion lost the whole message; now the headers and attachments stay and a note says so."""
    from chrono_mail.pst import mail

    def broken(rtf: bytes) -> str:
        raise ValueError("damaged")

    monkeypatch.setattr(mail, "text_from_rtf", broken)
    document = Attachment(props({0x3705: 1, 0x3707: "a.pdf", 0x370E: "application/pdf"}), b"%PDF")
    raw, notes = mail.rebuild(_message({0x0037: "Kept", 0x1009: _compressed(b"{\\rtf1 text}")}, attachments=(document,)))
    parsed = _parse(raw)
    assert parsed["Subject"] == "Kept"
    assert "application/pdf" in [part.get_content_type() for part in parsed.walk()]
    assert notes == ("its text could not be read",)


@pytest.mark.parametrize("order", ["attachment-first", "picture-first"])
@pytest.mark.parametrize("plain", [True, False], ids=["with-plain", "html-only"])
def test_inline_picture_and_attachment_in_any_order(order: str, plain: bool) -> None:
    """Review fault 2: an attachment before the inline picture of an HTML-only message made the library refuse the picture."""
    picture = Attachment(props({0x3705: 1, 0x3707: "logo.png", 0x370E: "image/png", 0x3712: "logo@1"}), b"\x89PNG")
    document = Attachment(props({0x3705: 1, 0x3707: "report.pdf", 0x370E: "application/pdf"}), b"%PDF")
    values: dict[int, Plain] = {0x1013: b'<img src="cid:logo@1">', 0x3FDE: 65001}
    if plain:
        values[0x1000] = "See"
    attachments = (document, picture) if order == "attachment-first" else (picture, document)
    parsed = _parse(build_mail(_message(values, attachments=attachments)))
    kinds = [part.get_content_type() for part in parsed.walk()]
    assert kinds[0] == "multipart/mixed" and "multipart/related" in kinds
    assert kinds.index("image/png") == kinds.index("text/html") + 1  # the picture sits beside the HTML that shows it


def test_addresses_with_non_ascii_characters() -> None:
    """Review fault 3: an internationalised address made the header block unserialisable and the message was lost."""
    message = _message(
        {0x0037: "Hello", 0x0042: "Jörg", 0x5D02: "jörg@exämple.de", 0x1000: "Body"},
        recipients=({0x0C15: 1, 0x3001: "Bob", 0x39FE: "bob@example.net"}, {0x0C15: 2, 0x3001: "Zoë", 0x39FE: "zoë@例え.jp"}),
    )
    raw = build_mail(message)
    parsed = _parse(raw)
    assert parsed["Subject"] == "Hello"
    assert "jörg@exämple.de" in raw.decode("utf-8")
    assert parsed["To"].addresses[0].addr_spec == "bob@example.net"


def test_recipient_type_ignores_the_high_flags() -> None:
    """Review fault 4: MAPI_SUBMITTED (0x80000000) on the recipient type moved a Cc to To."""
    message = _message({0x1000: "x"}, recipients=({0x0C15: -0x80000000 | 2, 0x3001: "Carol", 0x39FE: "carol@example.com"},))
    parsed = _parse(build_mail(message))
    assert parsed["Cc"].addresses[0].addr_spec == "carol@example.com" and parsed["To"] is None


def test_partial_kept_headers_are_completed() -> None:
    """Review fault 8: kept headers with only Received lines gave a message without From, Subject and Date."""
    sent = datetime(2026, 3, 2, 8, 30, tzinfo=UTC)
    message = _message(
        {0x007D: "Received: from a by b; Mon, 2 Mar 2026 09:00:00 +0100\r\n\r\n", 0x0037: "Subject here", 0x0039: sent, 0x0042: "Ann",
         0x5D02: "ann@example.org", 0x1035: "<x@example.org>", 0x1000: "Body"},
        recipients=({0x0C15: 1, 0x3001: "Bob", 0x39FE: "bob@example.net"},),
    )
    parsed = _parse(build_mail(message))
    assert parsed["Received"] is not None
    assert parsed["Subject"] == "Subject here" and parsed["Date"].datetime == sent and parsed["Message-ID"] == "<x@example.org>"
    assert parsed["From"].addresses[0].addr_spec == "ann@example.org" and parsed["To"].addresses[0].addr_spec == "bob@example.net"


SIGNED = (
    b'Content-Type: multipart/signed; protocol="application/pkcs7-signature"; micalg=sha-256; boundary="sig"\r\n\r\n'
    b"--sig\r\nContent-Type: text/plain\r\n\r\nSigned text\r\n--sig\r\nContent-Type: application/pkcs7-signature; name=smime.p7s\r\n"
    b"Content-Transfer-Encoding: base64\r\n\r\nAAEC\r\n--sig--\r\n"
)


def test_signed_message_keeps_its_signed_body() -> None:
    """Review fault 9: the signed MIME body is the single attachment; it is used as it is, so the signature still verifies."""
    holder = Attachment(props({0x3705: 1, 0x3707: "smime.p7m", 0x370E: "multipart/signed"}), SIGNED)
    message = _message({0x001A: "IPM.Note.SMIME.MultipartSigned", 0x007D: HEADERS, 0x1000: "Signed text"}, attachments=(holder,))
    raw = build_mail(message)
    assert raw.endswith(SIGNED)
    parsed = _parse(raw)
    assert parsed.get_content_type() == "multipart/signed" and parsed["Subject"] == "Kept subject"


def test_encrypted_message_keeps_its_encrypted_body() -> None:
    holder = Attachment(props({0x3705: 1, 0x3707: "smime.p7m", 0x370E: "application/pkcs7-mime"}), b"\x30\x82\x01\x00encrypted")
    raw = build_mail(_message({0x001A: "IPM.Note.SMIME", 0x0037: "Secret", 0x1000: ""}, attachments=(holder,)))
    parsed = _parse(raw)
    assert parsed.get_content_type() == "application/pkcs7-mime"
    assert parsed.get_content() == b"\x30\x82\x01\x00encrypted"


def test_attachment_without_data_is_noted() -> None:
    """Review fault 17: an attachment kept as a link (or with nothing read) was left out without a word."""
    from chrono_mail.pst.mail import rebuild

    linked = Attachment(props({0x3705: 2, 0x3707: "link.doc"}), None)
    _, notes = rebuild(_message({0x1000: "Body"}, attachments=(linked, linked)))
    assert notes == ("2 attachment(s) kept only as a link or without contents",)


def _bytes_in_this_process() -> bytes:
    picture = Attachment(props({0x3705: 1, 0x3707: "logo.png", 0x370E: "image/png", 0x3712: "logo@1"}), b"\x89PNG")
    document = Attachment(props({0x3705: 1, 0x3707: "report.pdf", 0x370E: "application/pdf"}), b"%PDF")
    message = _message({0x0037: "Same", 0x1000: "Text", 0x1013: b'<img src="cid:logo@1">', 0x3FDE: 65001}, attachments=(picture, document))
    return build_mail(message)


def test_same_bytes_in_another_process() -> None:
    """Review point 20: the same item gives the same bytes on every import (no random boundaries, no hash order)."""
    import multiprocessing

    with multiprocessing.get_context("spawn").Pool(1) as pool:
        other = pool.apply(_bytes_in_this_process)
    assert other == _bytes_in_this_process()


# --- faults found by the second review --------------------------------------------------------------------------------------------------


def test_attached_message_is_kept_byte_for_byte() -> None:
    """Second review fault 1: an attached message was parsed and written again in 7 bits: its 8-bit headers became unknown-8bit, its
    internationalised address broke, a signed 8-bit part was turned into base64 (the signature then fails)."""
    from chrono_mail.pst.mail import rebuild

    inner = _message({0x007D: "From: Jörg <jörg@exämple.org>\r\nSubject: Grüße\r\nTo: a@example.org\r\n\r\n", 0x1000: "inner"})
    inner_raw, _ = rebuild(inner)
    outer, _ = rebuild(_message({0x1000: "outer"}, attachments=(Attachment(props({0x3705: 5}), None, inner),)))
    assert inner_raw in outer
    assert b"unknown-8bit" not in outer
    signed_part = 'Content-Type: text/plain; charset="utf-8"\r\nContent-Transfer-Encoding: 8bit\r\n\r\nGrüße\r\n'.encode()
    entity = (b'Content-Type: multipart/signed; protocol="application/pkcs7-signature"; micalg=sha-256; boundary="BB"\r\n\r\n--BB\r\n'
              + signed_part + b"\r\n--BB\r\nContent-Type: application/pkcs7-signature\r\nContent-Transfer-Encoding: base64\r\n\r\nAAAA\r\n--BB--\r\n")
    signed = Message(props({0x001A: "IPM.Note.SMIME.MultipartSigned", 0x007D: "From: a@example.org\r\nSubject: s\r\n\r\n"}),
                     (), (Attachment(props({0x370E: "multipart/signed", 0x3707: "smime.p7m"}), entity),))
    outer, _ = rebuild(_message({0x1000: "outer"}, attachments=(Attachment(props({0x3705: 5}), None, signed),)))
    assert signed_part in outer
    parsed = _parse(outer)
    attached = next(part for part in parsed.walk() if part.get_content_type() == "message/rfc822")
    assert attached.get_param("boundary") is None  # a boundary belongs to a multipart, never to the attached message


def test_kept_headers_lose_every_content_header() -> None:
    """Second review fault 5: Content-Disposition, Content-ID and the like described the old body and stayed with the new one."""
    headers = "From: a@example.org\r\nContent-Disposition: inline\r\nContent-ID: <x@y>\r\nContent-Description: old\r\nSubject: s\r\n\r\n"
    raw = build_mail(_message({0x007D: headers, 0x1000: "Body"}))
    head = raw.split(b"\r\n\r\n", 1)[0].lower()
    assert b"content-disposition" not in head and b"content-id" not in head and b"content-description" not in head
    assert b"subject: s" in head


def test_signed_entity_with_its_own_mime_version() -> None:
    """Second review fault 6: a kept signed entity that starts with MIME-Version gave the message that header twice."""
    entity = b"MIME-Version: 1.0\r\n" + SIGNED
    holder = Attachment(props({0x3705: 1, 0x3707: "smime.p7m", 0x370E: "multipart/signed"}), entity)
    raw = build_mail(_message({0x001A: "IPM.Note.SMIME.MultipartSigned", 0x007D: HEADERS}, attachments=(holder,)))
    assert raw.lower().count(b"mime-version:") == 1
    assert raw.endswith(SIGNED)


# --- faults found by the third review ----------------------------------------------------------------------------------------------------


def test_an_attached_message_cannot_hold_another_ones_place() -> None:
    """Third review fault 1: the stand-ins were predictable and replaced one after another, so one attached message holding the next
    one's stand-in in a header took that message's bytes, and the next kept only the stand-in."""
    import hashlib

    from chrono_mail.pst.mail import rebuild

    second = _message({0x007D: "From: b@example.org\r\nSubject: second\r\n\r\n", 0x1000: "second message"})
    second_raw, _ = rebuild(second)
    old_stand_in = f"=_attached_message_{hashlib.sha256(second_raw).hexdigest()}_1_="
    first = _message({0x007D: f"From: a@example.org\r\nSubject: first\r\nX-Note: {old_stand_in}\r\n\r\n", 0x1000: "first message"})
    first_raw, _ = rebuild(first)
    outer, _ = rebuild(_message({0x1000: "outer"}, attachments=(Attachment(props({0x3705: 5}), None, first), Attachment(props({0x3705: 5}), None, second))))
    assert first_raw in outer and second_raw in outer
    assert outer.count(second_raw) == 1


def test_a_stand_in_found_twice_refuses_the_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.pst import mail

    def fixed(raws: list[bytes]) -> list[bytes]:
        return [b"=_fixed_=" for _ in raws]

    inner = _message({0x1000: "x"})
    monkeypatch.setattr(mail, "_tokens", fixed)
    with pytest.raises(MailError):  # the outer text holds the stand-in too: put together, the message would be wrong
        mail.rebuild(_message({0x1000: "outer =_fixed_="}, attachments=(Attachment(props({0x3705: 5}), None, inner),)))


def test_transfer_encoding_label_of_an_attached_message() -> None:
    """Third review fault 4: NUL, a lone CR or LF, and lines over 998 octets are binary, whatever the characters."""
    from chrono_mail.pst.mail import transfer_encoding

    assert transfer_encoding(b"Subject: a\r\n\r\nb\r\n") == "7bit"
    assert transfer_encoding("Subject: ü\r\n\r\nb\r\n".encode()) == "8bit"
    assert transfer_encoding(b"Subject: a\r\n\r\nb\x00\r\n") == "binary"
    assert transfer_encoding(b"Subject: a\r\n\r\nb\rc\r\n") == "binary"
    assert transfer_encoding(b"Subject: a\r\n\r\nb\nc\r\n") == "binary"
    assert transfer_encoding(b"Subject: a\r\n\r\n" + b"x" * 999 + b"\r\n") == "binary"
