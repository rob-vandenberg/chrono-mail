import base64
import re
import ssl
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from chrono_mail.protocols.imap import decode_folder_name, encode_folder_name
from tests.fakeserver import Conversation, ScriptedServer

# --- Version ------------------------------------------------------------
__version__ = "fakeimap.py 0.0.32"

# --- Version history ----------------------------------------------------
# v0.0.32: IDLE
# v0.0.30: IMAP sync, store to server
# v0.0.28: Initial fake IMAP server

# A small stateful IMAP server in memory: folders with UIDs, flags and UIDVALIDITY, the commands our client uses, and ways to
# change what is on the server from the test and to cut the connection at an exact moment. The happy path against a real
# Dovecot is covered by the live tests.

Fault = Callable[[str, int], bool]


@dataclass
class FakeMessage:
    uid: int
    flags: set[str]
    raw: bytes


@dataclass
class FakeFolder:
    uid_validity: int = 1000
    uid_next: int = 1
    messages: list[FakeMessage] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)
    subscribed: bool = True


def _unquote(text: str) -> str:
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return re.sub(r"\\(.)", r"\1", text[1:-1])
    return text


def _split_arguments(text: str) -> list[str]:
    """Arguments separated by spaces; quoted strings and parenthesised groups stay together."""
    parts: list[str] = []
    current = ""
    depth = 0
    quoted = False
    index = 0
    while index < len(text):
        character = text[index]
        if quoted:
            current += character
            if character == "\\" and index + 1 < len(text):
                index += 1
                current += text[index]
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
            current += character
        elif character == "(":
            depth += 1
            current += character
        elif character == ")":
            depth -= 1
            current += character
        elif character == " " and depth == 0:
            if current:
                parts.append(current)
            current = ""
        else:
            current += character
        index += 1
    if current:
        parts.append(current)
    return parts


