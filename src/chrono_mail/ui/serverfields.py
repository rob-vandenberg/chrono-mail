"""The incoming and outgoing server boxes of an account (Server, Security, Port, User name, Password, and the tick for one login), the same in
Settings > Accounts > Add and in Edit > Servers (R-05). A port follows the security choice while it holds the standard port of the old choice;
a port the person typed stays. Also the one way to show the result of Test connection."""

from dataclasses import dataclass

from PySide6.QtWidgets import QCheckBox, QComboBox, QGroupBox, QLabel, QLineEdit, QSpinBox, QWidget

from chrono_mail.engine.commands_accounts import AccountCheckResult, AccountSummary
from chrono_mail.core import AccountKind
from chrono_mail.engine.commands_accounts import AccountCheckParameters, Kind, Policy, Security
from chrono_mail.ui import forms
from chrono_mail.ui.colours import green, red
from chrono_mail.ui.forms import part
from chrono_mail.ui.passwordfield import make_password_field

# --- Version ------------------------------------------------------------
__version__ = "serverfields.py 4.2.441"

# --- Version history ----------------------------------------------------
# v4.2.441: Security has a fourth choice, "TLS, certificate not checked (weak)", with the ports of TLS (OR-6, D-299)
# v4.2.437: the two password fields have the eye (UR-03, D-296)
# v4.0.376: the two boxes are built from their Qt Designer form (ui/forms/server_fields.ui, D-276) as one part (widget) that Add account
#           and Edit account place in their own forms; the Tab order follows the boxes from top to bottom
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.272: mail_server_kind: the incoming server's kind for the certificate look
# v1.9.271: R-19: check_parameters gives account.check's typed parameters; security_of, mail_kind_of, policy_of
# v1.1.243: Initial: the server boxes of Add account and the account editor, built once (R-05)

SECURITY_CHOICES: tuple[tuple[str, str], ...] = (  # from strong to none: the person is guided to security, never forced (D-299)
    ("tls", "TLS (encrypted from the start)"), ("starttls", "STARTTLS (upgrade to encrypted)"),
    ("tls-unchecked", "TLS, certificate not checked (weak)"), ("none", "None (not encrypted)"),
)
POP3_PORTS: dict[str, int] = {"tls": 995, "starttls": 110, "tls-unchecked": 995, "none": 110}
IMAP_PORTS: dict[str, int] = {"tls": 993, "starttls": 143, "tls-unchecked": 993, "none": 143}
OUTGOING_PORTS: dict[str, int] = {"tls": 465, "starttls": 587, "tls-unchecked": 465, "none": 25}
SAME_LOGIN_TEXT: str = "Use the same user name and password as for incoming mail"


def incoming_ports(kind: AccountKind) -> dict[str, int]:
    return IMAP_PORTS if kind is AccountKind.IMAP else POP3_PORTS


def incoming_title(kind: AccountKind) -> str:
    return "Incoming mail (IMAP)" if kind is AccountKind.IMAP else "Incoming mail (POP3)"


def kind_of(account: AccountSummary) -> AccountKind:
    """The kind of an account summary (pop3 when it says nothing)."""
    try:
        return AccountKind(account.kind)
    except ValueError:
        return AccountKind.POP3


@dataclass(frozen=True, slots=True)
class StartValues:
    """What the boxes show when they open: an account being edited, or the suggestions of a new one."""

    incoming_host: str = ""
    incoming_port: int = POP3_PORTS["tls"]
    incoming_security: str = "tls"
    username: str = ""
    outgoing_host: str = ""
    outgoing_port: int = OUTGOING_PORTS["starttls"]
    outgoing_security: str = "starttls"
    outgoing_username: str = ""
    same_login: bool = True


def start_values_of(account: AccountSummary) -> StartValues:
    return StartValues(
        account.incoming_host, account.incoming_port, account.incoming_security, account.incoming_username,
        account.outgoing_host, account.outgoing_port, account.outgoing_security, account.outgoing_username,
        account.outgoing_same_login,
    )


