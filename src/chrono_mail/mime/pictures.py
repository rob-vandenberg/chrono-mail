"""Pictures inside the text of a message (D-353).

While a message is written its pictures travel inside its HTML as data: addresses, so a draft, a send and the command line carry them as
text and nothing is written to disk unencrypted. When the message is built, each becomes an inline part of its own that the HTML shows
by cid:, the standard way; when a stored message is opened again for writing, its inline pictures become data: addresses again."""

import base64
import binascii
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import quote, unquote

from chrono_mail.core import MessageFormatError
from chrono_mail.mime.model import Attachment

# --- Version ------------------------------------------------------------
__version__ = "pictures.py 4.4.621"

# --- Version history ----------------------------------------------------
# v4.4.621: Initial version: pictures inside the text of a message, as data: while written, as inline parts when built (D-353)

MAXIMUM_PICTURE_BYTES: int = 100 * 1024 * 1024  # one picture, the same as one attachment

# <img ... src="data:image/png;name=photo.png;base64,AAAA" ...>: the type, its parameters (the name), the data
_DATA_PICTURE: re.Pattern[str] = re.compile(
    r"""(?P<before><img\b[^>]*?\bsrc=)(?P<quote>["'])data:(?P<type>image/[A-Za-z0-9.+-]+)(?P<parameters>(?:;[^;,"']*)*?);base64,(?P<data>[^"']*)(?P=quote)""",
    re.IGNORECASE,
)
_CID_PICTURE: re.Pattern[str] = re.compile(r"""(?P<before><img\b[^>]*?\bsrc=)(?P<quote>["'])cid:(?P<cid>[^"'\s>]+)(?P=quote)""", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Picture:
    """One picture taken out of the HTML: its content id (without angle brackets), name, type and bytes."""

    content_id: str
    name: str
    content_type: str
    data: bytes


def data_address(content_type: str, name: str, data: bytes) -> str:
    """The data: address of a picture, with its name kept as a parameter so that it travels along."""
    return f"data:{content_type};name={quote(name, safe='')};base64,{base64.b64encode(data).decode('ascii')}"


def _name_of(parameters: str, content_type: str, position: int) -> str:
    for parameter in parameters.split(";"):
        key, _, value = parameter.partition("=")
        if key.strip().lower() == "name" and value.strip():
            return unquote(value.strip())[:200]
    return f"picture-{position}.{content_type.partition('/')[2].lower() or 'png'}"


def pictures_out_of(html: str, make_id: Callable[[], str], leave_out_bad: bool = False) -> tuple[str, tuple[Picture, ...]]:
    """The HTML with every embedded picture replaced by a cid: reference, and the pictures. make_id gives a new content id
    (without angle brackets). The same picture twice is one part. A picture that is not valid base64 or too large fails the message;
    with leave_out_bad (text pasted or opened for writing) it is left out and the rest stays."""
    pictures: list[Picture] = []
    known: dict[tuple[str, str], str] = {}

    def replaced(match: re.Match[str]) -> str:
        content_type = match.group("type").lower()
        text = re.sub(r"\s+", "", match.group("data"))
        key = (content_type, text)
        if key not in known:
            try:
                if len(text) * 3 // 4 > MAXIMUM_PICTURE_BYTES:
                    raise MessageFormatError(f"A picture in the text is larger than {MAXIMUM_PICTURE_BYTES // (1024 * 1024)} MB")
                try:
                    data = base64.b64decode(text, validate=True)
                except (binascii.Error, ValueError):
                    raise MessageFormatError("A picture in the text cannot be read") from None
            except MessageFormatError:
                if leave_out_bad:  # no picture: an empty address, which the caller leaves out with its tag
                    return f"{match.group('before')}{match.group('quote')}{match.group('quote')}"
                raise
            content_id = make_id()
            known[key] = content_id
            pictures.append(Picture(content_id, _name_of(match.group("parameters"), content_type, len(pictures) + 1), content_type, data))
        return f"{match.group('before')}{match.group('quote')}cid:{known[key]}{match.group('quote')}"

    return _DATA_PICTURE.sub(replaced, html), tuple(pictures)


def pictures_into(html: str, parts: Sequence[Attachment]) -> str:
    """The HTML with every cid: picture of these parts as an embedded data: picture, so that it can be written further.
    A reference to a part that is not there, or not a picture, stays as it is."""
    by_id = {
        part.content_id.strip("<>").lower(): part
        for part in parts
        if part.content_id and part.content_type.lower().startswith("image/") and len(part.data) <= MAXIMUM_PICTURE_BYTES
    }

    def replaced(match: re.Match[str]) -> str:
        part = by_id.get(unquote(match.group("cid")).strip("<>").lower())
        if part is None:
            return match.group(0)
        name = part.filename or f"picture.{part.content_type.partition('/')[2].lower() or 'png'}"
        return f"{match.group('before')}{match.group('quote')}{data_address(part.content_type.lower(), name, part.data)}{match.group('quote')}"

    return _CID_PICTURE.sub(replaced, html)


_DATA_ADDRESS: re.Pattern[str] = re.compile(r"data:(?P<type>image/[A-Za-z0-9.+-]+)(?P<parameters>(?:;[^;,]*)*?);base64,(?P<data>[A-Za-z0-9+/=]*)", re.IGNORECASE)


def attachment_from_data(text: str) -> Attachment | None:
    """A picture given as a data: address (a picture of the text that became an attachment) as an ordinary attachment; None when the text
    is not such an address. Too large or not valid base64 fails the message."""
    match = _DATA_ADDRESS.fullmatch(text)
    if match is None:
        return None
    if len(match.group("data")) * 3 // 4 > MAXIMUM_PICTURE_BYTES:
        raise MessageFormatError(f"A picture is larger than {MAXIMUM_PICTURE_BYTES // (1024 * 1024)} MB")
    try:
        data = base64.b64decode(match.group("data"), validate=True)
    except (binascii.Error, ValueError):
        raise MessageFormatError("A picture cannot be read") from None
    content_type = match.group("type").lower()
    return Attachment(_name_of(match.group("parameters"), content_type, 1), content_type, None, False, data)
