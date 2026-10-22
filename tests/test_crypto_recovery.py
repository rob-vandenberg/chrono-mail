import itertools

import pytest

from chrono_mail.core import CryptoError
from chrono_mail.crypto import (
    DataKey,
    generate_data_key,
    key_from_recovery_code,
    recovery_code_for,
    seal_key_with_passphrase,
    unseal_key_with_passphrase,
)
from chrono_mail.crypto import recovery

# --- Version ------------------------------------------------------------
__version__ = "test_crypto_recovery.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Initial recovery tests

KEY = DataKey(bytes(range(32)))
CHEAP = 14  # lowest accepted scrypt work factor; keeps the tests fast
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def test_file_round_trip() -> None:
    blob = seal_key_with_passphrase(KEY, "correct horse battery staple", CHEAP)
    assert unseal_key_with_passphrase(blob, "correct horse battery staple") == KEY


def test_file_does_not_contain_the_key() -> None:
    blob = seal_key_with_passphrase(KEY, "passphrase", CHEAP)
    assert KEY.reveal() not in blob


def test_sealing_twice_gives_different_files() -> None:
    assert seal_key_with_passphrase(KEY, "passphrase", CHEAP) != seal_key_with_passphrase(KEY, "passphrase", CHEAP)


def test_wrong_passphrase_is_rejected() -> None:
    blob = seal_key_with_passphrase(KEY, "right", CHEAP)
    for wrong in ("wrong", "Right", "right ", ""):
        with pytest.raises(CryptoError):
            unseal_key_with_passphrase(blob, wrong)


def test_empty_passphrase_cannot_seal() -> None:
    with pytest.raises(CryptoError):
        seal_key_with_passphrase(KEY, "", CHEAP)


@pytest.mark.parametrize("log2_n", [0, 13, 19, 40])
def test_work_factor_outside_the_range_is_refused(log2_n: int) -> None:
    with pytest.raises(CryptoError):
        seal_key_with_passphrase(KEY, "passphrase", log2_n)


def test_unicode_passphrase_is_normalised() -> None:
    composed, decomposed = "café ☕", "café ☕"
    blob = seal_key_with_passphrase(KEY, composed, CHEAP)
    assert unseal_key_with_passphrase(blob, decomposed) == KEY


def test_every_single_byte_change_in_the_file_is_detected() -> None:
    blob = seal_key_with_passphrase(KEY, "passphrase", CHEAP)
    for position in range(len(blob)):
        damaged = bytearray(blob)
        damaged[position] ^= 0x01
        with pytest.raises(CryptoError):
            unseal_key_with_passphrase(bytes(damaged), "passphrase")


def test_truncated_or_extended_files_are_rejected() -> None:
    blob = seal_key_with_passphrase(KEY, "passphrase", CHEAP)
    for length in range(len(blob)):
        with pytest.raises(CryptoError):
            unseal_key_with_passphrase(blob[:length], "passphrase")
    with pytest.raises(CryptoError):
        unseal_key_with_passphrase(blob + b"\x00", "passphrase")


def test_file_asking_for_too_much_memory_is_refused_before_deriving() -> None:
    blob = bytearray(seal_key_with_passphrase(KEY, "passphrase", CHEAP))
    blob[5], blob[6] = 31, 16  # log2(N) = 31, r = 16: 2 TiB if believed
    with pytest.raises(CryptoError, match="unacceptable|too much"):
        unseal_key_with_passphrase(bytes(blob), "passphrase")
    blob[5], blob[6] = 18, 16  # inside the individual limits, but N * r is above the memory limit
    with pytest.raises(CryptoError, match="too much memory"):
        unseal_key_with_passphrase(bytes(blob), "passphrase")


def test_file_uses_the_documented_settings_by_default() -> None:
    blob = seal_key_with_passphrase(KEY, "passphrase")
    assert unseal_key_with_passphrase(blob, "passphrase") == KEY
    assert blob[:4] == b"CMR1"
    assert (blob[5], blob[6], blob[7]) == (recovery.SCRYPT_LOG2_N, recovery.SCRYPT_R, recovery.SCRYPT_P)


def test_code_round_trip() -> None:
    code = recovery_code_for(KEY)
    assert key_from_recovery_code(code) == KEY
    assert len(code.replace("-", "")) == 58
    assert all(len(group) == 4 for group in code.split("-")[:-1])


def test_random_keys_round_trip_through_codes() -> None:
    for _ in range(50):
        key = generate_data_key()
        assert key_from_recovery_code(recovery_code_for(key)) == key


def test_code_tolerates_case_spaces_and_dashes() -> None:
    code = recovery_code_for(KEY)
    flat = code.replace("-", "")
    assert key_from_recovery_code(code.lower()) == KEY
    assert key_from_recovery_code(flat) == KEY
    assert key_from_recovery_code("  " + code.replace("-", " \n ") + " ") == KEY


def test_code_reads_zero_one_and_eight_as_o_i_and_b() -> None:
    for _ in range(200):
        key = generate_data_key()
        code = recovery_code_for(key)
        typed = code.replace("O", "0").replace("I", "1").replace("B", "8")
        assert key_from_recovery_code(typed) == key


def test_every_single_character_error_is_detected() -> None:
    code = recovery_code_for(KEY).replace("-", "")
    for position, replacement in itertools.product(range(len(code)), ALPHABET):
        if replacement == code[position]:
            continue
        typo = code[:position] + replacement + code[position + 1 :]
        with pytest.raises(CryptoError):
            key_from_recovery_code(typo)


def test_every_swap_of_neighbouring_characters_is_detected() -> None:
    code = recovery_code_for(KEY).replace("-", "")
    for position in range(len(code) - 1):
        if code[position] == code[position + 1]:
            continue
        swapped = code[:position] + code[position + 1] + code[position] + code[position + 2 :]
        with pytest.raises(CryptoError):
            key_from_recovery_code(swapped)


@pytest.mark.parametrize("bad", ["", "ABCD", "A" * 57, "A" * 59, "!" * 58, "a-b-c", "9" * 58, "É" * 58])
def test_wrong_codes_are_rejected(bad: str) -> None:
    with pytest.raises(CryptoError):
        key_from_recovery_code(bad)


def test_error_texts_do_not_contain_the_key() -> None:
    code = recovery_code_for(KEY).replace("-", "")
    with pytest.raises(CryptoError) as raised:
        key_from_recovery_code(code[:-1] + ("A" if code[-1] != "A" else "B"))
    assert KEY.reveal().hex() not in str(raised.value) and code not in str(raised.value)
