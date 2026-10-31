import socket
from pathlib import Path

import pytest

from chrono_mail.core import CertificateError, ConnectionSecurity
from chrono_mail.engine import Engine
from chrono_mail.engine.commands_accounts import (
    AccountAddParameters,
    AccountCheckParameters,
    AccountCheckResult,
    CertificateParameters,
    RunTrustParameters,
    TrustParameters,
    account_add,
    account_check,
    account_forget_certificate,
    certificate_inspect,
    certificate_trust_for_run,
)
from chrono_mail.engine.commands_dav import FindNewParameters, dav_find_new
from chrono_mail.engine.commands_mail import SendParameters, mail_fetch, mail_send
from chrono_mail.engine.commands_outbox import SendOutboxParameters, mail_send_outbox
from chrono_mail.engine.commands_schedule import SendDueParameters, mail_send_due
from chrono_mail.engine.commands_accounts import AccountParameters
from chrono_mail.protocols import tls
from chrono_mail.protocols.tls import CertificateChanged, PinnedContext
from tests.certs import make_certificate
from tests.enginefactory import make_engine
from tests.fakedav import FakeDav
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_engine_verify_certificate.py 4.2.447"

# --- Version history ----------------------------------------------------
# v4.2.447: a fresh check and Find count nothing accepted before; certificate.inspect kept; account.forget-certificate
# v4.2.441: Initial version: a certificate that could not be verified names its server, Connect until closing, the weak security (UR-05, D-299)

MESSAGE = raw_message(subject="Over TLS", body="x\r\n", message_id="<verify1@x>")


def check(engine: Engine, pop: int, smtp: int, pins: tuple[str, ...] = (), incoming: str = "tls") -> AccountCheckResult:
    security = "tls-unchecked" if incoming == "tls-unchecked" else "tls"
    return account_check(engine, AccountCheckParameters(
        username="user", incoming_host="127.0.0.1", outgoing_host="127.0.0.1", password="secret", incoming_port=pop, incoming_security=security,
        outgoing_port=smtp, outgoing_security=security, trusted_certificate=pins,
    ))


def fingerprint_of(engine: Engine, port: int, kind: str = "pop3") -> str:
    return certificate_inspect(engine, CertificateParameters("127.0.0.1", port, "tls", "smtp" if kind == "smtp" else "pop3")).fingerprint


def test_a_certificate_that_cannot_be_verified_names_its_server(tmp_path: Path) -> None:
    server_context, _ = make_certificate(tmp_path)
    engine = make_engine(tmp_path / "e")
    with FakePop3Server({}, implicit_tls=server_context) as pop, FakeSmtpServer(implicit_tls=server_context) as smtp:
        result = check(engine, pop.port, smtp.port)
    incoming, outgoing = result.incoming_certificate, result.outgoing_certificate
    assert incoming is not None and (incoming.host, incoming.port, incoming.kind, incoming.security, incoming.changed) == ("127.0.0.1", pop.port, "pop3", "tls", False)
    assert outgoing is not None and (outgoing.port, outgoing.kind) == (smtp.port, "smtp")
    assert not result.incoming_ok and "certificate" in result.incoming_message


def test_a_refused_connection_is_no_certificate_problem(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "e")
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    result = check(engine, port, port)
    assert not result.incoming_ok and result.incoming_certificate is None and result.outgoing_certificate is None


