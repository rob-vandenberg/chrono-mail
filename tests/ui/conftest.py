import sys
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import cast

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtPrintSupport import QPrintDialog
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QColorDialog, QDialog, QFileDialog, QInputDialog, QMessageBox, QStyle, QWidget

from chrono_mail.ui import appearance, colours, scale, sendnotices
from chrono_mail.ui.passwordfield import PasswordBox
from tests.ui.world import dialogs, world

__all__ = ["dialogs", "world"]  # fixtures, found by pytest in this directory

# --- Version ------------------------------------------------------------
__version__ = "conftest.py 4.2.437"

# --- Version history ----------------------------------------------------
# v4.2.437: the password box (PasswordBox.ask) that no test answers is answered with Cancel and fails the test, as QInputDialog.getText
# v4.2.409: after every test no key is held: a simulated Ctrl+Z left Qt sure that Ctrl was still down, so a later test's selection in
#           code was a Ctrl-click that selected nothing (let_go_of_keys)
# v3.6.349: the About boxes and the print window too (they run without QDialog.exec; the reviewer of 3.5.348)
# v3.5.347: every window that waits for a click (a dialog's exec, a message box made by hand, the text, file, folder and colour
#           pickers) is never shown for real either: noted, answered with Cancel, and the test fails (a restore's recovery code window hung a run)
# v2.2.314: the question on Quit while messages wait in the Outbox (a box of its own, B-20) is never shown for real either
# v2.0.293: the window tests on Windows use Segoe UI 9 pt, the real program's font there, not Qt's "Sans Serif" (the Windows computer's report of 2.0.287)
# v1.9.266: R-45: every window test starts as the first test of a run does: the colours, sizes and style of the program are put
#           back, every window a test left is deleted (an old message window's autosave fired in a later test), and a message
#           box no test answers fails the test instead of waiting for a click for ever
# v0.0.80: After every test the pending deletions are done, so that no web view is left to the end of the process
# v0.0.13: Initial ui fixtures


@dataclass
class _ProgramLook:
    """What the program sets for the whole application (appearance.apply) and keeps between windows."""

    palette: QPalette
    menu_palette: QPalette
    scheme: Qt.ColorScheme
    style: str  # the name of the program's style: a style the program replaces is deleted, so it is never kept itself
    font: QFont
    accents: dict[bool, str]
    scale_state: dict[str, object]  # the fields of scale._state
    appearance_state: dict[str, object]  # the fields of appearance._state


@dataclass
class _UnansweredBoxes:
    """The message boxes that came up while no test answered them (a test that expects one answers it, `dialogs`)."""

    shown: list[str] = field(default_factory=list)


WINDOWS_FONT: str = "Segoe UI"  # the default font of the real program on Windows (the Windows computer measured it)
_first_look: list[_ProgramLook] = []  # the look of the program before the first test: every test starts from it
_unanswered = _UnansweredBoxes()


def _style_name(style: QStyle) -> str:
    """The style by its name; the program's own style (Fusion at the chosen sizes) is not the plain style of that name."""
    return f"scaled {style.name()}" if isinstance(style, scale.ScaledStyle) else style.name()


def _look_now(app: QApplication) -> _ProgramLook:
    return _ProgramLook(
        QPalette(app.palette()), QPalette(app.palette("QMenu")), app.styleHints().colorScheme(), _style_name(app.style()), QFont(app.font()),
        dict(colours._accents), dict(vars(scale._state)), dict(vars(appearance._state)),  # noqa: SLF001 - the program-wide state of the look
    )


def _put_back(app: QApplication, look: _ProgramLook) -> None:
    app.styleHints().setColorScheme(look.scheme)
    if _style_name(app.style()) != look.style:  # the program's own style (sizes) or another: the plain first one again
        app.setStyle(look.style.removeprefix("scaled "))
    app.setPalette(look.palette)
    app.setPalette(look.menu_palette, cast("bytes", "QMenu"))  # the stub says bytes for the class name, the call wants a str
    app.setFont(look.font)
    colours._accents.clear()  # noqa: SLF001
    colours._accents.update(look.accents)  # noqa: SLF001
    for state, fields in ((scale._state, look.scale_state), (appearance._state, look.appearance_state)):  # noqa: SLF001 - in place: others hold them
        vars(state).clear()
        vars(state).update(fields)


MODIFIER_KEYS: tuple[Qt.Key, ...] = (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta)


def let_go_of_keys() -> None:
    """A key pressed with a modifier through QTest leaves Qt sure that the modifier is still down (the release carries it): every
    modifier is let go, so no later test starts with a key held (a selection made in code would be a Ctrl-click)."""
    if QGuiApplication.keyboardModifiers() == Qt.KeyboardModifier.NoModifier:
        return
    stand_in = QWidget()  # never shown: it takes no window, focus or activation from the next test
    for key in MODIFIER_KEYS:
        QTest.keyRelease(stand_in, key, Qt.KeyboardModifier.NoModifier)
    stand_in.deleteLater()


