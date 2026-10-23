from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import override

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_outbox import MAIL_CANCEL_SEND, MAIL_END_COUNTDOWN, CancelSendResult, OutboxMessageParameters
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.composing import Composing
from chrono_mail.ui.mailchecking import MailChecking
from chrono_mail.ui.mailstate import MailState
from chrono_mail.ui.prefs import InUse
from chrono_mail.ui.statusreport import StatusReport

# --- Version ------------------------------------------------------------
__version__ = "sendnotices.py 2.2.314"

# --- Version history ----------------------------------------------------
# v2.2.314: Initial: the notice of a delayed send at the bottom left (Send now, Cancel, its countdown) and the question on Quit while
#           messages wait in the Outbox (B-21, B-20)

MARGIN: int = 8  # between the notices, and to the edges of the window


def quit_question(parent: QWidget, count: int) -> tuple[str, bool]:
    """Quitting while messages wait in the Outbox (B-20): "send" (Send now, then quit), "quit" (Quit anyway) or "cancel", and whether the
    person ticked Do not warn again."""
    box = QMessageBox(QMessageBox.Icon.Question, "Quit", f"{count:,} message{'' if count == 1 else 's'} wait{'s' if count == 1 else ''} in the Outbox.", parent=parent)
    send = box.addButton("Send now", QMessageBox.ButtonRole.AcceptRole)
    leave = box.addButton("Quit anyway", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    tick = QCheckBox("Do not warn again", box)
    box.setCheckBox(tick)
    box.setDefaultButton(send)
    box.exec()
    clicked = box.clickedButton()
    return ("send" if clicked is send else "quit" if clicked is leave else "cancel"), tick.isChecked()


@dataclass(frozen=True, slots=True)
class Waiting:
    account: str  # the account it is sent from: its Outbox holds it
    message_id: str


class SendNotice(QFrame):
    """One delayed send at the bottom left of the main window: "Sending "Subject" in 7 s" with Send now and Cancel; it counts down."""

    send_now = Signal()
    cancel = Signal()
    ended = Signal()  # the countdown is over

    def __init__(self, subject: str, seconds: int, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)  # the window's own colours (D-158)
        self._subject: str = subject or "(no subject)"
        self.remaining: int = seconds
        self.label: QLabel = QLabel(self)
        self.send_button: QPushButton = QPushButton("Send now", self)
        self.cancel_button: QPushButton = QPushButton("Cancel", self)
        layout = QHBoxLayout(self)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.send_button)
        layout.addWidget(self.cancel_button)
        self.send_button.clicked.connect(self.send_now)
        self.cancel_button.clicked.connect(self.cancel)
        self._timer: QTimer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._show_text()
        self._timer.start()

    def _show_text(self) -> None:
        self.label.setText(f"Sending “{self._subject}” in {self.remaining} s")

    def _tick(self) -> None:
        self.remaining -= 1
        if self.remaining <= 0:
            self.stop()
            self.ended.emit()
            return
        self._show_text()

    def stop(self) -> None:
        self._timer.stop()


