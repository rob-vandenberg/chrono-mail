from typing import Protocol

# --- Version ------------------------------------------------------------
__version__ = "credentials.py 0.0.9"

# --- Version history ----------------------------------------------------
# v0.0.9: Moved here from crypto so that config can use it too


class CredentialBackend(Protocol):
    """What the app needs from an operating system credential store. Every keyring backend provides it.

    A protocol, not a KeyringBackend subclass: keyring registers every subclass globally,
    which would let a test double leak into the real credential store lookup.
    """

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...
