import hashlib
import hmac
import random

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from chrono_mail.core import CryptoError
from chrono_mail.crypto import DataKey, decrypt_message, encrypt_message, generate_data_key, read_key_id
from chrono_mail.crypto import cipher

# --- Version ------------------------------------------------------------
__version__ = "test_crypto_cipher.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Initial cipher tests

KEY = DataKey(bytes(range(32)))
MESSAGE = b"From: a@example.com\r\nSubject: secret\r\n\r\nthe body\r\n"


def test_round_trip() -> None:
    assert decrypt_message(encrypt_message(MESSAGE, KEY), KEY) == MESSAGE


@pytest.mark.parametrize("size", [0, 1, 15, 16, 17, 4096, 5 * 1024 * 1024])
def test_round_trip_of_many_sizes(size: int) -> None:
    plaintext = random.Random(size).randbytes(size)
    blob = encrypt_message(plaintext, KEY)
    assert len(blob) == size + cipher.OVERHEAD
    assert decrypt_message(blob, KEY) == plaintext


def test_same_plaintext_gives_different_files() -> None:
    assert encrypt_message(MESSAGE, KEY) != encrypt_message(MESSAGE, KEY)


def test_plaintext_does_not_appear_in_the_file() -> None:
    assert b"secret" not in encrypt_message(MESSAGE, KEY)
    assert b"the body" not in encrypt_message(MESSAGE, KEY)


def test_header_names_the_key() -> None:
    blob = encrypt_message(MESSAGE, KEY)
    assert blob[:4] == b"CMF1"
    assert read_key_id(blob) == KEY.key_id


def test_known_answer_against_an_independent_computation() -> None:
    salt, nonce = bytes(range(16)), bytes(range(12))
    blob = cipher._encrypt(MESSAGE, KEY, salt, nonce)
    # HKDF-SHA256 (RFC 5869) written out with the standard library.
    pseudo_random_key = hmac.new(salt, KEY.reveal(), hashlib.sha256).digest()
    message_key = hmac.new(pseudo_random_key, b"chrono-mail/message/v1" + b"\x01", hashlib.sha256).digest()
    header = b"CMF1" + bytes([1, 1]) + KEY.key_id
    expected = header + salt + nonce + AESGCM(message_key).encrypt(nonce, MESSAGE, header)
    assert blob == expected
    assert decrypt_message(expected, KEY) == MESSAGE


def test_every_single_byte_change_is_detected() -> None:
    blob = encrypt_message(MESSAGE, KEY)
    for position in range(len(blob)):
        damaged = bytearray(blob)
        damaged[position] ^= 0x01
        with pytest.raises(CryptoError):
            decrypt_message(bytes(damaged), KEY)


def test_every_single_bit_change_is_detected() -> None:
    blob = encrypt_message(b"short", KEY)
    for position in range(len(blob)):
        for bit in range(8):
            damaged = bytearray(blob)
            damaged[position] ^= 1 << bit
            with pytest.raises(CryptoError):
                decrypt_message(bytes(damaged), KEY)


def test_truncation_at_every_length_is_detected() -> None:
    blob = encrypt_message(MESSAGE, KEY)
    for length in range(len(blob)):
        with pytest.raises(CryptoError):
            decrypt_message(blob[:length], KEY)


def test_extra_bytes_at_the_end_are_detected() -> None:
    with pytest.raises(CryptoError):
        decrypt_message(encrypt_message(MESSAGE, KEY) + b"\x00", KEY)


def test_wrong_key_is_rejected() -> None:
    blob = encrypt_message(MESSAGE, KEY)
    with pytest.raises(CryptoError):
        decrypt_message(blob, generate_data_key())


def test_swapping_the_key_id_does_not_help_an_attacker() -> None:
    other = generate_data_key()
    blob = bytearray(encrypt_message(MESSAGE, KEY))
    blob[6:14] = other.key_id
    with pytest.raises(CryptoError):
        decrypt_message(bytes(blob), other)


def test_unknown_format_versions_are_rejected() -> None:
    blob = bytearray(encrypt_message(MESSAGE, KEY))
    blob[4] = 2
    with pytest.raises(CryptoError):
        decrypt_message(bytes(blob), KEY)
    blob[4], blob[5] = 1, 2
    with pytest.raises(CryptoError):
        decrypt_message(bytes(blob), KEY)


@pytest.mark.parametrize("junk", [b"", b"CMF1", b"not an encrypted file at all", b"\x00" * 200])
def test_junk_is_rejected(junk: bytes) -> None:
    with pytest.raises(CryptoError):
        decrypt_message(junk, KEY)
    with pytest.raises(CryptoError):
        read_key_id(junk)


def test_random_blobs_only_ever_raise_crypto_error() -> None:
    generator = random.Random(20261008)
    valid = encrypt_message(MESSAGE, KEY)
    for _ in range(3000):
        blob = generator.randbytes(generator.randint(0, 200))
        if generator.random() < 0.5:
            blob = valid[: generator.randint(0, 20)] + blob
        with pytest.raises(CryptoError):
            decrypt_message(blob, KEY)


def test_error_texts_do_not_leak_content_or_key() -> None:
    blob = bytearray(encrypt_message(MESSAGE, KEY))
    blob[-1] ^= 1
    with pytest.raises(CryptoError) as raised:
        decrypt_message(bytes(blob), KEY)
    text = str(raised.value)
    assert KEY.reveal().hex() not in text and "secret" not in text
