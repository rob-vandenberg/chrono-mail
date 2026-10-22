import pytest

from datetime import UTC, datetime, timedelta, timezone

from chrono_mail.mime import Address, ParsedMessage, parse_message

# --- Version ------------------------------------------------------------
__version__ = "test_mime_parse.py 0.0.16"

# --- Version history ----------------------------------------------------
# v0.0.16: Drafts
# v0.0.13: Changes for the user interface (see DEVLOG.md)
# v0.0.5: Initial parser tests


def test_plain_text_message() -> None:
    raw = (
        b"From: Alice Example <alice@example.com>\r\n"
        b"To: bob@example.com, Carol <carol@example.com>\r\n"
        b"Cc: dave@example.com\r\n"
        b"Reply-To: replies@example.com\r\n"
        b"Subject: Hello\r\n"
        b"Date: Thu, 08 Oct 2026 10:30:00 +0200\r\n"
        b"Message-ID: <abc123@example.com>\r\n"
        b"In-Reply-To: <parent@example.com>\r\n"
        b"References: <root@example.com> <parent@example.com>\r\n"
        b"\r\n"
        b"Line one\r\nLine two\r\n"
    )
    message = parse_message(raw)
    assert message.subject == "Hello"
    assert message.from_addresses == (Address("Alice Example", "alice@example.com"),)
    assert message.to_addresses == (Address("", "bob@example.com"), Address("Carol", "carol@example.com"))
    assert message.cc_addresses == (Address("", "dave@example.com"),)
    assert message.reply_to_addresses == (Address("", "replies@example.com"),)
    assert message.date == datetime(2026, 10, 8, 10, 30, tzinfo=timezone(timedelta(hours=2)))
    assert message.message_id == "<abc123@example.com>"
    assert message.in_reply_to == "<parent@example.com>"
    assert message.references == ("<root@example.com>", "<parent@example.com>")
    assert message.text_body == "Line one\nLine two\n"
    assert message.html_body is None
    assert message.attachments == ()
    assert message.defects == ()


def test_html_only_message() -> None:
    raw = b"From: a@example.com\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>Hi</p>\r\n"
    message = parse_message(raw)
    assert message.text_body is None
    assert message.html_body == "<p>Hi</p>\n"


def test_multipart_alternative_with_attachment() -> None:
    raw = (
        b"From: a@example.com\r\nSubject: mixed\r\n"
        b"Content-Type: multipart/mixed; boundary=OUTER\r\n\r\n"
        b"--OUTER\r\nContent-Type: multipart/alternative; boundary=INNER\r\n\r\n"
        b"--INNER\r\nContent-Type: text/plain\r\n\r\nplain body\r\n"
        b"--INNER\r\nContent-Type: text/html\r\n\r\n<b>html body</b>\r\n"
        b"--INNER--\r\n"
        b"--OUTER\r\nContent-Type: application/pdf; name=report.pdf\r\n"
        b"Content-Disposition: attachment; filename=report.pdf\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\nJVBERi0x\r\n"
        b"--OUTER--\r\n"
    )
    message = parse_message(raw)
    assert message.text_body == "plain body"
    assert message.html_body == "<b>html body</b>"
    assert len(message.attachments) == 1
    attachment = message.attachments[0]
    assert (attachment.filename, attachment.content_type, attachment.inline) == ("report.pdf", "application/pdf", False)
    assert attachment.data == b"%PDF-1"


def test_inline_image_keeps_content_id() -> None:
    raw = (
        b"From: a@example.com\r\nContent-Type: multipart/related; boundary=R\r\n\r\n"
        b"--R\r\nContent-Type: text/html\r\n\r\n<img src=\"cid:logo\">\r\n"
        b"--R\r\nContent-Type: image/png\r\nContent-ID: <logo>\r\nContent-Disposition: inline\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\niVBORw==\r\n--R--\r\n"
    )
    message = parse_message(raw)
    assert message.html_body is not None and "cid:logo" in message.html_body
    assert [(a.content_type, a.content_id, a.inline) for a in message.attachments] == [("image/png", "<logo>", True)]


def test_forwarded_message_is_an_attachment() -> None:
    raw = (
        b"From: a@example.com\r\nContent-Type: multipart/mixed; boundary=B\r\n\r\n"
        b"--B\r\nContent-Type: text/plain\r\n\r\nSee below\r\n"
        b"--B\r\nContent-Type: message/rfc822\r\n\r\n"
        b"From: other@example.com\r\nSubject: original\r\n\r\noriginal body\r\n"
        b"--B--\r\n"
    )
    message = parse_message(raw)
    assert message.text_body == "See below"
    assert len(message.attachments) == 1
    assert message.attachments[0].content_type == "message/rfc822"
    assert b"Subject: original" in message.attachments[0].data


def test_rfc2047_headers() -> None:
    raw = (
        b"From: =?utf-8?q?J=C3=B6rg_M=C3=BCller?= <joerg@example.com>\r\n"
        b"Subject: =?iso-8859-1?q?Caf=E9_cr=E8me?=\r\n\r\nbody\r\n"
    )
    message = parse_message(raw)
    assert message.subject == "Café crème"
    assert message.from_addresses == (Address("Jörg Müller", "joerg@example.com"),)


