from collections.abc import Generator

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from tests.ui.world import dialogs, world

__all__ = ["dialogs", "world"]  # fixtures, found by pytest in this directory

# --- Version ------------------------------------------------------------
__version__ = "conftest.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.80: After every test the pending deletions are done, so that no web view is left to the end of the process
# v0.0.13: Initial ui fixtures


@pytest.fixture(autouse=True)
def finish_the_windows() -> Generator[None]:
    """A web view that is still waiting to be deleted when the process ends crashes it (a segmentation fault at exit). So after each test what is waiting to be deleted is deleted. (The windows are not closed here: closing a composer asks a question and waits for the answer.)"""
    yield
    app = QApplication.instance()
    if isinstance(app, QApplication):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
