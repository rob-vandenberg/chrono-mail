"""Key management, encryption and decryption of message files."""

from chrono_mail.crypto.cipher import decrypt_message, encrypt_message, read_key_id
from chrono_mail.crypto.keys import DataKey, generate_data_key
from chrono_mail.crypto.keystore import CredentialStoreError, InMemoryKeyStore, KeyNotFoundError, KeyringKeyStore, KeyStore
from chrono_mail.crypto.recovery import (
    key_from_recovery_code,
    recovery_code_for,
    seal_key_with_passphrase,
    unseal_key_with_passphrase,
)

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 1.1.197"

# --- Version history ----------------------------------------------------
# v1.1.197: Export KeyNotFoundError and CredentialStoreError
# v0.0.9: CredentialBackend moved to core
# v0.0.6: Export the cipher, the data key, the key stores and the recovery primitives
# v0.0.2: Initial skeleton

__all__ = [
    "CredentialStoreError",
    "DataKey",
    "InMemoryKeyStore",
    "KeyNotFoundError",
    "KeyStore",
    "KeyringKeyStore",
    "decrypt_message",
    "encrypt_message",
    "generate_data_key",
    "key_from_recovery_code",
    "read_key_id",
    "recovery_code_for",
    "seal_key_with_passphrase",
    "unseal_key_with_passphrase",
]
