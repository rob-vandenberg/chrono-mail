import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, override

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from chrono_mail.core import CertificateTrouble, ChronoMailError
from chrono_mail.engine.commands_accounts import (
    ACCOUNT_FORGET_CERTIFICATE,
    ACCOUNT_TRUST_CERTIFICATE,
    CERTIFICATE_INSPECT,
    CERTIFICATE_TRUST_FOR_RUN,
    AccountSummary,
    CertificateParameters,
    CertificateResult,
    RunTrustParameters,
    RunTrustResult,
    Security,
    TrustParameters,
)
from chrono_mail.ui.backend import Backend, alive
from chrono_mail.ui.certificatedialog import VerifyCertificateDialog

# --- Version ------------------------------------------------------------
__version__ = "certificatequestions.py 4.2.447"

# --- Version history ----------------------------------------------------
# v4.2.447: a test or Find the person starts always asks (even when accepted meanwhile); the tick starts ticked for a kept certificate, and
#           Connect without it takes that one back (from the account, or from the window of an account not added yet)
# v4.2.443: one window per server name and certificate: the other ports of that server showing the same certificate (incoming, outgoing,
#           calendars on one machine) get the same answer, and the work they met is done again once
# v4.2.442: by_person: a Send the person made asks also after Do not connect earlier in this run (D-299)
# v4.2.441: Initial version: a certificate that could not be verified asks at once, one Verify certificate window at a time (UR-05, D-299)

# A server whose certificate could not be verified asks the person at once, wherever it was met: a test or Find the person started (asked
# over that window, at once), or a Refresh (asked over the main window, only while it is on screen and no other question waits for an answer;
# else as soon as it is). One window at a time; a server already waiting is asked once, and every piece of work that met it is done again
# after Connect. Do not connect: the Refreshes of this run ask no more about that server; a test or Find the person starts always asks.

Server = tuple[str, int]
KINDS: dict[str, Literal["pop3", "imap", "smtp", "https"]] = {"pop3": "pop3", "imap": "imap", "smtp": "smtp", "https": "https"}
SECURITIES: dict[str, Security] = {"tls": "tls", "starttls": "starttls"}
Keep = Callable[[str, bool], None]  # keeps (True) or takes back (False) a certificate (host:port=fingerprint) with an account being added or edited
Kept = Callable[[], Sequence[str]]  # the certificates a window keeps with an account not added yet (host:port=fingerprint)


@dataclass(slots=True)
class _Retry:
    again: Callable[[], None]
    owner: QWidget | None  # the window of a test or Find (its work is done again only while it is open); None: a Refresh's


@dataclass(slots=True)
class _Question:
    trouble: CertificateTrouble
    by_hand: bool
    parent: QWidget | None
    retries: list[_Retry] = field(default_factory=list)
    keeps: list[tuple[Keep, QWidget | None]] = field(default_factory=list)  # with the window each belongs to
    accounts: list[str] = field(default_factory=list)  # every account that met this server: Don't ask me again keeps it with each
    kept: list[Kept] = field(default_factory=list)  # what the windows that asked keep: the tick starts ticked for one of these
    fresh: bool = False  # met by a check the person started (Test, Find): asked even when accepted meanwhile


def _look(trouble: CertificateTrouble) -> CertificateParameters:
    """certificate.inspect for the server of this trouble (and whether the certificate is kept with its account)."""
    return CertificateParameters(trouble.host, trouble.port, SECURITIES.get(trouble.security, "tls"), KINDS.get(trouble.kind, "imap"), trouble.account)


def _open(window: QWidget | None) -> bool:
    """A window of a test or Find that is still there and shown (closed, its question and its work are no longer wanted)."""
    return window is not None and alive(window) and window.isVisible()


