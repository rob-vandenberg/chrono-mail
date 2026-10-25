from collections.abc import Callable
from html import escape
from typing import Literal

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMessageBox, QVBoxLayout, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_accounts import CERTIFICATE_INSPECT, CertificateParameters, CertificateResult, Security
from chrono_mail.protocols.tls import readable
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import red_style

# --- Version ------------------------------------------------------------
__version__ = "certificatedialog.py 3.0.339"

# --- Version history ----------------------------------------------------
# v3.0.339: every text from the certificate and the server is escaped: a forged certificate can no longer draw rows of its own (RV-01)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.272: inspect_and_ask is a typed call; security and kind typed, kind https for a calendar and contacts server
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.43: Initial dialog for accepting a server certificate



class CertificateDialog(QDialog):
    """Shows a server's certificate and why it is not trusted, and asks whether to accept exactly this certificate."""

    def __init__(self, host: str, info: CertificateResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("The server's certificate")
        self.setMinimumWidth(560)
        self.fingerprint: str = info.fingerprint
        layout = QVBoxLayout(self)
        problem = escape(info.problem or "")  # every text here comes from the server's certificate: shown as text, never as layout (RV-01)
        names = escape(", ".join(info.names)) or "(none)"
        warning = (
            f"<span style='{red_style()}'><b>Chrono Mail does not trust this certificate</b></span>: {problem}.<br>"
            "Anyone between you and the server could present a certificate like this one. Accept it only if you know this is your server "
            "and the fingerprint below is the one the server's owner gave you."
            if problem else "This certificate is trusted by the usual checks: there is nothing to accept."
        )
        if info.expired:
            warning += f"<br><span style='{red_style()}'>The certificate has expired.</span>"
        label = QLabel(
            f"<b>{escape(host)}</b><br><br>{warning}<br><br>"
            f"<table cellspacing='4'><tr><td>Issued to</td><td><b>{escape(info.subject)}</b></td></tr>"
            f"<tr><td>Issued by</td><td>{escape(info.issuer)}</td></tr>"
            f"<tr><td>Names</td><td>{names}</td></tr>"
            f"<tr><td>Valid</td><td>{escape(info.not_before)} to {escape(info.not_after)}</td></tr>"
            f"<tr><td>SHA-256</td><td><tt>{readable(self.fingerprint)}</tt></td></tr></table>",
            self,
        )
        label.setWordWrap(True)
        label.setTextInteractionFlags(label.textInteractionFlags().TextSelectableByMouse)
        layout.addWidget(label)
        box = QDialogButtonBox(self)
        self.accept_button = box.addButton("Accept this certificate", QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.accept_button.setEnabled(bool(problem))
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)


def inspect_and_ask(
    backend: Backend, parent: QWidget, host: str, port: int, security: Security, kind: Literal["pop3", "imap", "smtp", "https"], accepted: Callable[[str], None],
) -> None:
    """Look at the certificate of the server and, when the person accepts it, call accepted(fingerprint)."""

    def got(result: CertificateResult) -> None:
        dialog = CertificateDialog(host, result, parent)
        if dialog.exec() == CertificateDialog.DialogCode.Accepted:
            accepted(dialog.fingerprint)

    def failed(error: ChronoMailError) -> None:
        QMessageBox.warning(parent, "The server's certificate", f"The certificate of {host} could not be read: {error}")

    backend.ask(CERTIFICATE_INSPECT, CertificateParameters(host, port, security, kind), got, failed, owner=parent)
