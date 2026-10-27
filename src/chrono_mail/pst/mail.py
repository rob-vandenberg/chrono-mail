import hashlib
import re
from email.message import MIMEPart

from chrono_mail.pst.headers import (
    ATTACH_CONTENT_ID, ATTACH_DATA, ATTACH_DISPLAY_NAME, ATTACH_FILENAME, ATTACH_LONG_FILENAME, ATTACH_METHOD, ATTACH_MIME_TAG, BODY, BY_VALUE,
    EMBEDDED, HTML, INTERNET_CPID, MESSAGE_CLASS, POLICY, RTF_COMPRESSED, header_block, one_line,
)
from chrono_mail.pst.properties import MAXIMUM_MESSAGE_BYTES, Attachment, Message, Properties, codec_for
from chrono_mail.pst.rtf import RtfError, decompress, html_from_rtf, text_from_rtf

# --- Version ------------------------------------------------------------
__version__ = "mail.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial version: a message of the data file rebuilt as RFC 5322 bytes, with notes on what of it could not be kept

MAXIMUM_EMBEDDING_DEPTH: int = 5  # messages attached to messages attached to ...: deeper ones are left out (and noted)
BODY_LOST = "its text could not be read"
_SMIME_TYPES = frozenset({"multipart/signed", "application/pkcs7-mime", "application/x-pkcs7-mime"})
_HEADER_START = re.compile(rb"^[A-Za-z0-9-]+:")
_BARE_LINE_FEED = re.compile(rb"(?<!\r)\n")



class MailError(ValueError):
    """A message that cannot be rebuilt; the text never holds its contents."""


def read_bodies(props: Properties) -> tuple[str | None, str | None, bool]:
    """The plain text and the HTML of a message, from its plain body, its HTML body and its compressed RTF (whichever are kept), and
    whether a body was there that could not be read."""
    plain = props.text(BODY) if props.has(BODY) else None
    html: str | None = None
    lost = False
    raw_html = props.raw(HTML)
    if raw_html is not None:
        value_type, data = raw_html
        if value_type == 0x001F:
            html = props.text(HTML)
        else:
            codepage = props.integer(INTERNET_CPID) or 65001
            html = data.decode(codec_for(codepage), errors="replace").rstrip("\x00")
    if html is None or plain is None:
        compressed = props.binary(RTF_COMPRESSED)
        if compressed:
            try:
                rtf = decompress(compressed)
                from_rtf = html_from_rtf(rtf) if rtf else None
                if from_rtf is not None and html is None:
                    html = from_rtf
                elif rtf and from_rtf is None and plain is None:
                    plain = text_from_rtf(rtf)
            except (RtfError, ValueError, LookupError, IndexError, RecursionError):
                lost = plain is None and html is None  # a damaged text costs only the text; the rest of the message stays
    return plain, html, lost


def bodies(props: Properties) -> tuple[str | None, str | None]:
    plain, html, _ = read_bodies(props)
    return plain, html


def _file_name(props: Properties) -> str:
    for tag in (ATTACH_LONG_FILENAME, ATTACH_FILENAME, ATTACH_DISPLAY_NAME):
        name = one_line(props.text(tag)).replace("/", "_").replace("\\", "_")
        if name:
            return name[:255]
    return "attachment"


def _media_type(props: Properties) -> tuple[str, str]:
    tag = props.text(ATTACH_MIME_TAG).strip().lower()
    if re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", tag):
        main, sub = tag.split("/", 1)
        if main not in ("multipart", "message"):
            return main, sub
    return "application", "octet-stream"


def _content_id(props: Properties) -> str:
    return one_line(props.text(ATTACH_CONTENT_ID)).strip("<>")


def _spend(budget: list[int], size: int) -> None:
    budget[0] -= size
    if budget[0] < 0:
        raise MailError("a message larger than 256 MB")


