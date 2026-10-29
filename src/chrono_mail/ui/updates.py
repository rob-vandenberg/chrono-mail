import html
import secrets
import sys
from collections.abc import Callable
from pathlib import Path
from typing import override

from PySide6.QtCore import QEvent, QObject, QStandardPaths, Qt, QTimer, QUrl, Signal, SignalInstance
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QProgressBar, QPushButton, QWidget

from chrono_mail.core import ChronoMailError, program_version
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_updates import (
    PROGRESS_WHAT,
    UPDATE_CHECK,
    UPDATE_DOWNLOAD,
    UPDATE_INSTALL,
    UPDATE_RESTART,
    UPDATE_SHOW,
    UPDATE_SKIP,
    UPDATE_STOP_DOWNLOAD,
    CheckParameters,
    DownloadParameters,
    StopParameters,
    InstallParameters,
    UpdateCheck,
    UpdateDownloaded,
    UpdateInstalled,
    UpdateSetup,
    VersionParameters,
)
from chrono_mail.protocols.releases import DownloadStopped
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.forms import part
from chrono_mail.ui.mailchecking import ask_choice
from chrono_mail.ui.plainboxes import plain_tip
from chrono_mail.ui.prefs import InUse

# --- Version ------------------------------------------------------------
__version__ = "updates.py 4.2.423"

# --- Version history ----------------------------------------------------
# v4.2.423: About shows Check for updates only in a copy updated through an installer; it keeps its place until the engine said (the owner)
# v4.2.419: Initial: Update available (Download, Skip, Cancel), Update downloaded (Install, Close), the About window with Check for updates,
#           and the check that goes along with a Refresh that went online (D-293)

WINDOW_WIDTH: int = 460  # at the standard size; never narrower than its texts need (forms.smallest)
COULD_NOT_REACH: str = "Chrono Mail could not reach GitHub. Try again later."


def downloads_folder() -> str:
    """The person's Downloads folder (the system's own), else their home folder."""
    return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home())


def _note(label: QLabel, text: str) -> None:
    label.setText(text)
    label.setVisible(bool(text))


def _plain(form: QWidget, name: str) -> QLabel:
    label = part(form, QLabel, name)
    label.setTextFormat(Qt.TextFormat.PlainText)  # text from GitHub, a folder name or an error: never markup (D-259)
    label.setWordWrap(True)
    return label


def _link(form: QWidget, name: str, address: str, text: str) -> QLabel:
    """A link that opens the browser or the file manager; the window stays open."""
    label = part(form, QLabel, name)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setText(f'<a href="{html.escape(address, quote=True)}">{html.escape(text)}</a>')
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    label.linkActivated.connect(lambda: QDesktopServices.openUrl(QUrl(address)))  # the address the program gave, never one read back from the text
    label.setToolTip(plain_tip(address))
    return label


