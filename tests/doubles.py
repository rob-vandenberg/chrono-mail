import keyring.errors

# --- Version ------------------------------------------------------------
__version__ = "doubles.py 0.0.9"

# --- Version history ----------------------------------------------------
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