class ServerFields:
    """The two server boxes. editing: the passwords are "New password", empty meaning the stored one (never asked for again)."""

    def __init__(self, parent: QWidget, kind: AccountKind, editing: bool, start: StartValues = StartValues()) -> None:
        self.kind: AccountKind = kind
        self.widget: QWidget = QWidget(parent)  # both boxes, one under the other, at the "servers" place of a window's form
        form = forms.fill(self.widget, "server_fields")  # D-276
        self.incoming_group: QGroupBox = part(form, QGroupBox, "incomingGroup")
        self.outgoing_group: QGroupBox = part(form, QGroupBox, "outgoingGroup")
        self.username: QLineEdit = part(form, QLineEdit, "username")
        self.username.setText(start.username)
        self.password: QLineEdit = part(form, QLineEdit, "password")
        self.incoming_host: QLineEdit = part(form, QLineEdit, "incomingHost")
        self.incoming_host.setText(start.incoming_host)
        self.incoming_port: QSpinBox = part(form, QSpinBox, "incomingPort")
        self.incoming_security: QComboBox = _security_combo(part(form, QComboBox, "incomingSecurity"), start.incoming_security)
        self.outgoing_username: QLineEdit = part(form, QLineEdit, "outgoingUsername")
        self.outgoing_username.setText(start.outgoing_username)
        self.outgoing_password: QLineEdit = part(form, QLineEdit, "outgoingPassword")
        self.outgoing_host: QLineEdit = part(form, QLineEdit, "outgoingHost")
        self.outgoing_host.setText(start.outgoing_host)
        self.outgoing_port: QSpinBox = part(form, QSpinBox, "outgoingPort")
        self.outgoing_security: QComboBox = _security_combo(part(form, QComboBox, "outgoingSecurity"), start.outgoing_security)
        self.same_login: QCheckBox = part(form, QCheckBox, "sameLogin")
        self.same_login.setChecked(start.same_login)
        for port, value in ((self.incoming_port, start.incoming_port), (self.outgoing_port, start.outgoing_port)):
            port.setRange(1, 65535)
            port.setValue(value)
        for password, label in ((self.password, "passwordLabel"), (self.outgoing_password, "outgoingPasswordLabel")):
            make_password_field(password)
            if editing:
                password.setPlaceholderText("(unchanged)")
                part(form, QLabel, label).setText("New password")
        self.incoming_group.setTitle(incoming_title(kind))
        self._incoming_was: str = start.incoming_security
        self._outgoing_was: str = start.outgoing_security
        self.incoming_security.currentIndexChanged.connect(self._incoming_security_changed)
        self.outgoing_security.currentIndexChanged.connect(self._outgoing_security_changed)
        self.username.textChanged.connect(self._follow_login)
        self.same_login.toggled.connect(self._same_login_changed)
        self._same_login_changed()

    # --- what follows what -----------------------------------------------------------------------------------------

    def _incoming_security_changed(self) -> None:
        now = str(self.incoming_security.currentData())
        ports = incoming_ports(self.kind)
        if self.incoming_port.value() == ports[self._incoming_was]:  # the standard port of the old choice: the new one's; a typed port stays
            self.incoming_port.setValue(ports[now])
        self._incoming_was = now

    def _outgoing_security_changed(self) -> None:
        now = str(self.outgoing_security.currentData())
        if self.outgoing_port.value() == OUTGOING_PORTS[self._outgoing_was]:
            self.outgoing_port.setValue(OUTGOING_PORTS[now])
        self._outgoing_was = now

    def set_kind(self, kind: AccountKind) -> None:
        """POP3 or IMAP (Add account): the title follows, and the port while it is the standard one of the old kind."""
        security = str(self.incoming_security.currentData())
        if self.incoming_port.value() == incoming_ports(self.kind)[security]:
            self.incoming_port.setValue(incoming_ports(kind)[security])
        self.kind = kind
        self.incoming_group.setTitle(incoming_title(kind))

    def _same_login_changed(self) -> None:
        """Ticked: the outgoing login is the incoming one, shown greyed; unticked: its own."""
        same = self.same_login.isChecked()
        self.outgoing_username.setEnabled(not same)
        self.outgoing_password.setEnabled(not same)
        if same:
            self.outgoing_username.setText(self.username.text())
            self.outgoing_password.clear()

    def _follow_login(self, text: str) -> None:
        if self.same_login.isChecked():
            self.outgoing_username.setText(text)

    # --- what is sent ----------------------------------------------------------------------------------------------

    def endpoint(self, which: str) -> tuple[str, int, str]:
        """The host, port and security as they stand now for the incoming or the outgoing server."""
        if which == "incoming":
            return self.incoming_host.text().strip(), self.incoming_port.value(), str(self.incoming_security.currentData())
        return self.outgoing_host.text().strip(), self.outgoing_port.value(), str(self.outgoing_security.currentData())

    def check_parameters(self, account: str = "", trusted: tuple[str, ...] = ()) -> AccountCheckParameters:
        """The servers and logins as account.check takes them (account.add takes the same, D-204); the outgoing login only when it is
        its own. An empty password is left empty: for an account that exists, the engine takes the stored one."""
        own_login = not self.same_login.isChecked()
        return AccountCheckParameters(
            username=self.username.text().strip(), incoming_host=self.incoming_host.text().strip(), outgoing_host=self.outgoing_host.text().strip(),
            password=self.password.text(), incoming_port=self.incoming_port.value(), incoming_security=security_of(self.incoming_security.currentData()),
            outgoing_port=self.outgoing_port.value(), outgoing_security=security_of(self.outgoing_security.currentData()),
            outgoing_username=self.outgoing_username.text().strip() if own_login else None, outgoing_password=(self.outgoing_password.text() or None) if own_login else None,
            kind=mail_kind_of(self.kind), trusted_certificate=trusted, account=account,
        )