def test_a_certificate_other_than_the_accepted_one_is_named_as_changed(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, _ = make_certificate(tmp_path / "a")
    second, _ = make_certificate(tmp_path / "b")
    engine = make_engine(tmp_path / "e")
    with FakePop3Server({}, implicit_tls=first) as old:
        pin = fingerprint_of(engine, old.port)
    with FakePop3Server({}, implicit_tls=second) as renewed, FakeSmtpServer(implicit_tls=second) as smtp:
        result = check(engine, renewed.port, smtp.port, (f"127.0.0.1:{renewed.port}={pin}",))
    assert result.incoming_certificate is not None and result.incoming_certificate.changed
    assert result.outgoing_certificate is not None and not result.outgoing_certificate.changed  # nothing was accepted for that server


def test_connect_trusts_the_certificate_until_chrono_mail_closes_and_never_saves_it(tmp_path: Path) -> None:
    server_context, _ = make_certificate(tmp_path)
    engine = make_engine(tmp_path / "e")
    with FakePop3Server({"u": MESSAGE}, implicit_tls=server_context) as pop, FakeSmtpServer(implicit_tls=server_context) as smtp:
        pop_pin, smtp_pin = fingerprint_of(engine, pop.port), fingerprint_of(engine, smtp.port, "smtp")
        certificate_trust_for_run(engine, RunTrustParameters("127.0.0.1", pop.port, pop_pin))
        certificate_trust_for_run(engine, RunTrustParameters("127.0.0.1", smtp.port, smtp_pin))
        assert check(engine, pop.port, smtp.port).incoming_ok and check(engine, pop.port, smtp.port).outgoing_ok
        created = account_add(engine, AccountAddParameters(
            display_name="Me", email_address="me@example.com", username="user", password="secret", incoming_host="127.0.0.1", outgoing_host="127.0.0.1",
            incoming_port=pop.port, outgoing_port=smtp.port, outgoing_security="tls",
        ))
        assert mail_fetch(engine, AccountParameters(created.account_id)).downloaded == 1  # every connection takes it
        assert engine.config().find(created.account_id).trusted_certificates == ()
        assert pop_pin not in engine.directories.config_file.read_text(encoding="utf-8")  # kept in memory only: the next start asks again
        restarted = make_engine(tmp_path / "e")
        assert restarted.pins() == ()
        assert not check(restarted, pop.port, smtp.port).incoming_ok


def test_the_weak_security_encrypts_without_checking_the_certificate(tmp_path: Path) -> None:
    server_context, _ = make_certificate(tmp_path)
    engine = make_engine(tmp_path / "e")
    with FakePop3Server({}, implicit_tls=server_context) as pop, FakeSmtpServer(implicit_tls=server_context) as smtp:
        assert not check(engine, pop.port, smtp.port).incoming_ok
        weak = check(engine, pop.port, smtp.port, incoming="tls-unchecked")
    assert weak.incoming_ok and weak.outgoing_ok and weak.incoming_certificate is None


def test_a_renewed_certificate_that_passes_the_usual_checks_connects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server_context, _ = make_certificate(tmp_path)
    with FakePop3Server({}, implicit_tls=server_context) as server:
        for usual, connects in ((False, False), (True, True)):
            def passes(host: str, port: int, security: ConnectionSecurity, kind: str, shown: str, usual: bool = usual) -> bool:
                return usual

            monkeypatch.setattr(tls, "_passes_usual_checks", passes)
            context = PinnedContext(frozenset({"0" * 64}))
            raw = socket.create_connection(("127.0.0.1", server.port), timeout=3)
            if connects:
                context.wrap_socket(raw, server_hostname="127.0.0.1").close()
            else:
                with pytest.raises(CertificateChanged):
                    context.wrap_socket(raw, server_hostname="127.0.0.1")


def test_a_refresh_names_the_server_of_a_certificate_it_could_not_verify(tmp_path: Path) -> None:
    server_context, _ = make_certificate(tmp_path)
    engine = make_engine(tmp_path / "e")
    with FakePop3Server({"u": MESSAGE}, implicit_tls=server_context) as pop, FakeSmtpServer(implicit_tls=server_context) as smtp:
        created = account_add(engine, AccountAddParameters(
            display_name="Me", email_address="me@example.com", username="user", password="secret", incoming_host="127.0.0.1", outgoing_host="127.0.0.1",
            incoming_port=pop.port, outgoing_port=smtp.port, outgoing_security="tls",
        ))
        with pytest.raises(CertificateError) as raised:
            mail_fetch(engine, AccountParameters(created.account_id))
        assert (raised.value.trouble.port, raised.value.trouble.kind) == (pop.port, "pop3")
        unsent = mail_send(engine, SendParameters(account=created.account_id, to=("you@example.com",), subject="Refused", body="x"))
        assert not unsent.sent and unsent.certificate is not None and (unsent.certificate.port, unsent.certificate.account) == (smtp.port, created.account_id)
        outbox = mail_send_outbox(engine, SendOutboxParameters(created.account_id))
        assert [(item.port, item.account) for item in outbox.certificates] == [(smtp.port, created.account_id)]


def test_a_calendar_server_names_its_certificate(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "e")
    with FakeDav(tls=True) as server:
        with pytest.raises(CertificateError) as raised:
            dav_find_new(engine, FindNewParameters(server.url, "ann", "secret"))
    assert raised.value.trouble.kind == "https" and raised.value.trouble.host == "127.0.0.1"


def test_a_scheduled_send_and_a_look_name_what_the_window_needs(tmp_path: Path) -> None:
    server_context, _ = make_certificate(tmp_path)
    engine = make_engine(tmp_path / "e")
    with FakePop3Server({}, implicit_tls=server_context) as pop, FakeSmtpServer(implicit_tls=server_context) as smtp:
        created = account_add(engine, AccountAddParameters(
            display_name="Me", email_address="me@example.com", username="user", password="secret", incoming_host="127.0.0.1", outgoing_host="127.0.0.1",
            incoming_port=pop.port, outgoing_port=smtp.port, outgoing_security="tls",
        ))
        scheduled = mail_send(engine, SendParameters(account=created.account_id, to=("you@example.com",), subject="Later", body="x", send_at="2030-01-01T09:00"))
        due = mail_send_due(engine, SendDueParameters(message=scheduled.message_id))
        assert [(item.port, item.account) for item in due.certificates] == [(smtp.port, created.account_id)]
        looked = certificate_inspect(engine, CertificateParameters("127.0.0.1", smtp.port, "tls", "smtp"))
        assert not looked.accepted_for_run
        certificate_trust_for_run(engine, RunTrustParameters("127.0.0.1", smtp.port, looked.fingerprint))
        assert certificate_inspect(engine, CertificateParameters("127.0.0.1", smtp.port, "tls", "smtp")).accepted_for_run  # nothing left to ask


def test_a_pinned_smtp_server_with_a_greeting_of_several_lines_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """The second look at a renewed certificate starts STARTTLS itself: a 220- greeting of several lines ends with its 220 line first."""
    ours, server = socket.socketpair()
    with ours, server:
        server.sendall(b"220-mail.example.org ESMTP\r\n220 ready\r\n250-mail.example.org\r\n250 STARTTLS\r\n220 go ahead\r\n")
        tls._start_tls(ours, "smtp")  # noqa: SLF001 - the step the second look takes
        sent = server.recv(200)
    assert sent == b"EHLO chrono-mail.invalid\r\nSTARTTLS\r\n"


def test_a_fresh_check_counts_nothing_accepted_before_and_one_certificate_can_be_taken_back(tmp_path: Path) -> None:
    server_context, _ = make_certificate(tmp_path)
    engine = make_engine(tmp_path / "e")
    with FakePop3Server({}, implicit_tls=server_context) as pop, FakeSmtpServer(implicit_tls=server_context) as smtp:
        pin = fingerprint_of(engine, pop.port)
        kept = (f"127.0.0.1:{pop.port}={pin}",)
        certificate_trust_for_run(engine, RunTrustParameters("127.0.0.1", smtp.port, pin))
        assert check(engine, pop.port, smtp.port, kept).incoming_ok and check(engine, pop.port, smtp.port, kept).outgoing_ok
        fresh = account_check(engine, AccountCheckParameters(
            username="user", incoming_host="127.0.0.1", outgoing_host="127.0.0.1", password="secret", incoming_port=pop.port, incoming_security="tls",
            outgoing_port=smtp.port, outgoing_security="tls", trusted_certificate=kept, fresh=True,
        ))
        assert fresh.incoming_certificate is not None and fresh.outgoing_certificate is not None  # the usual checks only
        created = account_add(engine, AccountAddParameters(
            display_name="Me", email_address="me@example.com", username="user", password="secret", incoming_host="127.0.0.1", outgoing_host="127.0.0.1",
            incoming_port=pop.port, outgoing_port=smtp.port, outgoing_security="tls", trusted_certificate=kept,
        ))
        looked = certificate_inspect(engine, CertificateParameters("127.0.0.1", pop.port, "tls", "pop3", created.account_id))
        assert looked.kept and not certificate_inspect(engine, CertificateParameters("127.0.0.1", pop.port, "tls", "pop3")).kept
        left = account_forget_certificate(engine, TrustParameters(created.account_id, "127.0.0.1", pop.port, pin))
        assert left.trusted_certificates == ()
        assert not certificate_inspect(engine, CertificateParameters("127.0.0.1", pop.port, "tls", "pop3", created.account_id)).kept


def test_a_fresh_find_counts_nothing_accepted_before(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "e")
    with FakeDav(tls=True) as server:
        host, port = "127.0.0.1", int(server.url.rsplit(":", 1)[1].split("/")[0])
        pin = certificate_inspect(engine, CertificateParameters(host, port, "tls", "https")).fingerprint
        certificate_trust_for_run(engine, RunTrustParameters(host, port, pin))
        dav_find_new(engine, FindNewParameters(server.url, "ann", "secret"))  # Connect counts for this run
        with pytest.raises(CertificateError):
            dav_find_new(engine, FindNewParameters(server.url, "ann", "secret", fresh=True))
