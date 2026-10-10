import pytest

from tests.live.account import LiveAccount, load_live_account

# --- Version ------------------------------------------------------------
__version__ = "conftest.py 0.0.3"

# --- Version history ----------------------------------------------------
# v0.0.3: Initial live fixtures


@pytest.fixture(scope="session")
def live_account() -> LiveAccount:
    return load_live_account()