def test_charsets_are_decoded() -> None:
    latin1 = parse_message(b"Content-Type: text/plain; charset=iso-8859-1\r\n\r\ncaf\xe9\r\n")
    utf8 = parse_message("Content-Type: text/plain; charset=utf-8\r\n\r\ncafé ☕\r\n".encode())
    japanese = parse_message(
        "Content-Type: text/plain; charset=iso-2022-jp\r\n\r\n".encode() + "こんにちは".encode("iso-2022-jp") + b"\r\n"
    )
    assert latin1.text_body == "café\n"
    assert utf8.text_body == "café ☕\n"
    assert japanese.text_body == "こんにちは\n"


def test_quoted_printable_body() -> None:
    raw = b"Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\ncaf=C3=A9=\r\n!\r\n"
    assert parse_message(raw).text_body == "café!\n"


def test_date_without_time_zone_is_taken_as_utc() -> None:
    message = parse_message(b"Date: Thu, 08 Oct 2026 10:00:00 -0000\r\n\r\nx\r\n")
    assert message.date == datetime(2026, 10, 8, 10, 0, tzinfo=UTC)


def test_empty_message_gives_an_empty_result() -> None:
    assert parse_message(b"") == ParsedMessage(text_body="")


def test_garbled_address_header_keeps_the_original_text() -> None:
    message = parse_message(b"To: <<<@@@\r\nSubject: x\r\n\r\nbody\r\n")
    assert message.to_addresses == ()
    assert ("to", "<<<@@@") in message.raw_address_headers
    assert "invalid-address-header-to" in message.defects
    assert message.text_body == "body\n"


def has_surrogates(text: str) -> bool:
    return any("\ud800" <= character <= "\udfff" for character in text)


def all_text(message: ParsedMessage) -> list[str]:
    texts = [message.subject, message.message_id or "", message.in_reply_to or "", *message.references, *message.defects, message.text_body or "", message.html_body or ""]
    texts += [text for _, text in message.raw_address_headers]
    for address in (*message.from_addresses, *message.to_addresses, *message.cc_addresses, *message.reply_to_addresses):
        texts += [address.name, address.address]
    for attachment in message.attachments:
        texts += [attachment.filename or "", attachment.content_type, attachment.content_id or ""]
    return texts


def test_raw_utf8_in_headers_is_read_as_text() -> None:
    # Many mailers send headers as raw UTF-8. Python's parser returns such bytes as lone surrogates,
    # which cannot be stored in the index (this crashed indexing before).
    raw = "From: Ünï Cödé <u@x.org>\r\nTo: 日本 <j@x.jp>\r\nSubject: 🙂 emoji ☕ 日本語 العربية\r\nMessage-ID: <é@x>\r\n\r\nbody\r\n".encode()
    message = parse_message(raw)
    assert message.subject == "🙂 emoji ☕ 日本語 العربية"
    assert message.from_addresses == (Address("Ünï Cödé", "u@x.org"),) and message.to_addresses == (Address("日本", "j@x.jp"),)
    assert message.message_id == "<é@x>" and not any(has_surrogates(text) for text in all_text(message))


@pytest.mark.parametrize("subject", [b"caf\xe9", b"\xff\xfe", b"\xc3", b"ok \xed\xa0\x80 surrogate in utf-8", b"\x80" * 50, b"mixed \xc3\xa9 and \xe9"])
def test_invalid_bytes_in_headers_become_replacement_characters(subject: bytes) -> None:
    message = parse_message(b"Subject: " + subject + b"\r\nFrom: a@x.org\r\n\r\nx")
    assert isinstance(message.subject, str) and not any(has_surrogates(text) for text in all_text(message))
    message.subject.encode("utf-8")  # must be storable


def test_raw_bytes_in_an_attachment_name_and_content_id() -> None:
    raw = (b"From: a@x.org\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=B\r\n\r\n--B\r\nContent-Type: text/plain\r\n\r\nx\r\n"
           b"--B\r\nContent-Type: image/png; name=\"caf\xc3\xa9\xff.png\"\r\nContent-ID: <\xc3\xa9\xff@x>\r\nContent-Disposition: attachment; filename=\"caf\xc3\xa9\xff.png\"\r\n\r\ndata\r\n--B--\r\n")
    message = parse_message(raw)
    assert not any(has_surrogates(text) for text in all_text(message)) and message.attachments


def test_bcc_and_chrono_headers_are_read() -> None:
    raw = (b"From: a@x.org\r\nTo: b@x.org\r\nBcc: Hidden <h@x.org>, i@x.org\r\nX-Chrono-Mail-Kind: forward\r\nx-chrono-mail-source: Inbox/abc\r\n"
           b"X-Other: ignored\r\nSubject: s\r\n\r\nbody\r\n")
    message = parse_message(raw)
    assert message.bcc_addresses == (Address("Hidden", "h@x.org"), Address("", "i@x.org"))
    assert message.chrono_headers == (("x-chrono-mail-kind", "forward"), ("x-chrono-mail-source", "Inbox/abc"))
    assert ("bcc", "Hidden <h@x.org>, i@x.org") in message.raw_address_headers


def test_chrono_headers_are_limited_and_scrubbed() -> None:
    many = b"".join(b"X-Chrono-Mail-N%d: v\r\n" % number for number in range(100))
    message = parse_message(b"From: a@x.org\r\n" + many + b"X-Chrono-Mail-Long: " + b"x" * 5000 + b"\r\nX-Chrono-Mail-Bytes: \xff\xfe\r\n\r\nx")
    assert len(message.chrono_headers) == 20
    assert not any(has_surrogates(value) for _, value in message.chrono_headers)
    assert max(len(value) for _, value in message.chrono_headers) <= 1000
