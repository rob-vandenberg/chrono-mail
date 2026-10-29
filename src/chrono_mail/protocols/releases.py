import hashlib
import http.client
import socket
import ssl
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO

from chrono_mail.core import ChronoMailError, ProtocolError
from chrono_mail.core.jsontext import load_json
from chrono_mail.core.words import digits

# --- Version ------------------------------------------------------------
__version__ = "releases.py 4.2.427"

# --- Version history ----------------------------------------------------
# v4.2.427: a download takes what has arrived each time and can be interrupted at once (opened gives the means: its connection is shut),
#           so a stop never waits for a slow or stalled server (registered as soon as each hop is connected, before its answer, also
#           for the list of releases a download reads first); GitHubBusy: GitHub answered with something else than what was asked; DownloadInterrupted: the network broke off a download (a full
#           disk is told apart); GitHubUnreachable: GitHub was not reached at all
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


class GitHubUnreachable(ProtocolError):
    """GitHub could not be reached (no network, no answer, or passed on to somewhere else); any other failure means it was reached."""


class DownloadInterrupted(GitHubUnreachable):
    """The connection to GitHub broke off during the download (the network, never the disk)."""


class _WriteFailed(Exception):
    """Saving a piece of the download failed (the disk): its OSError, kept apart from the network's."""

    def __init__(self, error: OSError) -> None:
        super().__init__(str(error))
        self.error: OSError = error


class GitHubBusy(ProtocolError):
    """GitHub answered, but not with what was asked (too many questions from this network, an outage)."""


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
    raise GitHubUnreachable("An update is only taken from GitHub")  # passed on elsewhere (a network's own sign-in page): GitHub was not reached


def _response(
    address: str, accept: str, user_agent: str, timeout: float, connected: Callable[[socket.socket | None], None] | None = None,
    stopped: Callable[[], bool] | None = None,
) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    """The answer to a GET of the address, after GitHub's redirects (only to its own addresses). connected gets the socket of every hop as soon
    as it is connected, before anything is asked (a stop shuts it, also while the server keeps its answer back); stopped is asked before each
    hop. The caller reads the answer and closes the connection."""
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
            if stopped is not None and stopped():
                raise DownloadStopped("The download was stopped")
            connection.connect()
            if connected is not None:
                connected(connection.sock)
            if stopped is not None and stopped():
                raise DownloadStopped("The download was stopped")
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
    raise GitHubUnreachable("GitHub passed the request on too often")


def fetch_releases(
    address: str, user_agent: str, timeout: float = TIMEOUT_SECONDS, connected: Callable[[socket.socket | None], None] | None = None,
    stopped: Callable[[], bool] | None = None,
) -> tuple[Release, ...]:
    """The published releases of Chrono Mail, newest first as GitHub lists them (connected and stopped: as for _response, for a download that
    can be stopped while its list is read)."""
    try:
        connection, response = _response(address, "application/vnd.github+json", user_agent, timeout, connected, stopped)
        try:
            if response.status != 200:
                raise GitHubBusy(f"GitHub answered {response.status}")
            body = response.read(MAXIMUM_LIST_BYTES + 1)
        finally:
            connection.close()
    except DownloadStopped:
        raise
    except (OSError, http.client.HTTPException) as error:
        if stopped is not None and stopped():
            raise DownloadStopped("The download was stopped") from None
        raise GitHubUnreachable(f"GitHub could not be reached: {error}") from None
    except Exception:
        if stopped is not None and stopped():
            raise DownloadStopped("The download was stopped") from None  # a shut encrypted socket can also fail this way
        raise
    if stopped is not None and stopped():
        raise DownloadStopped("The download was stopped")  # a shut connection reads as a short list
    if len(body) > MAXIMUM_LIST_BYTES:
        raise ProtocolError("GitHub's list of releases is too large")
    return parse_releases(body)


def interrupter(sock: socket.socket | None) -> Callable[[], None]:
    """What stops a download at once from another thread: its socket is shut, so a read that waits for the server returns."""
    def interrupt() -> None:
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # closed already
    return interrupt


def download(
    file: ReleaseFile, sink: BinaryIO, user_agent: str, progress: Callable[[int, int], None], stopped: Callable[[], bool], timeout: float = TIMEOUT_SECONDS,
    opened: Callable[[Callable[[], None]], None] | None = None,
) -> str:
    """Download the file into sink and give its SHA-256 fingerprint. Never more than the size GitHub gave is taken; progress tells the bytes so
    far and the size; stopped is asked after every read, which takes what has arrived (DownloadStopped when it says so); opened gets, once the
    server answered, what interrupts a read that waits."""
    hasher = hashlib.sha256()
    received = 0

    def connected(sock: socket.socket | None) -> None:
        if opened is not None:
            opened(interrupter(sock))

    try:
        connection, response = _response(file.address, "application/octet-stream", user_agent, timeout, connected, stopped)
        try:
            if response.status != 200:
                raise GitHubBusy(f"GitHub answered {response.status}")
            while True:
                if stopped():
                    raise DownloadStopped("The download was stopped")
                piece = response.read1(CHUNK_BYTES)  # what has arrived, never waiting for a whole piece
                if not piece:
                    break
                received += len(piece)
                if received > file.size:
                    raise ProtocolError("The download is larger than GitHub said")
                hasher.update(piece)
                try:
                    sink.write(piece)
                except OSError as error:
                    raise _WriteFailed(error) from None  # the disk, told as such
                progress(received, file.size)
        finally:
            connection.close()
    except DownloadStopped:
        raise
    except _WriteFailed as failed:
        if stopped():
            raise DownloadStopped("The download was stopped") from None
        raise failed.error from None  # the caller says where it could not be saved
    except (OSError, http.client.HTTPException) as error:
        if stopped():
            raise DownloadStopped("The download was stopped") from None  # interrupted: its connection was shut
        raise DownloadInterrupted(f"The download was interrupted: {error}") from None  # the network
    except Exception:
        if stopped():
            raise DownloadStopped("The download was stopped") from None  # a shut encrypted socket can also fail this way
        raise
    if stopped():
        raise DownloadStopped("The download was stopped")  # interrupted: the shut connection read as its end
    if received != file.size:
        raise DownloadInterrupted("The download was interrupted before it was complete")
    return hasher.hexdigest()
