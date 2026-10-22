import urllib.parse
import base64
import datetime
import re
import ssl
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Self, override
from xml.sax.saxutils import escape

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

# --- Version ------------------------------------------------------------
__version__ = "fakedav.py 1.10.278"

# --- Version history ----------------------------------------------------
# v1.10.278: like Kerio, a GET of an existing contact answers 404 (the multiget gives it); GET that fails for now (503)
# v1.1.236: a server that keeps no colour keeps none of a new calendar either
# v1.1.235: DELETE of a whole collection; like Kerio, a MKCOL that says yes and makes nothing, and an empty answer for an unknown address
# v1.1.234: MKCALENDAR and MKCOL make a collection (a key made from its place); a home whose listing fails (503), and a collection's own
#           PROPFIND that fails (503), for "the server did not confirm" (R-25)
# v1.1.225: writes that fail for now (PUT and DELETE answer 503), or are refused (403)
# v1.1.223: counts the connections; can close a connection after each answer without saying so (an idle connection the server dropped), or say so
# v1.1.221: PROPPATCH of a collection's name and colour; a server that keeps no colour, refuses names, or fails for now (503)
# v1.1.218: a change report cut short after so many items (507, RFC 6578 3.6)
# v1.1.217: like Kerio: the change report writes @ as %40 and lists every item twice
# v1.1.210: public folders in #public next to the user's own; a plain web server; which requests carried a login
# v0.0.65: Initial fake CalDAV and CardDAV server

NS = 'xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:card="urn:ietf:params:xml:ns:carddav" xmlns:cs="http://calendarserver.org/ns/" xmlns:a="http://apple.com/ns/ical/"'


class Collection:
    def __init__(self, name: str, kind: str, components: str = "", colour: str = "") -> None:
        self.name, self.kind, self.components, self.colour = name, kind, components, colour  # kind: calendar, addressbook, folder
        self.items: dict[str, tuple[str, str]] = {}  # item name -> (etag, text)
        self.version: int = 1
        self.log: list[tuple[int, str, bool]] = []  # (version, item name, removed)

    def changed(self, item: str, removed: bool) -> None:
        self.version += 1
        self.log.append((self.version, item, removed))


