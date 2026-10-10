import poplib
import ssl
from contextlib import suppress

import pytest

from tests.live.account import LiveAccount
from tests.live.helpers import TIMEOUT_SECONDS, pop3_session, server_ip_address

# --- Version ------------------------------------------------------------
__version__ = "test_live_pop3.py 0.0.3"

# --- Version history ----------------------------------------------------
# v0.0.3: Initial live POP3 tests

pytestmark = pytest.mark.live


@pytest.mark.parametrize("use_stls", [False, True], ids=["pop3s-995", "stls-110"])
def test_login_and_mailbox_commands(live_account: LiveAccount, use_stls: bool) -> None:
    with pop3_session(live_account, use_stls=use_stls) as connection:
        count, size = connection.stat()
        assert count >= 0 and size >= 0
        _, unique_ids, _ = connection.uidl()
        _, listing, _ = connection.list()
        assert len(unique_ids) == count == len(listing)


def test_plain_connection_offers_stls_and_refuses_plaintext_login(live_account: LiveAccount) -> None:
    connection = poplib.POP3(live_account.host, live_account.pop3_port, timeout=TIMEOUT_SECONDS)
    try:
        assert "STLS" in connection.capa()
        with pytest.raises(poplib.error_proto):
            connection.user(live_account.user)
            connection.pass_(live_account.password)
    finally:
        connection.close()


@pytest.mark.parametrize("use_stls", [False, True], ids=["pop3s-995", "stls-110"])
def test_certificate_for_ip_address_is_rejected(live_account: LiveAccount, use_stls: bool) -> None:
    address = server_ip_address(live_account)
    context = ssl.create_default_context()
    with pytest.raises(ssl.SSLCertVerificationError):
        if use_stls:
            connection = poplib.POP3(address, live_account.pop3_port, timeout=TIMEOUT_SECONDS)
            try:
                connection.stls(context=context)
            finally:
                with suppress(OSError):  # a failed TLS handshake already closed the socket
                    connection.close()
        else:
            poplib.POP3_SSL(address, live_account.pop3_ssl_port, timeout=TIMEOUT_SECONDS, context=context).close()


def test_wrong_password_is_rejected(live_account: LiveAccount) -> None:
    if live_account.wrong_password_attempts < 1:
        pytest.skip("wrong_password_attempts is 0 (disabled by configuration)")
    for _ in range(live_account.wrong_password_attempts):
        with pop3_session(live_account, log_in=False) as connection:
            connection.user(live_account.user)
            with pytest.raises(poplib.error_proto):
                connection.pass_(live_account.password + "-wrong")
