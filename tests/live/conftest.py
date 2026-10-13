import pytest

from tests.live.account import KERIO_FILE, LiveAccount, load_live_account

# --- Version ------------------------------------------------------------
__version__ = "conftest.py 0.5.123"

# --- Version history ----------------------------------------------------
# v0.5.123: The IMAP tests run against both servers: DirectAdmin and Kerio
# v0.0.3: Initial live fixtures


@pytest.fixture(scope="session")
def live_account() -> LiveAccount:
    return load_live_account()


IMAP_SERVERS: tuple[str, ...] = ("directadmin", "kerio")


def imap_account(server: str) -> LiveAccount:
    """The account of the IMAP tests on one of the two servers (each its own account named chronomail@bccbox.com)."""
    if server == "kerio":
        if not KERIO_FILE.exists():
            pytest.skip("tests/live/kerio.local.toml is missing (the same settings as account.local.toml, for the Kerio server)")
        return load_live_account(KERIO_FILE)
    return load_live_account()
