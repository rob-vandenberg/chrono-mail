from html import escape

from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QPushButton, QWidget

from chrono_mail.engine.commands_accounts import CertificateResult
from chrono_mail.protocols.tls import readable
from chrono_mail.ui import forms
from chrono_mail.ui.colours import red_style
from chrono_mail.ui.forms import part
from chrono_mail.ui.icons import bundled_icon

# --- Version ------------------------------------------------------------
__version__ = "certificatedialog.py 4.2.445"

# --- Version history ----------------------------------------------------
# v4.2.445: Verify certificate opens 700 pixels wide and is at least 500 (the owner's screen, forms.owner_width)
# v4.2.441: VerifyCertificateDialog, the question that opens by itself (UR-05, D-299); CertificateDialog only shows a certificate (Show
#           certificate), with Close: accepting is Connect; inspect_and_ask is gone with the Look at the server's certificate… buttons
# v4.0.376: built from its Qt Designer form (ui/forms/certificate.ui, D-276); its smallest width follows the size factor
# v3.0.339: every text from the certificate and the server is escaped: a forged certificate can no longer draw rows of its own (RV-01)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.272: inspect_and_ask is a typed call; security and kind typed, kind https for a calendar and contacts server
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.43: Initial dialog for accepting a server certificate



CHANGED_REASON: str = "Its certificate has changed since you accepted it."


class CertificateDialog(QDialog):
    """Shows a server's certificate and why it is not trusted (Show certificate in the Verify certificate window). Nothing is accepted here."""

    def __init__(self, host: str, info: CertificateResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.fingerprint: str = info.fingerprint
        form = forms.fill(self, "certificate")  # D-276
        problem = escape(info.problem or "")  # every text here comes from the server's certificate: shown as text, never as layout (RV-01)
        names = escape(", ".join(info.names)) or "(none)"
        warning = (
            f"<span style='{red_style()}'><b>Chrono Mail does not trust this certificate</b></span>: {problem}.<br>"
            "Anyone between you and the server could present a certificate like this one. Connect only if you know this is your server "
            "and the fingerprint below is the one the server's owner gave you."
            if problem else "This certificate is trusted by the usual checks."
        )
        if info.expired:
            warning += f"<br><span style='{red_style()}'>The certificate has expired.</span>"
        label = part(form, QLabel, "details")
        label.setText(
            f"<b>{escape(host)}</b><br><br>{warning}<br><br>"
            f"<table cellspacing='4'><tr><td>Issued to</td><td><b>{escape(info.subject)}</b></td></tr>"
            f"<tr><td>Issued by</td><td>{escape(info.issuer)}</td></tr>"
            f"<tr><td>Names</td><td>{names}</td></tr>"
            f"<tr><td>Valid</td><td>{escape(info.not_before)} to {escape(info.not_after)}</td></tr>"
            f"<tr><td>SHA-256</td><td><tt>{readable(self.fingerprint)}</tt></td></tr></table>"
        )
        label.setTextInteractionFlags(label.textInteractionFlags().TextSelectableByMouse)
        box = part(form, QDialogButtonBox, "buttons")
        box.addButton(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        forms.smallest(self, 560)  # its texts are never cut off, also at the largest size


def _sentence(text: str) -> str:
    """A reason from the certificate check as a sentence: a capital first, a full stop last."""
    text = text.strip()
    if not text:
        return "The certificate could not be verified."
    return text[0].upper() + text[1:] + ("" if text.endswith(".") else ".")


class VerifyCertificateDialog(QDialog):
    """Asks whether to connect to a server whose certificate could not be verified (UR-05, D-299): Connect accepts exactly this certificate,
    until Chrono Mail closes or, with Don't ask me again, for good; Do not connect closes it; Show certificate shows the certificate."""

    def __init__(self, host: str, info: CertificateResult, changed: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.host: str = host
        self.info: CertificateResult = info
        form = forms.fill(self, "verify_certificate")  # D-276
        sign = part(form, QLabel, "sign")
        size = self.fontMetrics().height() * 3
        sign.setPixmap(bundled_icon("dialog-warning").pixmap(size, size))
        heading = part(form, QLabel, "heading")
        heading.setText(f"<b>Chrono Mail can't verify the identity of “{escape(host)}”</b>")  # the name is shown as text, never as layout (RV-01)
        reason = part(form, QLabel, "reason")
        reason.setText(escape(CHANGED_REASON if changed else _sentence(info.problem or "")))
        question = part(form, QLabel, "question")
        question.setText(
            f"You could be connecting to a server that is pretending to be “{escape(host)}”. Would you like to connect to the server anyway?"
        )
        expires = escape(info.not_after) + (" (expired)" if info.expired else "")
        details = part(form, QLabel, "details")
        details.setText(
            f"<table cellspacing='4'><tr><td>Subject</td><td>{escape(info.subject)}</td></tr>"
            f"<tr><td>Issued by</td><td>{escape(info.issuer)}</td></tr>"
            f"<tr><td>Expiration date</td><td>{expires}</td></tr></table>"
        )
        self.dont_ask: QCheckBox = part(form, QCheckBox, "dontAsk")
        box = part(form, QDialogButtonBox, "buttons")
        self.connect_button: QPushButton = box.addButton("Connect", QDialogButtonBox.ButtonRole.AcceptRole)
        self.refuse_button: QPushButton = box.addButton("Do not connect", QDialogButtonBox.ButtonRole.RejectRole)
        self.show_button: QPushButton = box.addButton("Show certificate", QDialogButtonBox.ButtonRole.ActionRole)
        self.refuse_button.setDefault(True)  # Enter never connects by itself: connecting is a choice made on purpose
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        self.show_button.clicked.connect(self._show_certificate)
        forms.open_at(self, forms.owner_width(500), forms.owner_width(700))  # the owner: 500 at least, opened at 700 pixels on his screen

    @property
    def remember(self) -> bool:
        """Don't ask me again: counts only with Connect."""
        return self.dont_ask.isChecked()

    def _show_certificate(self) -> None:
        CertificateDialog(self.host, self.info, self).open()
