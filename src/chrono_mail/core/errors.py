# --- Version ------------------------------------------------------------
__version__ = "errors.py 0.0.4"

# --- Version history ----------------------------------------------------
# v0.0.4: Initial error hierarchy


class ChronoMailError(Exception):
    """Base class of every error raised by Chrono Mail itself.

    Messages describe what went wrong, never message contents, passwords or keys.
    """


class ConfigurationError(ChronoMailError):
    """Settings or account definitions are missing or invalid."""


class ProtocolError(ChronoMailError):
    """A server or network protocol failure (POP3, SMTP, later IMAP)."""


class StorageError(ChronoMailError):
    """The message store or the index could not be read or written."""


class MessageFormatError(ChronoMailError):
    """A message could not be parsed or built."""


class CryptoError(ChronoMailError):
    """Encryption, decryption or key handling failed."""
