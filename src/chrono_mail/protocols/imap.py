import base64
import re
import socket
import ssl
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

from chrono_mail.core import AuthenticationError, ConnectionSecurity, ProtocolError, ServerSettings
from chrono_mail.protocols.lineconnection import LineConnection

# --- Version ------------------------------------------------------------
__version__ = "imap.py 3.6.349"

# --- Version history ----------------------------------------------------
# v3.6.349: a refusal keeps what the server sent before it; uid_fetch_what_comes gives it (RV-202); a refused SELECT leaves no folder
#           open here either
# v3.4.344: a body the server sends as NIL is not sent, never an empty message (RV-51)
# v1.8.263: R-16: sending and reading lines go through LineConnection (protocols/lineconnection.py), shared with POP3
# v1.1.248: What changed in a folder (RFC 7162): enable_changes switches on QRESYNC or CONDSTORE where the server has them, uid_changes asks for the
#           messages changed since a change counter and, with QRESYNC, the UIDs removed since then (VANISHED) (R-09)
# v1.1.247: COPYUID with sets of UIDs (a move or copy of many messages at once): AppendResult.uids pairs each old UID with its new one (R-08)
# v1.1.238: uid_expunge never removes other messages: without UIDPLUS it refuses (a plain EXPUNGE removed every message marked deleted in the
#           folder, also another program's); uid_move without MOVE removes the original only with UIDPLUS, else leaves it marked deleted (R-35)
# v1.1.237: folder_exists: the server asked about one folder by its exact name (an answer without it confirms it is not there, R-30)
# v0.5.138: Tags as keywords (tag_to_keyword, keyword_to_tag); PERMANENTFLAGS of a selected folder; keywords in STORE and APPEND
# v0.5.123: UNSUBSCRIBE
# v0.0.32: IDLE
# v0.0.30: uid_move tells the new UID
# v0.0.28: Initial IMAP client

# The standard library's imaplib has no limits on the size of lines and literals, which a hostile server can use to
# exhaust memory. This client has its own, explicit caps (D-048, like the POP3 client of D-027).

_LITERAL_MARK: str = "\x00"  # replaces a literal inside a response line while it is read; NUL itself is refused in lines
_ATOM_END: frozenset[str] = frozenset(' (){"\r\n')
_NUMBER: re.Pattern[str] = re.compile(r"[0-9]{1,12}")
_STATUS_WORDS: frozenset[str] = frozenset({"OK", "NO", "BAD", "BYE", "PREAUTH"})


class ImapRefused(ProtocolError):
    """The server answered a command with NO or BAD. What it sent before that answer is kept (a FETCH may have sent part of the messages)."""

    def __init__(self, text: str, before: tuple["Response", ...] = ()) -> None:
        super().__init__(text)
        self.before: tuple[Response, ...] = before


@dataclass(frozen=True, slots=True)
class ImapLimits:
    maximum_line_bytes: int = 1024 * 1024
    maximum_literal_bytes: int = 256 * 1024 * 1024
    maximum_response_bytes: int = 512 * 1024 * 1024
    maximum_tokens: int = 2_000_000
    maximum_depth: int = 20
    connect_timeout_seconds: float = 20.0
    read_timeout_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class Response:
    """One response of the server. tag is '*' for untagged responses and '+' for a continuation request."""

    tag: str
    name: str  # OK, NO, BAD, BYE, PREAUTH, or CAPABILITY, LIST, SEARCH, EXISTS, FETCH, ...
    number: int | None = None
    items: tuple[object, ...] = ()  # atoms and strings as str, literals as bytes, NIL as None, lists as tuples
    code: tuple[object, ...] = ()  # the [CODE ...] of a status response
    text: str = ""


@dataclass(frozen=True, slots=True)
class SelectInfo:
    exists: int
    uid_validity: int
    uid_next: int
    highest_modseq: int | None
    flags: frozenset[str]
    read_only: bool
    permanent_flags: frozenset[str] | None = None  # PERMANENTFLAGS; \\* in it: keywords (tags) are kept

    @property
    def keeps_keywords(self) -> bool:
        return self.permanent_flags is not None and "\\*" in self.permanent_flags


@dataclass(frozen=True, slots=True)
class FolderEntry:
    name: str  # decoded from modified UTF-7
    delimiter: str | None
    attributes: frozenset[str] = frozenset()  # lower case, for example '\\sent'