class FakeDav:
    """A server for CalDAV and CardDAV on this computer, for tests. It can behave like awkward servers: no change report, no multiget, no ETag on PUT."""

    def __init__(
        self, user: str = "ann", password: str = "secret", sync_tokens: bool = True, multiget: bool = True, etag_on_put: bool = True, tls: bool = False,
        read_only: tuple[str, ...] = (), public: bool = False, plain_web: bool = False, like_kerio: bool = False,
    ) -> None:
        self.like_kerio: bool = like_kerio  # the change report writes @ as %40 and lists each item twice; a multiget answers with @
        self.cut_after: int = 0  # a change report gives at most this many items and then 507 with a token to go on from (0: all)
        self.user, self.password = user, password
        self.plain_web: bool = plain_web  # a web server that is no calendar server: every request gets 404
        self.logins: list[bool] = []  # per request: whether it carried a login
        self.sync_tokens, self.multiget, self.etag_on_put, self.tls = sync_tokens, multiget, etag_on_put, tls
        self.read_only: tuple[str, ...] = read_only
        self.keeps_colour: bool = True  # PROPPATCH of calendar-color is kept (else 403 for that property)
        self.refuses_names: bool = False  # PROPPATCH of displayname is refused (403)
        self.failing: bool = False  # PROPPATCH answers 503 (busy for now)
        self.writes_failing: bool = False  # PUT and DELETE answer 503 (busy for now)
        self.writes_refused: bool = False  # PUT and DELETE answer 403 (not allowed)
        self.connections: int = 0  # how many connections were opened to it
        self.drop_silently: bool = False  # closes the connection after each answer without saying so (as a server drops an idle one)
        self.say_close: bool = False  # answers with "Connection: close" and closes
        self.homes_failing: bool = False  # the listing of the calendar and address book homes answers 503
        self.collection_check_failing: bool = False  # a PROPFIND of one collection (Depth 0) answers 503
        self.makes_nothing: bool = False  # MKCALENDAR and MKCOL answer 207 with every property taken, and nothing is made (Kerio for address books)
        self.empty_for_unknown: bool = False  # a PROPFIND of an unknown address answers an empty 207 (Kerio) instead of 404
        self.no_contact_by_get: bool = False  # a GET of an existing contact answers 404, the multiget gives it (Kerio, checked 2026-10-22)
        self.gets_failing: bool = False  # GET answers 503 (busy for now)
        self.collections: dict[str, Collection] = {
            "events": Collection("Calendar", "calendar", "VEVENT", "#336699FF"), "tasks": Collection("Tasks", "calendar", "VTODO"),
            "contacts": Collection("Contacts", "addressbook"), "inbox": Collection("INBOX", "folder", "VEVENT"),
        }
        self.public_keys: set[str] = set()
        if public:
            self.collections["pubevents"] = Collection("Calendar", "calendar", "VEVENT")
            self.collections["pubcontacts"] = Collection("Public Contacts", "addressbook")
            self.public_keys = {"pubevents", "pubcontacts"}
        self.requests: list[tuple[str, str]] = []
        self.counter: int = 0
        self.certificate: bytes = b""
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int = 0

    @property
    def url(self) -> str:
        return f"{'https' if self.tls else 'http'}://127.0.0.1:{self.port}/dav/"

    def add(self, collection: str, name: str, text: str) -> str:
        entry = self.collections[collection]
        self.counter += 1
        etag = f"e{self.counter}"
        entry.items[name] = (etag, text)
        entry.changed(name, False)
        return etag

    def remove(self, collection: str, name: str) -> None:
        entry = self.collections[collection]
        entry.items.pop(name, None)
        entry.changed(name, True)

    def count(self, method: str) -> int:
        return sum(1 for m, _ in self.requests if m == method)

    # --- the server --------------------------------------------------------------------------

    def __enter__(self) -> Self:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            @override
            def log_message(self, format: str, *args: object) -> None:
                return None

            @override
            def setup(self) -> None:
                outer.connections += 1
                super().setup()

            def do_OPTIONS(self) -> None:  # noqa: N802
                outer.handle(self, "OPTIONS")

            def do_PROPFIND(self) -> None:  # noqa: N802
                outer.handle(self, "PROPFIND")

            def do_REPORT(self) -> None:  # noqa: N802
                outer.handle(self, "REPORT")

            def do_GET(self) -> None:  # noqa: N802
                outer.handle(self, "GET")

            def do_PUT(self) -> None:  # noqa: N802
                outer.handle(self, "PUT")

            def do_DELETE(self) -> None:  # noqa: N802
                outer.handle(self, "DELETE")

            def do_PROPPATCH(self) -> None:  # noqa: N802
                outer.handle(self, "PROPPATCH")

            def do_MKCALENDAR(self) -> None:  # noqa: N802
                outer.handle(self, "MKCALENDAR")

            def do_MKCOL(self) -> None:  # noqa: N802
                outer.handle(self, "MKCOL")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        if self.tls:
            directory = Path(tempfile.mkdtemp())
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake dav server")])
            now = datetime.datetime.now(datetime.UTC)
            certificate = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30)).sign(key, hashes.SHA256())
            (directory / "c.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            (directory / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(directory / "c.pem", directory / "k.pem")
            self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
            self.certificate = certificate.public_bytes(serialization.Encoding.DER)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def respond(self, handler: BaseHTTPRequestHandler, status: int, body: str = "", headers: dict[str, str] | None = None) -> None:
        data = body.encode("utf-8")
        handler.send_response(status)
        for name, value in (headers or {}).items():
            handler.send_header(name, value)
        handler.send_header("Content-Length", str(len(data)))
        if self.say_close:
            handler.send_header("Connection", "close")
        handler.end_headers()
        if self.drop_silently or self.say_close:
            handler.close_connection = True
        if handler.command != "HEAD":
            handler.wfile.write(data)

    def handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        length = int(handler.headers.get("Content-Length", "0") or 0)
        body = handler.rfile.read(length).decode("utf-8") if length else ""
        path = handler.path.split("?", 1)[0]
        self.requests.append((method, path))
        self.logins.append("Authorization" in handler.headers)
        if self.plain_web:
            self.respond(handler, 404, "not here")
            return
        expected = "Basic " + base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        if handler.headers.get("Authorization") != expected:
            self.respond(handler, 401, "no", {"WWW-Authenticate": 'Basic realm="fake"'})
            return
        if path in ("/.well-known/caldav", "/.well-known/carddav"):
            self.respond(handler, 301, "", {"Location": "/dav/"})
            return
        if method == "OPTIONS":
            self.respond(handler, 200, "", {"DAV": "1, calendar-access, addressbook" + (", sync-collection" if self.sync_tokens else "")})
            return
        if method == "PROPFIND":
            self.propfind(handler, path, handler.headers.get("Depth", "0"), body)
        elif method == "REPORT":
            self.report(handler, path, body)
        elif method == "GET":
            found = self.find_item(path)
            if self.gets_failing:
                self.respond(handler, 503, "not now")
            elif found is None or (self.no_contact_by_get and found[1].kind == "addressbook"):
                self.respond(handler, 404)
            else:
                self.respond(handler, 200, found[2][1], {"ETag": f'"{found[2][0]}"', "Content-Type": "text/plain"})
        elif method in ("PUT", "DELETE") and (self.writes_failing or self.writes_refused):
            self.respond(handler, 503 if self.writes_failing else 403, "not now" if self.writes_failing else "not allowed")
        elif method == "PUT":
            self.put(handler, path, body)
        elif method == "DELETE":
            self.delete(handler, path)
        elif method == "PROPPATCH":
            self.proppatch(handler, path, body)
        elif method in ("MKCALENDAR", "MKCOL"):
            self.make(handler, path, body)

    # --- paths -----------------------------------------------------------------------------------

    def make(self, handler: BaseHTTPRequestHandler, path: str, body: str) -> None:
        """A new collection at `path` (in a home): its key is the last part of the path; a place that is taken is 405."""
        import xml.etree.ElementTree as ElementTree

        if self.writes_failing or self.writes_refused:
            self.respond(handler, 503 if self.writes_failing else 403, "not now" if self.writes_failing else "not allowed")
            return
        if self.makes_nothing:
            self.respond(handler, 207, f'<?xml version="1.0" encoding="utf-8"?><d:multistatus {NS}><d:response><d:href>{escape(path)}</d:href><d:propstat><d:prop/><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>')
            return
        key = path.rstrip("/").rsplit("/", 1)[-1]
        if key in self.collections or self.find_collection(path) is not None:
            self.respond(handler, 405)
            return
        root = ElementTree.fromstring(body)
        name = root.findtext(".//{DAV:}displayname") or key
        colour = (root.findtext(".//{http://apple.com/ns/ical/}calendar-color") or "") if self.keeps_colour else ""
        components = ",".join(item.get("name", "") for item in root.iter("{urn:ietf:params:xml:ns:caldav}comp"))
        self.collections[key] = Collection(name, "addressbook" if handler.command == "MKCOL" else "calendar", components, colour)
        if self.collection_path(key).rstrip("/") != path.rstrip("/"):
            del self.collections[key]
            self.respond(handler, 409)  # not in a home
            return
        self.respond(handler, 201)

    def collection_path(self, key: str) -> str:
        home = "/dav/addressbooks/" if self.collections[key].kind == "addressbook" else "/dav/calendars/"
        return f"{home}{'%23public' if key in self.public_keys else 'ann'}/{key}/"

    def find_collection(self, path: str) -> tuple[str, Collection] | None:
        for key, collection in self.collections.items():
            if path.rstrip("/") == self.collection_path(key).rstrip("/"):
                return key, collection
        return None

    def find_item(self, path: str) -> tuple[str, Collection, tuple[str, str]] | None:
        for key, collection in self.collections.items():
            prefix = self.collection_path(key)
            if path.startswith(prefix):
                name = path[len(prefix):]
                if name in collection.items:
                    return key, collection, collection.items[name]
        return None

    def respond_multistatus(self, handler: BaseHTTPRequestHandler, responses: list[str], token: str = "") -> None:
        extra = f"<d:sync-token>{escape(token)}</d:sync-token>" if token else ""
        self.respond(handler, 207, f'<?xml version="1.0" encoding="utf-8"?><d:multistatus {NS}>' + "".join(responses) + extra + "</d:multistatus>", {"Content-Type": "application/xml"})

    @staticmethod
    def entry(href: str, props: str, status: str = "HTTP/1.1 200 OK") -> str:
        return f"<d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop>{props}</d:prop><d:status>{status}</d:status></d:propstat></d:response>"

    def propfind(self, handler: BaseHTTPRequestHandler, path: str, depth: str, body: str) -> None:
        if path.rstrip("/") in ("/dav", "/dav/principals/ann"):
            props = '<d:current-user-principal><d:href>/dav/principals/ann/</d:href></d:current-user-principal>'
            if path.rstrip("/") == "/dav/principals/ann":
                props = '<c:calendar-home-set><d:href>/dav/calendars/ann/</d:href></c:calendar-home-set><card:addressbook-home-set><d:href>/dav/addressbooks/ann/</d:href></card:addressbook-home-set>'
            self.respond_multistatus(handler, [self.entry(path, props)])
            return
        homes = ("/dav/calendars/ann", "/dav/addressbooks/ann") + (("/dav/calendars/%23public", "/dav/addressbooks/%23public") if self.public_keys else ())
        if path.rstrip("/") in homes and self.homes_failing:
            self.respond(handler, 503, "busy")
            return
        if path.rstrip("/") in homes:
            wanted_kind = "addressbook" if "addressbooks" in path else "calendar"
            wanted_public = "%23public" in path
            responses = [self.entry(path, "<d:resourcetype><d:collection/></d:resourcetype>")]
            if depth != "0":
                for key, collection in self.collections.items():
                    if (collection.kind == "addressbook") != (wanted_kind == "addressbook") or (key in self.public_keys) != wanted_public:
                        continue
                    kind_xml = {"calendar": "<c:calendar/>", "addressbook": "<card:addressbook/>", "folder": ""}[collection.kind]
                    comps = "".join(f'<c:comp name="{c}"/>' for c in collection.components.split(",") if c)
                    props = (
                        f"<d:displayname>{escape(collection.name)}</d:displayname><d:resourcetype><d:collection/>{kind_xml}</d:resourcetype>"
                        + (f"<c:supported-calendar-component-set>{comps}</c:supported-calendar-component-set>" if comps else "")
                        + f"<cs:getctag>ctag-{collection.version}</cs:getctag>"
                        + (f"<d:sync-token>token-{collection.version}</d:sync-token>" if self.sync_tokens else "")
                        + (f"<a:calendar-color>{collection.colour}</a:calendar-color>" if collection.colour else "")
                        + ("<d:current-user-privilege-set><d:privilege><d:read/></d:privilege></d:current-user-privilege-set>" if key in self.read_only else "")
                    )
                    responses.append(self.entry(self.collection_path(key), props))
            self.respond_multistatus(handler, responses)
            return
        if self.collection_check_failing and depth == "0" and path.rstrip("/").count("/") == 4:
            self.respond(handler, 503, "busy")
            return
        found = self.find_collection(path)
        if found is not None:
            responses = [self.entry(path, "<d:resourcetype><d:collection/></d:resourcetype>")]
            if depth != "0":
                for name, (etag, _) in found[1].items.items():
                    responses.append(self.entry(path + name, f'<d:getetag>"{etag}"</d:getetag><d:resourcetype/>'))
            self.respond_multistatus(handler, responses)
            return
        item = self.find_item(path)
        if item is not None:
            self.respond_multistatus(handler, [self.entry(path, f'<d:getetag>"{item[2][0]}"</d:getetag>')])
            return
        if self.empty_for_unknown:
            self.respond_multistatus(handler, [])
            return
        self.respond(handler, 404)

    def proppatch(self, handler: BaseHTTPRequestHandler, path: str, body: str) -> None:
        """A name and/or colour for a collection, all or nothing (RFC 4918 9.2): a refused property is 403, the others then 424."""
        import xml.etree.ElementTree as ElementTree

        found = self.find_collection(path)
        if found is None:
            self.respond(handler, 404)
            return
        key, collection = found
        if self.failing:
            self.respond(handler, 503, "busy")
            return
        if key in self.read_only:
            self.respond(handler, 403, "read only")
            return
        root = ElementTree.fromstring(body)
        name = root.find(".//{DAV:}displayname")
        colour = root.find(".//{http://apple.com/ns/ical/}calendar-color")
        refused = {"name": name is not None and self.refuses_names, "colour": colour is not None and not self.keeps_colour}
        failed = any(refused.values())
        stats: list[str] = []
        for part, element, tag in (("name", name, "<d:displayname/>"), ("colour", colour, "<a:calendar-color/>")):
            if element is not None:
                status = "403 Forbidden" if refused[part] else ("424 Failed Dependency" if failed else "200 OK")
                stats.append(f"<d:propstat><d:prop>{tag}</d:prop><d:status>HTTP/1.1 {status}</d:status></d:propstat>")
        if not failed:
            if name is not None:
                collection.name = name.text or ""
            if colour is not None:
                collection.colour = colour.text or ""
        self.respond(handler, 207, f'<?xml version="1.0" encoding="utf-8"?><d:multistatus {NS}><d:response><d:href>{escape(path)}</d:href>' + "".join(stats) + "</d:response></d:multistatus>", {"Content-Type": "application/xml"})

    def report(self, handler: BaseHTTPRequestHandler, path: str, body: str) -> None:
        found = self.find_collection(path)
        if found is None:
            self.respond(handler, 404)
            return
        key, collection = found
        if "sync-collection" in body:
            if not self.sync_tokens:
                self.respond(handler, 501)
                return
            match = re.search(r"<d:sync-token>(.*?)</d:sync-token>", body)
            token = match.group(1) if match else ""
            known = re.fullmatch(r"token-(\d+)", token) or re.fullmatch(r"token-cut-\d+", token)
            if token and known is None:
                self.respond(handler, 403)  # a token this server never gave
                return
            since = int(known.group(1)) if known and token.startswith("token-") and not token.startswith("token-cut-") else 0
            responses: list[str] = []
            written = (lambda name: name.replace("@", "%40")) if self.like_kerio else (lambda name: name)
            times = 2 if self.like_kerio else 1
            if since == 0:
                for name, (etag, _) in collection.items.items():
                    responses += [self.entry(self.collection_path(key) + written(name), f'<d:getetag>"{etag}"</d:getetag>')] * times
            else:
                latest: dict[str, bool] = {}
                for version, name, removed in collection.log:
                    if version > since:
                        latest[name] = removed
                for name, removed in latest.items():
                    if removed or name not in collection.items:
                        responses.append(f"<d:response><d:href>{escape(self.collection_path(key) + written(name))}</d:href><d:status>HTTP/1.1 404 Not Found</d:status></d:response>")
                    else:
                        responses += [self.entry(self.collection_path(key) + written(name), f'<d:getetag>"{collection.items[name][0]}"</d:getetag>')] * times
            if self.cut_after and len(responses) > self.cut_after:
                # the next round starts after what was given: the token says how many of the full answer are done
                done = int(re.search(r"token-cut-(\d+)", body).group(1)) if "token-cut-" in body else 0
                part = responses[done : done + self.cut_after]
                if done + self.cut_after < len(responses):
                    part.append(f"<d:response><d:href>{escape(self.collection_path(key))}</d:href><d:status>HTTP/1.1 507 Insufficient Storage</d:status></d:response>")
                    self.respond_multistatus(handler, part, f"token-cut-{done + self.cut_after}")
                    return
                self.respond_multistatus(handler, part, f"token-{collection.version}")
                return
            self.respond_multistatus(handler, responses, f"token-{collection.version}")
            return
        if "multiget" in body:
            if not self.multiget:
                self.respond(handler, 501)
                return
            hrefs = [urllib.parse.unquote(href) for href in re.findall(r"<d:href>(.*?)</d:href>", body)]
            responses = []
            for href in hrefs:
                name = href[len(self.collection_path(key)):]
                if name in collection.items:
                    etag, text = collection.items[name]
                    tag = "card:address-data" if collection.kind == "addressbook" else "c:calendar-data"
                    responses.append(self.entry(href, f'<d:getetag>"{etag}"</d:getetag><{tag}>{escape(text)}</{tag}>'))
                else:
                    responses.append(f"<d:response><d:href>{escape(href)}</d:href><d:status>HTTP/1.1 404 Not Found</d:status></d:response>")
            self.respond_multistatus(handler, responses)
            return
        self.respond(handler, 400)

    def put(self, handler: BaseHTTPRequestHandler, path: str, body: str) -> None:
        for key, collection in self.collections.items():
            prefix = self.collection_path(key)
            if path.startswith(prefix) and "/" not in path[len(prefix):]:
                if key in self.read_only:
                    self.respond(handler, 403)
                    return
                name = path[len(prefix):]
                existing = collection.items.get(name)
                if handler.headers.get("If-None-Match") == "*" and existing is not None:
                    self.respond(handler, 412)
                    return
                match = handler.headers.get("If-Match")
                if match is not None and (existing is None or f'"{existing[0]}"' != match):
                    self.respond(handler, 412)
                    return
                etag = self.add(key, name, body)
                self.respond(handler, 201 if existing is None else 204, "", {"ETag": f'"{etag}"'} if self.etag_on_put else {})
                return
        self.respond(handler, 404)

    def delete(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        whole = self.find_collection(path)
        if whole is not None:
            del self.collections[whole[0]]
            self.respond(handler, 204)
            return
        found = self.find_item(path)
        if found is None:
            self.respond(handler, 404)
            return
        match = handler.headers.get("If-Match")
        if match is not None and f'"{found[2][0]}"' != match:
            self.respond(handler, 412)
            return
        self.remove(found[0], path[len(self.collection_path(found[0])):])
        self.respond(handler, 204)
