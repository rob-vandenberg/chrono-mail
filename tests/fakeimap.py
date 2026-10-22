import base64
import re
import ssl
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from chrono_mail.protocols.imap import decode_folder_name, encode_folder_name
from tests.fakeserver import Conversation, ScriptedServer

# --- Version ------------------------------------------------------------
__version__ = "fakeimap.py 1.1.248"

# --- Version history ----------------------------------------------------
# v1.1.248: CONDSTORE and QRESYNC on request (ENABLE, HIGHESTMODSEQ, CHANGEDSINCE, VANISHED): every change of a message counts up the folder's
#           change counter; set_flags changes marks as another program would (R-09)
# v1.1.237: LIST of one name lists only that folder; refuse_single_list: a LIST of one name answers NO (no confirmation either way)
# v1.1.227: refuse_stores, refuse_moves, refuse_creates: a server that says a lasting no
# v0.5.138: PERMANENTFLAGS; keywords kept (\\*) or dropped (keywords=False), as a server without keyword support
# v0.5.123: RENAME takes the folders below along and leaves the subscriptions on the old names, as real servers do; UNSUBSCRIBE
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
    modseq: int = 0  # the folder's change counter when the message last changed


@dataclass
class FakeFolder:
    uid_validity: int = 1000
    uid_next: int = 1
    messages: list[FakeMessage] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)
    subscribed: bool = True
    highest_modseq: int = 1
    vanished: dict[int, int] = field(default_factory=dict)  # removed UID -> the change counter of its removal

    def bump(self) -> int:
        self.highest_modseq += 1
        return self.highest_modseq

    def drop(self, keep: Callable[[FakeMessage], bool]) -> None:
        for message in self.messages:
            if not keep(message):
                self.vanished[message.uid] = self.bump()
        self.messages = [message for message in self.messages if keep(message)]


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
        keywords: bool = True,
        condstore: bool = False,
        qresync: bool = False,
    ) -> None:
        super().__init__(self._session, implicit_tls)
        self.keywords: bool = keywords  # False: a server that keeps no keywords (PERMANENTFLAGS without \\*)
        self.condstore: bool = condstore or qresync  # the server can tell what changed (RFC 7162); qresync also what was removed
        self.qresync: bool = qresync
        self.enabled_for: dict[int, set[str]] = {}  # per connection: what ENABLE switched on
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
        self.refuse_stores: bool = False  # UID STORE answers NO (marks not allowed)
        self.refuse_moves: bool = False  # UID MOVE and UID COPY answer NO
        self.refuse_creates: bool = False  # CREATE answers NO
        self.refuse_single_list: bool = False  # a LIST of one name answers NO
        self.stale_subscriptions: set[str] = set()  # subscribed names of folders that no longer exist
        self.idle_pushes: list[str] = []
        self._idle = idle

    # --- what a test can do to the server -------------------------------------------------------------

    def add(self, folder: str, raw: bytes, flags: set[str] | None = None) -> int:
        with self.lock:
            target = self.folders.setdefault(folder, FakeFolder())
            message = FakeMessage(target.uid_next, set(flags or ()), raw, target.bump())
            target.uid_next += 1
            target.messages.append(message)
            return message.uid

    def push(self) -> None:
        """Tell the clients that are in IDLE that something happened."""
        with self.lock:
            self.idle_pushes.append("x")

    def remove(self, folder: str, uid: int) -> None:
        with self.lock:
            self.folders[folder].drop(lambda message: message.uid != uid)

    def set_flags(self, folder: str, uid: int, flags: set[str]) -> None:
        """Change a message's marks, as another program would."""
        with self.lock:
            target = self.folders[folder]
            for message in target.messages:
                if message.uid == uid:
                    message.flags = set(flags)
                    message.modseq = target.bump()

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
            target.vanished = {}
            for message in old:
                target.messages.append(FakeMessage(target.uid_next, message.flags, message.raw, target.bump()))
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
        if self.condstore:
            words.extend(["ENABLE", "CONDSTORE"] + (["QRESYNC"] if self.qresync else []))
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
        enabled = self.enabled_for[id(conversation)] = set()
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
            elif verb == "ENABLE" and self.condstore and authenticated:
                wanted = {word.upper() for word in argument.split()} & ({"CONDSTORE", "QRESYNC"} if self.qresync else {"CONDSTORE"})
                enabled |= wanted | ({"CONDSTORE"} if "QRESYNC" in wanted else set())
                conversation.send_line("* ENABLED " + " ".join(sorted(wanted)) if wanted else "* ENABLED")
                conversation.send_line(f"{tag} OK enabled")
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
                pattern = self._folder_argument(_split_arguments(argument)[1]) if len(_split_arguments(argument)) > 1 else "*"
                if pattern != "*" and self.refuse_single_list:
                    conversation.send_line(f"{tag} NO [UNAVAILABLE] try later")
                    return selected
                for name, folder in sorted(self.folders.items()):
                    if verb == "LSUB" and not folder.subscribed:
                        continue
                    if pattern != "*" and name != pattern:
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
                conversation.send_line(f"* OK [PERMANENTFLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft{' \\*' if self.keywords else ''})] kept")
                conversation.send_line(f"* OK [UIDVALIDITY {folder.uid_validity}] UIDs valid")
                conversation.send_line(f"* OK [UIDNEXT {folder.uid_next}] Predicted next UID")
                if "CONDSTORE" in self.enabled_for.get(id(conversation), set()):
                    conversation.send_line(f"* OK [HIGHESTMODSEQ {folder.highest_modseq}] changes")
                conversation.send_line(f"{tag} OK [{'READ-ONLY' if verb == 'EXAMINE' else 'READ-WRITE'}] done")
                return name
            elif verb == "CREATE":
                name = self._folder_argument(argument)
                if self.refuse_creates:
                    conversation.send_line(f"{tag} NO [NOPERM] not allowed")
                elif name in self.folders:
                    conversation.send_line(f"{tag} NO [ALREADYEXISTS] exists")
                else:
                    self.folders[name] = FakeFolder(uid_validity=2000 + len(self.folders))
                    conversation.send_line(f"{tag} OK created")
            elif verb == "RENAME":
                old, new = (self._folder_argument(part) for part in _split_arguments(argument)[:2])
                if old not in self.folders or new in self.folders:
                    conversation.send_line(f"{tag} NO cannot rename")
                else:
                    for name in [name for name in self.folders if name == old or name.startswith(old + "/")]:
                        moved = self.folders[new + name[len(old) :]] = self.folders.pop(name)  # the folders below it go along (RFC 3501)
                        if moved.subscribed:  # a subscription is a name: it stays on the old one
                            self.stale_subscriptions.add(name)
                            moved.subscribed = False
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
            elif verb == "UNSUBSCRIBE":
                name = self._folder_argument(argument)
                self.stale_subscriptions.discard(name)
                if name in self.folders:
                    self.folders[name].subscribed = False
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
        self.folders[name].drop(lambda message: not ("\\Deleted" in message.flags and (uids is None or message.uid in uids)))

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
            modifier = parts[2].strip("()").upper().split() if len(parts) > 2 else []
            since = int(modifier[1]) if len(modifier) > 1 and modifier[0] == "CHANGEDSINCE" else None
            if since is not None and "VANISHED" in modifier and "QRESYNC" in self.enabled_for.get(id(conversation), set()):
                gone = sorted(uid for uid, modseq in folder.vanished.items() if modseq > since and (parts[0] == "1:*" or uid in wanted))
                if gone:
                    conversation.send_line("* VANISHED (EARLIER) " + ",".join(str(uid) for uid in gone))
            for number, message in enumerate(folder.messages, 1):
                if message.uid not in wanted or (since is not None and message.modseq <= since):
                    continue
                pieces = [f"UID {message.uid}"]
                if "FLAGS" in items:
                    pieces.append(f"FLAGS ({' '.join(sorted(message.flags))})")
                if "RFC822.SIZE" in items:
                    pieces.append(f"RFC822.SIZE {len(message.raw)}")
                if since is not None:
                    pieces.append(f"MODSEQ ({message.modseq})")
                if "BODY.PEEK[]" in items or "BODY[]" in items:
                    conversation.send(f"* {number} FETCH ({' '.join(pieces)} BODY[] {{{len(message.raw)}}}\r\n".encode() + message.raw + b")\r\n")
                else:
                    conversation.send_line(f"* {number} FETCH ({' '.join(pieces)})")
            conversation.send_line(f"{tag} OK done")
        elif subverb == "STORE":
            if self.refuse_stores:
                conversation.send_line(f"{tag} NO [NOPERM] read only")
                return
            parts = _split_arguments(rest)
            wanted = self._uid_set(parts[0], folder)
            mode = parts[1].upper()
            names = set(parts[2].strip("()").split())
            if not self.keywords:
                names = {name for name in names if name.startswith("\\")}
            for message in folder.messages:
                if message.uid in wanted:
                    if mode.startswith("+"):
                        message.flags |= names
                    elif mode.startswith("-"):
                        message.flags -= names
                    else:
                        message.flags = set(names)
                    message.modseq = folder.bump()
            conversation.send_line(f"{tag} OK stored")
        elif subverb in ("COPY", "MOVE"):
            if self.refuse_moves:
                conversation.send_line(f"{tag} NO [NOPERM] not allowed")
                return
            parts = _split_arguments(rest)
            target = self._folder_argument(parts[1])
            if target not in self.folders or (subverb == "MOVE" and not self._move):
                conversation.send_line(f"{tag} NO [TRYCREATE] no such folder" if subverb == "COPY" else f"{tag} BAD unknown command")
                return
            wanted = self._uid_set(parts[0], folder)
            copied = [(m.uid, self.add(target, m.raw, set(m.flags))) for m in folder.messages if m.uid in wanted]
            if subverb == "MOVE":
                folder.drop(lambda m: m.uid not in wanted)
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