class UpdateAvailableWindow(QDialog):
    """A newer version: what it is, its page on GitHub, and Download, Skip or Cancel. Download shows its progress here; Cancel stops it."""

    downloaded = Signal(object)  # UpdateDownloaded

    def __init__(self, backend: Backend, check: UpdateCheck, parent: QWidget) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self.check: UpdateCheck = check
        self.downloading: bool = False
        self._closing: bool = False  # Cancel or the close button during a download: the window closes once it stopped
        self._ticket: str = ""  # the number of the download under way
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # never takes the keyboard from someone typing
        form = forms.fill(self, "update_available")  # D-276
        _plain(form, "headline").setText(f"Chrono Mail {check.version} is available. You have {check.running}.")
        self.page_link: QLabel = _link(form, "pageLink", check.page, f"What is new in Chrono Mail {check.version} (on GitHub)")
        self.progress: QProgressBar = part(form, QProgressBar, "progress")
        self.progress.setVisible(False)
        self.note: QLabel = _plain(form, "note")
        _note(self.note, "")
        self.download_button: QPushButton = part(form, QPushButton, "download")
        self.skip_button: QPushButton = part(form, QPushButton, "skip")
        self.cancel_button: QPushButton = part(form, QPushButton, "cancel")
        self.download_button.setDefault(True)
        self.download_button.clicked.connect(self.download)
        self.skip_button.clicked.connect(self.skip)
        self.cancel_button.clicked.connect(self.reject)
        backend.progress.connect(self._progress)
        forms.smallest(self, WINDOW_WIDTH)

    def download(self) -> None:
        """Download and check the installer; the window says how far it is."""
        self.downloading = True
        self.download_button.setEnabled(False)
        self.skip_button.setEnabled(False)
        self.progress.setRange(0, 0)  # moving until the size is known
        self.progress.setVisible(True)
        _note(self.note, "Downloading the update…")
        self._ticket = secrets.token_hex(8)  # Cancel names this download, so it stops even before it began
        self._backend.ask(UPDATE_DOWNLOAD, DownloadParameters(self.check.version, downloads_folder(), self._ticket), self._downloaded, self._failed, owner=self)

    def _progress(self, what: str, done: int, total: object) -> None:
        if self.downloading and what == PROGRESS_WHAT and isinstance(total, int):
            self.progress.setRange(0, total)
            self.progress.setValue(min(done, total))
            _note(self.note, f"Downloading the update… {done:,} of {total:,} MB")

    def _downloaded(self, result: UpdateDownloaded) -> None:
        self.downloading = False
        if self._closing:  # Cancel came too late to stop it: the file is complete and stays where it is
            super().reject()
            return
        self.downloaded.emit(result)
        self.accept()

    def _failed(self, error: ChronoMailError) -> None:
        self.downloading = False
        if self._closing or isinstance(error, DownloadStopped):
            super().reject()
            return
        self.progress.setVisible(False)
        _note(self.note, str(error))
        self.download_button.setEnabled(True)
        self.skip_button.setEnabled(True)

    def skip(self) -> None:
        """Not this version: nothing is offered until a newer one is there."""
        self.skip_button.setEnabled(False)
        self.download_button.setEnabled(False)  # one answer at a time: a Download meanwhile would run on without its window
        self._backend.ask(UPDATE_SKIP, VersionParameters(self.check.version), self._skipped, self._skip_failed, owner=self)

    def _skipped(self, setup: UpdateSetup) -> None:
        self.accept()

    def _skip_failed(self, error: ChronoMailError) -> None:
        _note(self.note, str(error))
        self.skip_button.setEnabled(True)
        self.download_button.setEnabled(True)

    @override
    def reject(self) -> None:
        """Cancel, Escape and the close button: nothing until the next check; a download that runs is stopped first."""
        if not self.downloading:
            super().reject()
            return
        if not self._closing:
            self._closing = True
            self.cancel_button.setEnabled(False)
            _note(self.note, "Stopping the download…")
            self._backend.ask(UPDATE_STOP_DOWNLOAD, StopParameters(self._ticket), lambda setup: None, owner=self)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.downloading:
            event.ignore()
            self.reject()
            return
        super().closeEvent(event)


