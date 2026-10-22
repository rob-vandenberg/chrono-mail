from typing import Protocol

import keyring
import keyring.errors

# --- Version ------------------------------------------------------------
__version__ = "credentials.py 1.8.263"

# --- Version history ----------------------------------------------------
# v1.8.263: R-16: the one copy of the wallet's service name, the reading, saving and removing of an entry and the reason text (was in crypto/keystore.py and config/passwords.py)
# v0.0.9: Moved here from crypto so that config can use it too

SERVICE_NAME: str = "chrono-mail"


class CredentialBackend(Protocol):
    """What the app needs from an operating system credential store. Every keyring backend provides it.

    A protocol, not a KeyringBackend subclass: keyring registers every subclass globally,
    which would let a test double leak into the real credential store lookup.
    """

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class CredentialFailure(Exception):
    """The credential store failed. Each store turns it into its own error with its own words; it never leaves them."""

    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause: str = cause

    @property
    def reason(self) -> str:
        return f"{self.cause}. Try again"


def _cause(error: Exception) -> str:
    """What went wrong in the credential store, in its own words, short and without secrets (the error texts hold none)."""
    text = str(error).strip()
    return f"{type(error).__name__}{': ' + text[:200] if text else ''}"


class CredentialEntries:
    """The entries of one service in the credential store. Every failure of the store is a CredentialFailure with its reason (D-127)."""

    def __init__(self, service: str = SERVICE_NAME, backend: CredentialBackend | None = None) -> None:
        self._service: str = service
        self._backend: CredentialBackend = backend if backend is not None else keyring.get_keyring()

    def read(self, name: str) -> str | None:
        """The entry, or None when the store answered that it holds none."""
        try:
            return self._backend.get_password(self._service, name)
        except Exception as error:  # the store's own errors and those of the desktop service behind it (D-127)
            raise CredentialFailure(_cause(error)) from None

    def write(self, name: str, value: str) -> None:
        try:
            self._backend.set_password(self._service, name, value)
        except Exception as error:  # the store's own errors and those of the desktop service behind it (D-127)
            raise CredentialFailure(_cause(error)) from None

    def remove(self, name: str) -> None:
        """Remove the entry. Removing an entry that is not there is not an error."""
        try:
            self._backend.delete_password(self._service, name)
        except keyring.errors.PasswordDeleteError:
            return  # already gone
        except Exception as error:  # the store's own errors and those of the desktop service behind it (D-127)
            raise CredentialFailure(_cause(error)) from None
