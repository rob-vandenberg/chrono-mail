import dataclasses

import pytest

from chrono_mail.core import Account, AccountKind, ConfigurationError, ConnectionSecurity, ServerSettings

# --- Version ------------------------------------------------------------
__version__ = "test_core_account.py 0.0.4"

# --- Version history ----------------------------------------------------
# v0.0.4: Initial core tests


def make_settings(host: str = "mail.example.com", port: int = 995) -> ServerSettings:
    return ServerSettings(host, port, ConnectionSecurity.TLS, "user@example.com")


def test_valid_settings() -> None:
    settings = make_settings()
    assert settings.host == "mail.example.com"
    assert settings.port == 995
    assert settings.security is ConnectionSecurity.TLS


@pytest.mark.parametrize("port", [0, -1, 65536, 100000])
def test_invalid_port_is_rejected(port: int) -> None:
    with pytest.raises(ConfigurationError):
        make_settings(port=port)


@pytest.mark.parametrize("port", [1, 65535])
def test_port_limits_are_accepted(port: int) -> None:
    assert make_settings(port=port).port == port


@pytest.mark.parametrize("host", ["", "   "])
def test_empty_host_is_rejected(host: str) -> None:
    with pytest.raises(ConfigurationError):
        make_settings(host=host)


def test_settings_are_immutable_and_hashable() -> None:
    settings = make_settings()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(settings, "port", 110)
    assert hash(settings) == hash(make_settings())


def test_account_holds_incoming_and_outgoing() -> None:
    account = Account(
        account_id="a1",
        display_name="Test",
        email_address="user@example.com",
        kind=AccountKind.POP3,
        incoming=make_settings(),
        outgoing=ServerSettings("mail.example.com", 587, ConnectionSecurity.STARTTLS, "user@example.com"),
    )
    assert account.kind is AccountKind.POP3
    assert account.incoming.port == 995
    assert account.outgoing.security is ConnectionSecurity.STARTTLS
    assert account == dataclasses.replace(account)
