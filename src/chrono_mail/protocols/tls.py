import hashlib
import socket
import ssl
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import override

from cryptography import x509
from cryptography.x509.oid import NameOID

from chrono_mail.core import CertificateError, CertificateTrouble, ConnectionSecurity, ProtocolError

# --- Version ------------------------------------------------------------
__version__ = "tls.py 4.2.441"

# --- Version history ----------------------------------------------------
# v4.2.441: a server with an accepted certificate that now shows another one is connected when that other one passes the usual checks
#           (a renewal by a known authority); otherwise CertificateChanged. failure() names the server (CertificateError, D-299);
#           the weak choice (ConnectionSecurity.TLS_UNCHECKED) encrypts without checking: unchecked_context, client_context (OR-6)
# v0.0.43: Initial certificate pinning and inspection (certificate exceptions)

# A person may accept the certificate of a server that the usual checks refuse (a self-signed or privately issued one).
# Accepting means remembering the SHA-256 fingerprint of exactly that certificate. From then on the connection is
# trusted if, and only if, the server presents that certificate: a different one (an attack, or a renewed certificate)
# is refused again until the person accepts it, unless it passes the usual checks by itself (AGENTS.md section 4.6.4). Nothing is accepted
# silently; only the person's own choice of the weak security connects without any check (D-299).

FINGERPRINT_LENGTH: int = 64


def fingerprint(der: bytes) -> str:
    """The SHA-256 fingerprint of a certificate (DER), as 64 lower-case hexadecimal characters."""
    return hashlib.sha256(der).hexdigest()


def valid_fingerprint(text: str) -> bool:
    return len(text) == FINGERPRINT_LENGTH and all(character in "0123456789abcdef" for character in text)


def readable(text: str) -> str:
    """A fingerprint in groups of two, for showing: AB:CD:..."""
    return ":".join(text[position : position + 2] for position in range(0, len(text), 2)).upper()


class CertificateChanged(ssl.SSLCertVerificationError):
    """The server shows another certificate than the one the person accepted, and the usual checks do not accept it either."""


CHECK_TIMEOUT_SECONDS: float = 15.0


class PinnedContext(ssl.SSLContext):
    """Trusts exactly the certificates whose fingerprints are listed: the handshake does not look at the chain or the host name, and the
    certificate is compared with the list at once. Another certificate is trusted only when the usual checks accept that very certificate
    (a renewal by a known authority); every other one is refused as changed."""

    trusted: frozenset[str]
    security: ConnectionSecurity
    kind: str

    def __new__(cls, trusted: frozenset[str], security: ConnectionSecurity = ConnectionSecurity.TLS, kind: str = "imap") -> "PinnedContext":
        context = super().__new__(cls, ssl.PROTOCOL_TLS_CLIENT)
        context.trusted = trusted
        context.security = security
        context.kind = kind
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    @override
    def wrap_socket(
        self,
        sock: socket.socket,
        server_side: bool = False,
        do_handshake_on_connect: bool = True,
        suppress_ragged_eofs: bool = True,
        server_hostname: str | bytes | None = None,
        session: ssl.SSLSession | None = None,
    ) -> ssl.SSLSocket:
        wrapped = super().wrap_socket(sock, server_side, do_handshake_on_connect, suppress_ragged_eofs, server_hostname, session)
        der = wrapped.getpeercert(binary_form=True)
        if der is not None and fingerprint(der) in self.trusted:
            return wrapped
        host = server_hostname.decode("ascii", "replace") if isinstance(server_hostname, bytes) else server_hostname
        try:
            port = int(wrapped.getpeername()[1])
        except (OSError, IndexError, TypeError, ValueError):
            port = 0
        if der is not None and host and port and _passes_usual_checks(host, port, self.security, self.kind, fingerprint(der)):
            return wrapped
        wrapped.close()
        error = CertificateChanged(1, "The server's certificate is not the one you accepted")
        error.verify_message = "it is not the certificate you accepted (it was changed or replaced)"
        raise error


_PASSED: set[tuple[str, int, str]] = set()  # (host, port, fingerprint) the usual checks accepted in this run: not asked again each time
_PASSED_LOCK: threading.Lock = threading.Lock()


def _passes_usual_checks(host: str, port: int, security: ConnectionSecurity, kind: str, shown: str) -> bool:
    """Would the usual checks accept exactly the certificate with this fingerprint? Asked over a connection of its own (nothing is sent that
    could log in), and only when the server shows a certificate the person did not accept; a yes is remembered for this run."""
    with _PASSED_LOCK:
        if (host.lower(), port, shown) in _PASSED:
            return True
    try:
        sock = _handshake(host, port, security, kind, ssl.create_default_context(), CHECK_TIMEOUT_SECONDS)
    except (OSError, ssl.SSLError, ProtocolError):
        return False
    try:
        der = sock.getpeercert(binary_form=True)
    finally:
        sock.close()
    if der is None or fingerprint(der) != shown:
        return False
    with _PASSED_LOCK:
        _PASSED.add((host.lower(), port, shown))
    return True


def context_for(trusted: frozenset[str], security: ConnectionSecurity = ConnectionSecurity.TLS, kind: str = "imap") -> ssl.SSLContext | None:
    """The context a client must use for a server with accepted certificates; None when there are none (the normal checks apply)."""
    return PinnedContext(trusted, security, kind) if trusted else None


