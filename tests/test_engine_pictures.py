import base64
from email import message_from_bytes, policy
from email.message import EmailMessage

import pytest

from chrono_mail.core import MessageFormatError
from chrono_mail.mime import Address, OutgoingMessage, build_message, parse_message
from chrono_mail.mime.pictures import attachment_from_data, data_address, pictures_into
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_pictures.py 4.4.621"

# --- Version history ----------------------------------------------------
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
