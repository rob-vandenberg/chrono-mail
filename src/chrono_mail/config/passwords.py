from enum import Enum
from typing import Protocol

from chrono_mail.core import SERVICE_NAME, ConfigurationError, CredentialBackend, CredentialEntries, CredentialFailure

# --- Version ------------------------------------------------------------
__version__ = "passwords.py 1.8.263"

# --- Version history ----------------------------------------------------
# v1.8.263: R-16: the credential store is reached through core's one copy (CredentialEntries); the texts are unchanged
# v0.5.133: Every failure of the credential store is a normal error with its reason (KDE Wallet's own errors were "unexpected")
# v0.0.66: password role for the calendar and contacts server
# v0.0.9: Initial password stores


class ServerRole(Enum):
    INCOMING = "incoming"
    OUTGOING = "outgoing"
    DAV = "dav"  # the calendar and contacts server, when its password is not the one for incoming mail


class PasswordStore(Protocol):
    """Where server passwords are kept. Never a plain-text file (AGENTS.md section 4.6)."""

    def get(self, account_id: str, role: ServerRole) -> str | None: ...

    def set(self, account_id: str, role: ServerRole, password: str) -> None: ...

    def delete(self, account_id: str, role: ServerRole) -> None: ...


class InMemoryPasswordStore:
    """For tests."""

    def __init__(self) -> None:
        self._passwords: dict[tuple[str, ServerRole], str] = {}

    def get(self, account_id: str, role: ServerRole) -> str | None:
        return self._passwords.get((account_id, role))

    def set(self, account_id: str, role: ServerRole, password: str) -> None:
        self._passwords[(account_id, role)] = password

    def delete(self, account_id: str, role: ServerRole) -> None:
        self._passwords.pop((account_id, role), None)


class KeyringPasswordStore:
    """Keeps passwords in the operating system's credential store."""

    def __init__(self, service: str = SERVICE_NAME, backend: CredentialBackend | None = None) -> None:
        self._entries: CredentialEntries = CredentialEntries(service, backend)

    @staticmethod
    def _name(account_id: str, role: ServerRole) -> str:
        return f"password-{account_id}-{role.value}"

    def get(self, account_id: str, role: ServerRole) -> str | None:
        try:
            return self._entries.read(self._name(account_id, role))
        except CredentialFailure as failure:
            raise ConfigurationError(f"The credential store could not be read: {failure.reason}") from None

    def set(self, account_id: str, role: ServerRole, password: str) -> None:
        try:
            self._entries.write(self._name(account_id, role), password)
        except CredentialFailure as failure:
            raise ConfigurationError(f"The credential store refused the password: {failure.reason}") from None

    def delete(self, account_id: str, role: ServerRole) -> None:
        try:
            self._entries.remove(self._name(account_id, role))
        except CredentialFailure as failure:
            raise ConfigurationError(f"The credential store could not delete the password: {failure.reason}") from None