SECURITIES: dict[str, Security] = {"tls": "tls", "starttls": "starttls", "tls-unchecked": "tls-unchecked", "none": "none"}
MAIL_KINDS: dict[AccountKind, Kind] = {AccountKind.POP3: "pop3", AccountKind.IMAP: "imap"}
DELETE_POLICIES: dict[str, Policy] = {"keep": "keep", "delete_after_download": "delete_after_download", "delete_after_days": "delete_after_days", "delete_when_deleted_locally": "delete_when_deleted_locally"}


def security_of(chosen: object) -> Security:
    """A Security choice's data as the engine's value (the boxes hold only these four)."""
    return SECURITIES.get(str(chosen), "tls")


def mail_kind_of(kind: AccountKind) -> Kind:
    """A mail account's kind as the engine's value (the server boxes are only for POP3 and IMAP)."""
    return MAIL_KINDS.get(kind, "pop3")


def policy_of(chosen: object) -> Policy:
    """A delete policy choice's data as the engine's value."""
    return DELETE_POLICIES.get(str(chosen), "keep")


def test_result_text(result: AccountCheckResult) -> str:
    """The result of Test connection: a green ✓ or a red ✗ line per server."""

    def line(ok: object, name: str, message: object) -> str:
        colour, mark = (green(), "✓") if ok else (red(), "✗")
        return f"<span style='color:{colour}'><b>{mark} {name}:</b> {str(message).replace('<', '&lt;')}</span>"

    return "<br>".join((
        line(result.incoming_ok, "Incoming", result.incoming_message),
        line(result.outgoing_ok, "Outgoing", result.outgoing_message),
    ))


def _security_combo(combo: QComboBox, current: str) -> QComboBox:
    for value, label in SECURITY_CHOICES:
        combo.addItem(label, value)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    return combo
