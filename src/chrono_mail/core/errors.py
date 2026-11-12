from dataclasses import dataclass

# --- Version ------------------------------------------------------------
__version__ = "errors.py 4.4.592"

# --- Version history ----------------------------------------------------
# v4.4.592: refused_login: a login Gmail refused says what an app password is and where to make one (D-335)
# v4.2.441: CertificateError and CertificateTrouble: a certificate that could not be verified names its server, so the window can ask (UR-05, D-299)
# v2.0.281: RebuildAborted, the person aborted rebuilding an index (D-229)
# v0.0.10: AuthenticationError
# v0.0.4: Initial error hierarchy


class ChronoMailError(Exception):
    """Base class of every error raised by Chrono Mail itself.

    Messages describe what went wrong, never message contents, passwords or keys.
    """


class ConfigurationError(ChronoMailError):
    """Settings or account definitions are missing or invalid."""


class ProtocolError(ChronoMailError):
    """A server or network protocol failure (POP3, SMTP, later IMAP)."""


class AuthenticationError(ProtocolError):
    """The server refused the user name or password. Retrying with the same password is pointless."""


GMAIL_HOSTS: tuple[str, ...] = ("gmail.com", "googlemail.com")  # Gmail's IMAP and SMTP servers end in one of these (D-335)
GMAIL_PASSWORD_ADVICE: str = (
    "Gmail did not accept the password. Gmail needs an app password here, not your Google password: turn on 2-Step Verification in "
    "your Google account and make an app password at myaccount.google.com/apppasswords."
)


def refused_login(host: str, detail: str) -> AuthenticationError:
    """A refused login, in words the person can act on: on Gmail what an app password is and where to make one (D-335), elsewhere what the
    server said."""
    name = host.strip().lower().rstrip(".")
    if any(name == domain or name.endswith("." + domain) for domain in GMAIL_HOSTS):
        return AuthenticationError(f"{GMAIL_PASSWORD_ADVICE} (Gmail said: {detail})")  # its own words too: a passing refusal is no wrong password
    return AuthenticationError(f"The server refused the login: {detail}")


@dataclass(frozen=True, slots=True)
class CertificateTrouble:
    """A server whose certificate could not be verified: enough to look at that certificate again and ask the person (D-299)."""

    host: str
    port: int
    security: str  # tls or starttls, as certificate.inspect takes it
    kind: str  # pop3, imap, smtp or https
    changed: bool  # a certificate the person accepted was there, and the server now shows another one
    account: str = ""  # the account it was met for (empty: none yet, a new account being tried)


class CertificateError(ProtocolError):
    """The server's certificate could not be verified (or it is not the one the person accepted)."""

    def __init__(self, message: str, trouble: CertificateTrouble) -> None:
        super().__init__(message)
        self.trouble: CertificateTrouble = trouble


class StorageError(ChronoMailError):
    """The message store or the index could not be read or written."""


class RebuildAborted(StorageError):
    """The person aborted rebuilding an index (or filling it in): the work done so far was put away, the mail is untouched."""


class MessageFormatError(ChronoMailError):
    """A message could not be parsed or built."""


class CryptoError(ChronoMailError):
    """Encryption, decryption or key handling failed."""