class UpdateDownloadedWindow(QDialog):
    """The installer is complete and checked: Install now, or Close and install it by hand from the folder shown."""

    install_on_quit = Signal(str)  # Windows: the setup runs once Chrono Mail has closed
    installed = Signal(str)  # Linux: the version installed; a restart takes it into use

    def __init__(self, backend: Backend, result: UpdateDownloaded, windows: bool, parent: QWidget) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self.download: UpdateDownloaded = result
        self._windows: bool = windows
        self.installing: bool = False
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        form = forms.fill(self, "update_downloaded")  # D-276
        _plain(form, "text").setText(f"The update to Chrono Mail {result.version} was downloaded and checked: it is complete and unchanged.")
        folder = str(Path(result.path).parent)
        self.folder_link: QLabel = _link(form, "folderLink", QUrl.fromLocalFile(folder).toString(), f"Saved in {folder}")
        self.note: QLabel = _plain(form, "note")
        _note(self.note, "")
        self.install_button: QPushButton = part(form, QPushButton, "install")
        self.close_button: QPushButton = part(form, QPushButton, "close")
        self.install_button.setDefault(True)
        self.install_button.clicked.connect(self.install)
        self.close_button.clicked.connect(self.reject)
        forms.smallest(self, WINDOW_WIDTH)

    def install(self) -> None:
        if self._windows:
            self.install_on_quit.emit(self.download.path)  # Chrono Mail closes first (its usual questions), then the setup runs
            return
        self.installing = True
        self.install_button.setEnabled(False)
        self.close_button.setEnabled(False)
        _note(self.note, "Installing the update… your system may ask for its password.")
        self._backend.ask(UPDATE_INSTALL, InstallParameters(self.download.path), self._installed, self._failed, owner=self)

    def _installed(self, result: UpdateInstalled) -> None:
        self.installing = False
        self.installed.emit(self.download.version)
        self.accept()

    def _failed(self, error: ChronoMailError) -> None:
        self.installing = False
        _note(self.note, f"{error} The update is still in its folder; you can also open it there to install it.")
        self.install_button.setEnabled(True)
        self.close_button.setEnabled(True)

    @override
    def reject(self) -> None:
        if not self.installing:  # the system's installer is at work: the window waits for its answer
            super().reject()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.installing:
            event.ignore()
            return
        super().closeEvent(event)


