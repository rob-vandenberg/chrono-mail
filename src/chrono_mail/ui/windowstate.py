from PySide6.QtCore import QByteArray, QRect, QSettings
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

# --- Version ------------------------------------------------------------
__version__ = "windowstate.py 0.0.83"

# --- Version history ----------------------------------------------------
# v0.0.83: Initial remembering of the size and position of a window, checked against the screens that are connected now

GRAB_WIDTH: int = 100  # how much of the top edge of a window must be on a screen, so that the person can still take hold of it
GRAB_HEIGHT: int = 20
TOP_BAND: int = 40


def reachable(frame: QRect) -> bool:
    """True when the top edge of a window (where its title bar is) is on a screen that is connected now by at least a width and height the person can grab."""
    band = QRect(frame.left(), frame.top(), frame.width(), TOP_BAND)
    for screen in QGuiApplication.screens():
        shown = band.intersected(screen.availableGeometry())
        if shown.width() >= GRAB_WIDTH and shown.height() >= GRAB_HEIGHT:
            return True
    return False


def restore_geometry(window: QWidget, settings: QSettings | None, key: str) -> bool:
    """Give a window the size and position it had. A saved position that would put the window out of reach (a screen that is gone, another resolution)
    is not used: the window keeps its own size and position. True when the saved geometry was used."""
    saved = settings.value(key) if settings is not None else None
    if not isinstance(saved, (QByteArray, bytes)):
        return False
    before = window.saveGeometry()
    if window.restoreGeometry(saved) and reachable(window.frameGeometry()):
        return True
    window.restoreGeometry(before)
    return False


def save_geometry(window: QWidget, settings: QSettings | None, key: str) -> None:
    if settings is not None:
        settings.setValue(key, window.saveGeometry())
