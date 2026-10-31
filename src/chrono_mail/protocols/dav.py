import base64
import http.client
import socket
import ssl
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Mapping
from types import TracebackType
from dataclasses import dataclass, field

from chrono_mail.core import AuthenticationError, CertificateError, ConnectionSecurity, ProtocolError
from chrono_mail.protocols.tls import context_for, failure
from chrono_mail.core.words import digits

# --- Version ------------------------------------------------------------
__version__ = "dav.py 4.2.441"

# --- Version history ----------------------------------------------------
# v4.2.441: DavCertificateError is a CertificateError that names the server, so the window can ask (D-299); a renewed certificate that passes
#           the usual checks is taken where one was accepted
# v3.6.351: numbers are read only from the digits 0 to 9 (core.words.digits): "²" passed isdigit() and stopped the work with a ValueError
# v1.10.278: read: one item by the multiget first (Kerio answers a GET of an existing contact with 404); not there only on the server's 404, any
#            other failure raises (D-196); fetch and read share _multiget
# v1.1.235: make_collection returns the collection as the server lists it (Kerio lists a new calendar at an address of its own) and says so when
#           the server said yes but made none (Kerio for an address book); exists: an answer that lists nothing at that address means not there
# v1.1.234: A home that cannot be listed is an error, never an empty list (a failed answer read as "no calendars", R-25); exists: whether the
#           server confirms a collection is there (only 404 or 410 say it is not); make_collection: a calendar, task list or address book made on
#           the server (MKCALENDAR, extended MKCOL)
# v1.1.224: The server's address is looked up once per client and used for every connection (a slow name lookup was paid per request);
#           a failed connection forgets it; the name stays for the certificate and the request
# v1.1.223: One connection kept open and reused (R-36): a read on a connection the server closed meanwhile is sent once more; a write never is, and
#           gets a new connection after the open one was idle; close() and with
# v1.1.221: canonical_colour: a server's colour in one form (#rrggbb, also from #RRGGBBAA); set_collection: a collection's name and colour on the
#           server (PROPPATCH, D-192); passing_failure tells a failure worth trying again from a refusal
# v1.1.218: A change report the server cut short (507) is followed to its end; a single item that times out is left out, as fetch promises (the Windows computer's report)
# v1.1.217: Every address from a server in one form (canonical_href: escapes decoded), so %40 and @ are the same item; an item listed twice in a change report counts once
# v1.1.210: A certificate failure is its own error; a client without a login asks without one; the public folders next to the user's own (all_collections, D-182)
# v0.0.65: Initial WebDAV, CalDAV and CardDAV client: discovery, collections, items, changes, writing

DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"
CARDDAV = "urn:ietf:params:xml:ns:carddav"
CALENDARSERVER = "http://calendarserver.org/ns/"
APPLE = "http://apple.com/ns/ical/"
MAXIMUM_BODY_BYTES: int = 64 * 1024 * 1024
MAXIMUM_XML_BYTES: int = 32 * 1024 * 1024
MAXIMUM_RESPONSES: int = 200_000
MAXIMUM_REDIRECTS: int = 5
MULTIGET_SIZE: int = 40
MAXIMUM_CHANGE_ROUNDS: int = 1_000  # a change report cut short (507) is asked on at most this many times
USER_AGENT: str = "Chrono Mail"
READS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "PROPFIND", "REPORT"})  # change nothing: safe to send once more
IDLE_BEFORE_WRITE: float = 4.0  # seconds: a connection idle longer than this may have been closed by the server, so a write gets a new one


