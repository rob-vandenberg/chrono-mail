import smtplib
import ssl
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import override

from chrono_mail.core import AuthenticationError, ConnectionSecurity, ProtocolError, ServerSettings
from chrono_mail.core.words import digits

# --- Version ------------------------------------------------------------
__version__ = "smtp.py 3.6.351"

# --- Version history ----------------------------------------------------
# v3.6.351: numbers are read only from the digits 0 to 9 (core.words.digits): "²" passed isdigit() and stopped the work with a ValueError
# v3.1.341: once the server accepted the message, a failed QUIT no longer makes the send fail (it was sent again, RV-140)
# v0.0.13: check_login; shared connection and error mapping
# v0.0.10: Initial SMTP client

_MAXIMUM_REPLY_LINES: int = 200
_MAXIMUM_REPLY_BYTES: int = 64 * 1024
_MAXIMUM_LINE_BYTES: int = 8192
_FORBIDDEN_IN_ADDRESS: frozenset[str] = frozenset(' <>\r\n\x00"\\,;:')


@dataclass(frozen=True, slots=True)
class SmtpLimits:
    timeout_seconds: float = 30.0  # smtplib applies one timeout to every step
    maximum_message_bytes: int = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Refusal:
    address: str
    code: int
    text: str


@dataclass(frozen=True, slots=True)
class SendResult:
    accepted: tuple[str, ...]
    refused: tuple[Refusal, ...]  # recipients the server refused while accepting the message for the others


def _printable(text: bytes | str, limit: int = 200) -> str:
    data = text if isinstance(text, bytes) else text.encode("utf-8", "replace")
    return "".join(chr(byte) if 32 <= byte < 127 else "?" for byte in data[:limit])


def _read_reply(connection: smtplib.SMTP) -> tuple[int, bytes]:
    """smtplib reads reply lines for as long as the server says '250-'. A hostile server could keep
    that up for ever, so this reader ends a reply after a fixed number of lines and bytes."""
    if connection.file is None and connection.sock is not None:
        connection.file = connection.sock.makefile("rb")
    reader = connection.file
    lines: list[bytes] = []
    total = 0
    while True:
        try:
            line = reader.readline(_MAXIMUM_LINE_BYTES + 1) if reader is not None else b""
        except OSError as error:
            connection.close()
            raise smtplib.SMTPServerDisconnected(f"Connection unexpectedly closed: {error.strerror}") from None
        if not line:
            connection.close()
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        total += len(line)
        if len(line) > _MAXIMUM_LINE_BYTES or len(lines) >= _MAXIMUM_REPLY_LINES or total > _MAXIMUM_REPLY_BYTES:
            connection.close()
            raise smtplib.SMTPResponseException(500, "The server's reply is too long")
        lines.append(line[4:].strip(b" \t\r\n"))
        try:
            code = int(line[:3])
        except ValueError:
            return -1, b"\n".join(lines)
        if line[3:4] != b"-":
            return code, b"\n".join(lines)


class _Smtp(smtplib.SMTP):
    @override
    def getreply(self) -> tuple[int, bytes]:
        return _read_reply(self)


class _SmtpSsl(smtplib.SMTP_SSL):
    @override
    def getreply(self) -> tuple[int, bytes]:
        return _read_reply(self)


def _check_address(address: str, what: str) -> None:
    if not address or not address.isascii() or any(character in _FORBIDDEN_IN_ADDRESS or ord(character) < 32 for character in address) or address.count("@") != 1:
        raise ProtocolError(f"The {what} address is not valid")


@contextmanager
def _mapped_errors() -> Generator[None]:
    """Turn what smtplib and the network raise into AuthenticationError and ProtocolError (text made safe)."""
    try:
        yield
    except smtplib.SMTPAuthenticationError as error:
        raise AuthenticationError(f"The server refused the login: {_printable(error.smtp_error)}") from None
    except smtplib.SMTPRecipientsRefused as error:
        details = "; ".join(f"{address}: {code} {_printable(text)}" for address, (code, text) in error.recipients.items())
        raise ProtocolError(f"The server refused every recipient: {details}") from None
    except smtplib.SMTPResponseException as error:
        raise ProtocolError(f"The server answered {error.smtp_code}: {_printable(error.smtp_error)}") from None
    except smtplib.SMTPException as error:
        raise ProtocolError(f"Sending failed: {type(error).__name__}") from None
    except ssl.SSLCertVerificationError as error:
        raise ProtocolError(f"The server's certificate could not be verified: {error.verify_message}") from None
    except (OSError, ssl.SSLError) as error:
        raise ProtocolError(f"Connection failed: {error.strerror or type(error).__name__}") from None