class FakeImapServer(ScriptedServer):
    def __init__(
        self,
        user: str = "user",
        password: str = "secret",
        starttls_context: ssl.SSLContext | None = None,
        implicit_tls: ssl.SSLContext | None = None,
        fault: Fault | None = None,
        uidplus: bool = True,
        move: bool = True,
        sasl_ir: bool = True,
        plain: bool = True,
        idle: bool = True,
    ) -> None:
        super().__init__(self._session, implicit_tls)
        self.folders: dict[str, FakeFolder] = {
            "INBOX": FakeFolder(),
            "Sent": FakeFolder(attributes=["\\Sent"]),
            "Drafts": FakeFolder(attributes=["\\Drafts"]),
            "Trash": FakeFolder(attributes=["\\Trash"]),
            "Junk": FakeFolder(attributes=["\\Junk"]),
        }
        self.commands: list[str] = []
        self.lock = threading.RLock()
        self._user, self._password, self._starttls, self._fault = user, password, starttls_context, fault
        self._uidplus, self._move, self._sasl_ir, self._plain = uidplus, move, sasl_ir, plain
        self.refuse_appends: bool = False
        self.idle_pushes: list[str] = []
        self._idle = idle

    # --- what a test can do to the server -------------------------------------------------------------

    def add(self, folder: str, raw: bytes, flags: set[str] | None = None) -> int:
        with self.lock:
            target = self.folders.setdefault(folder, FakeFolder())
            message = FakeMessage(target.uid_next, set(flags or ()), raw)
            target.uid_next += 1
            target.messages.append(message)
            return message.uid

    def push(self) -> None:
        """Tell the clients that are in IDLE that something happened."""
        with self.lock:
            self.idle_pushes.append("x")

    def remove(self, folder: str, uid: int) -> None:
        with self.lock:
            target = self.folders[folder]
            target.messages = [message for message in target.messages if message.uid != uid]

    def uids(self, folder: str) -> list[int]:
        with self.lock:
            return [message.uid for message in self.folders[folder].messages]

    def flags_of(self, folder: str, uid: int) -> set[str]:
        with self.lock:
            return next(set(message.flags) for message in self.folders[folder].messages if message.uid == uid)

    def raw_of(self, folder: str, uid: int) -> bytes:
        with self.lock:
            return next(message.raw for message in self.folders[folder].messages if message.uid == uid)

    def new_uid_validity(self, folder: str) -> None:
        """What a server does when it has to renumber a folder: a new UIDVALIDITY and new UIDs."""
        with self.lock:
            target = self.folders[folder]
            target.uid_validity += 1
            old = target.messages
            target.messages = []
            target.uid_next = 1
            for message in old:
                target.messages.append(FakeMessage(target.uid_next, message.flags, message.raw))
                target.uid_next += 1

    # --- the protocol -----------------------------------------------------------------------------------

    def _capabilities(self, secure: bool) -> str:
        words = ["IMAP4rev1", "SPECIAL-USE"]
        if self._idle:
            words.append("IDLE")
        if self._uidplus:
            words.append("UIDPLUS")
        if self._move:
            words.append("MOVE")
        if self._starttls is not None and not secure:
            words.append("STARTTLS")
            words.append("LOGINDISABLED")
        elif self._plain:
            words.append("AUTH=PLAIN")
            if self._sasl_ir:
                words.append("SASL-IR")
        return " ".join(words)

    def _session(self, conversation: Conversation) -> None:
        secure = isinstance(conversation.connection, ssl.SSLSocket)
        conversation.send_line(f"* OK [CAPABILITY {self._capabilities(secure)}] fake IMAP ready")
        authenticated = False
        selected: str | None = None
        counts: dict[str, int] = {}
        while True:
            raw = conversation.read_line()
            if raw is None:
                return
            text = raw.decode("utf-8", "replace")
            tag, _, rest = text.partition(" ")
            verb, _, argument = rest.partition(" ")
            verb = verb.upper()
            with self.lock:
                self.commands.append(f"{verb} ***" if verb in ("LOGIN", "AUTHENTICATE") else rest)
                counts[verb] = counts.get(verb, 0) + 1
            if self._fault is not None and self._fault(verb, counts[verb]):
                return
            if verb == "CAPABILITY":
                conversation.send_line(f"* CAPABILITY {self._capabilities(secure)}")
                conversation.send_line(f"{tag} OK done")
            elif verb == "NOOP":
                conversation.send_line(f"{tag} OK done")
            elif verb == "LOGOUT":
                conversation.send_line("* BYE bye")
                conversation.send_line(f"{tag} OK logged out")
                return
            elif verb == "STARTTLS" and self._starttls is not None and not secure:
                conversation.send_line(f"{tag} OK begin TLS")
                conversation.start_tls(self._starttls)
                secure = True
            elif verb == "LOGIN":
                parts = _split_arguments(argument)
                if len(parts) == 2 and not (self._starttls is not None and not secure) and _unquote(parts[0]) == self._user and _unquote(parts[1]) == self._password:
                    authenticated = True
                    conversation.send_line(f"{tag} OK logged in")
                else:
                    conversation.send_line(f"{tag} NO [AUTHENTICATIONFAILED] Authentication failed.")
            elif verb == "AUTHENTICATE":
                parts = argument.split(" ")
                if len(parts) < 2:
                    conversation.send_line("+ ")
                    answer = conversation.read_line()
                    initial = (answer or b"").decode()
                else:
                    initial = parts[1]
                try:
                    fields = base64.b64decode(initial).split(b"\x00")
                    good = len(fields) == 3 and fields[1].decode() == self._user and fields[2].decode() == self._password
                except ValueError:
                    good = False
                if good:
                    authenticated = True
                    conversation.send_line(f"{tag} OK logged in")
                else:
                    conversation.send_line(f"{tag} NO [AUTHENTICATIONFAILED] Authentication failed.")
            elif not authenticated:
                conversation.send_line(f"{tag} NO [AUTHENTICATIONREQUIRED] Log in first")
            elif verb == "IDLE" and selected is not None and self._idle:
                conversation.send_line("+ idling")
                while True:
                    status, line = conversation.read_line_within(0.05)
                    if status == "closed":
                        return
                    if status == "line":
                        conversation.send_line(f"{tag} OK idle terminated")
                        break
                    with self.lock:
                        if self.idle_pushes:
                            self.idle_pushes.clear()
                            conversation.send_line(f"* {len(self.folders[selected].messages)} EXISTS")
            else:
                selected = self._authenticated_command(conversation, tag, verb, argument, selected)

    def _folder_argument(self, text: str) -> str:
        return decode_folder_name(_unquote(text))

    def _authenticated_command(self, conversation: Conversation, tag: str, verb: str, argument: str, selected: str | None) -> str | None:
        with self.lock:
            if verb in ("LIST", "LSUB"):
                for name, folder in sorted(self.folders.items()):
                    if verb == "LSUB" and not folder.subscribed:
                        continue
                    attributes = " ".join(folder.attributes)
                    conversation.send_line(f'* {verb} ({attributes}) "/" "{encode_folder_name(name)}"')
                conversation.send_line(f"{tag} OK done")
            elif verb in ("SELECT", "EXAMINE"):
                name = self._folder_argument(argument)
                folder = self.folders.get(name)
                if folder is None:
                    conversation.send_line(f"{tag} NO [NONEXISTENT] no such folder")
                    return selected
                conversation.send_line(f"* {len(folder.messages)} EXISTS")
                conversation.send_line("* 0 RECENT")
                conversation.send_line("* FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)")
                conversation.send_line(f"* OK [UIDVALIDITY {folder.uid_validity}] UIDs valid")
                conversation.send_line(f"* OK [UIDNEXT {folder.uid_next}] Predicted next UID")
                conversation.send_line(f"{tag} OK [{'READ-ONLY' if verb == 'EXAMINE' else 'READ-WRITE'}] done")
                return name
            elif verb == "CREATE":
                name = self._folder_argument(argument)
                if name in self.folders:
                    conversation.send_line(f"{tag} NO [ALREADYEXISTS] exists")
                else:
                    self.folders[name] = FakeFolder(uid_validity=2000 + len(self.folders))
                    conversation.send_line(f"{tag} OK created")
            elif verb == "RENAME":
                old, new = (self._folder_argument(part) for part in _split_arguments(argument)[:2])
                if old not in self.folders or new in self.folders:
                    conversation.send_line(f"{tag} NO cannot rename")
                else:
                    self.folders[new] = self.folders.pop(old)
                    conversation.send_line(f"{tag} OK renamed")
            elif verb == "DELETE":
                name = self._folder_argument(argument)
                if name in self.folders and name != "INBOX":
                    del self.folders[name]
                    conversation.send_line(f"{tag} OK deleted")
                else:
                    conversation.send_line(f"{tag} NO cannot delete")
            elif verb == "SUBSCRIBE":
                folder = self.folders.get(self._folder_argument(argument))
                if folder is not None:
                    folder.subscribed = True
                conversation.send_line(f"{tag} OK done")
            elif verb == "APPEND":
                return self._append(conversation, tag, argument, selected)
            elif selected is None:
                conversation.send_line(f"{tag} BAD select a folder first")
            elif verb == "EXPUNGE":
                self._expunge(selected, None)
                conversation.send_line(f"{tag} OK expunged")
            elif verb == "UID":
                self._uid_command(conversation, tag, argument, selected)
            else:
                conversation.send_line(f"{tag} BAD unknown command")
        return selected

    def _append(self, conversation: Conversation, tag: str, argument: str, selected: str | None) -> str | None:
        match = re.match(r'^(".*?(?<!\\)"|\S+)(?: \((.*?)\))?(?: "[^"]*")? \{(\d+)\+?\}$', argument)
        if match is None:
            conversation.send_line(f"{tag} BAD cannot parse APPEND")
            return selected
        if self.refuse_appends:
            conversation.send_line("+ ready for the message")
            conversation.read_exactly(int(match.group(3)))
            conversation.read_line()
            conversation.send_line(f"{tag} NO [OVERQUOTA] mailbox full")
            return selected
        name = self._folder_argument(match.group(1))
        flags = set(match.group(2).split()) if match.group(2) else set()
        size = int(match.group(3))
        if name not in self.folders:
            conversation.send_line(f"{tag} NO [TRYCREATE] no such folder")
            return selected
        conversation.send_line("+ ready for the message")
        data = conversation.read_exactly(size)
        conversation.read_line()  # the line break after the literal
        if data is None:
            return selected
        uid = self.add(name, data, flags)
        suffix = f"[APPENDUID {self.folders[name].uid_validity} {uid}] " if self._uidplus else ""
        conversation.send_line(f"{tag} OK {suffix}appended")
        return selected

    def _uid_set(self, text: str, folder: FakeFolder) -> set[int]:
        highest = max((message.uid for message in folder.messages), default=0)
        chosen: set[int] = set()
        for part in text.split(","):
            first, _, last = part.partition(":")
            low = highest if first == "*" else int(first)
            high = low if not last else (highest if last == "*" else int(last))
            low, high = min(low, high), max(low, high)
            chosen.update(range(low, high + 1))
        return chosen

    def _expunge(self, name: str, uids: set[int] | None) -> None:
        folder = self.folders[name]
        folder.messages = [message for message in folder.messages if not ("\\Deleted" in message.flags and (uids is None or message.uid in uids))]

    def _uid_command(self, conversation: Conversation, tag: str, argument: str, selected: str) -> None:
        folder = self.folders[selected]
        subverb, _, rest = argument.partition(" ")
        subverb = subverb.upper()
        if subverb == "SEARCH":
            criteria = rest.upper()
            found = [m.uid for m in folder.messages if criteria == "ALL" or (criteria == "UNSEEN" and "\\Seen" not in m.flags) or (criteria == "SEEN" and "\\Seen" in m.flags)]
            if criteria.startswith("UID "):
                wanted = self._uid_set(criteria[4:], folder)
                found = [m.uid for m in folder.messages if m.uid in wanted]
            conversation.send_line("* SEARCH " + " ".join(str(uid) for uid in found) if found else "* SEARCH")
            conversation.send_line(f"{tag} OK done")
        elif subverb == "FETCH":
            parts = _split_arguments(rest)
            wanted = self._uid_set(parts[0], folder)
            items = parts[1].upper()
            for number, message in enumerate(folder.messages, 1):
                if message.uid not in wanted:
                    continue
                pieces = [f"UID {message.uid}"]
                if "FLAGS" in items:
                    pieces.append(f"FLAGS ({' '.join(sorted(message.flags))})")
                if "RFC822.SIZE" in items:
                    pieces.append(f"RFC822.SIZE {len(message.raw)}")
                if "BODY.PEEK[]" in items or "BODY[]" in items:
                    conversation.send(f"* {number} FETCH ({' '.join(pieces)} BODY[] {{{len(message.raw)}}}\r\n".encode() + message.raw + b")\r\n")
                else:
                    conversation.send_line(f"* {number} FETCH ({' '.join(pieces)})")
            conversation.send_line(f"{tag} OK done")
        elif subverb == "STORE":
            parts = _split_arguments(rest)
            wanted = self._uid_set(parts[0], folder)
            mode = parts[1].upper()
            names = set(parts[2].strip("()").split())
            for message in folder.messages:
                if message.uid in wanted:
                    if mode.startswith("+"):
                        message.flags |= names
                    elif mode.startswith("-"):
                        message.flags -= names
                    else:
                        message.flags = set(names)
            conversation.send_line(f"{tag} OK stored")
        elif subverb in ("COPY", "MOVE"):
            parts = _split_arguments(rest)
            target = self._folder_argument(parts[1])
            if target not in self.folders or (subverb == "MOVE" and not self._move):
                conversation.send_line(f"{tag} NO [TRYCREATE] no such folder" if subverb == "COPY" else f"{tag} BAD unknown command")
                return
            wanted = self._uid_set(parts[0], folder)
            copied = [(m.uid, self.add(target, m.raw, set(m.flags))) for m in folder.messages if m.uid in wanted]
            if subverb == "MOVE":
                folder.messages = [m for m in folder.messages if m.uid not in wanted]
            code = ""
            if self._uidplus and copied:
                code = f"[COPYUID {self.folders[target].uid_validity} {','.join(str(old) for old, _ in copied)} {','.join(str(new) for _, new in copied)}] "
            conversation.send_line(f"{tag} OK {code}done")
        elif subverb == "EXPUNGE":
            if not self._uidplus:
                conversation.send_line(f"{tag} BAD unknown command")
                return
            self._expunge(selected, self._uid_set(rest, folder))
            conversation.send_line(f"{tag} OK expunged")
        else:
            conversation.send_line(f"{tag} BAD unknown command")