class DavError(ProtocolError):
    """A server answered with an error status (not a login failure)."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status: int = status


class DavConflict(DavError):
    """The item on the server is not the one that was changed (precondition failed), or it exists already."""


class DavCertificateError(CertificateError):
    """The certificate of the server was not accepted (the usual checks, or it is not the one the person accepted)."""


@dataclass(frozen=True, slots=True)
class DavResponse:
    status: int
    headers: Mapping[str, str]  # names in lower case
    body: bytes


@dataclass(frozen=True, slots=True)
class DavEntry:
    """One response of a multistatus answer: where, and the properties the server gave (name in {namespace}local form)."""

    href: str
    status: int
    properties: Mapping[str, ElementTree.Element] = field(default_factory=dict)

    def text(self, name: str) -> str:
        element = self.properties.get(name)
        return (element.text or "").strip() if element is not None else ""

    def hrefs(self, name: str) -> list[str]:
        element = self.properties.get(name)
        if element is None:
            return []
        return [(child.text or "").strip() for child in element if child.tag == f"{{{DAV}}}href" and (child.text or "").strip()]

    def has_child(self, name: str, child: str) -> bool:
        element = self.properties.get(name)
        return element is not None and any(item.tag == child for item in element.iter())


@dataclass(frozen=True, slots=True)
class RemoteCollection:
    """A calendar, task list or address book on a server."""

    href: str
    name: str
    kind: str  # events, tasks or contacts
    component: str  # VEVENT, VTODO or VCARD: what it holds
    colour: str
    read_only: bool
    ctag: str
    sync_token: str
    public: bool = False  # one of the public folders next to the user's own (D-182)


def canonical_href(href: str) -> str:
    """An address on a server in one form, its escapes decoded: Kerio writes the same item as …%40x.ics in a change report and …@x.ics in a multiget,
    so addresses are only ever compared, kept and remembered in this form, and escaped again only when sent (DavClient.request)."""
    return urllib.parse.unquote(href)


def folder_key(collection: RemoteCollection) -> str:
    """How an account remembers a folder of its server it leaves out: what it holds and where (one calendar can hold events and tasks)."""
    return f"{collection.component} {canonical_href(collection.href)}"


def canonical_key(key: str) -> str:
    """A folder_key in the one form of its address (keys remembered before 1.1.217 may hold escapes)."""
    component, _, href = key.partition(" ")
    return f"{component} {canonical_href(href)}"


@dataclass(frozen=True, slots=True)
class RemoteItem:
    href: str
    etag: str


@dataclass(frozen=True, slots=True)
class RemoteChanges:
    changed: tuple[RemoteItem, ...]
    removed: tuple[str, ...]
    token: str


def canonical_colour(text: str) -> str:
    """A colour as a server gives it (#RRGGBB, Apple's #RRGGBBAA with transparency, or #RGB) in the one form Chrono Mail keeps and compares,
    #rrggbb; '' when it is none of these. Without one form a colour written one way and read back the other would look changed at every sync."""
    value = text.strip().lower()
    if not value.startswith("#") or any(character not in "0123456789abcdef" for character in value[1:]):
        return ""
    if len(value) == 4:
        return "#" + "".join(character * 2 for character in value[1:])
    return value[:7] if len(value) in (7, 9) else ""


def passing_failure(error: ProtocolError) -> bool:
    """Whether a failure is worth trying again later (no connection, a timeout, the server busy or failing) rather than a lasting "no" of the server
    (read only, no permission, the change rejected)."""
    if isinstance(error, AuthenticationError):
        return True  # the login, not the change: it goes once the login works again
    if not isinstance(error, DavError):
        return True
    return error.status == 0 or error.status >= 500 or error.status in (408, 429)


def _root(body: bytes) -> ElementTree.Element:
    """The root of an XML answer, refused when it is far too large, not XML, or carries a DOCTYPE or entities (which an honest server never sends)."""
    if len(body) > MAXIMUM_XML_BYTES:
        raise DavError("The answer of the server is far too large")
    head = body[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in body[:65536].lower():
        raise DavError("The answer of the server has a DOCTYPE or entities and is refused")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        raise DavError("The answer of the server is not valid XML") from None
    return root


def parse_multistatus(body: bytes) -> list[DavEntry]:
    """The responses in a multistatus XML. Anything that is not one (or that carries a DOCTYPE or entities, which an honest server never sends) raises DavError."""
    root = _root(body)
    if root.tag != f"{{{DAV}}}multistatus":
        raise DavError("The answer of the server is not a multistatus")
    entries: list[DavEntry] = []
    for response in root.findall(f"{{{DAV}}}response"):
        if len(entries) >= MAXIMUM_RESPONSES:
            break
        href = canonical_href((response.findtext(f"{{{DAV}}}href") or "").strip())
        status_text = response.findtext(f"{{{DAV}}}status") or ""
        properties: dict[str, ElementTree.Element] = {}
        status = _status_of(status_text) if status_text else 200
        for propstat in response.findall(f"{{{DAV}}}propstat"):
            propstat_status = _status_of(propstat.findtext(f"{{{DAV}}}status") or "HTTP/1.1 200 OK")
            if propstat_status == 200:
                prop = propstat.find(f"{{{DAV}}}prop")
                if prop is not None:
                    for item in prop:
                        properties[item.tag] = item
        if href:
            entries.append(DavEntry(href, status, properties))
    return entries


def _status_of(text: str) -> int:
    parts = text.split()
    return int(parts[1]) if len(parts) > 1 and digits(parts[1]) else 0


def parse_sync(body: bytes) -> tuple[list[DavEntry], str]:
    """The answer of a sync-collection report: the entries and the new token."""
    entries = parse_multistatus(body)
    root = ElementTree.fromstring(body)
    return entries, (root.findtext(f"{{{DAV}}}sync-token") or "").strip()


def _propfind(properties: Iterable[tuple[str, str]]) -> bytes:
    body = ElementTree.Element(f"{{{DAV}}}propfind")
    prop = ElementTree.SubElement(body, f"{{{DAV}}}prop")
    for namespace, name in properties:
        ElementTree.SubElement(prop, f"{{{namespace}}}{name}")
    return b'<?xml version="1.0" encoding="utf-8"?>' + ElementTree.tostring(body)


def _escape_xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class DavClient:
    """A client for one server: Basic authentication over TLS (a certificate the person accepted is pinned, as for mail), one connection kept open
    and reused (R-36), redirects only to the same server. Plain http is refused except to the computer itself (tests). Without a user name and password it asks
    without logging in (to see whether a place is a calendar server before the password goes there)."""

    def __init__(self, base_url: str, username: str, password: str, trusted: frozenset[str] = frozenset(), timeout: float = 30.0) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            raise DavError("A server address starts with https:// and has a host name")
        if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise DavError("A server must be reached over https (plain http is only allowed to this computer)")
        self.scheme: str = parsed.scheme
        self.host: str = parsed.hostname
        self.port: int = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.base_path: str = parsed.path or "/"
        self._authorization: str = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode("ascii") if username or password else ""
        self._context: ssl.SSLContext | None = context_for(trusted, ConnectionSecurity.TLS, "https") if parsed.scheme == "https" else None
        self.timeout: float = timeout
        self._open: http.client.HTTPConnection | None = None  # the connection kept open for the next request
        self._last_used: float = 0.0  # time.monotonic() of its last answer
        self._lock: threading.Lock = threading.Lock()  # one request at a time on the connection
        self._addresses: list[tuple[str, int]] = []  # where the server's name led, looked up once per client (empty: not yet, or forgotten)

    def close(self) -> None:
        """Close the connection kept open (the next request opens a new one)."""
        with self._lock:
            self._drop()

    def __enter__(self) -> "DavClient":
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None) -> None:
        self.close()

    def _drop(self) -> None:
        if self._open is not None:
            self._open.close()
            self._open = None

    # --- the requests -------------------------------------------------------------------------

    def _connection(self, scheme: str, host: str, port: int) -> http.client.HTTPConnection:
        """A new connection to the server, to the address its name led to the first time (one lookup per client: a name can take long to look up,
        and a server that closes every connection would pay it for every request). The name stays where it matters: the certificate is checked
        against it and the request names it."""
        if scheme == "https":
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(host, port, timeout=self.timeout, context=self._context or ssl.create_default_context())
        else:
            connection = http.client.HTTPConnection(host, port, timeout=self.timeout)
        setattr(connection, "_create_connection", self._connect_to_known_address)  # http.client's own hook for making the socket
        return connection

    def _connect_to_known_address(self, address: tuple[str, int], timeout: float | None = None, source: tuple[str, int] | None = None) -> socket.socket:
        """The socket for a connection: to the addresses found for the name the first time, each in turn; when none answers they are forgotten, so
        the next connection looks the name up again (a server that moved)."""
        if not self._addresses:
            found = socket.getaddrinfo(address[0], address[1], type=socket.SOCK_STREAM)
            self._addresses = list(dict.fromkeys((str(item[4][0]), int(item[4][1])) for item in found))
        failure: OSError | None = None
        for known in self._addresses:
            try:
                return socket.create_connection(known, timeout, source)
            except OSError as error:
                failure = error
        self._addresses = []
        raise failure if failure is not None else OSError(f"No address was found for {address[0]}")

    def _exchange(self, method: str, target: str, body: bytes | None, send: Mapping[str, str]) -> DavResponse:
        """One request on the connection kept open (a new one when there is none). A read that finds the reused connection closed by the server is
        sent once more on a new one. A write is never sent twice: it gets a new connection when the open one was idle a while, and a break of its
        connection is an error (the next synchronisation compares and sees whether it arrived)."""
        if self._open is not None and method not in READS and time.monotonic() - self._last_used > IDLE_BEFORE_WRITE:
            self._drop()
        for _ in range(2):
            reused = self._open is not None
            if self._open is None:
                self._open = self._connection(self.scheme, self.host, self.port)
            connection = self._open
            try:
                connection.request(method, _escaped(target), body=body, headers=dict(send))
                response = connection.getresponse()
                data = response.read(MAXIMUM_BODY_BYTES + 1)
            except (ConnectionError, http.client.HTTPException):
                self._drop()
                if reused and method in READS:
                    continue  # the server had closed the connection while it was idle: a read is safe to send again
                raise
            except BaseException:
                self._drop()
                raise
            self._last_used = time.monotonic()
            if response.will_close or len(data) > MAXIMUM_BODY_BYTES:
                self._drop()  # the server closes it (or the answer was not read to its end): the next request opens a new one
            return DavResponse(response.status, {name.lower(): value for name, value in response.getheaders()}, data)
        raise ProtocolError("The server closed the connection")  # never reached: the second try is on a new connection

    def absolute_path(self, path: str) -> str:
        """A path or address as a path on this server."""
        if path.startswith(("http://", "https://")):
            parts = urllib.parse.urlsplit(path)
            return parts.path + (f"?{parts.query}" if parts.query else "")
        return path if path.startswith("/") else self.base_path.rstrip("/") + "/" + path

    def request(self, method: str, path: str, headers: Mapping[str, str] | None = None, body: bytes | None = None) -> DavResponse:
        target = self.absolute_path(path)
        for _ in range(MAXIMUM_REDIRECTS + 1):
            send = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity", **(headers or {})}
            if self._authorization:
                send["Authorization"] = self._authorization
            try:
                with self._lock:
                    result = self._exchange(method, target, body, send)
            except ssl.SSLCertVerificationError as error:
                named = failure(error, self.host, self.port, ConnectionSecurity.TLS, "https")
                raise DavCertificateError(str(named), named.trouble) from None
            except ssl.SSLError as error:
                raise ProtocolError(f"The secure connection failed: {error.reason or error}") from None
            except TimeoutError:
                raise ProtocolError("The server did not answer in time") from None
            except (OSError, http.client.HTTPException) as error:
                raise ProtocolError(f"The server could not be reached: {error}") from None
            if len(result.body) > MAXIMUM_BODY_BYTES:
                raise DavError("The answer of the server is far too large", result.status)
            if result.status in (301, 302, 307, 308) and "location" in result.headers:
                location = urllib.parse.urlsplit(urllib.parse.urljoin(f"{self.scheme}://{self.host}:{self.port}{target}", result.headers["location"]))
                if location.hostname != self.host or (location.port or (443 if location.scheme == "https" else 80)) != self.port or location.scheme != self.scheme:
                    raise DavError("The server sent us to another server, which is not followed", result.status)
                target = location.path + (f"?{location.query}" if location.query else "")
                continue
            if result.status == 401:
                raise AuthenticationError("The server refused the user name or password")
            return result
        raise DavError("The server sent us round in circles")

    def _ok(self, response: DavResponse, accepted: Iterable[int]) -> DavResponse:
        if response.status in set(accepted):
            return response
        if response.status in (409, 412):
            raise DavConflict("The item on the server has been changed, or exists already", response.status)
        raise DavError(f"The server answered {response.status}", response.status)

    # --- WebDAV --------------------------------------------------------------------------------

    def options(self, path: str = "") -> set[str]:
        """What the server says it supports (the DAV header, in lower case)."""
        response = self.request("OPTIONS", path or self.base_path)
        self._ok(response, (200, 204))
        return {part.strip().lower() for part in response.headers.get("dav", "").split(",") if part.strip()}

    def propfind(self, path: str, properties: Iterable[tuple[str, str]], depth: int = 0) -> list[DavEntry]:
        response = self.request("PROPFIND", path, {"Depth": str(depth), "Content-Type": "application/xml; charset=utf-8"}, _propfind(properties))
        self._ok(response, (207,))
        return parse_multistatus(response.body)

    def report(self, path: str, xml: str, depth: int = 1) -> DavResponse:
        response = self.request("REPORT", path, {"Depth": str(depth), "Content-Type": "application/xml; charset=utf-8"}, xml.encode("utf-8"))
        return self._ok(response, (200, 207))

    # --- discovery -----------------------------------------------------------------------------

    def homes(self) -> tuple[str, str]:
        """The place of the calendars and the place of the address books of the user: (calendar home, address book home); a part that the server does not
        have is empty. Starts at the address given, then tries what the standards name."""
        wanted = [(DAV, "current-user-principal"), (CALDAV, "calendar-home-set"), (CARDDAV, "addressbook-home-set")]
        calendar_home = address_home = ""
        tried: set[str] = set()
        starts = [self.base_path, "/.well-known/caldav", "/.well-known/carddav"]
        for start in starts:
            if start in tried:
                continue
            tried.add(start)
            try:
                entries = self.propfind(start, wanted)
            except DavError:
                continue
            for entry in entries[:1]:
                calendar_home = calendar_home or next(iter(entry.hrefs(f"{{{CALDAV}}}calendar-home-set")), "")
                address_home = address_home or next(iter(entry.hrefs(f"{{{CARDDAV}}}addressbook-home-set")), "")
                principal = next(iter(entry.hrefs(f"{{{DAV}}}current-user-principal")), "")
                if principal and (not calendar_home or not address_home) and principal not in tried:
                    tried.add(principal)
                    try:
                        more = self.propfind(principal, wanted[1:])
                    except DavError:
                        more = list[DavEntry]()
                    for item in more[:1]:
                        calendar_home = calendar_home or next(iter(item.hrefs(f"{{{CALDAV}}}calendar-home-set")), "")
                        address_home = address_home or next(iter(item.hrefs(f"{{{CARDDAV}}}addressbook-home-set")), "")
            if calendar_home and address_home:
                break
        return calendar_home, address_home

    def collections(self, calendar_home: str, address_home: str) -> list[RemoteCollection]:
        """The calendars, task lists and address books in the two homes. A home the server announced but does not list is an error, never "no
        collections": a failed answer must never look like a deleted calendar (R-25)."""
        found: list[RemoteCollection] = []
        properties = [
            (DAV, "displayname"), (DAV, "resourcetype"), (CALDAV, "supported-calendar-component-set"), (CALENDARSERVER, "getctag"), (DAV, "sync-token"),
            (APPLE, "calendar-color"), (DAV, "current-user-privilege-set"),
        ]
        for home in (calendar_home, address_home):
            if not home:
                continue
            for entry in self.propfind(home, properties, depth=1):
                if entry.href.rstrip("/") == home.rstrip("/"):
                    continue
                found.extend(_collections_of(entry))
        return found

    def public_collections(self, calendar_home: str, address_home: str) -> list[RemoteCollection]:
        """The public folders some servers keep next to the user's own place, in `#public` (Kerio Connect does; no standard announces them, D-182).
        A server without them answers with an error, which only means there are none."""
        found: list[RemoteCollection] = []
        properties = [
            (DAV, "displayname"), (DAV, "resourcetype"), (CALDAV, "supported-calendar-component-set"), (CALENDARSERVER, "getctag"), (DAV, "sync-token"),
            (APPLE, "calendar-color"), (DAV, "current-user-privilege-set"),
        ]
        for home in (calendar_home, address_home):
            parent = home.rstrip("/").rsplit("/", 1)[0] if home.strip("/") else ""
            if not parent:
                continue
            place = parent + "/#public/"
            try:
                entries = self.propfind(place, properties, depth=1)
            except (DavError, AuthenticationError):
                continue
            for entry in entries:
                if entry.href.rstrip("/") == place.rstrip("/"):
                    continue
                found.extend(_collections_of(entry, public=True))
        return found

    def all_collections(self) -> list[RemoteCollection]:
        """Every calendar, task list and address book the user can reach: the own ones and the public ones, each once."""
        calendar_home, address_home = self.homes()
        found: list[RemoteCollection] = []
        seen: set[str] = set()
        for collection in self.collections(calendar_home, address_home) + self.public_collections(calendar_home, address_home):
            if folder_key(collection) not in seen:
                seen.add(folder_key(collection))
                found.append(collection)
        return found

    # --- items ----------------------------------------------------------------------------------

    def items(self, collection: RemoteCollection) -> list[RemoteItem]:
        """Every item of a collection with its version tag (for a server that has no change report)."""
        entries = self.propfind(collection.href, [(DAV, "getetag"), (DAV, "resourcetype")], depth=1)
        found: list[RemoteItem] = []
        for entry in entries:
            if entry.href.rstrip("/") == collection.href.rstrip("/") or entry.has_child(f"{{{DAV}}}resourcetype", f"{{{DAV}}}collection"):
                continue
            found.append(RemoteItem(entry.href, _etag(entry.text(f"{{{DAV}}}getetag"))))
        return found

    def changes(self, collection: RemoteCollection, token: str) -> RemoteChanges:
        """What changed since a sync token (RFC 6578). The first time, an empty token gives everything."""
        latest: dict[str, RemoteItem | None] = {}  # by address: an item listed twice (Kerio lists a new item twice) counts once, as its last line says
        current = token
        for _ in range(MAXIMUM_CHANGE_ROUNDS):
            xml = (
                '<?xml version="1.0" encoding="utf-8"?><d:sync-collection xmlns:d="DAV:"><d:sync-token>' + _escape_xml(current) +
                "</d:sync-token><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>"
            )
            response = self.request("REPORT", collection.href, {"Depth": "1", "Content-Type": "application/xml; charset=utf-8"}, xml.encode("utf-8"))
            if response.status in (403, 409, 412) and current:
                raise DavConflict("The server no longer knows the sync token", response.status)  # the caller starts again from nothing
            self._ok(response, (200, 207))
            entries, new_token = parse_sync(response.body)
            cut_short = False  # RFC 6578 3.6: the server says 507 for the collection itself and gives a token to go on from
            for entry in entries:
                if entry.href.rstrip("/") == collection.href.rstrip("/"):
                    cut_short = cut_short or entry.status == 507
                    continue
                latest.pop(entry.href, None)
                latest[entry.href] = None if entry.status == 404 else RemoteItem(entry.href, _etag(entry.text(f"{{{DAV}}}getetag")))
            if not cut_short:
                current = new_token or current
                break
            if not new_token or new_token == current:
                raise DavError("The server cut its list of changes short without saying where to go on")
            current = new_token
        else:
            raise DavError("The server's list of changes never ended")
        changed = tuple(item for item in latest.values() if item is not None)
        removed = tuple(href for href, item in latest.items() if item is None)
        return RemoteChanges(changed, removed, current)

    def _multiget(self, collection: RemoteCollection, hrefs: list[str]) -> dict[str, tuple[str, str]]:
        """What one multiget gives of these items: {href: (etag, text)}; a server without multiget, or an answer that cannot be read, gives none."""
        if collection.kind == "contacts":
            root, data = ("addressbook-multiget", "address-data")
            namespace = CARDDAV
        else:
            root, data = ("calendar-multiget", "calendar-data")
            namespace = CALDAV
        xml = (
            f'<?xml version="1.0" encoding="utf-8"?><c:{root} xmlns:d="DAV:" xmlns:c="{namespace}"><d:prop><d:getetag/><c:{data}/></d:prop>'
            + "".join(f"<d:href>{_escape_xml(href)}</d:href>" for href in hrefs) + f"</c:{root}>"
        )
        try:
            response = self.report(collection.href, xml)
            entries = parse_multistatus(response.body)
        except DavError:
            entries = list[DavEntry]()
        found: dict[str, tuple[str, str]] = {}
        for entry in entries:
            text = entry.text(f"{{{namespace}}}{data}")
            if text:
                found[entry.href] = (_etag(entry.text(f"{{{DAV}}}getetag")), text)
        return found

    def read(self, collection: RemoteCollection, href: str) -> tuple[str, str] | None:
        """One item as the server holds it now: (etag, text), or None only when the server says it is not there (404). Asked by the multiget first,
        as the lists are: Kerio answers a GET of an existing contact with 404 (and of one just written with its own mail message). Any other failure
        raises: it never counts as "not there" (D-196)."""
        wanted = canonical_href(href)
        found = next((value for key, value in self._multiget(collection, [href]).items() if canonical_href(key) == wanted), None)
        return found if found is not None else self.get(href)

    def fetch(self, collection: RemoteCollection, hrefs: list[str]) -> dict[str, tuple[str, str]]:
        """The text and version tag of items: {href: (etag, text)}. Several at a time (multiget); an item that cannot be fetched is left out."""
        found: dict[str, tuple[str, str]] = {}
        for start in range(0, len(hrefs), MULTIGET_SIZE):
            chunk = hrefs[start : start + MULTIGET_SIZE]
            found.update(self._multiget(collection, chunk))
            for href in chunk:  # a server without multiget, or an item it left out: one by one
                if href not in found:
                    try:
                        single = self.get(href)
                    except AuthenticationError:
                        raise
                    except ProtocolError:
                        continue  # this item only (a timeout, an error status): left out, the caller says so and asks again next time
                    if single is not None:
                        found[href] = single
        return found

    def get(self, href: str) -> tuple[str, str] | None:
        response = self.request("GET", href)
        if response.status == 404:
            return None
        self._ok(response, (200,))
        return _etag(response.headers.get("etag", "")), response.body.decode("utf-8", "replace")

    def put(self, href: str, text: str, kind: str, etag: str = "", create: bool = False) -> str:
        """Write an item. With an etag the write only happens if the item on the server is still that version; with create only if it does not exist.
        Returns the new version tag ('' when the server does not say: ask with items())."""
        headers = {"Content-Type": f"{'text/vcard' if kind == 'contacts' else 'text/calendar'}; charset=utf-8"}
        if create:
            headers["If-None-Match"] = "*"
        elif etag:
            headers["If-Match"] = f'"{etag}"'
        response = self.request("PUT", href, headers, text.encode("utf-8"))
        self._ok(response, (200, 201, 204))
        return _etag(response.headers.get("etag", ""))

    def delete(self, href: str, etag: str = "") -> None:
        response = self.request("DELETE", href, {"If-Match": f'"{etag}"'} if etag else {})
        if response.status == 404:
            return  # already gone
        self._ok(response, (200, 204))

    def set_collection(self, href: str, name: str | None = None, colour: str | None = None) -> dict[str, int]:
        """Give a collection on the server another name (displayname) and/or colour (Apple's calendar-color, sent as #RRGGBBFF as Apple's programs
        write it), in one PROPPATCH. Returns per part ("name", "colour") the status the server gave it: 200 when it was kept. An error of the
        whole request raises (DavError with its status; a passing one, see passing_failure, is worth trying again)."""
        update = ElementTree.Element(f"{{{DAV}}}propertyupdate")
        prop = ElementTree.SubElement(ElementTree.SubElement(update, f"{{{DAV}}}set"), f"{{{DAV}}}prop")
        parts: dict[str, str] = {}
        if name is not None:
            ElementTree.SubElement(prop, f"{{{DAV}}}displayname").text = name
            parts[f"{{{DAV}}}displayname"] = "name"
        if colour is not None:
            ElementTree.SubElement(prop, f"{{{APPLE}}}calendar-color").text = colour.upper() + "FF"
            parts[f"{{{APPLE}}}calendar-color"] = "colour"
        if not parts:
            return {}
        body = b'<?xml version="1.0" encoding="utf-8"?>' + ElementTree.tostring(update)
        response = self.request("PROPPATCH", href, {"Content-Type": "application/xml; charset=utf-8"}, body)
        if response.status in (200, 204):
            return {part: 200 for part in parts.values()}  # no multistatus: the whole update was done
        self._ok(response, (207,))
        root = _root(response.body)
        statuses = {part: 0 for part in parts.values()}
        for propstat in root.iter(f"{{{DAV}}}propstat"):
            status = _status_of(propstat.findtext(f"{{{DAV}}}status") or "")
            prop_element = propstat.find(f"{{{DAV}}}prop")
            for item in prop_element if prop_element is not None else []:
                if item.tag in parts:
                    statuses[parts[item.tag]] = status
        return statuses

    def exists(self, href: str) -> bool:
        """Whether the server confirms that a collection (or item) is at this address. Only an answer that it is not there (404 Not Found, 410 Gone)
        is False; no answer, a timeout or any other error raises, because that confirms nothing (the owner: "confirmation of deletion on the
        server", R-25)."""
        response = self.request("PROPFIND", href, {"Depth": "0", "Content-Type": "application/xml; charset=utf-8"}, _propfind([(DAV, "resourcetype")]))
        if response.status in (404, 410):
            return False
        self._ok(response, (200, 207))
        if response.status == 207 and not parse_multistatus(response.body):
            return False  # the server answered and lists nothing at that address (Kerio for an address that was never there)
        return True

    def make_collection(self, href: str, kind: str, name: str, colour: str = "") -> RemoteCollection:
        """Make a calendar (kind events), task list (tasks) or address book (contacts) at this address on the server, with its name and colour:
        MKCALENDAR (RFC 4791) or extended MKCOL (RFC 5689), and return it as the server lists it: a server may list it at an address of its own
        (Kerio). A place that is taken raises DavConflict; any other refusal, or a "yes" after which the server lists none, DavError."""
        before = {folder_key(item) for item in self.all_collections()}
        if kind == "contacts":
            root = ElementTree.Element(f"{{{DAV}}}mkcol")
            prop = ElementTree.SubElement(ElementTree.SubElement(root, f"{{{DAV}}}set"), f"{{{DAV}}}prop")
            types = ElementTree.SubElement(prop, f"{{{DAV}}}resourcetype")
            ElementTree.SubElement(types, f"{{{DAV}}}collection")
            ElementTree.SubElement(types, f"{{{CARDDAV}}}addressbook")
            method = "MKCOL"
        else:
            root = ElementTree.Element(f"{{{CALDAV}}}mkcalendar")
            prop = ElementTree.SubElement(ElementTree.SubElement(root, f"{{{DAV}}}set"), f"{{{DAV}}}prop")
            components = ElementTree.SubElement(prop, f"{{{CALDAV}}}supported-calendar-component-set")
            ElementTree.SubElement(components, f"{{{CALDAV}}}comp", {"name": "VTODO" if kind == "tasks" else "VEVENT"})
            if colour:
                ElementTree.SubElement(prop, f"{{{APPLE}}}calendar-color").text = colour.upper() + "FF"
            method = "MKCALENDAR"
        ElementTree.SubElement(prop, f"{{{DAV}}}displayname").text = name
        body = b'<?xml version="1.0" encoding="utf-8"?>' + ElementTree.tostring(root)
        response = self.request(method, href, {"Content-Type": "application/xml; charset=utf-8"}, body)
        if response.status == 405:
            raise DavConflict("There is something at that place on the server already", response.status)
        word = {"contacts": "address book", "tasks": "task list"}.get(kind, "calendar")
        if response.status == 207:  # some servers (Kerio for MKCOL) answer per property: made only when every property was taken
            statuses = [_status_of(item.findtext(f"{{{DAV}}}status") or "") for item in _root(response.body).iter(f"{{{DAV}}}propstat")]
            if any(not 200 <= status < 300 for status in statuses):
                raise DavError(f"The server did not take the {word} as asked", 207)
        else:
            self._ok(response, (200, 201))
        listed = [item for item in self.all_collections() if item.kind == kind]
        new = [item for item in listed if folder_key(item) not in before]
        at_place = [item for item in listed if canonical_href(item.href) == canonical_href(self.absolute_path(href))]
        found = (new if len(new) == 1 else list[RemoteCollection]()) or at_place
        if not found:
            raise DavError(f"The server said yes but made no {word} that it lists (it may not make them)", response.status)
        return found[0]

    def etag_of(self, href: str) -> str:
        entries = self.propfind(href, [(DAV, "getetag")])
        return _etag(entries[0].text(f"{{{DAV}}}getetag")) if entries else ""


def _escaped(target: str) -> str:
    """A path (in the one form of canonical_href) as it is sent: escaped once, the query left as it is."""
    path, separator, query = target.partition("?")
    return urllib.parse.quote(canonical_href(path), safe="/:@!$&'()*+,;=-._~") + separator + query


def _etag(text: str) -> str:
    """A version tag without its quotes (and without a weak marker)."""
    cleaned = text.strip()
    if cleaned.startswith("W/"):
        cleaned = cleaned[2:]
    return cleaned.strip('"')


def _collections_of(entry: DavEntry, public: bool = False) -> list[RemoteCollection]:
    types = entry.properties.get(f"{{{DAV}}}resourcetype")
    kinds = {child.tag for child in types} if types is not None else set()
    if f"{{{DAV}}}collection" not in kinds:
        return []
    name = entry.text(f"{{{DAV}}}displayname") or entry.href.rstrip("/").rsplit("/", 1)[-1]
    colour = canonical_colour(entry.text(f"{{{APPLE}}}calendar-color"))
    ctag = entry.text(f"{{{CALENDARSERVER}}}getctag")
    token = entry.text(f"{{{DAV}}}sync-token")
    privileges = entry.properties.get(f"{{{DAV}}}current-user-privilege-set")
    writable = privileges is None or any(item.tag in (f"{{{DAV}}}write", f"{{{DAV}}}all", f"{{{DAV}}}write-content", f"{{{DAV}}}bind") for item in privileges.iter())
    read_only = not writable
    if f"{{{CARDDAV}}}addressbook" in kinds:
        return [RemoteCollection(entry.href, name, "contacts", "VCARD", "", read_only, ctag, token, public)]
    if f"{{{CALDAV}}}calendar" not in kinds:
        return []  # a folder that is not a calendar (some servers list their mail folders here)
    components_element = entry.properties.get(f"{{{CALDAV}}}supported-calendar-component-set")
    components = {child.get("name", "").upper() for child in components_element} if components_element is not None else {"VEVENT"}
    found: list[RemoteCollection] = []
    if "VEVENT" in components or not components:
        found.append(RemoteCollection(entry.href, name, "events", "VEVENT", colour, read_only, ctag, token, public))
    if "VTODO" in components:
        found.append(RemoteCollection(entry.href, name, "tasks", "VTODO", colour, read_only, ctag, token, public))
    return found