@dataclass(frozen=True, slots=True)
class FetchedItem:
    uid: int
    flags: frozenset[str] = frozenset()
    size: int | None = None
    data: bytes | None = None
    modseq: int | None = None


@dataclass(frozen=True, slots=True)
class FolderChanges:
    """What changed in the selected folder since a change counter (RFC 7162): the messages changed or new, with their marks and size, and the
    UIDs removed since then (None: not known, because the server has no QRESYNC or named more than can be read)."""

    items: tuple[FetchedItem, ...]
    vanished: frozenset[int] | None


MAXIMUM_UID_SET: int = 100_000  # a set of UIDs from the server longer than this is not read (no allocation sized by input)


@dataclass(frozen=True, slots=True)
class AppendResult:
    uid_validity: int | None = None
    uid: int | None = None  # the new UID when one message was put there
    uids: dict[int, int] = field(default_factory=dict[int, int])  # a move or copy of several: each old UID and its new one (COPYUID)


def uid_set(text: str) -> list[int]:
    """The UIDs of a set as the server writes it ('4:6,9' is 4, 5, 6, 9), in the order written (RFC 4315 pairs two sets by that order).
    Anything else, or more than MAXIMUM_UID_SET, is a ProtocolError."""
    found: list[int] = []
    for part in text.split(","):
        first, separator, last = part.partition(":")
        if not first.isdigit() or (separator and not last.isdigit()):
            raise ProtocolError("The server sent an invalid set of UIDs")
        start, end = int(first), int(last or first)
        step = 1 if end >= start else -1
        if len(found) + abs(end - start) + 1 > MAXIMUM_UID_SET:
            raise ProtocolError("The server sent too many UIDs at once")
        found.extend(range(start, end + step, step))
    return found


@dataclass
class _Reader:
    """Reads one response line, literals included, into text with literal markers and the literals themselves."""

    text: str = ""
    literals: list[bytes] = field(default_factory=list)


def _printable(text: str, limit: int = 200) -> str:
    """Server text made safe to show: ASCII only, no control characters, cut short."""
    return "".join(character if 32 <= ord(character) < 127 else "?" for character in text[:limit])


# --- modified UTF-7 (RFC 3501 section 5.1.3) for folder names -----------------------------------------------------


def encode_folder_name(name: str) -> str:
    """A folder name as it goes over the wire."""
    out: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            raw = "".join(pending).encode("utf-16-be")
            out.append("&" + base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",") + "-")
            pending.clear()

    for character in name:
        if 0x20 <= ord(character) <= 0x7E:
            flush()
            out.append("&-" if character == "&" else character)
        else:
            pending.append(character)
    flush()
    return "".join(out)


_KEYWORD_SAFE: re.Pattern[str] = re.compile(r"[A-Za-z0-9_.-]")
MAXIMUM_KEYWORD: int = 200
NOT_TAGS: frozenset[str] = frozenset({"junk", "nonjunk", "notjunk", "forwarded", "mdnsent", "phishing"})  # marks of other programs, never tags


def tag_to_keyword(tag: str) -> str:
    """A tag as an IMAP keyword (an atom): letters, digits, _ . - as they are; every other character (a space, an accent,
    $ or &) in the modified UTF-7 form IMAP uses for folder names, so it can be read back exactly. Never starts with $."""
    out: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            raw = "".join(pending).encode("utf-16-be")
            out.append("&" + base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",") + "-")
            pending.clear()

    for character in " ".join(tag.split()):
        if _KEYWORD_SAFE.fullmatch(character):
            flush()
            out.append(character)
        else:
            pending.append(character)
    flush()
    keyword = "".join(out)
    if not keyword or len(keyword) > MAXIMUM_KEYWORD:
        raise ProtocolError("A tag cannot be written as a keyword of the server (too long or empty)")
    return keyword


def keyword_to_tag(keyword: str) -> str | None:
    """The tag a keyword stands for; None for a system flag, a keyword of the server or another program ($Forwarded,
    Junk ...), or something that is not one of ours."""
    if not keyword or keyword.startswith(("\\", "$")) or keyword.casefold() in NOT_TAGS:
        return None
    if any(character != "&" and character not in "+," and not _KEYWORD_SAFE.fullmatch(character) for character in keyword if character != "-"):
        return None
    name = decode_folder_name(keyword)
    cleaned = " ".join(name.split())
    return cleaned if cleaned and cleaned.isprintable() and len(cleaned) <= 100 else None


