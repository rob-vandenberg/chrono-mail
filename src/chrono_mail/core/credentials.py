import threading
from collections.abc import Callable
from contextlib import closing
from typing import Literal, Protocol, cast

import keyring
import keyring.errors
from keyring.backends.SecretService import Keyring as SecretServiceKeyring
from keyring.backends.Windows import WinVaultKeyring
from keyring.backends.chainer import ChainerBackend

# --- Version ------------------------------------------------------------
__version__ = "credentials.py 4.2.459"

# --- Version history ----------------------------------------------------
# v4.2.459: CredentialEntries.under (the same store under another wallet name) and move_entry (an entry moved to another wallet name: written,
#           read back, only then removed; a garbled one stays)
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

    def __init__(
        self, service: str = SERVICE_NAME, backend: CredentialBackend | None = None, time_limit: float = STORE_TIME_LIMIT, former: str = "",
    ) -> None:
        self._service: str = service
        self._backend: CredentialBackend = backend if backend is not None else keyring.get_keyring()
        self._limit: float = time_limit
        self._former: str = former  # the wallet name these entries had before (a profile's, D-274): read there too until moved; "" none

    def read(self, name: str) -> str | None:
        """The entry, or None when the store answered that it holds none. An entry not moved yet from the former name is found there."""
        def both() -> str | None:
            value = self._backend.get_password(self._service, name)
            if self._former and (value is None or "\x00" in value):  # missing or garbled here: a good copy under the former name is the one
                older = self._backend.get_password(self._former, name)
                if older is not None and "\x00" not in older:
                    return older
            return value

        try:
            return _within(both, self._limit)
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

    @property
    def service(self) -> str:
        return self._service

    def under(self, service: str) -> "CredentialEntries":
        """The same store, under another wallet name only."""
        return CredentialEntries(service, self._backend, self._limit)

    def remove(self, name: str) -> None:
        """Remove the entry, also a copy under the former name. Removing an entry that is not there is not an error."""
        for service in (self._service, *((self._former,) if self._former else ())):
            self._remove_under(service, name)

    def _remove_under(self, service: str, name: str) -> None:
        backend = self._backend
        try:
            if isinstance(backend, WinVaultKeyring):
                _within(lambda: _windows_remove(backend, service, name), self._limit)
            else:
                _within(lambda: backend.delete_password(service, name), self._limit)
        except keyring.errors.PasswordDeleteError:
            return  # already gone
        except CredentialFailure:
            raise
        except Exception as error:  # the store's own errors and those of the desktop service behind it (D-127)
            raise CredentialFailure(_cause(error)) from None


MovedEntry = Literal["moved", "absent", "garbled", "kept"]


def move_entry(source: CredentialEntries, target: CredentialEntries, name: str) -> MovedEntry:
    """Move one entry to another wallet name: written there, read back, and only then removed where it was, so a move stopped half-way
    leaves it in both places, never in neither. The old copy goes only when the new place holds exactly it, or the old one is garbled (it
    holds NUL characters, which no password or key has) and the new one is not; a garbled new copy is replaced by a good old one; a copy
    that does not read back as written is taken away again, so it never hides the good one. absent: nothing to move; garbled: garbled and
    nothing good at the new place, left where it is; kept: the new place holds another good value (typed again since), both left."""
    source, target = source.under(source.service), target.under(target.service)  # each under its one name: a former-name fallback never counts
    value = source.read(name)
    if value is None:
        return "absent"
    garbled = "\x00" in value
    present = target.read(name)
    present_garbled = present is not None and "\x00" in present
    if garbled and (present is None or present_garbled):
        return "garbled"  # nothing good anywhere: left as it is, and said
    if present is None or (present != value and present_garbled):
        target.write(name, value)
        if target.read(name) != value:
            target.remove(name)  # never left to hide the good copy, which stays where it was
            raise CredentialFailure("The password store did not give back the entry just written; it stays where it was")
    elif present != value and not garbled:
        return "kept"  # another good value at the new place (typed again since)
    source.remove(name)  # the new place holds it good (a garbled old copy of it goes too)
    return "moved"
