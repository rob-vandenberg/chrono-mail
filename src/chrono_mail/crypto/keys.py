import hashlib
import hmac
import os
from typing import NoReturn

from chrono_mail.core import CryptoError

# --- Version ------------------------------------------------------------
__version__ = "keys.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Initial data key

KEY_LENGTH: int = 32
KEY_ID_LENGTH: int = 8
_KEY_ID_CONTEXT: bytes = b"chrono-mail/key-id/v1"


class DataKey:
    """The key that encrypts message files. It never shows its bytes in repr, str or errors.

    Python cannot reliably wipe immutable bytes from memory; the key stays in memory while
    the object lives (known limitation, see DECISIONS.md).
    """

    __slots__ = ("_material", "key_id")

    def __init__(self, material: bytes) -> None:
        if len(material) != KEY_LENGTH:
            raise CryptoError(f"A data key is {KEY_LENGTH} bytes long")
        self._material: bytes = material
        # Derived from the key, so a key restored from a backup has the same id.
        self.key_id: bytes = hashlib.sha256(_KEY_ID_CONTEXT + self._material).digest()[:KEY_ID_LENGTH]

    def reveal(self) -> bytes:
        """The key bytes. Only the crypto module itself should call this."""
        return self._material

    def __repr__(self) -> str:
        return f"DataKey(id={self.key_id.hex()})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, DataKey) and hmac.compare_digest(self._material, other._material)

    def __hash__(self) -> int:
        return hash(self.key_id)

    def __reduce__(self) -> NoReturn:
        raise TypeError("A DataKey must not be pickled or copied")


def generate_data_key() -> DataKey:
    return DataKey(os.urandom(KEY_LENGTH))