def decode_folder_name(wire: str) -> str:
    """A folder name from the wire. Anything that is not valid modified UTF-7 is kept as it is (a server may be sloppy)."""
    out: list[str] = []
    position = 0
    while position < len(wire):
        character = wire[position]
        if character != "&":
            out.append(character)
            position += 1
            continue
        end = wire.find("-", position)
        if end == -1:
            out.append(wire[position:])
            break
        chunk = wire[position + 1 : end]
        if not chunk:
            out.append("&")
        else:
            try:
                padded = chunk.replace(",", "/") + "=" * (-len(chunk) % 4)
                out.append(base64.b64decode(padded, validate=True).decode("utf-16-be"))
            except (ValueError, UnicodeDecodeError):
                out.append(wire[position : end + 1])
        position = end + 1
    return "".join(out)


def quote(text: str) -> str:
    """A string as an IMAP quoted string. Line breaks and NUL cannot be sent at all."""
    if any(character in text for character in "\r\n\x00"):
        raise ProtocolError("A command may not contain a line break")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class ImapClient:
    """An IMAP4rev1 client over plain, STARTTLS or implicit TLS. Certificates are always verified."""

    def __init__(self, settings: ServerSettings, password: str, limits: ImapLimits = ImapLimits(), ssl_context: ssl.SSLContext | None = None) -> None:
        self._settings: ServerSettings = settings
        self._password: str = password
        self._limits: ImapLimits = limits
        self._context: ssl.SSLContext = ssl_context if ssl_context is not None else ssl.create_default_context()
        self._connection: LineConnection = LineConnection(limits.maximum_line_bytes, limits.read_timeout_seconds)
        self._counter: int = 0
        self._tokens: int = 0
        self.capabilities: frozenset[str] = frozenset()
        self.enabled: frozenset[str] = frozenset()  # the extensions switched on with ENABLE (CONDSTORE, QRESYNC)
        self.selected: str | None = None

    # --- connection ------------------------------------------------------------------------------

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None) -> None:
        self.close()

    def connect(self) -> None:
        """Connect, secure the connection and log in."""
        settings = self._settings
        try:
            raw = socket.create_connection((settings.host, settings.port), timeout=self._limits.connect_timeout_seconds)
            raw.settimeout(self._limits.read_timeout_seconds)
            if settings.security is ConnectionSecurity.TLS:
                raw = self._context.wrap_socket(raw, server_hostname=settings.host)
            self._connection.socket = raw
            greeting = self._read_response()
            if greeting.tag != "*" or greeting.name not in ("OK", "PREAUTH"):
                raise ProtocolError("The server did not greet us with OK")
            self.capabilities = self._capabilities_from(greeting) or self._read_capabilities()
            if settings.security is ConnectionSecurity.STARTTLS:
                if "STARTTLS" not in self.capabilities:
                    raise ProtocolError("The server does not offer STARTTLS")
                self._command("STARTTLS")
                if self._connection.waiting():
                    raise ProtocolError("The server sent data before the secure connection was set up")
                self._connection.socket = self._context.wrap_socket(raw, server_hostname=settings.host)
                self.capabilities = self._read_capabilities()
            if greeting.name != "PREAUTH":
                self._login()
        except ProtocolError:
            self.close()
            raise
        except ssl.SSLCertVerificationError as error:
            self.close()
            raise ProtocolError(f"The server's certificate could not be verified: {error.verify_message}") from None
        except (OSError, ssl.SSLError) as error:
            self.close()
            raise ProtocolError(f"Connection failed: {error.strerror or type(error).__name__}") from None

    def close(self) -> None:
        self._connection.close()
        self.selected = None

    def logout(self) -> None:
        try:
            self._command("LOGOUT", allow_bye=True)
        except ProtocolError:
            pass
        self.close()

    def _login(self) -> None:
        if "LOGINDISABLED" in self.capabilities and "AUTH=PLAIN" not in self.capabilities:
            raise AuthenticationError("The server does not allow logging in over this connection (it needs an encrypted connection)")
        try:
            if "AUTH=PLAIN" in self.capabilities:
                secret = base64.b64encode(f"\x00{self._settings.username}\x00{self._password}".encode()).decode("ascii")
                self._command(f"AUTHENTICATE PLAIN {secret}" if "SASL-IR" in self.capabilities else "AUTHENTICATE PLAIN", continuation=secret)
            else:
                self._command(f"LOGIN {quote(self._settings.username)} {quote(self._password)}")
        except ImapRefused as error:
            raise AuthenticationError(f"The server refused the login: {error}") from None
        self.capabilities = self._read_capabilities()  # they may differ once logged in

    # --- low level -------------------------------------------------------------------------------

    def _read_response(self) -> Response:
        """One full response (a line, and for every {n} at its end the n bytes of the literal and the rest of the line)."""
        reader = _Reader()
        total = 0
        while True:
            line = self._connection.read_line()
            total += len(line)
            if b"\x00" in line:
                raise ProtocolError("The server sent an invalid character")
            try:
                reader.text += line.decode("utf-8")
            except UnicodeDecodeError:
                reader.text += line.decode("latin-1")  # servers may send 8-bit text in the human readable part
            match = re.search(r"\{([0-9]{1,12})\+?\}$", reader.text)
            if match is None:
                break
            size = int(match.group(1))
            if size > self._limits.maximum_literal_bytes:
                raise ProtocolError("The server sent a message part that is larger than allowed")
            total += size
            if total > self._limits.maximum_response_bytes:
                raise ProtocolError("The server's response is larger than allowed")
            reader.literals.append(self._connection.read_exactly(size))
            reader.text = reader.text[: match.start()] + _LITERAL_MARK + str(len(reader.literals) - 1) + _LITERAL_MARK
        return self._parse(reader)

    # --- parsing ---------------------------------------------------------------------------------

    def _parse(self, reader: _Reader) -> Response:
        text = reader.text
        self._tokens = 0
        if text.startswith("+"):
            return Response("+", "CONTINUE", text=_printable(text[1:]).strip())
        first, _, rest = text.partition(" ")
        if not first:
            raise ProtocolError("The server sent an empty response")
        tag = first
        if tag != "*" and not re.fullmatch(r"[A-Za-z0-9]{1,32}", tag):
            raise ProtocolError("The server sent a response with an invalid tag")
        word, _, remainder = rest.partition(" ")
        if _NUMBER.fullmatch(word) and tag == "*":
            number: int | None = int(word)
            name, _, remainder = remainder.partition(" ")
        else:
            number, name = None, word
        name = name.upper()
        if not name:
            raise ProtocolError("The server sent a response without a name")
        if name in _STATUS_WORDS:
            return self._parse_status(tag, name, number, remainder, reader.literals)
        items = self._parse_items(remainder, reader.literals)
        return Response(tag, name, number, items)

    def _parse_status(self, tag: str, name: str, number: int | None, rest: str, literals: list[bytes]) -> Response:
        code: tuple[object, ...] = ()
        if rest.startswith("["):
            end = rest.find("]")
            if end == -1:
                raise ProtocolError("The server sent an invalid response code")
            code = self._parse_items(rest[1:end].replace("[", "(").replace("]", ")"), literals)
            rest = rest[end + 1 :].lstrip()
        return Response(tag, name, number, (), code, _printable(rest).strip())

    def _parse_items(self, text: str, literals: list[bytes]) -> tuple[object, ...]:
        items, position = self._parse_list(text, 0, literals, 0, top=True)
        if position < len(text.rstrip()):
            raise ProtocolError("The server sent an invalid response")
        return items

    def _count_token(self) -> None:
        self._tokens += 1
        if self._tokens > self._limits.maximum_tokens:
            raise ProtocolError("The server's response has too many parts")

    def _parse_list(self, text: str, position: int, literals: list[bytes], depth: int, top: bool = False) -> tuple[tuple[object, ...], int]:
        if depth > self._limits.maximum_depth:
            raise ProtocolError("The server's response is nested too deeply")
        items: list[object] = []
        length = len(text)
        while position < length:
            character = text[position]
            if character == " ":
                position += 1
            elif character == "(":
                inner, position = self._parse_list(text, position + 1, literals, depth + 1)
                items.append(inner)
                self._count_token()
            elif character == ")":
                if top:
                    raise ProtocolError("The server sent an unbalanced response")
                return tuple(items), position + 1
            elif character == '"':
                value: list[str] = []
                position += 1
                while True:
                    if position >= length:
                        raise ProtocolError("The server sent an unfinished quoted string")
                    current = text[position]
                    if current == "\\" and position + 1 < length:
                        value.append(text[position + 1])
                        position += 2
                    elif current == '"':
                        position += 1
                        break
                    else:
                        value.append(current)
                        position += 1
                items.append("".join(value))
                self._count_token()
            elif character == _LITERAL_MARK:
                end = text.find(_LITERAL_MARK, position + 1)
                if end == -1 or not text[position + 1 : end].isdigit() or int(text[position + 1 : end]) >= len(literals):
                    raise ProtocolError("The server sent an invalid literal")
                items.append(literals[int(text[position + 1 : end])])
                position = end + 1
                self._count_token()
            else:
                start = position
                while position < length and text[position] not in _ATOM_END and text[position] != _LITERAL_MARK:
                    position += 1
                atom = text[start:position]
                items.append(None if atom.upper() == "NIL" else atom)
                self._count_token()
        if not top:
            raise ProtocolError("The server sent an unbalanced response")
        return tuple(items), position

    # --- commands --------------------------------------------------------------------------------

    def _next_tag(self) -> str:
        self._counter += 1
        return f"C{self._counter}"

    def _command(self, line: str, literal: bytes | None = None, continuation: str | None = None, allow_bye: bool = False) -> list[Response]:
        """Send a command and collect the untagged responses until its tagged completion. NO and BAD raise ImapRefused."""
        if any(character in line for character in "\r\n\x00"):
            raise ProtocolError("A command may not contain a line break")
        tag = self._next_tag()
        if literal is not None:
            self._connection.send(f"{tag} {line} {{{len(literal)}}}\r\n".encode())
            while True:
                reply = self._read_response()
                if reply.tag == "+":
                    break
                if reply.tag == tag:
                    raise ImapRefused(f"The server refused the command: {reply.text}")
            self._connection.send(literal + b"\r\n")
        else:
            self._connection.send(f"{tag} {line}\r\n".encode())
        collected: list[Response] = []
        while True:
            reply = self._read_response()
            if reply.tag == "+":
                if continuation is None:
                    raise ProtocolError("The server asked for more input than expected")
                self._connection.send(continuation.encode() + b"\r\n")
                continuation = None
                continue
            if reply.tag == "*":
                if reply.name == "BYE" and not allow_bye:
                    raise ProtocolError(f"The server ended the connection: {reply.text}")
                collected.append(reply)
                continue
            if reply.tag != tag:
                raise ProtocolError("The server answered a command we did not send")
            if reply.name == "OK":
                collected.append(reply)
                return collected
            raise ImapRefused(reply.text or reply.name, tuple(collected))

    @staticmethod
    def _capabilities_from(response: Response) -> frozenset[str]:
        words = [item.upper() for item in response.code[1:] if isinstance(item, str)] if response.code and str(response.code[0]).upper() == "CAPABILITY" else []
        return frozenset(words)

    def _read_capabilities(self) -> frozenset[str]:
        found: set[str] = set()
        for response in self._command("CAPABILITY"):
            if response.name == "CAPABILITY":
                found.update(item.upper() for item in response.items if isinstance(item, str))
            found.update(self._capabilities_from(response))
        return frozenset(found)

    def noop(self) -> None:
        self._command("NOOP")

    def list_folders(self, subscribed_only: bool = False) -> tuple[FolderEntry, ...]:
        """The folders of the account (or only the subscribed ones), with their delimiter and attributes."""
        folders: list[FolderEntry] = []
        verb = "LSUB" if subscribed_only else "LIST"
        for response in self._command(f'{verb} "" "*"'):
            if response.name not in ("LIST", "LSUB") or len(response.items) < 3:
                continue
            attributes, delimiter, name = response.items[0], response.items[1], response.items[2]
            if not isinstance(attributes, tuple) or not isinstance(name, (str, bytes)):
                raise ProtocolError("The server sent an invalid folder entry")
            text = name.decode("utf-8", errors="replace") if isinstance(name, bytes) else name
            folders.append(FolderEntry(decode_folder_name(text), delimiter if isinstance(delimiter, str) else None, frozenset(str(item).lower() for item in attributes)))
        return tuple(folders)

    def select(self, folder: str, read_only: bool = False) -> SelectInfo:
        verb = "EXAMINE" if read_only else "SELECT"
        self.selected = None  # a refused SELECT or EXAMINE leaves no folder open (RFC 3501 6.3.1), never the one open before
        responses = self._command(f"{verb} {quote(encode_folder_name(folder))}")
        exists, validity, next_uid, modseq = 0, 0, 1, None
        flags: frozenset[str] = frozenset()
        permanent: frozenset[str] | None = None
        mode_read_only = read_only
        for response in responses:
            if response.name == "EXISTS" and response.number is not None:
                exists = response.number
            elif response.name == "FLAGS" and response.items and isinstance(response.items[0], tuple):
                flags = frozenset(str(item) for item in response.items[0])
            if response.code:
                key = str(response.code[0]).upper()
                value = response.code[1] if len(response.code) > 1 else None
                if key == "UIDVALIDITY":
                    validity = self._number(value)
                elif key == "UIDNEXT":
                    next_uid = self._number(value)
                elif key == "HIGHESTMODSEQ":
                    modseq = self._number(value)
                elif key == "READ-ONLY":
                    mode_read_only = True
                elif key == "PERMANENTFLAGS" and isinstance(value, tuple):
                    permanent = frozenset(str(item) for item in value)
        if validity <= 0:
            raise ProtocolError("The server did not tell the folder's UIDVALIDITY")
        self.selected = folder
        return SelectInfo(exists, validity, next_uid, modseq, flags, mode_read_only, permanent)

    @staticmethod
    def _number(value: object) -> int:
        if not isinstance(value, str) or _NUMBER.fullmatch(value) is None:
            raise ProtocolError("The server sent an invalid number")
        return int(value)

    def uid_search(self, criteria: str = "ALL") -> tuple[int, ...]:
        found: list[int] = []
        for response in self._command(f"UID SEARCH {criteria}"):
            if response.name == "SEARCH":
                found.extend(self._number(item) for item in response.items)
        return tuple(found)

    def enable_changes(self) -> frozenset[str]:
        """Ask the server to tell what changed in a folder (RFC 7162): QRESYNC where it has it (CONDSTORE comes with it), else CONDSTORE. Returns
        what is switched on; empty when the server has neither (every folder is then listed in full). A refusal switches nothing on."""
        wanted = "QRESYNC" if "QRESYNC" in self.capabilities else "CONDSTORE" if "CONDSTORE" in self.capabilities else ""
        if not wanted or "ENABLE" not in self.capabilities:
            return self.enabled
        found: set[str] = set()
        try:
            responses = self._command(f"ENABLE {wanted}")
        except ImapRefused:
            return self.enabled
        for response in responses:
            if response.name == "ENABLED":
                found.update(item.upper() for item in response.items if isinstance(item, str))
        if "QRESYNC" in found:
            found.add("CONDSTORE")  # QRESYNC switches on CONDSTORE too (RFC 7162 3.2.3)
        self.enabled = frozenset(found & {"CONDSTORE", "QRESYNC"})
        return self.enabled

    def uid_changes(self, changed_since: int) -> FolderChanges:
        """The messages of the selected folder changed or added since this change counter, with their marks and size, and with QRESYNC the UIDs
        removed since then. Needs enable_changes first."""
        vanished_too = "QRESYNC" in self.enabled
        responses = self._command(f"UID FETCH 1:* (UID FLAGS RFC822.SIZE) (CHANGEDSINCE {changed_since}{' VANISHED' if vanished_too else ''})")
        vanished: set[int] | None = set() if vanished_too else None
        for response in responses:
            if response.name != "VANISHED" or vanished is None:
                continue
            for item in response.items:
                if isinstance(item, str):  # the set; (EARLIER) is a list
                    try:
                        vanished.update(uid_set(item))
                    except ProtocolError:  # more than can be read, or not a set: the removals are not known
                        vanished = None
                        break
        return FolderChanges(self._fetched(responses), frozenset(vanished) if vanished is not None else None)

    def uid_fetch(self, uids: str, items: str = "(UID FLAGS RFC822.SIZE)", changed_since: int | None = None) -> tuple[FetchedItem, ...]:
        """FETCH for a UID set such as '5', '5:9' or '1:*'. The items decide what is filled in: FLAGS, RFC822.SIZE, BODY[] (data)."""
        suffix = f" (CHANGEDSINCE {changed_since})" if changed_since is not None else ""
        return self._fetched(self._command(f"UID FETCH {uids} {items}{suffix}"))

    def uid_fetch_what_comes(self, uids: str, items: str) -> tuple[tuple[FetchedItem, ...], bool]:
        """FETCH, also when the server refuses it: what it sent before the refusal, and whether it refused (RV-202: that part was thrown
        away and asked again)."""
        try:
            return self.uid_fetch(uids, items), False
        except ImapRefused as refusal:
            return self._fetched(list(refusal.before)), True

    def _fetched(self, responses: list[Response]) -> tuple[FetchedItem, ...]:
        fetched: list[FetchedItem] = []
        for response in responses:
            if response.name != "FETCH" or not response.items or not isinstance(response.items[0], tuple):
                continue
            pairs = response.items[0]
            uid, size, data, modseq = 0, None, None, None
            flags: frozenset[str] = frozenset()
            for key, value in zip(pairs[0::2], pairs[1::2], strict=False):
                name = str(key).upper()
                if name == "UID":
                    uid = self._number(value)
                elif name == "FLAGS" and isinstance(value, tuple):
                    flags = frozenset(str(item) for item in value)
                elif name == "RFC822.SIZE":
                    size = self._number(value)
                elif name == "MODSEQ" and isinstance(value, tuple) and value:
                    modseq = self._number(value[0])
                elif name.startswith("BODY[") and isinstance(value, bytes):
                    data = value  # NIL stays None: not sent (stored as an empty message it was never fetched again, RV-51)
            if uid:
                fetched.append(FetchedItem(uid, flags, size, data, modseq))
        return tuple(fetched)

    def uid_store(self, uids: str, flags: Sequence[str], mode: str = "set") -> None:
        """Change flags: mode 'set', 'add' or 'remove'. Silent: no FETCH responses are asked for."""
        sign = {"set": "", "add": "+", "remove": "-"}[mode]
        names = " ".join(self._flag(flag) for flag in flags)
        self._command(f"UID STORE {uids} {sign}FLAGS.SILENT ({names})")

    @staticmethod
    def _flag(flag: str) -> str:
        if not re.fullmatch(r"\\?[A-Za-z0-9$_.&+,-]{1,200}", flag):
            raise ProtocolError("A flag may only contain letters, digits and a few signs")
        return flag

    def uid_copy(self, uids: str, folder: str) -> AppendResult:
        responses = self._command(f"UID COPY {uids} {quote(encode_folder_name(folder))}")
        return self._append_uid(responses)

    def uid_move(self, uids: str, folder: str) -> AppendResult:
        """Move with MOVE where the server has it, otherwise copy (the copy is there when the server said OK) and mark the originals as deleted;
        they are removed only where the server can remove single messages (UIDPLUS), else they stay marked deleted (D-117).
        Returns the new UID when the server tells it (UIDPLUS) and one message was moved."""
        target = quote(encode_folder_name(folder))
        if "MOVE" in self.capabilities:
            return self._append_uid(self._command(f"UID MOVE {uids} {target}"))
        result = self._append_uid(self._command(f"UID COPY {uids} {target}"))
        self.uid_store(uids, ["\\Deleted"], "add")
        if "UIDPLUS" in self.capabilities:
            self.uid_expunge(uids)
        return result

    def uid_expunge(self, uids: str) -> None:
        """Remove exactly these messages (marked deleted) from the open folder. Without UIDPLUS a server can only remove every message marked
        deleted in the folder, also another program's: that is refused, never done in their place (R-35)."""
        if "UIDPLUS" not in self.capabilities:
            raise ProtocolError("The server cannot remove single messages (it has no UIDPLUS); nothing was removed")
        self._command(f"UID EXPUNGE {uids}")

    def append(self, folder: str, message: bytes, flags: Sequence[str] = (), ) -> AppendResult:
        """Put a message into a folder. Returns the new UID when the server tells it (UIDPLUS)."""
        marks = f" ({' '.join(self._flag(flag) for flag in flags)})" if flags else ""
        responses = self._command(f"APPEND {quote(encode_folder_name(folder))}{marks}", literal=message)
        return self._append_uid(responses)

    def _append_uid(self, responses: list[Response]) -> AppendResult:
        """The UIDVALIDITY and the new UID from an APPENDUID or COPYUID response code; for COPYUID with sets also each old UID with its new
        one. Something the server wrote that cannot be read gives an empty result: the sync matches by Message-ID then."""
        for response in responses:
            if not response.code:
                continue
            kind = str(response.code[0]).upper()
            position = 2 if kind == "APPENDUID" else 3 if kind == "COPYUID" else 0
            if not position or len(response.code) <= position:
                continue
            try:
                validity = self._number(response.code[1])
                if kind == "APPENDUID":
                    return AppendResult(validity, self._number(response.code[2]))
                old, new = uid_set(str(response.code[2])), uid_set(str(response.code[3]))
            except ProtocolError:
                return AppendResult()
            if len(old) != len(new):
                return AppendResult()
            return AppendResult(validity, new[0] if len(new) == 1 else None, dict(zip(old, new, strict=True)))
        return AppendResult()

    def idle(self, seconds: float, stop: Callable[[], bool] = lambda: False) -> bool:
        """Wait for the server to report a change in the selected folder (IDLE), for at most this many seconds or until
        stop() says so. True when the server reported a change. Needs a selected folder and a server that offers IDLE."""
        if "IDLE" not in self.capabilities:
            raise ProtocolError("The server does not offer IDLE")
        tag = self._next_tag()
        self._connection.send(f"{tag} IDLE\r\n".encode())
        while True:
            reply = self._read_response()
            if reply.tag == "+":
                break
            if reply.tag == tag:
                raise ImapRefused(f"The server would not start IDLE: {reply.text}")
        changed = False
        waited = 0.0
        step = 1.0
        while waited < seconds and not changed and not stop():
            if not self._connection.line_waiting() and not self._connection.fill_within(step):
                waited += step
                continue
            if self._connection.line_waiting():
                reply = self._read_response()
                if reply.tag == "*" and reply.name == "BYE":
                    raise ProtocolError(f"The server ended the connection: {reply.text}")
                changed = reply.tag == "*" and reply.name in ("EXISTS", "EXPUNGE", "FETCH", "RECENT")
        self._connection.send(b"DONE\r\n")
        while True:
            reply = self._read_response()
            if reply.tag == tag:
                if reply.name != "OK":
                    raise ImapRefused(f"The server ended IDLE with an error: {reply.text}")
                return changed
            if reply.tag == "*" and reply.name in ("EXISTS", "EXPUNGE", "FETCH", "RECENT"):
                changed = True

    def folder_exists(self, folder: str) -> bool:
        """Whether the server has a folder of exactly this name: LIST for that name alone. An answer that lists it (and not as \\NonExistent) is
        True; an answer that does not is the server's confirmation that it is not there (R-30). A refusal or a broken connection raises: that
        confirms nothing."""
        for response in self._command(f'LIST "" {quote(encode_folder_name(folder))}'):
            if response.name != "LIST" or len(response.items) < 3:
                continue
            attributes, name = response.items[0], response.items[2]
            if not isinstance(attributes, tuple) or not isinstance(name, (str, bytes)):
                raise ProtocolError("The server sent an invalid folder entry")
            text = decode_folder_name(name.decode("utf-8", errors="replace") if isinstance(name, bytes) else name)
            if text == folder and "\\nonexistent" not in {str(item).lower() for item in attributes}:
                return True  # a name with * or % in it is a pattern too: only the exact name counts
        return False

    def create_folder(self, folder: str) -> None:
        self._command(f"CREATE {quote(encode_folder_name(folder))}")

    def rename_folder(self, old: str, new: str) -> None:
        self._command(f"RENAME {quote(encode_folder_name(old))} {quote(encode_folder_name(new))}")

    def delete_folder(self, folder: str) -> None:
        self._command(f"DELETE {quote(encode_folder_name(folder))}")

    def subscribe(self, folder: str) -> None:
        self._command(f"SUBSCRIBE {quote(encode_folder_name(folder))}")

    def unsubscribe(self, folder: str) -> None:
        self._command(f"UNSUBSCRIBE {quote(encode_folder_name(folder))}")
