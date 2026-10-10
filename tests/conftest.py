import os
import sys

import pytest

# Qt runs without a screen in the tests; set before anything creates the application object.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu --disable-gpu-compositing")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

# --- Version ------------------------------------------------------------
__version__ = "conftest.py 0.0.39"

# --- Version history ----------------------------------------------------
# v0.0.39: fast exit on the build server
# v0.0.13: Initial test environment


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """On the build server Qt WebEngine crashes while the program is shutting down (after every test has run and been
    reported). Setting CHRONO_MAIL_FAST_EXIT ends the process right after the results are written, with the right exit code."""
    if os.environ.get("CHRONO_MAIL_FAST_EXIT"):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(exitstatus)
