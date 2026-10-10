import pytest

from chrono_mail.core import (
    AuthenticationError,
    ChronoMailError,
    ConfigurationError,
    CryptoError,
    MessageFormatError,
    ProtocolError,
    StorageError,
)

# --- Version ------------------------------------------------------------
__version__ = "test_core_errors.py 0.0.4"

# --- Version history ----------------------------------------------------
# v0.0.4: Initial core tests


@pytest.mark.parametrize(
    "error_type",
    [ConfigurationError, ProtocolError, AuthenticationError, StorageError, MessageFormatError, CryptoError],
)
def test_errors_derive_from_the_base_class(error_type: type[ChronoMailError]) -> None:
    with pytest.raises(ChronoMailError, match="something went wrong"):
        raise error_type("something went wrong")


def test_base_class_is_an_exception() -> None:
    assert issubclass(ChronoMailError, Exception)


def test_an_authentication_error_is_a_protocol_error() -> None:
    assert issubclass(AuthenticationError, ProtocolError)
    with pytest.raises(ProtocolError):
        raise AuthenticationError("refused")
