import keyring.errors

# --- Version ------------------------------------------------------------
__version__ = "doubles.py 0.5.133"

# --- Version history ----------------------------------------------------
# v0.5.133: SessionLostBackend: KDE Wallet's service that lost its session (an error that is not keyring's own)
# v0.0.9: Shared credential store doubles (plain classes, not KeyringBackend subclasses: see CredentialBackend)


class MemoryBackend:
    """A credential store that keeps entries in a dictionary (not a KeyringBackend: see CredentialBackend)."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.entries[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.entries:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.entries[(service, username)]


class BrokenBackend:
    """A credential store that is locked or unavailable."""

    def get_password(self, service: str, username: str) -> str | None:
        raise keyring.errors.KeyringLocked("locked")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise keyring.errors.PasswordSetError("refused")

    def delete_password(self, service: str, username: str) -> None:
        raise keyring.errors.KeyringLocked("locked")


class DBusErrorResponse(Exception):
    """Stands in for jeepney's error: what KDE Wallet's Secret Service answers when it has lost the session."""


class SessionLostBackend:
    """A credential store whose desktop service answers with its own error, not one of keyring's (as KDE Wallet did on 2026-10-15)."""

    def __init__(self) -> None:
        self.failures_left: int = 10**9

    def _fail(self) -> None:
        if self.failures_left > 0:
            self.failures_left -= 1
            raise DBusErrorResponse("[org.qtproject.QtDBus.Error.InvalidObjectPath] (\"Can't find session /org/freedesktop/secrets/session/2291\",)")

    def get_password(self, service: str, username: str) -> str | None:
        self._fail()
        return None

    def set_password(self, service: str, username: str, password: str) -> None:
        self._fail()

    def delete_password(self, service: str, username: str) -> None:
        self._fail()

