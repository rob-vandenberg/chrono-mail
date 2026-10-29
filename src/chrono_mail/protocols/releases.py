import hashlib
import http.client
import ssl
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO

from chrono_mail.core import ChronoMailError, ProtocolError
from chrono_mail.core.jsontext import load_json
from chrono_mail.core.words import digits

# --- Version ------------------------------------------------------------
__version__ = "releases.py 4.2.419"

# --- Version history ----------------------------------------------------
# v4.2.419: Initial: the releases of Chrono Mail on GitHub and the download of one installer, every answer capped (D-293)

# The list of releases comes from GitHub's own address for them; a download starts at the release's file address and is passed on by
# GitHub to its file server. Only those GitHub addresses are used, always over https with the system's certificate check; plain http only
# to this computer (the tests).
RELEASES_ADDRESS: str = "https://api.github.com/repos/rob-vandenberg/chrono-mail/releases?per_page=30"
GITHUB_HOSTS: frozenset[str] = frozenset({"api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"})
LOCAL_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})
MAXIMUM_LIST_BYTES: int = 4 * 1024 * 1024
MAXIMUM_INSTALLER_BYTES: int = 1024 * 1024 * 1024
MAXIMUM_REDIRECTS: int = 5
MAXIMUM_RELEASES: int = 100
MAXIMUM_FILES: int = 50
CHUNK_BYTES: int = 1024 * 1024
TIMEOUT_SECONDS: float = 30.0  # a server that sends nothing for this long has stalled


class DownloadStopped(ChronoMailError):
    """The person stopped the download."""


@dataclass(frozen=True, slots=True)
class ReleaseFile:
    name: str
    size: int  # bytes
    sha256: str  # the fingerprint GitHub gives, 64 lower-case hexadecimal characters
    address: str  # where it is downloaded


@dataclass(frozen=True, slots=True)
class Release:
    version: tuple[int, ...]
    tag: str
    page: str  # the release's page on GitHub
    files: tuple[ReleaseFile, ...]


def parse_version(text: str) -> tuple[int, ...] | None:
    """A version as numbers (4.2.419 -> (4, 2, 419)); None for anything else (three or four numbers of the digits 0 to 9 only)."""
    parts = text.split(".")
    if len(parts) not in (3, 4) or not all(digits(part) and len(part) <= 9 for part in parts):
        return None
    return tuple(int(part) for part in parts)


def version_text(version: tuple[int, ...]) -> str:
    return ".".join(str(number) for number in version)


def _text(item: dict[str, object], key: str) -> str:
    value = item.get(key)
    return value if isinstance(value, str) else ""


def _file(item: object) -> ReleaseFile | None:
    if not isinstance(item, dict):
        return None
    name, digest, address = _text(item, "name"), _text(item, "digest"), _text(item, "browser_download_url")
    size = item.get("size")
    fingerprint = digest.removeprefix("sha256:")
    if not name or not address or not digest.startswith("sha256:") or len(fingerprint) != 64 or any(character not in "0123456789abcdef" for character in fingerprint):
        return None  # no fingerprint: never downloaded, since it could not be checked
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAXIMUM_INSTALLER_BYTES:
        return None
    return ReleaseFile(name, size, fingerprint, address)


def parse_releases(body: bytes) -> tuple[Release, ...]:
    """The published releases in GitHub's answer (drafts and pre-releases left out); a release or file that is not what it should be is left out,
    an answer that is not a list of releases is an error."""
    try:
        data = load_json(body)
    except (ValueError, UnicodeDecodeError):
        raise ProtocolError("GitHub's list of releases could not be read") from None
    if not isinstance(data, list):
        raise ProtocolError("GitHub's list of releases could not be read")
    releases: list[Release] = []
    for item in data[:MAXIMUM_RELEASES]:
        if not isinstance(item, dict) or item.get("draft") is not False or item.get("prerelease") is not False:
            continue
        tag = _text(item, "tag_name")
        version = parse_version(tag)
        page = _text(item, "html_url")
        assets = item.get("assets")
        if version is None or not page.startswith(("https://", "http://")) or not isinstance(assets, list):
            continue
        files = tuple(found for found in (_file(asset) for asset in assets[:MAXIMUM_FILES]) if found is not None)
        releases.append(Release(version, tag, page, files))
    return tuple(releases)


