# --- Version ------------------------------------------------------------
__version__ = "errors.py 2.0.281"

# --- Version history ----------------------------------------------------
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


class StorageError(ChronoMailError):
    """The message store or the index could not be read or written."""


class RebuildAborted(StorageError):
    """The person aborted rebuilding an index (or filling it in): the work done so far was put away, the mail is untouched."""


class MessageFormatError(ChronoMailError):
    """A message could not be parsed or built."""


class CryptoError(ChronoMailError):
    """Encryption, decryption or key handling failed."""
