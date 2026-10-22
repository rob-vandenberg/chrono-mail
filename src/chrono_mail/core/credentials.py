import threading
from collections.abc import Callable
from typing import Protocol, cast

import keyring
import keyring.errors

# --- Version ------------------------------------------------------------
__version__ = "credentials.py 2.0.295"

# --- Version history ----------------------------------------------------
# v2.0.295: Every call to the store has a time limit (60 s, time to unlock the wallet): the one wait outside our code that had none; after it
#           the job ends with a clear error instead of waiting for ever (a late answer is ignored) (D-127)
# v1.8.263: R-16: the one copy of the wallet's service name, the reading, saving and removing of an entry and the reason text (was in crypto/keystore.py and config/passwords.py)
# v0.0.9: Moved here from crypto so that config can use it too

SERVICE_NAME: str = "chrono-mail"
STORE_TIME_LIMIT: float = 60.0  # seconds a call to the store may take: time for the person to unlock the wallet when the desktop asks (D-127)


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


def _within[T](call: Callable[[], T], limit: float) -> T:
    """The answer of a call to the store, or a CredentialFailure when it gives none within the limit. A call to the store cannot be stopped:
    it waits in a thread of its own, and its late answer is ignored."""
    outcome: list[tuple[bool, object]] = []

    def work() -> None:
        try:
            outcome.append((True, call()))
        except BaseException as error:  # noqa: BLE001 - handed to the caller as it is
            outcome.append((False, error))

    worker = threading.Thread(target=work, name="credential store", daemon=True)
    worker.start()
    worker.join(limit)
    if not outcome:
        raise CredentialFailure(f"The password store did not answer within {limit:g} seconds (is the wallet waiting to be unlocked?)")
    answered, value = outcome[0]
    if not answered:
        raise cast("BaseException", value)
    return cast("T", value)


class CredentialEntries:
    """The entries of one service in the credential store. Every failure of the store is a CredentialFailure with its reason (D-127)."""

    def __init__(self, service: str = SERVICE_NAME, backend: CredentialBackend | None = None, time_limit: float = STORE_TIME_LIMIT) -> None:
        self._service: str = service
        self._backend: CredentialBackend = backend if backend is not None else keyring.get_keyring()
        self._limit: float = time_limit

    def read(self, name: str) -> str | None:
        """The entry, or None when the store answered that it holds none."""
        try:
            return _within(lambda: self._backend.get_password(self._service, name), self._limit)
        except CredentialFailure:
            raise
        except Exception as error:  # the store's own errors and those of the desktop service behind it (D-127)
            raise CredentialFailure(_cause(error)) from None

    def write(self, name: str, value: str) -> None:
        try:
            _within(lambda: self._backend.set_password(self._service, name, value), self._limit)
        except CredentialFailure:
            raise
        except Exception as error:  # the store's own errors and those of the desktop service behind it (D-127)
            raise CredentialFailure(_cause(error)) from None

    def remove(self, name: str) -> None:
        """Remove the entry. Removing an entry that is not there is not an error."""
        try:
            _within(lambda: self._backend.delete_password(self._service, name), self._limit)
        except keyring.errors.PasswordDeleteError:
            return  # already gone
        except CredentialFailure:
            raise
        except Exception as error:  # the store's own errors and those of the desktop service behind it (D-127)
            raise CredentialFailure(_cause(error)) from None
