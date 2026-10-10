import base64
from datetime import datetime
from email.utils import format_datetime

# --- Version ------------------------------------------------------------
__version__ = "mailfactory.py 0.0.8"

# --- Version history ----------------------------------------------------
# v0.0.8: Initial mail factory for tests


def raw_message(
    subject: str = "Hello",
    sender: str = "Alice <alice@example.com>",
    to: str = "bob@example.com",
    body: str = "Hello Bob\n",
    date: datetime | None = None,
    message_id: str | None = None,
    references: str | None = None,
    in_reply_to: str | None = None,
    html: str | None = None,
    attachment: bytes | None = None,
) -> bytes:
    """A small, valid RFC 5322 message with exactly the given properties."""
    lines = [f"From: {sender}", f"To: {to}", f"Subject: {subject}"]
    if date is not None:
        lines.append(f"Date: {format_datetime(date)}")
    if message_id:
        lines.append(f"Message-ID: {message_id}")
    if references:
        lines.append(f"References: {references}")
    if in_reply_to:
        lines.append(f"In-Reply-To: {in_reply_to}")
    lines.append("MIME-Version: 1.0")
    if attachment is not None or html is not None:
        lines += ["Content-Type: multipart/mixed; boundary=BND", "", "--BND", "Content-Type: text/plain; charset=utf-8", "", body]
        if html is not None:
            lines += ["--BND", "Content-Type: text/html; charset=utf-8", "", html]
        if attachment is not None:
            lines += ["--BND", "Content-Type: application/octet-stream", 'Content-Disposition: attachment; filename="a.bin"', "Content-Transfer-Encoding: base64", "", base64.b64encode(attachment).decode()]
        lines += ["--BND--", ""]
    else:
        lines += ["Content-Type: text/plain; charset=utf-8", "", body]
    return "\r\n".join(lines).encode("utf-8")