def _allowed(address: str) -> urllib.parse.SplitResult:
    parts = urllib.parse.urlsplit(address)
    host = parts.hostname or ""
    if (parts.scheme == "https" and host in GITHUB_HOSTS) or (parts.scheme == "http" and host in LOCAL_HOSTS):
        return parts
    raise ProtocolError("An update is only taken from GitHub")


def _response(address: str, accept: str, user_agent: str, timeout: float) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    """The answer to a GET of the address, after GitHub's redirects (only to its own addresses). The caller reads it and closes the connection."""
    for _ in range(MAXIMUM_REDIRECTS + 1):
        parts = _allowed(address)
        host = parts.hostname or ""
        if parts.scheme == "https":
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(host, parts.port or 443, timeout=timeout, context=ssl.create_default_context())
        else:
            connection = http.client.HTTPConnection(host, parts.port or 80, timeout=timeout)
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        try:
            connection.request("GET", target, headers={"Accept": accept, "User-Agent": user_agent})
            response = connection.getresponse()
        except BaseException:
            connection.close()
            raise
        if response.status in (301, 302, 303, 307, 308):
            location = response.getheader("Location") or ""
            connection.close()
            if not location:
                raise ProtocolError("GitHub passed the request on without saying where")
            address = urllib.parse.urljoin(address, location)
            continue
        return connection, response
    raise ProtocolError("GitHub passed the request on too often")


def fetch_releases(address: str, user_agent: str, timeout: float = TIMEOUT_SECONDS) -> tuple[Release, ...]:
    """The published releases of Chrono Mail, newest first as GitHub lists them."""
    try:
        connection, response = _response(address, "application/vnd.github+json", user_agent, timeout)
        try:
            if response.status != 200:
                raise ProtocolError(f"GitHub answered {response.status}")
            body = response.read(MAXIMUM_LIST_BYTES + 1)
        finally:
            connection.close()
    except (OSError, http.client.HTTPException) as error:
        raise ProtocolError(f"GitHub could not be reached: {error}") from None
    if len(body) > MAXIMUM_LIST_BYTES:
        raise ProtocolError("GitHub's list of releases is too large")
    return parse_releases(body)


def download(
    file: ReleaseFile, sink: BinaryIO, user_agent: str, progress: Callable[[int, int], None], stopped: Callable[[], bool], timeout: float = TIMEOUT_SECONDS,
) -> str:
    """Download the file into sink and give its SHA-256 fingerprint. Never more than the size GitHub gave is taken; progress tells the bytes so
    far and the size; stopped is asked between the pieces (DownloadStopped when it says so)."""
    hasher = hashlib.sha256()
    received = 0
    try:
        connection, response = _response(file.address, "application/octet-stream", user_agent, timeout)
        try:
            if response.status != 200:
                raise ProtocolError(f"GitHub answered {response.status}")
            while True:
                if stopped():
                    raise DownloadStopped("The download was stopped")
                piece = response.read(CHUNK_BYTES)
                if not piece:
                    break
                received += len(piece)
                if received > file.size:
                    raise ProtocolError("The download is larger than GitHub said")
                hasher.update(piece)
                sink.write(piece)
                progress(received, file.size)
        finally:
            connection.close()
    except (OSError, http.client.HTTPException) as error:
        raise ProtocolError(f"The download did not complete: {error}") from None  # the network, or a full disk
    if received != file.size:
        raise ProtocolError("The download is not complete")
    return hasher.hexdigest()