class _Builder:
    def __init__(self, depth: int, budget: list[int]) -> None:
        self.depth = depth
        self.budget = budget
        self.notes: list[str] = []
        self.without_data = 0
        self.too_deep = 0
        self.embedded: list[tuple[MIMEPart, bytes]] = []  # each attached message's part and its bytes, put in after the body is written

    def body(self, message: Message) -> MIMEPart:
        """The MIME body (without the message's own headers): the texts, the pictures the HTML shows, then the attachments. The
        pictures go in first: once the body is mixed, the library can no longer put a picture beside the HTML."""
        plain, html, lost = read_bodies(message.props)
        if lost:
            self.notes.append(BODY_LOST)
        part = MIMEPart(policy=POLICY)
        if plain is None and html is None:
            plain = ""
        if plain is not None:
            part.set_content(plain, subtype="plain", charset="utf-8")
            if html is not None:
                part.add_alternative(html, subtype="html", charset="utf-8")
        elif html is not None:
            part.set_content(html, subtype="html", charset="utf-8")
        html_part: MIMEPart | None = None
        if html is not None:
            if part.is_multipart():
                payload = part.get_payload()
                last = payload[-1] if isinstance(payload, list) and payload else None
                html_part = last if isinstance(last, MIMEPart) else None
            else:
                html_part = part
        shown = (html or "").lower()
        later: list[Attachment] = []
        for attachment in message.attachments:
            content_id = _content_id(attachment.props)
            data = self._data(attachment)
            if html_part is not None and data is not None and content_id and f"cid:{content_id}".lower() in shown:
                _spend(self.budget, len(data) * 4 // 3)
                main, sub = _media_type(attachment.props)
                html_part.add_related(data, main, sub, cid=f"<{content_id}>", filename=_file_name(attachment.props), disposition="inline")
            else:
                later.append(attachment)
        for attachment in later:
            self._attach(part, attachment)
        if self.without_data:
            self.notes.append(f"{self.without_data} attachment(s) kept only as a link or without contents")
        if self.too_deep:
            self.notes.append(f"{self.too_deep} attached message(s) nested too deep")
        return part

    @staticmethod
    def _data(attachment: Attachment) -> bytes | None:
        if attachment.embedded is not None or (attachment.props.integer(ATTACH_METHOD) or BY_VALUE) == EMBEDDED:
            return None
        return attachment.data if attachment.data is not None else attachment.props.binary(ATTACH_DATA)

    def _attach(self, part: MIMEPart, attachment: Attachment) -> None:
        props = attachment.props
        if attachment.embedded is not None or (props.integer(ATTACH_METHOD) or BY_VALUE) == EMBEDDED:
            if attachment.embedded is None:
                self.without_data += 1
                return
            if self.depth >= MAXIMUM_EMBEDDING_DEPTH:
                self.too_deep += 1
                return
            raw, notes = rebuild(attachment.embedded, self.depth + 1, self.budget)
            self.notes.extend(f"an attached message: {note}" for note in notes)
            name = _file_name(props) if props.has(ATTACH_LONG_FILENAME) or props.has(ATTACH_FILENAME) else None
            self.embedded.append((_attach_message(part, transfer_encoding(raw), name), raw))
            return
        data = self._data(attachment)
        if data is None:
            self.without_data += 1
            return
        _spend(self.budget, len(data) * 4 // 3)
        main, sub = _media_type(props)
        content_id = _content_id(props)
        part.add_attachment(data, main, sub, filename=_file_name(props), cid=f"<{content_id}>" if content_id else None)


def transfer_encoding(raw: bytes) -> str:
    """The label the bytes of an attached message can honestly carry (RFC 2045 2.7-2.9): 7bit and 8bit need lines of at most 998 octets,
    no NUL and no CR or LF on its own; anything else is binary."""
    if b"\x00" in raw or re.search(rb"\r(?!\n)|(?<!\r)\n", raw) or any(len(line) > 998 for line in raw.split(b"\r\n")):
        return "binary"
    return "7bit" if raw.isascii() else "8bit"


def _tokens(raws: list[bytes]) -> list[bytes]:
    """Stand-ins for the attached messages: made from all of them together, so no attached message can hold another one's stand-in (it
    would have to hold a hash of itself)."""
    seed = hashlib.sha256(b"".join(hashlib.sha256(raw).digest() for raw in raws)).hexdigest()
    return [f"=_attached_message_{hashlib.sha256(f'{seed}_{index}'.encode()).hexdigest()}_=".encode("ascii") for index in range(len(raws))]


def _put_in(body: bytes, tokens: list[bytes], raws: list[bytes]) -> bytes:
    """The attached messages in their places, in one pass over the body as it was written (what was put in is never looked through
    again). Each stand-in must be there exactly once; otherwise the message is refused rather than put together wrongly."""
    if not tokens:
        return body
    if any(body.count(token) != 1 for token in tokens):
        raise MailError("an attached message could not be put in its place")
    by_token = dict(zip(tokens, raws, strict=True))
    return re.sub(b"|".join(re.escape(token) for token in tokens), lambda match: by_token[match.group(0)], body)


def _attach_message(part: MIMEPart, encoding: str, name: str | None) -> MIMEPart:
    """An attached message as message/rfc822 whose bytes go in exactly as they were made (a stand-in now, the bytes after the body is
    written): never parsed and written again, which would put 8-bit headers in encoded words, break an internationalised address, or
    re-encode a signed part so its signature fails."""
    holder = MIMEPart(policy=POLICY)
    holder["Content-Type"] = "message/rfc822"
    holder["Content-Transfer-Encoding"] = encoding
    holder["Content-Disposition"] = "attachment"
    if name:
        holder.set_param("filename", name, header="Content-Disposition")
    if part.get_content_type() != "multipart/mixed":
        part.make_mixed()
    part.attach(holder)
    return holder


def _without_mime_version(entity: bytes) -> bytes:
    """A kept entity without its own MIME-Version (the message has one already); its other headers and its body byte for byte."""
    head, separator, body = entity.partition(b"\r\n\r\n")
    kept: list[bytes] = []
    dropping = False
    for line in head.split(b"\r\n"):
        if line[:1] in (b" ", b"\t"):
            if not dropping:
                kept.append(line)
            continue
        dropping = line.split(b":", 1)[0].strip().lower() == b"mime-version"
        if not dropping:
            kept.append(line)
    return b"\r\n".join(kept) + separator + body


def _signed_or_encrypted(message: Message) -> bytes | None:
    """The MIME body of a signed or encrypted message: the data file keeps it whole as the message's one attachment. Used as it is, so a
    signature still verifies and an encrypted message still opens."""
    if not message.props.text(MESSAGE_CLASS).strip().upper().startswith("IPM.NOTE.SMIME") or len(message.attachments) != 1:
        return None
    attachment = message.attachments[0]
    data = attachment.data if attachment.data is not None else attachment.props.binary(ATTACH_DATA)
    media = attachment.props.text(ATTACH_MIME_TAG).strip().lower()
    if not data or (media not in _SMIME_TYPES and _file_name(attachment.props).lower() != "smime.p7m"):
        return None
    head = data[:4096]
    if _HEADER_START.match(head) and b"content-type:" in head.split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0].lower():
        entity = _BARE_LINE_FEED.sub(b"\r\n", data)  # a whole MIME entity (multipart/signed): its own headers and body
        return _without_mime_version(entity)
    part = MIMEPart(policy=POLICY)
    part.set_content(data, "application", "pkcs7-mime", filename="smime.p7m", disposition="attachment")
    return part.as_bytes(policy=POLICY)


def _fix_boundaries(part: MIMEPart, headers: bytes) -> None:
    """Boundaries made from the contents instead of at random, so the same item gives the same bytes on every import."""
    digest = hashlib.sha256(headers)
    multiparts: list[MIMEPart] = []
    waiting = [part]
    while waiting:  # never into an attached message: its bytes are kept as they are
        piece = waiting.pop(0)
        payload = piece.get_payload()
        if piece.get_content_maintype() == "multipart" and isinstance(payload, list):
            multiparts.append(piece)
            waiting.extend(child for child in payload if isinstance(child, MIMEPart))
        elif isinstance(payload, str):
            digest.update(payload.encode("utf-8", errors="surrogateescape"))
    seed = digest.hexdigest()[:24]
    for index, piece in enumerate(multiparts):
        piece.set_boundary(f"=_{seed}_{index}")


def rebuild(message: Message, depth: int = 0, budget: list[int] | None = None) -> tuple[bytes, tuple[str, ...]]:
    """The message as RFC 5322 bytes with CRLF line ends: the headers it came with when the data file kept them (completed from its
    properties, or made from them), and a body made anew from its texts and attachments; and notes (never contents) on what of it could
    not be kept."""
    budget = budget if budget is not None else [MAXIMUM_MESSAGE_BYTES]
    headers = header_block(message)
    protected = _signed_or_encrypted(message)
    notes: tuple[str, ...] = ()
    if protected is not None:
        _spend(budget, len(protected))
        body = protected
    else:
        builder = _Builder(depth, budget)
        part = builder.body(message)
        raws = [raw for _, raw in builder.embedded]
        tokens = _tokens(raws)
        for (holder, _), token in zip(builder.embedded, tokens, strict=True):
            holder.set_payload(token.decode("ascii"))
        _fix_boundaries(part, headers)
        body = _put_in(part.as_bytes(policy=POLICY), tokens, raws)
        notes = tuple(builder.notes)
    raw = headers + b"MIME-Version: 1.0\r\n" + body
    if len(raw) > MAXIMUM_MESSAGE_BYTES:
        raise MailError("a message larger than 256 MB")
    return raw, notes


def build_mail(message: Message, depth: int = 0, budget: list[int] | None = None) -> bytes:
    return rebuild(message, depth, budget)[0]
