import smtplib
import ssl

import pytest

from tests.live.account import LiveAccount
from tests.live.helpers import SMTP_MODES, open_smtp, server_ip_address, smtp_session

# --- Version ------------------------------------------------------------
__version__ = "test_live_smtp.py 0.0.3"

# --- Version history ----------------------------------------------------
# v0.0.3: Initial live SMTP tests

pytestmark = pytest.mark.live


@pytest.mark.parametrize("mode", SMTP_MODES)
def test_secure_connection_offers_auth(live_account: LiveAccount, mode: str) -> None:
    with smtp_session(live_account, mode) as connection:
        assert connection.has_extn("auth")
        assert "PLAIN" in connection.esmtp_features["auth"]


@pytest.mark.parametrize("mode", SMTP_MODES)
def test_login_succeeds(live_account: LiveAccount, mode: str) -> None:
    with smtp_session(live_account, mode) as connection:
        code, _ = connection.login(live_account.user, live_account.password)
        assert code == 235


@pytest.mark.parametrize("mode", SMTP_MODES)
def test_certificate_for_ip_address_is_rejected(live_account: LiveAccount, mode: str) -> None:
    with pytest.raises(ssl.SSLCertVerificationError):
        open_smtp(live_account, mode, host=server_ip_address(live_account)).close()


@pytest.mark.parametrize("mode", SMTP_MODES)
def test_wrong_password_is_rejected(live_account: LiveAccount, mode: str) -> None:
    if live_account.wrong_password_attempts < 1:
        pytest.skip("wrong_password_attempts is 0 (disabled by configuration)")
    for _ in range(live_account.wrong_password_attempts):
        with smtp_session(live_account, mode) as connection:
            with pytest.raises(smtplib.SMTPAuthenticationError):
                connection.login(live_account.user, live_account.password + "-wrong")
