import os
import signal
import sys
from collections.abc import Generator
from types import FrameType

import pytest

# Qt runs without a screen in the tests; set before anything creates the application object.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":  # Qt without a screen finds no fonts on Windows (text then measures twice as wide): give it the real ones
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"))
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu --disable-gpu-compositing")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

# --- Version ------------------------------------------------------------
__version__ = "conftest.py 3.6.353"

# --- Version history ----------------------------------------------------
# v3.6.353: the kept index connections are let go after every test (D-266)
# v2.0.293: on Windows the run without a screen gets Windows' own fonts (the Windows computer's report of 2.0.287)
# v1.9.266: R-45: no test may take longer than 20 seconds (setup, run and clean-up together); a longer one fails, and where
#           it waited is printed (faulthandler_timeout in pyproject.toml)
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


TEST_TIME_LIMIT_SECONDS: int = 20  # the owner, 2026-10-21: no test should ever need longer; a test may never hang


class TestTookTooLong(Exception):  # noqa: N818 - the name says what happened
    pass


def _too_long(signum: int, frame: FrameType | None) -> None:
    raise TestTookTooLong(f"the test took longer than {TEST_TIME_LIMIT_SECONDS} s")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Generator[None]:
    """Every test, its setup and its clean-up together, within the limit. Where the program waits inside Qt (where Python cannot
    stop it), faulthandler_timeout in pyproject.toml prints where every thread waits, at the same limit."""
    if not hasattr(signal, "setitimer") or item.get_closest_marker("perf") is not None:
        yield  # Windows has no alarm (the printed waits still show a hang); the opt-in 500,000-message measurement runs for an hour
        return
    earlier = signal.signal(signal.SIGALRM, _too_long)
    signal.setitimer(signal.ITIMER_REAL, TEST_TIME_LIMIT_SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, earlier)


@pytest.fixture(autouse=True)
def _kept_indexes_let_go() -> Generator[None]:
    """Every test leaves no index connection open behind it: each test makes indexes of its own, and the engine keeps one connection to
    each open (D-266); a whole run would run out of open files, and Windows could not remove the test's folder."""
    yield
    from chrono_mail.engine.session import let_go_kept_indexes

    let_go_kept_indexes()
