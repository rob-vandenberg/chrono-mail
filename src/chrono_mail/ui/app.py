import sys
from collections.abc import Sequence

# QtWebEngine must be imported before the application object is created.
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PySide6.QtCore import QCoreApplication, QSettings, Qt, QTimer
from PySide6.QtWidgets import QApplication

from chrono_mail.config import KeyringPasswordStore, default_directories
from chrono_mail.crypto import KeyringKeyStore
from chrono_mail.engine import Engine
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.icons import application_icon
from chrono_mail.ui.mainwindow import MainWindow

# --- Version ------------------------------------------------------------
__version__ = "app.py 0.0.14"

# --- Version history ----------------------------------------------------
# v0.0.14: application icon, launcher name
# v0.0.13: Initial application start

APPLICATION_NAME: str = "Chrono Mail"
DESKTOP_FILE_NAME: str = "chrono-mail"  # the launcher file packaging/chrono-mail.desktop; the desktop portal looks it up by this name


def main(argv: Sequence[str] | None = None) -> int:
    """Start the graphical program with the real credential store and the user's own directories."""
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    arguments = list(argv) if argv is not None else sys.argv
    app = QApplication(arguments)
    app.setApplicationName(APPLICATION_NAME)
    app.setOrganizationName("chrono-mail")
    app.setDesktopFileName(DESKTOP_FILE_NAME)
    app.setWindowIcon(application_icon())
    engine = Engine(default_directories(), KeyringPasswordStore(), KeyringKeyStore())
    backend = Backend(engine)
    window = MainWindow(backend, QSettings("chrono-mail", "chrono-mail"))
    window.show()
    QTimer.singleShot(0, window.start)
    return app.exec()
