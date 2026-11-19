import base64
from email import message_from_bytes, policy
from email.message import EmailMessage

import pytest

from chrono_mail.core import MessageFlag, MessageFormatError
from chrono_mail.engine.compose import build_draft
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.mime import Address, Attachment, OutgoingMessage, ParsedMessage, build_message, parse_message
from chrono_mail.mime.pictures import attachment_from_data, data_address, pictures_into
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_pictures.py 4.6.631"

# --- Version history ----------------------------------------------------
# v4.6.631: The quote keeps the original's own style rules (D-360)
# v4.4.622: Replies and forwards quote the original with its pictures; a plain-text forward carries them as attachments
# v4.4.621: Initial tests: pictures inside the text are sent, saved, opened and taken back unaltered as inline parts (D-353)

# a real 1 x 1 PNG; its bytes must arrive exactly as given
PNG: bytes = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def html_with(*pictures: tuple[bytes, str]) -> str:
    images = "".join(f'<img src="{data_address("image/png", name, data)}" />' for data, name in pictures)
    return f"<html><body><p>Look at this: {images}</p></body></html>"


def built(html: str) -> EmailMessage:
    raw = build_message(OutgoingMessage(Address("", "me@example.com"), (Address("", "you@example.com"),), (), (), "Pictures", "Look at this: [picture: photo.png]", html_body=html))
    message = message_from_bytes(raw, policy=policy.default)
    assert isinstance(message, EmailMessage)
    return message


def test_a_picture_in_the_text_goes_as_an_inline_part_the_html_shows_by_cid_with_its_bytes_unaltered() -> None:
    message = built(html_with((PNG, "photo.png")))
    html_part = message.get_body(("html",))
    assert html_part is not None and "data:" not in html_part.get_content()
    (picture,) = [part for part in message.walk() if part.get_content_type() == "image/png"]
    content_id = str(picture["Content-ID"]).strip("<>")
    assert f"cid:{content_id}" in html_part.get_content()
    assert picture.get_content() == PNG and picture.get_filename() == "photo.png" and picture.get_content_disposition() == "inline"
    assert any(part.get_content_type() == "multipart/related" for part in message.walk())
    parsed = parse_message(message.as_bytes())
    (attachment,) = parsed.attachments
    assert attachment.inline and attachment.data == PNG  # shown in the text by the reading pane, not listed as an attachment


def test_the_same_picture_twice_is_one_part_and_two_pictures_are_two() -> None:
    twice = built(html_with((PNG, "a.png"), (PNG, "a.png")))
    assert len([part for part in twice.walk() if part.get_content_type() == "image/png"]) == 1
    other = PNG[:-1] + b"\x83"  # other bytes: another picture
    two = built(html_with((PNG, "a.png"), (other, "b.png")))
    assert [part.get_filename() for part in two.walk() if part.get_content_type() == "image/png"] == ["a.png", "b.png"]


def test_a_picture_that_cannot_be_read_fails_the_message_and_never_goes_half() -> None:
    with pytest.raises(MessageFormatError):
        built('<p><img src="data:image/png;name=x.png;base64,@@@not base64@@@" /></p>')


def test_the_pictures_of_a_stored_message_become_embedded_again_with_their_names() -> None:
    parsed = parse_message(built(html_with((PNG, "photo.png"))).as_bytes())
    assert parsed.html_body is not None
    again = pictures_into(parsed.html_body, parsed.attachments)
    assert data_address("image/png", "photo.png", PNG) in again and "cid:" not in again


def test_a_picture_given_as_an_attachment_is_an_ordinary_attachment_and_anything_else_is_not_a_picture() -> None:
    attachment = attachment_from_data(data_address("image/png", "photo 1.png", PNG))
    assert attachment is not None and attachment.filename == "photo 1.png" and attachment.data == PNG and not attachment.inline
    assert attachment_from_data("/home/someone/report.pdf") is None
    assert attachment_from_data("data:text/html;base64,PGI+") is None


def test_a_draft_keeps_its_pictures_and_opens_with_them(setup: Setup) -> None:
    saved = call(setup.engine, "draft.save", account=setup.account_id, subject="Holiday", body="Look [picture: beach.png]", html=html_with((PNG, "beach.png")))
    opened = call(setup.engine, "draft.open", account=setup.account_id, message=saved.message_id)
    assert data_address("image/png", "beach.png", PNG) in opened.html and opened.attachments == ()  # in the text, not listed as an attachment


def test_a_sent_picture_reaches_the_server_unaltered(setup: Setup) -> None:
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Holiday", body="Look", html=html_with((PNG, "beach.png")))
    (received,) = setup.smtp.received
    parsed = parse_message(received.data)
    (picture,) = parsed.attachments
    assert picture.inline and picture.data == PNG and picture.filename == "beach.png"