def _unanswered_box(kind: str) -> object:
    """A message box that no test answers: in a run without a screen nobody could click it, so it would wait for ever. It is
    noted (the test then fails) and answered with the safe button, so the run goes on."""

    def show(parent: object, title: str, text: str, *args: object, **kwargs: object) -> QMessageBox.StandardButton:
        _unanswered.shown.append(f"{kind} '{title}': {text}")
        return QMessageBox.StandardButton.No if kind == "question" else QMessageBox.StandardButton.Ok

    return staticmethod(show)


def _unanswered_exec(self: QDialog) -> int:
    """A window of the program that waits for a click (exec) and that no test answers: noted (the test then fails) and answered with
    Cancel, the safe answer, so the run goes on. A test that expects the window answers it itself (its class's exec, which wins)."""
    _unanswered.shown.append(f"window '{self.windowTitle()}' ({type(self).__name__})")
    return QDialog.DialogCode.Rejected.value


def _unanswered_picker(name: str, cancelled: object) -> object:
    """A ready-made picker (text, file, folder, colour) that no test answers (answer_input, answer_open_file, answer_save_file): noted, and
    answered as cancelled."""

    def show(*args: object, **kwargs: object) -> object:
        _unanswered.shown.append(f"picker {name}")
        return cancelled

    return staticmethod(show)


_PICKERS: tuple[tuple[type[QWidget], str, object], ...] = (
    (QMessageBox, "about", None),  # made and run inside Qt itself, so the stand-in exec never sees them
    (QMessageBox, "aboutQt", None),
    (QInputDialog, "getText", ("", False)),
    (PasswordBox, "ask", ("", False)),
    (QInputDialog, "getItem", ("", False)),
    (QInputDialog, "getInt", (0, False)),
    (QFileDialog, "getOpenFileName", ("", "")),
    (QFileDialog, "getOpenFileNames", ([], "")),
    (QFileDialog, "getSaveFileName", ("", "")),
    (QFileDialog, "getExistingDirectory", ""),
    (QColorDialog, "getColor", QColor()),
)


@pytest.fixture(autouse=True, scope="session")
def no_box_waits_for_a_click() -> Generator[None]:
    """For the whole run: a message box, a window that waits for a click or a picker is never shown for real (a test answers it, with
    `dialogs`, its own patch or the answer_ helpers of world.py; else it is noted and the test fails)."""
    real = {name: QMessageBox.__dict__[name] for name in ("question", "warning", "information", "critical")}
    for name in real:
        setattr(QMessageBox, name, _unanswered_box(name))
    real_exec = {owner: owner.__dict__["exec"] for owner in (QDialog, QPrintDialog)}  # a message box made by hand runs QDialog.exec too;
    # the print window has an exec of its own
    for owner in real_exec:
        setattr(owner, "exec", _unanswered_exec)
    real_pickers = [(owner, name, owner.__dict__[name]) for owner, name, _ in _PICKERS]
    for owner, name, cancelled in _PICKERS:
        setattr(owner, name, _unanswered_picker(f"{owner.__name__}.{name}", cancelled))
    real_quit_question = sendnotices.quit_question
    sendnotices.quit_question = _unanswered_quit_question
    yield
    for name, function in real.items():
        setattr(QMessageBox, name, function)
    for owner, function in real_exec.items():
        setattr(owner, "exec", function)
    for owner, name, function in real_pickers:
        setattr(owner, name, function)
    sendnotices.quit_question = real_quit_question


def _unanswered_quit_question(parent: object, count: int) -> tuple[str, bool]:
    """The question on Quit (B-20) that no test answers: noted, and answered Quit anyway (nothing is sent)."""
    _unanswered.shown.append(f"quit question: {count} message(s) wait in the Outbox")
    return "quit", False


@pytest.fixture(autouse=True)
def every_test_starts_clean(qapp: QApplication) -> Generator[None]:
    """Every window test starts as the first test of a run does, whatever ran before it, and leaves nothing behind."""
    app = qapp
    if not _first_look:
        if sys.platform == "win32":
            app.setFont(QFont(WINDOWS_FONT, 9))  # the real program's font there; Qt's "Sans Serif" would be a decorative font on Windows
        _first_look.append(_look_now(app))
    _unanswered.shown.clear()
    yield
    let_go_of_keys()
    for window in app.topLevelWidgets():  # what the test left open (a message window keeps its autosave timer while it exists)
        window.hide()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # a web view left to the end of the process crashes it
    app.processEvents()
    _put_back(app, _first_look[0])
    if _unanswered.shown:
        shown = list(_unanswered.shown)
        _unanswered.shown.clear()
        pytest.fail(f"A message box or window came up that the test did not answer: {shown}", pytrace=False)