class CertificateQuestions(QObject):
    """Asks about the certificates that could not be verified, one Verify certificate window at a time (D-299)."""

    def __init__(self, backend: Backend) -> None:
        super().__init__()
        self._backend: Backend = backend
        self._window: QWidget | None = None
        self._on_screen: Callable[[], bool] = lambda: True
        self._waiting: list[_Question] = []
        self._asking: _Question | None = None  # looked at or shown now
        self._group: list[_Question] = []  # every question its window answers (the other ports of that server with its certificate)
        self.open_dialog: VerifyCertificateDialog | None = None
        self._declined: set[Server] = set()  # Do not connect: the Refreshes of this run ask no more about these servers
        self._done_again: set[tuple[str, int, str]] = set()  # a certificate accepted already, whose work was done again once (never a loop)

    def watch(self, window: QWidget, on_screen: Callable[[], bool]) -> None:
        """The main window: the questions of a Refresh are asked over it, while it is on screen."""
        self._window = window
        self._on_screen = on_screen
        window.installEventFilter(self)

    def ask(
        self, trouble: CertificateTrouble, retry: Callable[[], None], parent: QWidget | None = None, keep: Keep | None = None, by_person: bool = False,
        kept: Kept | None = None, fresh: bool = False,
    ) -> None:
        """Ask about this server's certificate; after Connect, retry runs. parent: the window of a test or Find the person started (asked at
        once over it); None: met by a Refresh (asked over the main window when it is on screen). keep: where an accepted-for-good certificate
        goes when it is not an account's yet (or the window edits that account's certificates itself). by_person: the person's own work without a
        window of its own (Send in a message window that has closed): asked as a Refresh's, but also after Do not connect earlier in this run."""
        by_hand = parent is not None
        server = (trouble.host.lower(), trouble.port)
        if by_person:
            self._declined.discard(server)
        if not by_hand and server in self._declined:
            return
        asked = self._group or ([self._asking] if self._asking is not None else [])
        for question in [*asked, *self._waiting]:
            if question is not None and (question.trouble.host.lower(), question.trouble.port) == server:
                self._join(question, trouble, retry, parent, keep, kept)
                question.fresh = question.fresh or fresh  # a check the person started: asked, also when its certificate is being read now
                if by_hand and not question.by_hand:
                    question.by_hand, question.parent = True, parent  # the person waits for it: asked over their window, also when it is read now
                    self._declined.discard(server)
                    if question not in asked:
                        self._waiting.remove(question)
                        self._waiting.insert(0, question)  # asked first
                        self._next()
                return
        question = _Question(trouble, by_hand, parent, fresh=fresh)
        self._join(question, trouble, retry, parent, keep, kept)
        if by_hand:
            self._declined.discard(server)  # asked by hand: a refusal before counts no more
            self._waiting.insert(0, question)
        else:
            self._waiting.append(question)
        self._next()

    @staticmethod
    def _join(
        question: _Question, trouble: CertificateTrouble, retry: Callable[[], None], parent: QWidget | None, keep: Keep | None, kept: Kept | None,
    ) -> None:
        """One more piece of work met this server: done again after Connect, and its account keeps the certificate with the tick."""
        if kept is not None:
            question.kept.append(kept)
        question.retries.append(_Retry(retry, parent))
        if keep is not None:
            question.keeps.append((keep, parent))
        if trouble.account and trouble.account not in question.accounts:
            question.accounts.append(trouble.account)

    def _may_ask(self, question: _Question) -> bool:
        if question.by_hand:
            return True
        return self._on_screen() and QApplication.activeModalWidget() is None

    def _wanted(self, question: _Question) -> bool:
        """Some work that met it is still wanted: a Refresh's, or a test or Find whose window is still open."""
        return any(retry.owner is None or _open(retry.owner) for retry in question.retries)

    def _next(self) -> None:
        if self._asking is not None:
            return
        ready = [question for question in self._waiting if self._may_ask(question)]
        if not ready:
            return  # a Refresh's is asked as soon as the main window is on screen and nothing else waits for an answer
        question = ready[0]  # a test's or Find's first: the person waits for it
        self._waiting.remove(question)
        if not self._wanted(question):
            QTimer.singleShot(0, self._next)  # its windows were closed: nothing to ask
            return
        self._asking = question
        trouble = question.trouble

        def looked(result: CertificateResult) -> None:
            if result.accepted_by_default:  # put right meanwhile: nothing to ask; the next attempt connects
                self._done([question], connect=False)
                return
            if result.accepted_for_run and not question.fresh:  # accepted meanwhile (a check the person started always asks) (a test that ran while it was asked): its work is done again, once per certificate
                again = (trouble.host.lower(), trouble.port, result.fingerprint) not in self._done_again
                self._done_again.add((trouble.host.lower(), trouble.port, result.fingerprint))
                self._done([question], connect=again)
                return
            if not self._may_ask(question):  # a question opened meanwhile, or the window went: asked once that is over
                self._asking = None
                self._waiting.append(question)  # behind the questions the person waits for
                QTimer.singleShot(0, self._next)
                return
            if not self._wanted(question):
                self._done([question], connect=False)
                return
            self._gather(question, result)

        def failed(error: ChronoMailError) -> None:
            self._done([question], connect=False)  # the server cannot be reached now: the next attempt meets it again

        self._backend.ask(CERTIFICATE_INSPECT, _look(trouble), looked, failed, owner=self)

    def _gather(self, question: _Question, result: CertificateResult) -> None:
        """The other ports of the same server name that wait to be asked about (incoming, outgoing, calendars on one machine): those that show
        this very certificate are answered with it, in one window; another certificate gets a window of its own."""
        host = question.trouble.host.lower()
        others = [waiting for waiting in self._waiting if waiting.trouble.host.lower() == host]
        group = [question]

        def look_next() -> None:
            if not others:
                for member in group[1:]:
                    if member in self._waiting:
                        self._waiting.remove(member)
                self._group = group  # asked now: a question met meanwhile joins them, never a second window
                if not any(self._wanted(member) for member in group):  # their windows closed while the other ports were read
                    self._done(group, connect=False)
                    return
                self._show(group, result)
                return
            other = others.pop(0)

            def looked(seen: CertificateResult) -> None:
                if seen.fingerprint == result.fingerprint and other in self._waiting:
                    group.append(other)
                look_next()

            self._backend.ask(CERTIFICATE_INSPECT, _look(other.trouble), looked, lambda error: look_next(), owner=self)

        look_next()

    def _show(self, group: list[_Question], result: CertificateResult) -> None:
        question = group[0]
        parent = question.parent if _open(question.parent) else self._window  # the test's window was closed: over the main window
        pins = {f"{member.trouble.host.strip().lower()}:{member.trouble.port}={result.fingerprint}" for member in group}
        kept = result.kept or any(pin in pins for member in group for given in member.kept for pin in given())  # the tick shows what is kept
        dialog = VerifyCertificateDialog(question.trouble.host, result, any(member.trouble.changed for member in group), parent, kept)
        self.open_dialog = dialog

        def answered(code: int) -> None:
            self.open_dialog = None
            dialog.deleteLater()
            if code == QDialog.DialogCode.Accepted:
                self._accept(group, result.fingerprint, dialog.remember, kept)
            else:
                for member in group:
                    if not member.by_hand:
                        self._declined.add((member.trouble.host.lower(), member.trouble.port))
                self._done(group, connect=False)

        dialog.finished.connect(answered)
        dialog.open()

    def _accept(self, group: list[_Question], fingerprint: str, remember: bool, was_kept: bool = False) -> None:
        """Connect: exactly this certificate, for every port of the server that showed it, until Chrono Mail closes; with Don't ask me again
        for good, with every account that met it; without it, one kept before is taken back (the tick showed it kept, D-299)."""
        change = remember or was_kept  # keep it, or take it back
        steps = [(member, account) for member in group for account in ([""] + (list(member.accounts) if change else []))]  # "": until closing

        def step(_: object = None) -> None:
            if not steps:
                self._done(group, connect=True)
                return
            member, account = steps.pop(0)
            trouble = member.trouble
            if not account:
                self._backend.ask(CERTIFICATE_TRUST_FOR_RUN, RunTrustParameters(trouble.host, trouble.port, fingerprint), kept, failed, owner=self)
                return
            command = ACCOUNT_TRUST_CERTIFICATE if remember else ACCOUNT_FORGET_CERTIFICATE
            self._backend.ask(command, TrustParameters(account, trouble.host, trouble.port, fingerprint), step, not_kept, owner=self)

        def kept(trusted: RunTrustResult) -> None:  # trusted until closing; the windows keep it, or take it back, as the tick says
            try:
                if change:
                    for member in group:
                        for keep, owner in member.keeps:
                            if f"{member.trouble.host.strip().lower()}:{member.trouble.port}=" in trusted.pin and (owner is None or _open(owner)):
                                keep(trusted.pin, remember)  # never into a window closed meanwhile
            finally:
                step()

        def not_kept(error: ChronoMailError) -> None:
            step()  # that account is gone meanwhile: the others still keep it, and the work goes on (it is trusted for this run)

        def failed(error: ChronoMailError) -> None:
            self._done(group, connect=False)

        step()

    def _done(self, group: list[_Question], connect: bool) -> None:
        self._asking = None
        self._group = []
        QTimer.singleShot(0, self._next)  # the next question comes whatever the work done again does
        if not connect:
            return
        done: list[Callable[[], None]] = []
        for retry in (retry for member in group for retry in member.retries):
            if retry.again in done:
                continue  # the same work met several ports of the server (a test of incoming and outgoing): done again once
            if retry.owner is None or _open(retry.owner):  # never the work of a window closed meanwhile
                done.append(retry.again)
                QTimer.singleShot(0, retry.again)  # each on its own: one that fails never keeps the others from being done

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        comes = (QEvent.Type.Show, QEvent.Type.WindowStateChange, QEvent.Type.WindowActivate)  # shown, no longer minimized, back after a question
        if watched is self._window and event.type() in comes and self._waiting:
            QTimer.singleShot(0, self._next)
        return False


_QUESTIONS: "weakref.WeakKeyDictionary[Backend, CertificateQuestions]" = weakref.WeakKeyDictionary()


def questions_for(backend: Backend) -> CertificateQuestions:
    """The one set of certificate questions of a program (of its backend): every window asks through it, so one window shows at a time."""
    found = _QUESTIONS.get(backend)
    if found is None:
        found = CertificateQuestions(backend)
        _QUESTIONS[backend] = found
    return found


def with_account(trouble: CertificateTrouble, account: str) -> CertificateTrouble:
    """The trouble as met for this account (its certificate is kept with it after Don't ask me again)."""
    return trouble if trouble.account else replace(trouble, account=account)