def test_a_message_taken_back_from_the_outbox_keeps_its_pictures(setup: Setup) -> None:
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Holiday", body="Look", html=html_with((PNG, "beach.png")), delay_seconds=900)
    (row,) = call(setup.engine, "message.list", account=setup.account_id, folder="Outbox").messages
    back = call(setup.engine, "mail.cancel-send", account=setup.account_id, message=row.message_id)
    opened = call(setup.engine, "draft.open", account=back.account, folder=back.folder, message=back.message_id)
    assert data_address("image/png", "beach.png", PNG) in opened.html


def test_a_picture_that_became_an_attachment_is_sent_as_one(setup: Setup) -> None:
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Holiday", body="Look", attach=(data_address("image/png", "beach.png", PNG),))
    (received,) = setup.smtp.received
    (picture,) = parse_message(received.data).attachments
    assert not picture.inline and picture.data == PNG and picture.filename == "beach.png"


def test_a_scheduled_message_taken_back_keeps_its_pictures(setup: Setup) -> None:
    from tests.test_engine_schedule import at, schedule

    at(setup, 0)
    result = schedule(setup, html=html_with((PNG, "beach.png")))
    draft = call(setup.engine, "mail.unschedule", account=setup.account_id, folder="Scheduled", message=result.message_id)
    opened = call(setup.engine, "draft.open", account=setup.account_id, folder=draft.folder, message=draft.message_id)
    assert data_address("image/png", "beach.png", PNG) in opened.html


def html_message_with_a_picture() -> ParsedMessage:
    return parse_message(build_message(OutgoingMessage(
        Address("Ann", "ann@example.com"), (Address("", "me@example.com"),), (), (), "Our garden", "Look [picture: garden.png]",
        html_body='<html><head><style>p { color: red }</style></head><body><p>Look at <b>our</b> garden</p><script>x()</script>'
        + f'<img src="{data_address("image/png", "garden.png", PNG)}" /></body></html>',
        attachments=(Attachment("plan.pdf", "application/pdf", None, False, b"%PDF made-up"),),
    )))


def test_a_reply_in_rich_text_quotes_the_original_with_its_formatting_and_its_picture_and_no_attachment() -> None:
    draft = build_draft(html_message_with_a_picture(), "reply", "me@example.com")
    assert draft.quoted_html is not None and "<b>our</b>" in draft.quoted_html and "<blockquote" in draft.quoted_html
    assert data_address("image/png", "garden.png", PNG) in draft.quoted_html
    assert "<script" not in draft.quoted_html and "p { color: red }" in draft.quoted_html  # the original keeps its own style rules (D-360)
    assert draft.attachment_names == ()


def test_a_forward_quotes_with_its_picture_and_names_the_picture_apart_from_the_attachments() -> None:
    draft = build_draft(html_message_with_a_picture(), "forward", "me@example.com")
    assert draft.quoted_html is not None and "Forwarded message" in draft.quoted_html and data_address("image/png", "garden.png", PNG) in draft.quoted_html
    assert draft.attachment_names == ("plan.pdf",)


def test_a_plain_text_message_has_no_html_quote() -> None:
    plain = parse_message(build_message(OutgoingMessage(Address("", "ann@example.com"), (Address("", "me@example.com"),), (), (), "Hi", "Hello")))
    assert build_draft(plain, "reply", "me@example.com").quoted_html is None


def test_a_forward_takes_along_only_the_attachments_of_the_original_its_pictures_come_from_the_window(setup: Setup) -> None:
    raw = build_message(OutgoingMessage(
        Address("Ann", "ann@example.com"), (Address("", "me@example.com"),), (), (), "Our garden", "Look",
        html_body=f'<p>Look <img src="{data_address("image/png", "garden.png", PNG)}" /></p>',
        attachments=(Attachment("plan.pdf", "application/pdf", None, False, b"%PDF made-up"),),
    ))
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        reference = mailbox.deliver(("Inbox",), raw, frozenset({MessageFlag.SEEN}))
    for html, expected in ((None, ["plan.pdf"]), ("<p>Look</p>", ["plan.pdf"])):  # never twice: the window gives a plain forward its pictures
        setup.smtp.received.clear()
        call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Fwd: Our garden", body="See below", html=html,
             source_folder="Inbox", source_message=reference.store_id, include_source_attachments=True)
        (received,) = setup.smtp.received
        assert sorted(part.filename or "" for part in parse_message(received.data).attachments if not part.inline) == sorted(expected)
