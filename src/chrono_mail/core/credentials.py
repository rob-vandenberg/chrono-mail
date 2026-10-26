import threading
from collections.abc import Callable
from contextlib import closing
from typing import Protocol, cast

import keyring
import keyring.errors
from keyring.backends.SecretService import Keyring as SecretServiceKeyring
from keyring.backends.Windows import WinVaultKeyring
from keyring.backends.chainer import ChainerBackend

# --- Version ------------------------------------------------------------
__version__ = "credentials.py 3.6.372"

# --- Version history ----------------------------------------------------
# v3.6.372: Windows: the readable text is the entry's User name (Credential Manager shows it when the entry is opened; the row title is the
#           lookup name and stays); keyring's newest entry under the bare service name is moved to its own name first (written, read back,
#           then the old one removed); an entry is removed by its own name, whatever its User name (B-23, D-275); one call to the store at
#           a time (a label's move must never fall between the read and the write of another password); a User name is at most 512 characters
# v3.6.371: label: an entry gets a label a person can read in the wallet (Secret Service: KDE Wallet, GNOME Keyring); the lookup name
#           stays; a store without labels (Windows, macOS) is left as it is (B-23, D-275)
# v2.0.295: Every call to the store has a time limit (60 s, time to unlock the wallet): the one wait outside our code that had none; after it
#           the job ends with a clear error instead of waiting for ever (a late answer is ignored) (D-127)
# v1.8.263: R-16: the one copy of the wallet's service name, the reading, saving and removing of an entry and the reason text (was in crypto/keystore.py and config/passwords.py)
# v0.0.9: Moved here from crypto so that config can use it too

SERVICE_NAME: str = "chrono-mail"
STORE_LOCK: threading.Lock = threading.Lock()  # one call to the store at a time, held by the call itself (also one that ran past its time limit)
WINDOWS_USER_NAME_LIMIT: int = 512  # characters Windows keeps in a credential's User name
STORE_TIME_LIMIT: float = 60.0  # seconds a call to the store may take: time for the person to unlock the wallet when the desktop asks (D-127)


class CredentialBackend(Protocol):
    """What the app needs from an operating system credential store. Every keyring backend provides it.

    A protocol, not a KeyringBackend subclass: keyring registers every subclass globally,
    which would let a test double leak into the real credential store lookup.
    """

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class LabelledBackend(Protocol):
    """A credential store whose entries carry a label of their own, apart from the name they are found by (a test double)."""

    def set_label(self, service: str, username: str, label: str) -> None: ...


def _secret_service_labeller(backend: SecretServiceKeyring) -> Callable[[str, str, str], None]:
    """Labels the entries keyring wrote in the Secret Service: they are found by the same attributes keyring uses, so the lookup is untouched."""

    def set_label(service: str, username: str, label: str) -> None:
        collection = backend.get_preferred_collection()
        with closing(collection.connection):
            for item in collection.search_items(backend._query(service, username)):  # noqa: SLF001 - keyring's own attributes of the entry
                if item.get_label() != label:
                    item.set_label(label)

    return set_label


def _windows_labeller(backend: WinVaultKeyring) -> Callable[[str, str, str], None]:
    """Windows Credential Manager shows an entry's target name as its title (the lookup: it stays) and, opened, its User name: the readable
    text goes there. keyring finds an entry under "<name>@<service>" whatever its User name; only its newest entry, under the bare service
    name, is recognised by the User name, so that one is moved to its own name first: written there, read back, and only then removed (a
    move stopped half-way leaves the password twice, never not at all)."""

    def set_label(service: str, username: str, text: str) -> None:
        label = text if len(text) <= WINDOWS_USER_NAME_LIMIT else f"{text[: WINDOWS_USER_NAME_LIMIT - 1]}…"
        own = backend._compound_name(username, service)  # noqa: SLF001 - keyring's own name of the entry
        bare = backend._read_credential(service)  # noqa: SLF001
        if bare is not None and bare["UserName"] == username:
            backend._set_password(own, label, bare.value)  # noqa: SLF001
            moved = backend._read_credential(own)  # noqa: SLF001
            if moved is None or moved.value != bare.value:
                raise CredentialFailure("The password store did not give back the entry just written; it stays where it was")
            backend._delete_password(service)  # noqa: SLF001
            return
        entry = backend._read_credential(own)  # noqa: SLF001
        if entry is not None and entry["UserName"] != label:
            backend._set_password(own, label, entry.value)  # noqa: SLF001

    return set_label


def _windows_remove(backend: WinVaultKeyring, service: str, username: str) -> None:
    """Remove the entry by its own name, whatever its User name (keyring's own removal looks for the User name, which holds the readable
    text); the bare service name only when it holds this entry."""
    bare = backend._read_credential(service)  # noqa: SLF001 - keyring's own names of the entry
    if bare is not None and bare["UserName"] == username:
        backend._delete_password(service)  # noqa: SLF001
    backend._delete_password(backend._compound_name(username, service))  # noqa: SLF001 - not there is no error


def _labeller(backend: object) -> Callable[[str, str, str], None] | None:
    """How entries of this store get a readable label, or None for a store that shows only the lookup name (macOS, KWallet's own)."""
    if isinstance(backend, ChainerBackend):  # several stores at once: the first one takes every new entry, so its labels are the ones shown
        chained = backend.backends
        backend = chained[0] if chained else None
    if isinstance(backend, SecretServiceKeyring):
        return _secret_service_labeller(backend)
    if isinstance(backend, WinVaultKeyring):
        return _windows_labeller(backend)
    set_label = getattr(backend, "set_label", None)  # getattr: a test double that keeps labels (LabelledBackend)
    return cast("LabelledBackend", backend).set_label if callable(set_label) else None


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
            with STORE_LOCK:  # a label that moves an entry must never fall between another call's read and write (keyring moves entries too)
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

    def label(self, name: str, text: str) -> None:
        """Give the entry a label a person can read in the wallet; the name it is found by stays. Nothing for a store without labels or
        an entry that is not there."""
        def work() -> None:  # finding the labeller asks the stores too (a chained store asks each over D-Bus): inside the time limit
            labeller = _labeller(self._backend)
            if labeller is not None:
                labeller(self._service, name, text)

        try:
            _within(work, self._limit)
        except CredentialFailure:
            raise
        except Exception as error:  # the store's own errors and those of the desktop service behind it (D-127)
            raise CredentialFailure(_cause(error)) from None

    def remove(self, name: str) -> None:
        """Remove the entry. Removing an entry that is not there is not an error."""
        backend = self._backend
        try:
            if isinstance(backend, WinVaultKeyring):
                _within(lambda: _windows_remove(backend, self._service, name), self._limit)
            else:
                _within(lambda: backend.delete_password(self._service, name), self._limit)
        except keyring.errors.PasswordDeleteError:
            return  # already gone
        except CredentialFailure:
            raise
        except Exception as error:  # the store's own errors and those of the desktop service behind it (D-127)
            raise CredentialFailure(_cause(error)) from None
