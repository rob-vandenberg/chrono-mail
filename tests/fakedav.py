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
__version__ = "fakedav.py 0.0.65"

# --- Version history ----------------------------------------------------
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

    def __init__(self, user: str = "ann", password: str = "secret", sync_tokens: bool = True, multiget: bool = True, etag_on_put: bool = True, tls: bool = False, read_only: tuple[str, ...] = ()) -> None:
        self.user, self.password = user, password
        self.sync_tokens, self.multiget, self.etag_on_put, self.tls = sync_tokens, multiget, etag_on_put, tls
        self.read_only: tuple[str, ...] = read_only
        self.collections: dict[str, Collection] = {
            "events": Collection("Calendar", "calendar", "VEVENT", "#336699FF"), "tasks": Collection("Tasks", "calendar", "VTODO"),
            "contacts": Collection("Contacts", "addressbook"), "inbox": Collection("INBOX", "folder", "VEVENT"),
        }
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
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(data)

    def handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        length = int(handler.headers.get("Content-Length", "0") or 0)
        body = handler.rfile.read(length).decode("utf-8") if length else ""
        path = handler.path.split("?", 1)[0]
        self.requests.append((method, path))
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
            if found is None:
                self.respond(handler, 404)
            else:
                self.respond(handler, 200, found[2][1], {"ETag": f'"{found[2][0]}"', "Content-Type": "text/plain"})
        elif method == "PUT":
            self.put(handler, path, body)
        elif method == "DELETE":
            self.delete(handler, path)

    # --- paths -----------------------------------------------------------------------------------

    def collection_path(self, key: str) -> str:
        home = "/dav/addressbooks/ann/" if self.collections[key].kind == "addressbook" else "/dav/calendars/ann/"
        return f"{home}{key}/"

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
        if path.rstrip("/") in ("/dav/calendars/ann", "/dav/addressbooks/ann"):
            wanted_kind = "addressbook" if "addressbooks" in path else "calendar"
            responses = [self.entry(path, "<d:resourcetype><d:collection/></d:resourcetype>")]
            if depth != "0":
                for key, collection in self.collections.items():
                    if (collection.kind == "addressbook") != (wanted_kind == "addressbook"):
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
        self.respond(handler, 404)

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
            known = re.fullmatch(r"token-(\d+)", token)
            if token and known is None:
                self.respond(handler, 403)  # a token this server never gave
                return
            since = int(known.group(1)) if known else 0
            responses: list[str] = []
            if since == 0:
                for name, (etag, _) in collection.items.items():
                    responses.append(self.entry(self.collection_path(key) + name, f'<d:getetag>"{etag}"</d:getetag>'))
            else:
                latest: dict[str, bool] = {}
                for version, name, removed in collection.log:
                    if version > since:
                        latest[name] = removed
                for name, removed in latest.items():
                    if removed or name not in collection.items:
                        responses.append(f"<d:response><d:href>{escape(self.collection_path(key) + name)}</d:href><d:status>HTTP/1.1 404 Not Found</d:status></d:response>")
                    else:
                        responses.append(self.entry(self.collection_path(key) + name, f'<d:getetag>"{collection.items[name][0]}"</d:getetag>'))
            self.respond_multistatus(handler, responses, f"token-{collection.version}")
            return
        if "multiget" in body:
            if not self.multiget:
                self.respond(handler, 501)
                return
            hrefs = re.findall(r"<d:href>(.*?)</d:href>", body)
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