class AboutWindow(QDialog):
    """The version and the license, and Check for updates in a copy updated through an installer: it may connect by itself and offers a
    skipped version again (D-293)."""

    found = Signal(object)  # UpdateCheck: a newer version, offered once this window closed

    def __init__(self, backend: Backend, parent: QWidget) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        form = forms.fill(self, "about")  # D-276
        text = part(form, QLabel, "text")
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setText(f"<b>Chrono Mail</b> version {html.escape(program_version())}<br>An open-source e-mail client for Linux.<br><br>License: AGPL-3.0")
        self.update_note: QLabel = _plain(form, "updateNote")
        _note(self.update_note, "")
        self.check_button: QPushButton = part(form, QPushButton, "checkUpdates")
        self.check_button.setEnabled(False)
        self.check_button.hide()  # until the engine said whether this copy is updated through an installer; never shown in one that is not (the owner)
        keep_place = self.check_button.sizePolicy()
        keep_place.setRetainSizeWhenHidden(True)  # the window keeps its size when the button comes
        self.check_button.setSizePolicy(keep_place)
        self.answered: bool = False  # the engine said whether this copy is updated through an installer
        self.check_button.clicked.connect(self.check_now)
        close = part(form, QPushButton, "close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        backend.ask(UPDATE_SHOW, NoParameters(), self._shown, owner=self)
        forms.smallest(self, WINDOW_WIDTH)

    def _shown(self, setup: UpdateSetup) -> None:
        self.check_button.setEnabled(setup.available)
        self.check_button.setVisible(setup.available)
        self.answered = True

    def check_now(self) -> None:
        self.check_button.setEnabled(False)
        _note(self.update_note, "Checking for updates…")
        self._backend.ask(UPDATE_CHECK, CheckParameters(by_hand=True), self._checked, self._failed, owner=self)

    def _checked(self, result: UpdateCheck) -> None:
        self.check_button.setEnabled(True)
        if result.outcome == "available":
            _note(self.update_note, "")
            self.found.emit(result)
            self.accept()
            return
        _note(self.update_note, f"You have the newest version, {result.running}.")

    def _failed(self, error: ChronoMailError) -> None:
        self.check_button.setEnabled(True)
        _note(self.update_note, COULD_NOT_REACH)


class Updates(QObject):
    """The part of the window that looks for updates: along with a Refresh that went online (at most once a day, when Settings > General says so),
    or from About; the windows that offer, download and install a newer version (D-293)."""

    def __init__(self, backend: Backend, prefs: InUse, window: QWidget, quit_program: Callable[[], None], closed_for_good: SignalInstance) -> None:
        super().__init__(window)
        self._backend: Backend = backend
        self._prefs: InUse = prefs
        self._window: QWidget = window
        self._quit_program: Callable[[], None] = quit_program
        self._closed_for_good: SignalInstance = closed_for_good
        self._checking: bool = False
        self._waiting: UpdateCheck | None = None  # found while the window was hidden in the notification area: offered when it is shown
        self.open_window: QDialog | None = None  # the update window shown now (one at a time)
        self._after_quit: Callable[[], None] | None = None
        window.installEventFilter(self)
        closed_for_good.connect(self._quit_done)

    def went_online(self) -> None:
        """A Refresh tried to reach a server (D-293): look for a newer version too, never as a connection of its own; the engine keeps it to once a day."""
        if self._checking or self.open_window is not None or not self._prefs.read().update_check:
            return
        self._checking = True
        self._backend.ask(UPDATE_CHECK, CheckParameters(), self._checked, self._quiet, owner=self)

    def _checked(self, result: UpdateCheck) -> None:
        self._checking = False
        if result.outcome == "available":
            self.offer(result)

    def _quiet(self, error: ChronoMailError) -> None:
        self._checking = False  # GitHub not reached: tried again with the next Refresh that goes online

    def offer(self, check: UpdateCheck, by_hand: bool = False) -> None:
        """Show the newer version; one found by a check of its own never takes the keyboard, one the person asked for in About does."""
        if self.open_window is not None:
            self.open_window.raise_()
            if by_hand:
                self.open_window.activateWindow()
            return
        if not self._window.isVisible():
            self._waiting = check
            return
        self._waiting = None
        offered = UpdateAvailableWindow(self._backend, check, self._window)
        offered.downloaded.connect(self._downloaded)
        self._show(offered)
        if by_hand:
            offered.activateWindow()

    def _show(self, dialog: QDialog) -> None:
        self.open_window = dialog
        dialog.destroyed.connect(lambda: self._closed(dialog))
        dialog.show()

    def _closed(self, dialog: QDialog) -> None:
        if self.open_window is dialog:
            self.open_window = None

    def _downloaded(self, result: UpdateDownloaded) -> None:
        self.open_window = None  # the first window closes as this one opens
        downloaded = UpdateDownloadedWindow(self._backend, result, sys.platform == "win32", self._window)
        downloaded.install_on_quit.connect(self.install_on_quit)
        downloaded.installed.connect(self._installed)
        self._show(downloaded)
        downloaded.activateWindow()  # the person asked for it

    def install_on_quit(self, path: str) -> None:
        """Windows: Chrono Mail closes as Exit does (its questions first); once it closed for good, the setup starts and opens it again when done."""
        def run_setup() -> None:
            try:
                self._backend.registry.ask(self._backend.engine, UPDATE_INSTALL, InstallParameters(path))
            except ChronoMailError as error:
                QMessageBox.warning(None, "Update", f"The update could not be started: {error}")

        self._after_quit = run_setup
        self._quit_program()  # closed for good: the setup ran from _quit_done before this returns
        self._after_quit = None  # kept open (the person said no to a question at Exit): nothing is installed

    def _installed(self, version: str) -> None:
        choice = ask_choice(self._window, "Chrono Mail was updated", f"Chrono Mail was updated to {version}. Restart it to use the new version.", ["Restart now", "Later"])
        if choice == 0:
            self._after_quit = self._restart
            self._quit_program()
            self._after_quit = None

    def _restart(self) -> None:
        """Linux: the new version starts once this program has ended."""
        try:
            self._backend.registry.ask(self._backend.engine, UPDATE_RESTART, NoParameters())
        except ChronoMailError as error:
            QMessageBox.warning(None, "Update", f"Chrono Mail could not be started again: {error}. Start it again yourself.")

    def _quit_done(self) -> None:
        work, self._after_quit = self._after_quit, None
        if work is not None:
            work()

    def about(self) -> None:
        about = AboutWindow(self._backend, self._window)
        about.found.connect(self._found_by_hand)
        about.open()

    def _found_by_hand(self, check: UpdateCheck) -> None:
        QTimer.singleShot(0, lambda: self.offer(check, by_hand=True))  # once About has closed

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._window and event.type() == QEvent.Type.Show and self._waiting is not None:
            waiting = self._waiting
            QTimer.singleShot(0, lambda: self.offer(waiting))
        return False
