import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Self, override

# --- Version ------------------------------------------------------------
__version__ = "fakegithub.py 4.2.419"

# --- Version history ----------------------------------------------------
# v4.2.419: Initial: a stand-in for GitHub's list of releases and its downloads, on this computer (D-293)


class FakeGitHub:
    """GitHub's list of releases at /releases and each release file at /download/<name>, passed on to /files/<name> as GitHub does. A file's
    fingerprint is right unless wrong_digest names it; a release is published unless draft or prerelease says otherwise."""

    def __init__(self) -> None:
        self.releases: list[dict[str, object]] = []
        self.contents: dict[str, bytes] = {}
        self.wrong_digest: set[str] = set()
        self.requests: list[str] = []
        self.body: bytes | None = None  # an answer of its own for /releases (hostile input)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def releases_address(self) -> str:
        return f"{self.address}/releases"

    def publish(self, version: str, files: dict[str, bytes], draft: bool = False, prerelease: bool = False) -> None:
        """A release with these files (name -> content), newest first as GitHub lists them."""
        assets: list[dict[str, object]] = []
        for name, content in files.items():
            self.contents[name] = content
            digest = hashlib.sha256(content + (b"!" if name in self.wrong_digest else b"")).hexdigest()
            assets.append({"name": name, "size": len(content), "digest": f"sha256:{digest}", "browser_download_url": f"{self.address}/download/{name}"})
        release: dict[str, object] = {"tag_name": version, "html_url": f"{self.address}/release/{version}", "draft": draft, "prerelease": prerelease, "assets": assets}
        self.releases.insert(0, release)

    def __enter__(self) -> Self:
        github = self

        class Handler(BaseHTTPRequestHandler):
            @override
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the name http.server gives it
                pass

            def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
                github.requests.append(self.path)
                if self.path.startswith("/releases"):
                    self._send(200, github.body if github.body is not None else json.dumps(github.releases).encode())
                elif self.path.startswith("/download/"):
                    self.send_response(302)
                    self.send_header("Location", "/files/" + self.path.removeprefix("/download/"))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif self.path.startswith("/files/") and self.path.removeprefix("/files/") in github.contents:
                    self._send(200, github.contents[self.path.removeprefix("/files/")])
                else:
                    self._send(404, b"")

            def _send(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None) -> None:
        assert self._server is not None and self._thread is not None
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(5)