def _open(
    settings: ServerSettings, password: str, limits: SmtpLimits, context: ssl.SSLContext, local_hostname: str | None
) -> smtplib.SMTP:
    """Connect, secure the connection and log in. The caller closes the connection."""
    if settings.security is ConnectionSecurity.TLS:
        connection: smtplib.SMTP = _SmtpSsl(settings.host, settings.port, local_hostname, timeout=limits.timeout_seconds, context=context)
    else:
        connection = _Smtp(settings.host, settings.port, local_hostname, timeout=limits.timeout_seconds)
    try:
        connection.ehlo()
        if settings.security is ConnectionSecurity.STARTTLS:
            if not connection.has_extn("starttls"):
                raise ProtocolError("The server does not offer STARTTLS")
            connection.starttls(context=context)
            connection.ehlo()
        if settings.username:
            if not connection.has_extn("auth"):
                raise ProtocolError("The server does not offer authentication")
            connection.login(settings.username, password)
    except BaseException:
        connection.close()
        raise
    return connection


def check_login(
    settings: ServerSettings,
    password: str,
    limits: SmtpLimits = SmtpLimits(),
    ssl_context: ssl.SSLContext | None = None,
    local_hostname: str | None = None,
) -> None:
    """Connect and log in without sending anything (a connection test). Raises like send_message."""
    context = ssl_context if ssl_context is not None else ssl.create_default_context()
    with _mapped_errors():
        connection = _open(settings, password, limits, context, local_hostname)
        try:
            connection.quit()
        finally:
            connection.close()


def send_message(
    settings: ServerSettings,
    password: str,
    sender: str,
    recipients: Sequence[str],
    raw: bytes,
    limits: SmtpLimits = SmtpLimits(),
    ssl_context: ssl.SSLContext | None = None,
    local_hostname: str | None = None,
) -> SendResult:
    """Send one message over plain, STARTTLS or implicit TLS, with a verified certificate.

    Raises AuthenticationError for a refused login and ProtocolError for everything else. A
    recipient the server refuses while it accepts the message for the others is reported in the
    result; if it refuses every recipient, that is an error. The text of errors never contains
    the password or any part of the message.
    """
    _check_address(sender, "sender")
    if not recipients:
        raise ProtocolError("There is no recipient")
    for recipient in recipients:
        _check_address(recipient, "recipient")
    if len(raw) > limits.maximum_message_bytes:
        raise ProtocolError("The message is larger than the limit")
    context = ssl_context if ssl_context is not None else ssl.create_default_context()
    with _mapped_errors():
        connection = _open(settings, password, limits, context, local_hostname)
        try:
            if connection.has_extn("size"):
                advertised = connection.esmtp_features.get("size", "")
                if digits(advertised) and 0 < int(advertised) < len(raw):
                    raise ProtocolError("The message is larger than the server accepts")
            options: list[str] = []
            if not raw.isascii():
                if not connection.has_extn("8bitmime"):
                    raise ProtocolError("The message contains 8-bit data and the server does not accept it")
                options.append("BODY=8BITMIME")
            refused = connection.sendmail(sender, list(recipients), raw, mail_options=options)
            try:
                connection.quit()
            except (smtplib.SMTPException, OSError):
                pass  # the server accepted the message already: a lost goodbye does not make it unsent (it would be sent again, RV-140)
            return SendResult(
                tuple(recipient for recipient in recipients if recipient not in refused),
                tuple(Refusal(address, code, _printable(text)) for address, (code, text) in refused.items()),
            )
        finally:
            connection.close()
