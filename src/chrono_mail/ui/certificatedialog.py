from collections.abc import Callable

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMessageBox, QVBoxLayout, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.protocols.tls import readable
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import red_style

# --- Version ------------------------------------------------------------
__version__ = "certificatedialog.py 0.0.51"

# --- Version history ----------------------------------------------------
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.43: Initial dialog for accepting a server certificate



class CertificateDialog(QDialog):
    """Shows a server's certificate and why it is not trusted, and asks whether to accept exactly this certificate."""

    def __init__(self, host: str, info: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("The server's certificate")
        self.setMinimumWidth(560)
        self.fingerprint: str = str(getattr(info, "fingerprint"))
        layout = QVBoxLayout(self)
        problem = getattr(info, "problem")
        names = ", ".join(getattr(info, "names")) or "(none)"
        warning = (
            f"<span style='{red_style()}'><b>Chrono Mail does not trust this certificate</b></span>: {problem}.<br>"
            "Anyone between you and the server could present a certificate like this one. Accept it only if you know this is your server "
            "and the fingerprint below is the one the server's owner gave you."
            if problem else "This certificate is trusted by the usual checks: there is nothing to accept."
        )
        if getattr(info, "expired"):
            warning += f"<br><span style='{red_style()}'>The certificate has expired.</span>"
        label = QLabel(
            f"<b>{host}</b><br><br>{warning}<br><br>"
            f"<table cellspacing='4'><tr><td>Issued to</td><td><b>{getattr(info, 'subject')}</b></td></tr>"
            f"<tr><td>Issued by</td><td>{getattr(info, 'issuer')}</td></tr>"
            f"<tr><td>Names</td><td>{names}</td></tr>"
            f"<tr><td>Valid</td><td>{getattr(info, 'not_before')} to {getattr(info, 'not_after')}</td></tr>"
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


def inspect_and_ask(backend: Backend, parent: QWidget, host: str, port: int, security: str, kind: str, accepted: Callable[[str], None]) -> None:
    """Look at the certificate of the server and, when the person accepts it, call accepted(fingerprint)."""

    def got(result: object) -> None:
        dialog = CertificateDialog(host, result, parent)
        if dialog.exec() == CertificateDialog.DialogCode.Accepted:
            accepted(dialog.fingerprint)

    def failed(error: ChronoMailError) -> None:
        QMessageBox.warning(parent, "The server's certificate", f"The certificate of {host} could not be read: {error}")

    backend.call("certificate.inspect", {"host": host, "port": port, "security": security, "kind": kind}, got, failed, owner=parent)
