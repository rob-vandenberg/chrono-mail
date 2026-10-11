from enum import Enum
from typing import Protocol

import keyring
import keyring.errors

from chrono_mail.core import ConfigurationError, CredentialBackend

# --- Version ------------------------------------------------------------
__version__ = "passwords.py 0.0.66"

# --- Version history ----------------------------------------------------
# v0.0.66: password role for the calendar and contacts server
# v0.0.9: Initial password stores

SERVICE_NAME: str = "chrono-mail"


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
        self._service: str = service
        self._backend: CredentialBackend = backend if backend is not None else keyring.get_keyring()

    @staticmethod
    def _name(account_id: str, role: ServerRole) -> str:
        return f"password-{account_id}-{role.value}"

    def get(self, account_id: str, role: ServerRole) -> str | None:
        try:
            return self._backend.get_password(self._service, self._name(account_id, role))
        except keyring.errors.KeyringError as error:
            raise ConfigurationError(f"The credential store could not be read: {type(error).__name__}") from None

    def set(self, account_id: str, role: ServerRole, password: str) -> None:
        try:
            self._backend.set_password(self._service, self._name(account_id, role), password)
        except keyring.errors.KeyringError as error:
            raise ConfigurationError(f"The credential store refused the password: {type(error).__name__}") from None

    def delete(self, account_id: str, role: ServerRole) -> None:
        try:
            self._backend.delete_password(self._service, self._name(account_id, role))
        except keyring.errors.PasswordDeleteError:
            return
        except keyring.errors.KeyringError as error:
            raise ConfigurationError(f"The credential store could not delete the password: {type(error).__name__}") from None
