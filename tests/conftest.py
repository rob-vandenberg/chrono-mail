import os

# Qt runs without a screen in the tests; set before anything creates the application object.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu --disable-gpu-compositing")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

# --- Version ------------------------------------------------------------
__version__ = "conftest.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial test environment
