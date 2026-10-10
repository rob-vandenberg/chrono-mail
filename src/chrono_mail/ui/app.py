import sys
from collections.abc import Sequence

# QtWebEngine must be imported before the application object is created.
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PySide6.QtCore import QCoreApplication, QSettings, Qt, QTimer
from PySide6.QtWidgets import QApplication

from chrono_mail.config import KeyringPasswordStore, default_directories
from chrono_mail.crypto import KeyringKeyStore
from chrono_mail.engine import Engine
from chrono_mail.ui import appearance
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.icons import application_icon
from chrono_mail.ui.mainwindow import MainWindow

# --- Version ------------------------------------------------------------
__version__ = "app.py 0.0.51"

# --- Version history ----------------------------------------------------
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.21: Tray icon, new mail notifications, unread count in the title
# v0.0.14: application icon, launcher name
# v0.0.13: Initial application start

APPLICATION_NAME: str = "Chrono Mail"
DESKTOP_FILE_NAME: str = "chrono-mail"  # the launcher file packaging/chrono-mail.desktop; the desktop portal looks it up by this name


def main(argv: Sequence[str] | None = None) -> int:
    """Start the graphical program with the real credential store and the user's own directories."""
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    arguments = list(argv) if argv is not None else sys.argv
    appearance.keep_web_pages_light()
    app = QApplication(arguments)
    app.setApplicationName(APPLICATION_NAME)
    app.setOrganizationName("chrono-mail")
    app.setDesktopFileName(DESKTOP_FILE_NAME)
    app.setQuitOnLastWindowClosed(False)  # the window may be hidden in the tray; closing it for good quits explicitly
    app.setWindowIcon(application_icon())
    engine = Engine(default_directories(), KeyringPasswordStore(), KeyringKeyStore())
    backend = Backend(engine)
    settings = QSettings("chrono-mail", "chrono-mail")
    appearance.start(app, arguments, settings)  # the colour scheme and the style, before any window is made
    window = MainWindow(backend, settings)
    window.closed_for_good.connect(app.quit)
    window.show()
    QTimer.singleShot(0, window.start)
    return app.exec()
