import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from chrono_mail.core import CryptoError
from chrono_mail.crypto.keys import KEY_ID_LENGTH, DataKey

# --- Version ------------------------------------------------------------
__version__ = "cipher.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Initial message file cipher

# File layout: magic | version | algorithm | key id | salt | nonce | ciphertext + tag.
# The header (magic .. key id) is authenticated as associated data.
MAGIC: bytes = b"CMF1"
FORMAT_VERSION: int = 1
ALGORITHM_AES_256_GCM_HKDF_SHA256: int = 1
SALT_LENGTH: int = 16
NONCE_LENGTH: int = 12
TAG_LENGTH: int = 16
HEADER_LENGTH: int = len(MAGIC) + 1 + 1 + KEY_ID_LENGTH
OVERHEAD: int = HEADER_LENGTH + SALT_LENGTH + NONCE_LENGTH + TAG_LENGTH
_HKDF_INFO: bytes = b"chrono-mail/message/v1"


def _message_key(key: DataKey, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=_HKDF_INFO).derive(key.reveal())


def _header(key: DataKey) -> bytes:
    return MAGIC + bytes([FORMAT_VERSION, ALGORITHM_AES_256_GCM_HKDF_SHA256]) + key.key_id


def _encrypt(plaintext: bytes, key: DataKey, salt: bytes, nonce: bytes) -> bytes:
    header = _header(key)
    ciphertext = AESGCM(_message_key(key, salt)).encrypt(nonce, plaintext, header)
    return header + salt + nonce + ciphertext


def encrypt_message(plaintext: bytes, key: DataKey) -> bytes:
    """Encrypt the complete raw message. Every call uses a fresh salt and nonce."""
    try:
        return _encrypt(plaintext, key, os.urandom(SALT_LENGTH), os.urandom(NONCE_LENGTH))
    except Exception as error:
        raise CryptoError(f"Encryption failed: {type(error).__name__}") from None


def read_key_id(blob: bytes) -> bytes:
    """The id of the key that encrypted this file, so the right key can be fetched."""
    if len(blob) < HEADER_LENGTH or blob[: len(MAGIC)] != MAGIC:
        raise CryptoError("Not a Chrono Mail message file")
    if blob[len(MAGIC)] != FORMAT_VERSION or blob[len(MAGIC) + 1] != ALGORITHM_AES_256_GCM_HKDF_SHA256:
        raise CryptoError("Unsupported message file format")
    return blob[len(MAGIC) + 2 : HEADER_LENGTH]


def decrypt_message(blob: bytes, key: DataKey) -> bytes:
    """Decrypt and verify a message file. Raises CryptoError when it is damaged or the key is wrong."""
    file_key_id = read_key_id(blob)
    if len(blob) < OVERHEAD:
        raise CryptoError("Message file is truncated")
    if file_key_id != key.key_id:
        raise CryptoError(f"Message file was encrypted with key {file_key_id.hex()}, not {key.key_id.hex()}")
    salt = blob[HEADER_LENGTH : HEADER_LENGTH + SALT_LENGTH]
    nonce = blob[HEADER_LENGTH + SALT_LENGTH : HEADER_LENGTH + SALT_LENGTH + NONCE_LENGTH]
    ciphertext = blob[HEADER_LENGTH + SALT_LENGTH + NONCE_LENGTH :]
    try:
        return AESGCM(_message_key(key, salt)).decrypt(nonce, ciphertext, blob[:HEADER_LENGTH])
    except Exception:
        raise CryptoError("Message file failed verification: it is damaged or the key is wrong") from None