class SendNotices(QObject):
    """The delayed sends of the message windows (B-21): a notice per message while its countdown runs, stacked at the bottom left of the
    main window. Its end tells the engine the message may go: it is sent then, or, with "until the next refresh", it waits for that."""

    def __init__(
        self, backend: Backend, state: MailState, checking: MailChecking, composing: Composing, status: StatusReport, prefs_in_use: InUse, window: QMainWindow,
    ) -> None:
        super().__init__(window)
        self._backend: Backend = backend
        self._state: MailState = state
        self._settled: bool = False  # quitting: the Outbox was asked about, or sent (B-20)
        self._checking: MailChecking = checking
        self._composing: Composing = composing
        self._status: StatusReport = status
        self._prefs: InUse = prefs_in_use
        self._window: QMainWindow = window
        self._notices: list[tuple[SendNotice, Waiting]] = []
        window.installEventFilter(self)  # the notices follow the size of the window

    @override
    def eventFilter(self, watched: QObject, event: QEvent, /) -> bool:
        if watched is self._window and event.type() == QEvent.Type.Resize:
            self._place()
        return False

    def add(self, account: str, message_id: str, subject: str, seconds: int) -> None:
        """A message window put a message in the Outbox: its countdown starts, or (none) it waits for the next refresh."""
        if seconds <= 0:
            self._status.show(f"In the Outbox: “{subject or '(no subject)'}” goes with the next refresh")
            return
        waiting = Waiting(account, message_id)
        notice = SendNotice(subject, seconds, self._window)
        notice.send_now.connect(lambda: self._send(notice))
        notice.cancel.connect(lambda: self._cancel(notice))
        notice.ended.connect(lambda: self._ended(notice))
        self._notices.append((notice, waiting))
        notice.show()
        self._place()

    def counting(self) -> int:
        """How many countdowns run."""
        return len(self._notices)

    def notices(self) -> list[SendNotice]:
        return [notice for notice, _ in self._notices]

    def _take(self, notice: SendNotice) -> Waiting | None:
        found = next((waiting for shown, waiting in self._notices if shown is notice), None)
        self._notices = [(shown, waiting) for shown, waiting in self._notices if shown is not notice]
        notice.stop()
        notice.hide()
        notice.deleteLater()
        self._place()
        return found

    def _send(self, notice: SendNotice, then: Callable[[], None] | None = None) -> None:
        """Send now: this message goes at once."""
        waiting = self._take(notice)
        if waiting is not None:
            self._checking.send_outbox(then=then, account=waiting.account, message=waiting.message_id)

    def _ended(self, notice: SendNotice, then: Callable[[], None] | None = None) -> None:
        """The countdown is over: the message is sent, or it waits for the next refresh (Settings > General)."""
        if not self._prefs.read().send_on_refresh:
            self._send(notice, then)
            return
        waiting = self._take(notice)
        if waiting is None:
            return

        def kept(result: OutboxMessageParameters) -> None:
            self._status.show("In the Outbox: the message goes with the next refresh")
            if then is not None:
                then()

        def failed(error: ChronoMailError) -> None:
            self._status.report("The message in the Outbox", error)
            if then is not None:
                then()

        self._backend.ask(MAIL_END_COUNTDOWN, OutboxMessageParameters(waiting.account, waiting.message_id), kept, failed, owner=self)

    def _cancel(self, notice: SendNotice) -> None:
        """Cancel: the message leaves the Outbox and opens again in a message window (not saved yet: closing asks first)."""
        waiting = self._take(notice)
        if waiting is None:
            return

        def taken_back(result: CancelSendResult) -> None:
            self._status.show("Not sent: the message is open again")
            self._composing.open_taken_back(result.account, result.folder, result.message_id)

        self._backend.ask(
            MAIL_CANCEL_SEND, OutboxMessageParameters(waiting.account, waiting.message_id), taken_back,
            lambda error: self._status.report("Could not cancel", error), owner=self,
        )

    def end_all_now(self, then: Callable[[], None]) -> None:
        """The program quits while countdowns run: each ends now, as its countdown would have (sent, or kept for the next refresh); then."""
        notices = self.notices()
        remaining = len(notices)
        if not remaining:
            then()
            return

        def one_done() -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                then()

        for notice in notices:
            self._ended(notice, one_done)

    def before_quit(self, quit_program: Callable[[], None]) -> bool:
        """The program is about to quit while messages wait in the Outbox (B-20): asked first, unless the person said Do not warn again; a
        countdown that runs ends now, as it would have (B-21). False: it does not quit now (Cancel, or it quits by itself once that is done)."""
        if self._settled:
            return True

        def settled() -> None:
            self._settled = True
            quit_program()

        count = self._state.outbox_count()
        if count and self._prefs.read().warn_outbox_on_quit:
            answer, quiet = quit_question(self._window, count)
            if quiet:
                self._prefs.keep(replace(self._prefs.read(), warn_outbox_on_quit=False))  # Settings > General has the tick to warn again
            if answer == "cancel":
                return False
            self.drop_all()
            if answer == "send":
                self._checking.send_outbox(then=settled, manual=True, now=True)
                return False
            return True
        if self._notices:
            self.end_all_now(settled)
            return False
        return True

    def drop_all(self) -> None:
        """Quit anyway, or everything sent: the notices go; what waits stays in the Outbox."""
        for notice in self.notices():
            self._take(notice)

    def _place(self) -> None:
        """Stacked upward from the bottom left, above the status line."""
        window = self._window
        bottom = window.height() - window.statusBar().height() - MARGIN
        for notice, _ in reversed(self._notices):
            notice.adjustSize()
            bottom -= notice.height()
            notice.move(MARGIN, bottom)
            notice.raise_()
            bottom -= MARGIN
