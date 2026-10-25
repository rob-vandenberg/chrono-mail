"""The incoming and outgoing server boxes of an account (Server, Security, Port, User name, Password, and the tick for one login), the same in
Settings > Accounts > Add and in Edit > Servers (R-05). A port follows the security choice while it holds the standard port of the old choice;
a port the person typed stays. Also the one way to show the result of Test connection."""

from dataclasses import dataclass
from typing import Literal

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QGroupBox, QLineEdit, QSpinBox, QWidget

from chrono_mail.engine.commands_accounts import AccountCheckResult, AccountSummary
from chrono_mail.core import AccountKind
from chrono_mail.engine.commands_accounts import AccountCheckParameters, Kind, Policy, Security
from chrono_mail.ui.colours import green, red

# --- Version ------------------------------------------------------------
__version__ = "serverfields.py 1.9.275"

# --- Version history ----------------------------------------------------
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.272: mail_server_kind: the incoming server's kind for the certificate look
# v1.9.271: R-19: check_parameters gives account.check's typed parameters; security_of, mail_kind_of, policy_of
# v1.1.243: Initial: the server boxes of Add account and the account editor, built once (R-05)

SECURITY_CHOICES: tuple[tuple[str, str], ...] = (("tls", "TLS (encrypted from the start)"), ("starttls", "STARTTLS (upgrade to encrypted)"), ("none", "None (not encrypted)"))
POP3_PORTS: dict[str, int] = {"tls": 995, "starttls": 110, "none": 110}
IMAP_PORTS: dict[str, int] = {"tls": 993, "starttls": 143, "none": 143}
OUTGOING_PORTS: dict[str, int] = {"tls": 465, "starttls": 587, "none": 25}
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
        self.username = QLineEdit(start.username, parent)
        self.password = QLineEdit(parent)
        self.incoming_host = QLineEdit(start.incoming_host, parent)
        self.incoming_port = QSpinBox(parent)
        self.incoming_security = _security_combo(start.incoming_security)
        self.outgoing_username = QLineEdit(start.outgoing_username, parent)
        self.outgoing_password = QLineEdit(parent)
        self.outgoing_host = QLineEdit(start.outgoing_host, parent)
        self.outgoing_port = QSpinBox(parent)
        self.outgoing_security = _security_combo(start.outgoing_security)
        self.same_login = QCheckBox(SAME_LOGIN_TEXT, parent)
        self.same_login.setChecked(start.same_login)
        for port, value in ((self.incoming_port, start.incoming_port), (self.outgoing_port, start.outgoing_port)):
            port.setRange(1, 65535)
            port.setValue(value)
        password_label = "New password" if editing else "Password"
        for password in (self.password, self.outgoing_password):
            password.setEchoMode(QLineEdit.EchoMode.Password)
            if editing:
                password.setPlaceholderText("(unchanged)")
        self.incoming_group = _group(parent, incoming_title(kind), self.incoming_host, self.incoming_security, self.incoming_port, self.username, self.password, password_label)
        self.outgoing_group = _group(
            parent, "Outgoing mail (SMTP)", self.outgoing_host, self.outgoing_security, self.outgoing_port, self.outgoing_username, self.outgoing_password, password_label,
            self.same_login,
        )
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


SECURITIES: dict[str, Security] = {"tls": "tls", "starttls": "starttls", "none": "none"}
MAIL_KINDS: dict[AccountKind, Kind] = {AccountKind.POP3: "pop3", AccountKind.IMAP: "imap"}
DELETE_POLICIES: dict[str, Policy] = {"keep": "keep", "delete_after_download": "delete_after_download", "delete_after_days": "delete_after_days", "delete_when_deleted_locally": "delete_when_deleted_locally"}


def security_of(chosen: object) -> Security:
    """A Security choice's data as the engine's value (the boxes hold only these three)."""
    return SECURITIES.get(str(chosen), "tls")


def mail_kind_of(kind: AccountKind) -> Kind:
    """A mail account's kind as the engine's value (the server boxes are only for POP3 and IMAP)."""
    return MAIL_KINDS.get(kind, "pop3")


def mail_server_kind(kind: str) -> Literal["pop3", "imap"]:
    """The incoming server's kind as certificate.inspect takes it (a mail account is POP3 or IMAP)."""
    return "imap" if kind == "imap" else "pop3"


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


def _security_combo(current: str) -> QComboBox:
    combo = QComboBox()
    for value, label in SECURITY_CHOICES:
        combo.addItem(label, value)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    return combo


def _group(
    parent: QWidget, title: str, host: QLineEdit, security: QComboBox, port: QSpinBox, user: QLineEdit, password: QLineEdit, password_label: str,
    same: QCheckBox | None = None,
) -> QGroupBox:
    group = QGroupBox(title, parent)
    form = QFormLayout(group)
    form.addRow("Server", host)
    form.addRow("Security", security)
    form.addRow("Port", port)
    if same is not None:
        form.addRow("", same)
    form.addRow("User name", user)
    form.addRow(password_label, password)
    return group
