import poplib
import smtplib
import socket
import ssl
from collections.abc import Generator
from contextlib import contextmanager

from tests.live.account import LiveAccount

# --- Version ------------------------------------------------------------
__version__ = "helpers.py 0.0.3"

# --- Version history ----------------------------------------------------
# v0.0.3: Initial live test helpers

TIMEOUT_SECONDS: float = 20.0
SMTP_MODES: list[str] = ["starttls-587", "starttls-25", "ssl-465"]


def server_ip_address(account: LiveAccount) -> str:
    """The server's IP address. The certificate does not cover it, so verification must fail."""
    return socket.gethostbyname(account.host)


def open_smtp(account: LiveAccount, mode: str, host: str | None = None) -> smtplib.SMTP:
    """Connect over the given mode and return the connection with TLS active and EHLO done."""
    context = ssl.create_default_context()
    target = host if host is not None else account.host
    if mode == "ssl-465":
        connection: smtplib.SMTP = smtplib.SMTP_SSL(
            target, account.smtp_ssl_port, timeout=TIMEOUT_SECONDS, context=context
        )
        connection.ehlo()
        return connection
    port = account.smtp_starttls_port if mode == "starttls-587" else account.smtp_plain_port
    connection = smtplib.SMTP(target, port, timeout=TIMEOUT_SECONDS)
    try:
        connection.ehlo()
        connection.starttls(context=context)
        connection.ehlo()
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def smtp_session(account: LiveAccount, mode: str) -> Generator[smtplib.SMTP]:
    connection = open_smtp(account, mode)
    try:
        yield connection
    finally:
        try:
            connection.quit()
        except smtplib.SMTPException:
            connection.close()


@contextmanager
def pop3_session(account: LiveAccount, use_stls: bool = False, log_in: bool = True) -> Generator[poplib.POP3]:
    """A POP3 session over implicit TLS (default) or plain + STLS. QUIT is always sent."""
    context = ssl.create_default_context()
    connection: poplib.POP3
    if use_stls:
        connection = poplib.POP3(account.host, account.pop3_port, timeout=TIMEOUT_SECONDS)
        connection.stls(context=context)
    else:
        connection = poplib.POP3_SSL(
            account.host, account.pop3_ssl_port, timeout=TIMEOUT_SECONDS, context=context
        )
    try:
        if log_in:
            connection.user(account.user)
            connection.pass_(account.password)
        yield connection
    finally:
        try:
            connection.quit()
        except (poplib.error_proto, OSError):
            connection.close()
