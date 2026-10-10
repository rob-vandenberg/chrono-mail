import ssl
import threading
from collections.abc import Callable

from tests.fakeserver import Conversation, ScriptedServer

# --- Version ------------------------------------------------------------
__version__ = "fakepop3.py 0.0.10"

# --- Version history ----------------------------------------------------
# v0.0.10: Initial fake POP3 server

# A small stateful POP3 server for tests that need to change what is on the server and to cut the
# connection at an exact moment. The happy path against a real Dovecot is covered by the live tests.

Fault = Callable[[str, int], bool]


class FakePop3Server(ScriptedServer):
    def __init__(
        self,
        messages: dict[str, bytes],
        user: str = "user",
        password: str = "secret",
        stls_context: ssl.SSLContext | None = None,
        implicit_tls: ssl.SSLContext | None = None,
        fault: Fault | None = None,
    ) -> None:
        super().__init__(self._session, implicit_tls)
        self.messages: dict[str, bytes] = dict(messages)  # unique id -> raw message
        self.commands: list[str] = []
        self.refuse_retr: set[str] = set()  # unique ids the server refuses to send
        self._user, self._password, self._stls, self._fault = user, password, stls_context, fault
        self._lock = threading.Lock()

    def _session(self, conversation: Conversation) -> None:
        conversation.send_line("+OK fake POP3 ready")
        authenticated = False
        given_user = ""
        deleted: set[str] = set()
        counts: dict[str, int] = {}
        while True:
            raw = conversation.read_line()
            if raw is None:
                return  # connection dropped without QUIT: nothing is deleted
            text = raw.decode("utf-8", "replace")
            verb, _, argument = text.partition(" ")
            verb = verb.upper()
            with self._lock:
                self.commands.append("PASS ***" if verb == "PASS" else text)
                counts[verb] = counts.get(verb, 0) + 1
                numbered = list(enumerate(self.messages.items(), 1))  # numbers never change during a session
                visible = [(number, uid, message) for number, (uid, message) in numbered if uid not in deleted]
            if self._fault is not None and self._fault(verb, counts[verb]):
                return
            if verb == "CAPA":
                conversation.send_line("+OK")
                for line in ("TOP", "UIDL", "USER") + (("STLS",) if self._stls is not None else ()):
                    conversation.send_line(line)
                conversation.send_line(".")
            elif verb == "STLS" and self._stls is not None:
                conversation.send_line("+OK begin TLS")
                conversation.start_tls(self._stls)
            elif verb == "USER":
                given_user = argument
                conversation.send_line("+OK")
            elif verb == "PASS":
                if given_user == self._user and argument == self._password:
                    authenticated = True
                    conversation.send_line("+OK logged in")
                else:
                    conversation.send_line("-ERR [AUTH] Authentication failed.")
            elif not authenticated:
                conversation.send_line("-ERR [AUTH] Authentication required")
            elif verb == "STAT":
                conversation.send_line(f"+OK {len(visible)} {sum(len(message) for _, _, message in visible)}")
            elif verb == "LIST":
                conversation.send_line("+OK")
                for number, _, message in visible:
                    conversation.send_line(f"{number} {len(message)}")
                conversation.send_line(".")
            elif verb == "UIDL":
                conversation.send_line("+OK")
                for number, uid, _ in visible:
                    conversation.send_line(f"{number} {uid}")
                conversation.send_line(".")
            elif verb in ("RETR", "TOP"):
                number = int(argument.split()[0]) if argument.split() and argument.split()[0].isdigit() else 0
                chosen = next(((uid, message) for n, uid, message in visible if n == number), None)
                if chosen is None:
                    conversation.send_line("-ERR no such message")
                    continue
                if verb == "RETR" and chosen[0] in self.refuse_retr:
                    conversation.send_line("-ERR [SYS/TEMP] cannot read that message")
                    continue
                message = chosen[1]
                if verb == "TOP":
                    message = message.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                conversation.send_line("+OK")
                self._send_message(conversation, message)
            elif verb == "DELE":
                number = int(argument) if argument.isdigit() else 0
                chosen_id = next((uid for n, uid, _ in visible if n == number), None)
                if chosen_id is None:
                    conversation.send_line("-ERR no such message")
                else:
                    deleted.add(chosen_id)
                    conversation.send_line("+OK marked")
            elif verb == "RSET":
                deleted.clear()
                conversation.send_line("+OK")
            elif verb == "NOOP":
                conversation.send_line("+OK")
            elif verb == "QUIT":
                with self._lock:
                    for uid in deleted:
                        self.messages.pop(uid, None)
                conversation.send_line("+OK bye")
                return
            else:
                conversation.send_line("-ERR unknown command")

    @staticmethod
    def _send_message(conversation: Conversation, message: bytes) -> None:
        lines = message.split(b"\r\n")
        if lines and lines[-1] == b"":
            lines.pop()
        for line in lines:
            conversation.send((b"." + line if line.startswith(b".") else line) + b"\r\n")
        conversation.send(b".\r\n")
