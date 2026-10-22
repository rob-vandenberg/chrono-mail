import copy
import pickle

import pytest

from chrono_mail.core import CryptoError
from chrono_mail.crypto import DataKey, generate_data_key

# --- Version ------------------------------------------------------------
__version__ = "test_crypto_keys.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Initial data key tests


def test_generated_keys_are_32_bytes_and_different() -> None:
    first, second = generate_data_key(), generate_data_key()
    assert len(first.reveal()) == 32
    assert first != second
    assert first.key_id != second.key_id


@pytest.mark.parametrize("length", [0, 16, 31, 33, 64])
def test_wrong_length_is_rejected(length: int) -> None:
    with pytest.raises(CryptoError):
        DataKey(b"\x01" * length)


def test_key_id_is_derived_from_the_key() -> None:
    material = bytes(range(32))
    assert DataKey(material).key_id == DataKey(material).key_id
    assert len(DataKey(material).key_id) == 8
    assert DataKey(material) == DataKey(material)
    assert hash(DataKey(material)) == hash(DataKey(material))


def test_key_bytes_never_appear_in_text_forms() -> None:
    material = bytes(range(32))
    key = DataKey(material)
    for text in (repr(key), str(key), f"{key}", f"{key!r}", "%s" % (key,)):
        assert material.hex() not in text
        assert str(list(material)) not in text
        assert repr(material) not in text
        assert "DataKey(id=" in text


def test_key_cannot_be_pickled_or_copied() -> None:
    key = generate_data_key()
    with pytest.raises(TypeError):
        pickle.dumps(key)
    with pytest.raises(TypeError):
        copy.copy(key)
    with pytest.raises(TypeError):
        copy.deepcopy(key)


def test_error_text_does_not_contain_key_bytes() -> None:
    material = b"\xab" * 40
    with pytest.raises(CryptoError) as raised:
        DataKey(material)
    assert material.hex() not in str(raised.value)
    assert repr(material) not in str(raised.value)
