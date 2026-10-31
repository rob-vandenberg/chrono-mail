from dataclasses import dataclass

# --- Version ------------------------------------------------------------
__version__ = "errors.py 4.2.441"

# --- Version history ----------------------------------------------------
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
