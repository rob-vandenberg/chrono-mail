import base64
import binascii
import hashlib
import os
import re
import unicodedata

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from chrono_mail.core import CryptoError
from chrono_mail.crypto.keys import KEY_LENGTH, DataKey

# --- Version ------------------------------------------------------------
__version__ = "recovery.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Initial key recovery primitives

# Recovery file layout: magic | version | log2(N) | r | p | salt | nonce | key + tag.
# Everything before the nonce is authenticated as associated data.
FILE_MAGIC: bytes = b"CMR1"
FILE_VERSION: int = 1
SCRYPT_LOG2_N: int = 17  # about 128 MiB and 0.4 s on the development machine
SCRYPT_R: int = 8
SCRYPT_P: int = 1
_SCRYPT_LIMITS: tuple[int, int] = (14, 18)  # accepted log2(N) when reading a file
_SCRYPT_MAXIMUM_MEMORY_FACTOR: int = 1 << 21  # N * r, about 256 MiB: input must not size allocations
_SALT_LENGTH: int = 16
_NONCE_LENGTH: int = 12
_HEADER_LENGTH: int = len(FILE_MAGIC) + 4 + _SALT_LENGTH
_FILE_LENGTH: int = _HEADER_LENGTH + _NONCE_LENGTH + KEY_LENGTH + 16

_CODE_CONTEXT: bytes = b"chrono-mail/recovery-code/v1"
_CODE_CHECKSUM_LENGTH: int = 4
_CODE_LENGTH: int = 58  # base32 characters for key + checksum (36 bytes, without padding)
_CODE_GROUP: int = 4
_CODE_ALIASES: dict[str, str] = {"0": "O", "1": "I", "8": "B"}
_CODE_ALPHABET: re.Pattern[str] = re.compile(r"[A-Z2-7]+")


def _passphrase_bytes(passphrase: str) -> bytes:
    if not passphrase:
        raise CryptoError("The passphrase must not be empty")
    return unicodedata.normalize("NFKC", passphrase).encode("utf-8")


def _derive(passphrase: str, salt: bytes, log2_n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=1 << log2_n, r=r, p=p).derive(_passphrase_bytes(passphrase))


def seal_key_with_passphrase(key: DataKey, passphrase: str, log2_n: int = SCRYPT_LOG2_N) -> bytes:
    """The key sealed under a passphrase, as the bytes of a recovery file.

    log2_n is the scrypt work factor; only tests use a value other than the default.
    """
    if not _SCRYPT_LIMITS[0] <= log2_n <= _SCRYPT_LIMITS[1]:
        raise CryptoError("The scrypt work factor is outside the accepted range")
    try:
        salt = os.urandom(_SALT_LENGTH)
        nonce = os.urandom(_NONCE_LENGTH)
        header = FILE_MAGIC + bytes([FILE_VERSION, log2_n, SCRYPT_R, SCRYPT_P]) + salt
        wrapping_key = _derive(passphrase, salt, log2_n, SCRYPT_R, SCRYPT_P)
        return header + nonce + AESGCM(wrapping_key).encrypt(nonce, key.reveal(), header)
    except CryptoError:
        raise
    except Exception as error:
        raise CryptoError(f"Sealing the key failed: {type(error).__name__}") from None


def unseal_key_with_passphrase(blob: bytes, passphrase: str) -> DataKey:
    """The key from a recovery file. Raises CryptoError for a wrong passphrase or a damaged file."""
    if len(blob) != _FILE_LENGTH or blob[: len(FILE_MAGIC)] != FILE_MAGIC:
        raise CryptoError("Not a Chrono Mail recovery file")
    version, log2_n, r, p = blob[len(FILE_MAGIC) : len(FILE_MAGIC) + 4]
    if version != FILE_VERSION:
        raise CryptoError("Unsupported recovery file version")
    if not _SCRYPT_LIMITS[0] <= log2_n <= _SCRYPT_LIMITS[1] or not 1 <= r <= 16 or not 1 <= p <= 4:
        raise CryptoError("Recovery file has unacceptable key derivation settings")
    if (1 << log2_n) * r > _SCRYPT_MAXIMUM_MEMORY_FACTOR:
        raise CryptoError("Recovery file asks for too much memory")
    header = blob[:_HEADER_LENGTH]
    salt = header[len(FILE_MAGIC) + 4 :]
    nonce = blob[_HEADER_LENGTH : _HEADER_LENGTH + _NONCE_LENGTH]
    try:
        wrapping_key = _derive(passphrase, salt, log2_n, r, p)
        return DataKey(AESGCM(wrapping_key).decrypt(nonce, blob[_HEADER_LENGTH + _NONCE_LENGTH :], header))
    except CryptoError:
        raise
    except Exception:
        raise CryptoError("Wrong passphrase or damaged recovery file") from None


def _checksum(material: bytes) -> bytes:
    return hashlib.sha256(_CODE_CONTEXT + material).digest()[:_CODE_CHECKSUM_LENGTH]


def recovery_code_for(key: DataKey) -> str:
    """A printable code for the key: base32 in groups of four, with a checksum."""
    material = key.reveal()
    text = base64.b32encode(material + _checksum(material)).decode("ascii").rstrip("=")
    return "-".join(text[index : index + _CODE_GROUP] for index in range(0, len(text), _CODE_GROUP))


def key_from_recovery_code(code: str) -> DataKey:
    """The key for a printed recovery code. Case, spaces and dashes are ignored; 0, 1 and 8 read as O, I and B."""
    text = "".join(character for character in code.upper() if not character.isspace() and character != "-")
    text = "".join(_CODE_ALIASES.get(character, character) for character in text)
    if len(text) != _CODE_LENGTH or _CODE_ALPHABET.fullmatch(text) is None:
        raise CryptoError("The recovery code has the wrong length or contains invalid characters")
    try:
        decoded = base64.b32decode(text + "=" * (-len(text) % 8))
    except (binascii.Error, ValueError):
        raise CryptoError("The recovery code is invalid") from None
    material, checksum = decoded[:KEY_LENGTH], decoded[KEY_LENGTH:]
    if base64.b32encode(decoded).decode("ascii").rstrip("=") != text or checksum != _checksum(material):
        raise CryptoError("The recovery code is wrong: check it for typing errors")
    return DataKey(material)
