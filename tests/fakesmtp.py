import base64
import ssl
from dataclasses import dataclass

from tests.fakeserver import Conversation, ScriptedServer

# --- Version ------------------------------------------------------------
__version__ = "fakesmtp.py 0.0.10"

# --- Version history ----------------------------------------------------
# v0.0.10: Initial fake SMTP server


@dataclass(frozen=True, slots=True)
class Received:
    sender: str
    recipients: tuple[str, ...]
    data: bytes


class FakeSmtpServer(ScriptedServer):
    """A small SMTP server that keeps what it receives. The real server is covered by the live tests."""

    def __init__(
        self,
        user: str = "user",
        password: str = "secret",
        starttls_context: ssl.SSLContext | None = None,
        implicit_tls: ssl.SSLContext | None = None,
        size_limit: int = 1_000_000,
        refuse: frozenset[str] = frozenset(),
        eight_bit: bool = True,
        offer_auth: bool = True,
    ) -> None:
        super().__init__(self._session, implicit_tls)
        self.received: list[Received] = []
        self.commands: list[str] = []
        self._user, self._password, self._starttls = user, password, starttls_context
        self._size_limit, self._refuse, self._eight_bit, self._offer_auth = size_limit, refuse, eight_bit, offer_auth
        self._implicit = implicit_tls is not None

    def _session(self, conversation: Conversation) -> None:
        conversation.send_line("220 fake.example ESMTP ready")
        secure = self._implicit
        authenticated = False
        sender = ""
        recipients: list[str] = []
        while (raw := conversation.read_line()) is not None:
            text = raw.decode("utf-8", "replace")
            verb = text.split(" ", 1)[0].upper()
            self.commands.append("AUTH ***" if verb == "AUTH" else text)
            if verb in ("EHLO", "HELO"):
                capabilities = ["fake.example", f"SIZE {self._size_limit}", "PIPELINING"]
                if self._eight_bit:
                    capabilities.append("8BITMIME")
                if self._starttls is not None and not secure:
                    capabilities.append("STARTTLS")
                if self._offer_auth and (secure or self._starttls is None):
                    capabilities.append("AUTH PLAIN LOGIN")
                for position, line in enumerate(capabilities):
                    conversation.send_line(f"250{'-' if position < len(capabilities) - 1 else ' '}{line}")
            elif verb == "STARTTLS" and self._starttls is not None:
                conversation.send_line("220 go ahead")
                conversation.start_tls(self._starttls)
                secure = True
            elif verb == "AUTH":
                parts = text.split(" ")
                if parts[1].upper() == "PLAIN":
                    credentials: list[bytes] = base64.b64decode(parts[2]).split(b"\x00") if len(parts) > 2 else []
                    ok = len(credentials) == 3 and credentials[1].decode() == self._user and credentials[2].decode() == self._password
                else:  # LOGIN
                    conversation.send_line("334 " + base64.b64encode(b"Username:").decode())
                    name = base64.b64decode(conversation.read_line() or b"").decode("utf-8", "replace")
                    conversation.send_line("334 " + base64.b64encode(b"Password:").decode())
                    word = base64.b64decode(conversation.read_line() or b"").decode("utf-8", "replace")
                    ok = name == self._user and word == self._password
                authenticated = ok
                conversation.send_line("235 2.7.0 Authentication successful" if ok else "535 5.7.8 Authentication credentials invalid")
            elif verb == "MAIL":
                if self._offer_auth and not authenticated:
                    conversation.send_line("530 5.7.0 Authentication required")
                    continue
                sender = text.split(":", 1)[1].split()[0].strip("<>")
                recipients = []
                conversation.send_line("250 OK")
            elif verb == "RCPT":
                address = text.split(":", 1)[1].strip().strip("<>")
                if address in self._refuse:
                    conversation.send_line("550 5.1.1 No such user here")
                else:
                    recipients.append(address)
                    conversation.send_line("250 OK")
            elif verb == "DATA":
                if not recipients:
                    conversation.send_line("554 5.5.1 No valid recipients")
                    continue
                conversation.send_line("354 End data with <CR><LF>.<CR><LF>")
                lines: list[bytes] = []
                while (line := conversation.read_line()) is not None and line != b".":
                    lines.append(line[1:] if line.startswith(b"..") else line)
                self.received.append(Received(sender, tuple(recipients), b"\r\n".join(lines) + b"\r\n"))
                conversation.send_line("250 2.0.0 Queued")
            elif verb == "RSET":
                conversation.send_line("250 OK")
            elif verb == "QUIT":
                conversation.send_line("221 Bye")
                return
            else:
                conversation.send_line("502 5.5.2 Command not implemented")
