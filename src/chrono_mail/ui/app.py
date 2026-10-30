import ctypes
import sys
from collections.abc import Sequence
from pathlib import Path

# QtWebEngine must be imported before the application object is created.
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PySide6.QtCore import QCoreApplication, QSettings, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from chrono_mail.config import AppDirectories, KeyringPasswordStore, default_directories, profile_directories
from chrono_mail.core import CredentialBackend
from chrono_mail.config.autostart import MINIMIZED_OPTION
from chrono_mail.config.running import program_is_running, set_process_name
from chrono_mail.crypto import KeyringKeyStore
from chrono_mail.engine import Engine
from chrono_mail.ui import appearance
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.icons import application_icon
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.plainboxes import plain_message_boxes
from chrono_mail.ui.singleinstance import ShowRequests, ask_to_show

# --- Version ------------------------------------------------------------
__version__ = "app.py 4.2.434"

# --- Version history ----------------------------------------------------
# v4.2.434: a copy run from the project folder tells the desktop it belongs to chrono-mail-project, the built program to chrono-mail (desktop_file_name)
# v4.2.402: --minimized (the start at login with the window minimized, D-284): the window starts minimized (MainWindow.start_minimized), a
#           maximized one stays maximized when it is opened; while the program runs already such a start does nothing
# v3.6.370: the passwords and the key are kept under the profile's own wallet name (a demo's are never mixed with the person's own,
#           credential_stores); --profile is resolved, so one folder has one name
# v3.5.348: The ready-made message boxes show plain text without an event filter on the whole program (the kind that crashed PySide on 2026-10-21)
# v3.4.344: every message box shows plain text unless it says otherwise (plainboxes, RV-167)
# v2.3.320: --profile <folder>: mail, settings and the window's own settings all in that folder, the person's own never touched (the demo for screenshots, D-243)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.1.172: The colour theme is started without the command line (the style is always Fusion, D-158)
# v0.5.144: The program runs once per user: a second start shows the running program's window, or says that it is running
# v0.0.55: Application id and icon file for Windows
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.21: Tray icon, new mail notifications, unread count in the title
# v0.0.14: application icon, launcher name
# v0.0.13: Initial application start

APPLICATION_NAME: str = "Chrono Mail"
DESKTOP_FILE_NAME: str = "chrono-mail"  # the launcher file packaging/chrono-mail.desktop; the desktop portal looks it up by this name
PROJECT_DESKTOP_FILE_NAME: str = "chrono-mail-project"  # a copy run from the project folder: the owner's own entry "Chrono Mail (project)"


def desktop_file_name() -> str:
    """The start-menu entry a window of this copy belongs to, as the desktop is told (the taskbar puts the window on that entry's button):
    the built program (Nuitka marks it with __compiled__; the .deb, .rpm and setup.exe) says chrono-mail, a copy run from the project
    folder says chrono-mail-project, so the two never share a button. Decided when the program starts; the build needs nothing of it."""
    return DESKTOP_FILE_NAME if "__compiled__" in globals() else PROJECT_DESKTOP_FILE_NAME


APPLICATION_ID: str = "chrono-mail.ChronoMail"  # what Windows knows the program by (the taskbar groups windows and takes the icon by it)


def set_application_id(identifier: str = APPLICATION_ID) -> bool:
    """On Windows, give the process an identity of its own. Without it the taskbar treats the program as python.exe and shows the icon of Python.
    Must be done before the first window. Does nothing elsewhere. Returns whether it was set."""
    if sys.platform != "win32":
        return False
    try:
        windll = getattr(ctypes, "windll")  # getattr: ctypes.windll exists only on Windows
        return int(windll.shell32.SetCurrentProcessExplicitAppUserModelID(identifier)) == 0
    except (AttributeError, OSError):
        return False  # an old Windows without the call: the icon of Python stays, nothing else is lost


PROFILE_OPTION: str = "--profile"
WINDOW_SETTINGS_FILE: str = "window.conf"  # the window's own settings (sizes, columns, theme) in a profile's config folder


def take_profile(arguments: list[str]) -> Path | None:
    """The folder given with --profile <folder> (or --profile=<folder>), taken out of the arguments so Qt never sees it; None without one.
    Not in any help text: it is for the demo of the documentation (D-243)."""
    for position, argument in enumerate(arguments):
        if argument == PROFILE_OPTION and position + 1 < len(arguments):
            folder = arguments[position + 1]
            del arguments[position:position + 2]
            return Path(folder).expanduser().resolve()  # one folder, one wallet name, however it is written
        if argument.startswith(PROFILE_OPTION + "="):
            del arguments[position]
            return Path(argument.partition("=")[2]).expanduser().resolve()
    return None


def take_minimized(arguments: list[str]) -> bool:
    """Whether --minimized was given (the start at login with the window minimized, D-284), taken out of the arguments so Qt never sees it."""
    if MINIMIZED_OPTION not in arguments:
        return False
    arguments[:] = [argument for argument in arguments if argument != MINIMIZED_OPTION]
    return True


def directories_and_settings(profile: Path | None) -> tuple[AppDirectories, QSettings]:
    """Where the mail and settings are, and the window's own settings: the person's own, or all of them inside the profile's folder."""
    if profile is None:
        return default_directories(), QSettings("chrono-mail", "chrono-mail")
    directories = profile_directories(profile)
    return directories, QSettings(str(directories.config_directory / WINDOW_SETTINGS_FILE), QSettings.Format.IniFormat)


def credential_stores(directories: AppDirectories, backend: CredentialBackend | None = None) -> tuple[KeyringPasswordStore, KeyringKeyStore]:
    """The wallet stores of these directories: the passwords and the key under their own wallet name (a profile's, or the person's)."""
    return KeyringPasswordStore(directories.credential_service, backend), KeyringKeyStore(directories.credential_service, backend)


def second_start() -> int:
    """The program is running already: ask it to show its window; when it does not answer, say so. Returns the exit code."""
    if ask_to_show():
        return 0
    QMessageBox.information(None, APPLICATION_NAME, "Chrono Mail is already running.")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Start the graphical program with the real credential store and the user's own directories."""
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    arguments = list(argv) if argv is not None else list(sys.argv)
    profile = take_profile(arguments)
    minimized = take_minimized(arguments)
    set_process_name()  # Linux: another start recognises this process by its name
    appearance.keep_web_pages_light()
    set_application_id()
    app = QApplication(arguments)
    app.setApplicationName(APPLICATION_NAME)
    app.setOrganizationName("chrono-mail")
    app.setDesktopFileName(desktop_file_name())
    app.setQuitOnLastWindowClosed(False)  # the window may be hidden in the tray; closing it for good quits explicitly
    app.setWindowIcon(application_icon())
    plain_message_boxes()  # text from mail and servers in a message box is never markup (RV-167); nothing watches every event
    if program_is_running():  # two copies would change the same mail at once
        return 0 if minimized else second_start()  # a start at login while it runs already: it is there, nothing to show
    directories, settings = directories_and_settings(profile)
    engine = Engine(directories, *credential_stores(directories))
    backend = Backend(engine)
    appearance.start(app, settings)  # the colour theme and the style, before any window is made
    window = MainWindow(backend, settings)
    window.closed_for_good.connect(app.quit)
    requests = ShowRequests()
    requests.show_requested.connect(window.bring_forward)
    requests.listen()  # without it a second start only says that the program is running
    if minimized:
        window.start_minimized()
    window.show()
    QTimer.singleShot(0, window.start)
    return app.exec()