def unchecked_context() -> ssl.SSLContext:
    """Encrypted, the certificate not checked: only for the person's own choice of the weak security (OR-6, D-299)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def client_context(security: ConnectionSecurity, given: ssl.SSLContext | None) -> ssl.SSLContext:
    """The context a mail client connects with: unchecked for the weak security, else the one given, else the usual checks."""
    if security is ConnectionSecurity.TLS_UNCHECKED:
        return unchecked_context()
    return given if given is not None else ssl.create_default_context()


def failure(error: ssl.SSLCertVerificationError, host: str, port: int, security: ConnectionSecurity, kind: str) -> CertificateError:
    """The error that names the server whose certificate could not be verified, so the window can show it and ask (D-299)."""
    reason = error.verify_message or "the certificate could not be verified"
    trouble = CertificateTrouble(host, port, "starttls" if security is ConnectionSecurity.STARTTLS else "tls", kind, isinstance(error, CertificateChanged))
    return CertificateError(f"The server's certificate could not be verified: {reason}", trouble)


@dataclass(frozen=True, slots=True)
class CertificateInfo:
    fingerprint: str
    subject: str
    issuer: str
    names: tuple[str, ...]
    not_before: str
    not_after: str
    expired: bool
    verified: bool  # would the usual checks accept it?
    problem: str | None  # why not


def _name(name: x509.Name) -> str:
    common = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    organisation = name.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
    return ", ".join(str(item.value) for item in (*common, *organisation)) or name.rfc4514_string()


def describe(der: bytes) -> tuple[str, str, tuple[str, ...], datetime, datetime]:
    certificate = x509.load_der_x509_certificate(der)
    try:
        names = tuple(certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        names = ()
    return _name(certificate.subject), _name(certificate.issuer), names[:20], certificate.not_valid_before_utc, certificate.not_valid_after_utc


# --- looking at a server's certificate, without logging in ----------------------------------------------------------


def _line(sock: socket.socket) -> bytes:
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(1)
        if not chunk or len(data) > 4096:
            raise ProtocolError("The server did not answer as expected")
        data += chunk
    return data


def _start_tls(sock: socket.socket, kind: str) -> None:
    """The few lines of each protocol that end in the start of TLS (nothing is sent that could log in)."""
    if kind == "pop3":
        _line(sock)
        sock.sendall(b"STLS\r\n")
        if not _line(sock).startswith(b"+OK"):
            raise ProtocolError("The server does not offer STLS")
    elif kind == "imap":
        _line(sock)
        sock.sendall(b"a1 STARTTLS\r\n")
        while True:
            reply = _line(sock)
            if reply.startswith(b"a1 "):
                if not reply.startswith(b"a1 OK"):
                    raise ProtocolError("The server does not offer STARTTLS")
                return
    elif kind == "smtp":
        while _line(sock)[3:4] == b"-":
            pass  # a greeting of several lines (220-) ends with its 220 line
        sock.sendall(b"EHLO chrono-mail.invalid\r\n")
        while True:
            reply = _line(sock)
            if reply[3:4] != b"-":
                break
        sock.sendall(b"STARTTLS\r\n")
        if not _line(sock).startswith(b"220"):
            raise ProtocolError("The server does not offer STARTTLS")
    else:
        raise ProtocolError("Unknown kind of server")


def _handshake(host: str, port: int, security: ConnectionSecurity, kind: str, context: ssl.SSLContext, timeout: float) -> ssl.SSLSocket:
    raw = socket.create_connection((host, port), timeout=timeout)
    raw.settimeout(timeout)
    try:
        if security.from_start:
            return context.wrap_socket(raw, server_hostname=host)
        if security is ConnectionSecurity.NONE:
            raise ProtocolError("This connection is not encrypted: there is no certificate to look at")
        _start_tls(raw, kind)
        return context.wrap_socket(raw, server_hostname=host)
    except BaseException:
        raw.close()
        raise


def inspect_certificate(host: str, port: int, security: ConnectionSecurity, kind: str, timeout: float = 15.0) -> CertificateInfo:
    """Connect and read the certificate the server presents, and say whether the usual checks would accept it."""
    unchecked = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    unchecked.check_hostname = False
    unchecked.verify_mode = ssl.CERT_NONE
    try:
        sock = _handshake(host, port, security, kind, unchecked, timeout)
        try:
            der = sock.getpeercert(binary_form=True)
        finally:
            sock.close()
    except (OSError, ssl.SSLError) as error:
        raise ProtocolError(f"Connection failed: {getattr(error, 'strerror', None) or type(error).__name__}") from None
    if der is None:
        raise ProtocolError("The server did not present a certificate")
    problem: str | None = None
    try:
        _handshake(host, port, security, kind, ssl.create_default_context(), timeout).close()
    except ssl.SSLCertVerificationError as error:
        problem = error.verify_message or "the certificate could not be verified"
    except (OSError, ssl.SSLError) as error:
        problem = f"the secure connection failed: {type(error).__name__}"
    try:
        subject, issuer, names, start, end = describe(der)
    except ValueError:
        raise ProtocolError("The certificate of the server could not be read") from None
    return CertificateInfo(fingerprint(der), subject, issuer, names, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), end < datetime.now(UTC), problem is None, problem)
