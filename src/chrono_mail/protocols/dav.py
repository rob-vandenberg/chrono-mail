import base64
import http.client
import ssl
import urllib.parse
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from chrono_mail.core import AuthenticationError, ProtocolError
from chrono_mail.protocols.tls import context_for

# --- Version ------------------------------------------------------------
__version__ = "dav.py 0.0.65"

# --- Version history ----------------------------------------------------
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
USER_AGENT: str = "Chrono Mail"


class DavError(ProtocolError):
    """A server answered with an error status (not a login failure)."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status: int = status


class DavConflict(DavError):
    """The item on the server is not the one that was changed (precondition failed), or it exists already."""


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


@dataclass(frozen=True, slots=True)
class RemoteItem:
    href: str
    etag: str


@dataclass(frozen=True, slots=True)
class RemoteChanges:
    changed: tuple[RemoteItem, ...]
    removed: tuple[str, ...]
    token: str


def parse_multistatus(body: bytes) -> list[DavEntry]:
    """The responses in a multistatus XML. Anything that is not one (or that carries a DOCTYPE or entities, which an honest server never sends) raises DavError."""
    if len(body) > MAXIMUM_XML_BYTES:
        raise DavError("The answer of the server is far too large")
    head = body[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in body[:65536].lower():
        raise DavError("The answer of the server has a DOCTYPE or entities and is refused")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        raise DavError("The answer of the server is not valid XML") from None
    if root.tag != f"{{{DAV}}}multistatus":
        raise DavError("The answer of the server is not a multistatus")
    entries: list[DavEntry] = []
    for response in root.findall(f"{{{DAV}}}response"):
        if len(entries) >= MAXIMUM_RESPONSES:
            break
        href = (response.findtext(f"{{{DAV}}}href") or "").strip()
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
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0


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
    """A client for one server: Basic authentication over TLS (a certificate the person accepted is pinned, as for mail), one connection for each
    request, redirects only to the same server. Plain http is refused except to the computer itself (tests)."""

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
        self._authorization: str = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        self._context: ssl.SSLContext | None = context_for(trusted) if parsed.scheme == "https" else None
        self.timeout: float = timeout

    # --- the requests -------------------------------------------------------------------------

    def _connection(self, scheme: str, host: str, port: int) -> http.client.HTTPConnection:
        if scheme == "https":
            return http.client.HTTPSConnection(host, port, timeout=self.timeout, context=self._context or ssl.create_default_context())
        return http.client.HTTPConnection(host, port, timeout=self.timeout)

    def absolute_path(self, path: str) -> str:
        """A path or address as a path on this server."""
        if path.startswith(("http://", "https://")):
            parts = urllib.parse.urlsplit(path)
            return parts.path + (f"?{parts.query}" if parts.query else "")
        return path if path.startswith("/") else self.base_path.rstrip("/") + "/" + path

    def request(self, method: str, path: str, headers: Mapping[str, str] | None = None, body: bytes | None = None) -> DavResponse:
        target = self.absolute_path(path)
        for _ in range(MAXIMUM_REDIRECTS + 1):
            send = {"Authorization": self._authorization, "User-Agent": USER_AGENT, "Accept-Encoding": "identity", **(headers or {})}
            try:
                connection = self._connection(self.scheme, self.host, self.port)
                try:
                    connection.request(method, urllib.parse.quote(target, safe="/:@!$&'()*+,;=-._~%?"), body=body, headers=send)
                    response = connection.getresponse()
                    data = response.read(MAXIMUM_BODY_BYTES + 1)
                    result = DavResponse(response.status, {name.lower(): value for name, value in response.getheaders()}, data)
                finally:
                    connection.close()
            except ssl.SSLCertVerificationError as error:
                raise ProtocolError(f"The certificate of the server is not accepted: {error.verify_message or error.reason}") from None
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
        """The calendars, task lists and address books in the two homes."""
        found: list[RemoteCollection] = []
        properties = [
            (DAV, "displayname"), (DAV, "resourcetype"), (CALDAV, "supported-calendar-component-set"), (CALENDARSERVER, "getctag"), (DAV, "sync-token"),
            (APPLE, "calendar-color"), (DAV, "current-user-privilege-set"),
        ]
        for home in (calendar_home, address_home):
            if not home:
                continue
            try:
                entries = self.propfind(home, properties, depth=1)
            except DavError:
                continue
            for entry in entries:
                if entry.href.rstrip("/") == home.rstrip("/"):
                    continue
                found.extend(_collections_of(entry))
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
        xml = (
            '<?xml version="1.0" encoding="utf-8"?><d:sync-collection xmlns:d="DAV:"><d:sync-token>' + _escape_xml(token) +
            "</d:sync-token><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>"
        )
        response = self.request("REPORT", collection.href, {"Depth": "1", "Content-Type": "application/xml; charset=utf-8"}, xml.encode("utf-8"))
        if response.status in (403, 409, 412) and token:
            raise DavConflict("The server no longer knows the sync token", response.status)  # the caller starts again from nothing
        self._ok(response, (200, 207))
        entries, new_token = parse_sync(response.body)
        changed: list[RemoteItem] = []
        removed: list[str] = []
        for entry in entries:
            if entry.href.rstrip("/") == collection.href.rstrip("/"):
                continue
            if entry.status == 404:
                removed.append(entry.href)
            else:
                changed.append(RemoteItem(entry.href, _etag(entry.text(f"{{{DAV}}}getetag"))))
        return RemoteChanges(tuple(changed), tuple(removed), new_token)

    def fetch(self, collection: RemoteCollection, hrefs: list[str]) -> dict[str, tuple[str, str]]:
        """The text and version tag of items: {href: (etag, text)}. Several at a time (multiget); an item that cannot be fetched is left out."""
        found: dict[str, tuple[str, str]] = {}
        for start in range(0, len(hrefs), MULTIGET_SIZE):
            chunk = hrefs[start : start + MULTIGET_SIZE]
            if collection.kind == "contacts":
                root, data = ("addressbook-multiget", "address-data")
                namespace = CARDDAV
            else:
                root, data = ("calendar-multiget", "calendar-data")
                namespace = CALDAV
            xml = (
                f'<?xml version="1.0" encoding="utf-8"?><c:{root} xmlns:d="DAV:" xmlns:c="{namespace}"><d:prop><d:getetag/><c:{data}/></d:prop>'
                + "".join(f"<d:href>{_escape_xml(href)}</d:href>" for href in chunk) + f"</c:{root}>"
            )
            try:
                response = self.report(collection.href, xml)
                entries = parse_multistatus(response.body)
            except DavError:
                entries = list[DavEntry]()
            for entry in entries:
                text = entry.text(f"{{{namespace}}}{data}")
                if text:
                    found[entry.href] = (_etag(entry.text(f"{{{DAV}}}getetag")), text)
            for href in chunk:  # a server without multiget, or an item it left out: one by one
                if href not in found:
                    single = self.get(href)
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

    def etag_of(self, href: str) -> str:
        entries = self.propfind(href, [(DAV, "getetag")])
        return _etag(entries[0].text(f"{{{DAV}}}getetag")) if entries else ""


def _etag(text: str) -> str:
    """A version tag without its quotes (and without a weak marker)."""
    cleaned = text.strip()
    if cleaned.startswith("W/"):
        cleaned = cleaned[2:]
    return cleaned.strip('"')


def _collections_of(entry: DavEntry) -> list[RemoteCollection]:
    types = entry.properties.get(f"{{{DAV}}}resourcetype")
    kinds = {child.tag for child in types} if types is not None else set()
    if f"{{{DAV}}}collection" not in kinds:
        return []
    name = entry.text(f"{{{DAV}}}displayname") or entry.href.rstrip("/").rsplit("/", 1)[-1]
    colour = entry.text(f"{{{APPLE}}}calendar-color")[:7].lower() if entry.text(f"{{{APPLE}}}calendar-color") else ""
    ctag = entry.text(f"{{{CALENDARSERVER}}}getctag")
    token = entry.text(f"{{{DAV}}}sync-token")
    privileges = entry.properties.get(f"{{{DAV}}}current-user-privilege-set")
    writable = privileges is None or any(item.tag in (f"{{{DAV}}}write", f"{{{DAV}}}all", f"{{{DAV}}}write-content", f"{{{DAV}}}bind") for item in privileges.iter())
    read_only = not writable
    if f"{{{CARDDAV}}}addressbook" in kinds:
        return [RemoteCollection(entry.href, name, "contacts", "VCARD", "", read_only, ctag, token)]
    if f"{{{CALDAV}}}calendar" not in kinds:
        return []  # a folder that is not a calendar (some servers list their mail folders here)
    components_element = entry.properties.get(f"{{{CALDAV}}}supported-calendar-component-set")
    components = {child.get("name", "").upper() for child in components_element} if components_element is not None else {"VEVENT"}
    found: list[RemoteCollection] = []
    if "VEVENT" in components or not components:
        found.append(RemoteCollection(entry.href, name, "events", "VEVENT", colour, read_only, ctag, token))
    if "VTODO" in components:
        found.append(RemoteCollection(entry.href, name, "tasks", "VTODO", colour, read_only, ctag, token))
    return found
