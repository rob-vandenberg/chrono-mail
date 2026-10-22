from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.core import ConfigurationError, ConnectionSecurity, ProtocolError
from chrono_mail.protocols.tls import PinnedContext, fingerprint, inspect_certificate, readable, valid_fingerprint
from tests.certs import make_certificate
from tests.enginefactory import make_engine
from tests.fakeimap import FakeImapServer
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message
from tests.test_engine_commands import call
from chrono_mail.engine import UsageError

# --- Version ------------------------------------------------------------
__version__ = "test_engine_certificates.py 1.9.272"

# --- Version history ----------------------------------------------------
# v1.9.272: The certificate of a calendar and contacts server (kind https, tls only) can be looked at
# v0.0.72: Accepted certificates belong to one server (host and port)
# v0.0.43: Initial certificate exception tests

MESSAGE = raw_message(subject="Over TLS", body="x\r\n", message_id="<tls1@x>")


def inspect(engine: Any, port: int, security: str = "tls", kind: str = "pop3") -> Any:  # noqa: ANN401
    return call(engine, "certificate.inspect", host="127.0.0.1", port=port, security=security, kind=kind)


def check(engine: Any, port: int, pins: list[str], kind: str = "pop3", security: str = "tls", **extra: Any) -> Any:  # noqa: ANN401
    return call(
        engine, "account.check", kind=kind, username="user", password="secret", incoming_host="127.0.0.1", incoming_port=port, incoming_security=security,
        outgoing_host="127.0.0.1", outgoing_port=extra.pop("smtp_port", 1), outgoing_security="tls", trusted_certificate=pins,
    )


def test_fingerprint_helpers() -> None:
    assert fingerprint(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert valid_fingerprint(fingerprint(b"x")) and not valid_fingerprint("ABC") and not valid_fingerprint("g" * 64) and not valid_fingerprint("a" * 63)
    assert readable("abcdef") == "AB:CD:EF"


@pytest.mark.parametrize(("kind", "security"), [("pop3", "tls"), ("pop3", "starttls"), ("imap", "tls"), ("imap", "starttls"), ("smtp", "tls"), ("smtp", "starttls")])
def test_the_certificate_of_every_kind_of_server_can_be_inspected(tmp_path: Path, kind: str, security: str) -> None:
    server_context, _ = make_certificate(tmp_path)
    implicit = server_context if security == "tls" else None
    upgrade = server_context if security == "starttls" else None
    servers = {
        "pop3": lambda: FakePop3Server({}, implicit_tls=implicit, stls_context=upgrade),
        "imap": lambda: FakeImapServer(implicit_tls=implicit, starttls_context=upgrade),
        "smtp": lambda: FakeSmtpServer(implicit_tls=implicit, starttls_context=upgrade),
    }
    engine = make_engine(tmp_path / "e")
    with servers[kind]() as server:
        info = inspect(engine, server.port, security, kind)
    assert valid_fingerprint(info.fingerprint) and info.subject == "localhost" and "localhost" in info.names
    assert not info.accepted_by_default and info.problem and not info.expired and info.not_before < info.not_after


def test_the_certificate_of_a_calendar_server_can_be_inspected(tmp_path: Path) -> None:
    """Calendar, Contacts or Tasks > Add account… > Look at the server's certificate asks with kind https: refused until 1.9.272."""
    server_context, _ = make_certificate(tmp_path)
    engine = make_engine(tmp_path / "e")
    with FakeImapServer(implicit_tls=server_context) as server:  # any server that speaks TLS at once, as a web server does
        info = inspect(engine, server.port, "tls", "https")
        assert valid_fingerprint(info.fingerprint) and info.subject == "localhost"
        with pytest.raises(UsageError, match="tls only"):
            inspect(engine, server.port, "starttls", "https")  # a web server has no STARTTLS


def test_looking_at_a_certificate_does_not_log_in_or_trust_it(tmp_path: Path) -> None:
    server_context, _ = make_certificate(tmp_path)
    engine = make_engine(tmp_path / "e")
    with FakeImapServer(starttls_context=server_context) as server:
        inspect(engine, server.port, "starttls", "imap")
        assert not any(command.startswith(("LOGIN", "AUTHENTICATE")) for command in server.commands)


def test_inspection_errors_are_protocol_errors(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with pytest.raises(ProtocolError):
        inspect(engine, 1)  # nothing listens
    with FakePop3Server({}) as plain:
        with pytest.raises(ProtocolError):
            call(engine, "certificate.inspect", host="127.0.0.1", port=plain.port, security="tls", kind="pop3")  # not a TLS server
        with pytest.raises(ProtocolError):
            inspect_certificate("127.0.0.1", plain.port, ConnectionSecurity.NONE, "pop3")
        with pytest.raises(ProtocolError, match="STLS"):
            inspect(engine, plain.port, "starttls", "pop3")  # offers no STLS
    with pytest.raises(ProtocolError):
        inspect_certificate("127.0.0.1", 1, ConnectionSecurity.STARTTLS, "ftp")


def test_a_self_signed_server_is_refused_until_its_certificate_is_accepted(tmp_path: Path) -> None:
    server_context, _ = make_certificate(tmp_path)
    engine = make_engine(tmp_path / "e")
    with FakePop3Server({"u": MESSAGE}, implicit_tls=server_context) as server:
        refused = check(engine, server.port, [])
        assert not refused.incoming_ok and "certificate" in refused.incoming_message
        pin = inspect(engine, server.port).fingerprint
        assert check(engine, server.port, [pin]).incoming_ok
        assert check(engine, server.port, [pin.upper()]).incoming_ok and check(engine, server.port, [readable(pin)]).incoming_ok  # as typed or shown
        wrong = check(engine, server.port, ["0" * 64])
        assert not wrong.incoming_ok and "not the certificate you accepted" in wrong.incoming_message
        with pytest.raises(Exception, match="64 hexadecimal"):
            check(engine, server.port, ["not a fingerprint"])


def test_an_account_with_an_accepted_certificate_works_and_forgetting_it_stops_it(tmp_path: Path) -> None:
    server_context, _ = make_certificate(tmp_path)
    engine = make_engine(tmp_path / "e")
    with FakePop3Server({"u": MESSAGE}, implicit_tls=server_context) as pop, FakeSmtpServer(implicit_tls=server_context) as smtp:
        pop_pin, smtp_pin = inspect(engine, pop.port).fingerprint, inspect(engine, smtp.port, kind="smtp").fingerprint
        created = call(
            engine, "account.add", display_name="Me", email_address="me@example.com", username="user", password="secret", incoming_host="127.0.0.1", incoming_port=pop.port,
            incoming_security="tls", outgoing_host="127.0.0.1", outgoing_port=smtp.port, outgoing_security="tls", trusted_certificate=[pop_pin, smtp_pin],
        )
        account = str(created.account_id)
        assert set(call(engine, "account.list")[0].trusted_certificates) == {pop_pin, smtp_pin}
        assert call(engine, "mail.fetch", account=account).downloaded == 1
        sent = call(engine, "mail.send", account=account, to="you@example.com", subject="Pinned", body="x")
        assert sent.sent and len(smtp.received) == 1
        call(engine, "account.forget-certificates", account=account)
        assert call(engine, "account.list")[0].trusted_certificates == ()
        with pytest.raises(ProtocolError, match="certificate"):
            call(engine, "mail.fetch", account=account)
        unsent = call(engine, "mail.send", account=account, to="you@example.com", subject="Refused", body="x")
        assert not unsent.sent and "certificate" in (unsent.error or "")
        call(engine, "account.trust-certificate", account=account, host="127.0.0.1", port=pop.port, fingerprint=pop_pin)
        call(engine, "account.trust-certificate", account=account, host="127.0.0.1", port=pop.port, fingerprint=pop_pin)  # twice is the same
        assert call(engine, "account.list")[0].trusted_certificates == (f"127.0.0.1:{pop.port}={pop_pin}",)
        assert call(engine, "mail.fetch", account=account).aborted is None


def test_a_changed_certificate_is_refused_even_though_one_was_accepted(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, _ = make_certificate(tmp_path / "a")
    second, _ = make_certificate(tmp_path / "b")
    engine = make_engine(tmp_path / "e")
    with FakePop3Server({}, implicit_tls=first) as old:
        pin = inspect(engine, old.port).fingerprint
    with FakePop3Server({}, implicit_tls=second) as replaced:
        result = check(engine, replaced.port, [pin])
        assert not result.incoming_ok and "not the certificate you accepted" in result.incoming_message


def test_imap_watching_and_sending_use_the_accepted_certificate(tmp_path: Path) -> None:
    server_context, _ = make_certificate(tmp_path)
    engine = make_engine(tmp_path / "e")
    with FakeImapServer(implicit_tls=server_context, plain=False) as imap:
        pin = inspect(engine, imap.port, kind="imap").fingerprint
        created = call(
            engine, "account.add", kind="imap", display_name="Me", email_address="me@example.com", username="user", password="secret", incoming_host="127.0.0.1",
            incoming_port=imap.port, incoming_security="tls", outgoing_host="127.0.0.1", outgoing_port=1, outgoing_security="tls", trusted_certificate=[pin],
        )
        imap.add("INBOX", MESSAGE)
        assert call(engine, "mail.fetch", account=str(created.account_id)).downloaded == 1
        assert call(engine, "mail.wait", account=str(created.account_id), seconds=1).supported


def test_a_certificate_accepted_for_one_server_does_not_touch_the_other(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    pop_context, _ = make_certificate(tmp_path / "a")
    smtp_context, _ = make_certificate(tmp_path / "b")
    engine = make_engine(tmp_path / "e")
    with FakePop3Server({}, implicit_tls=pop_context) as pop, FakeSmtpServer(implicit_tls=smtp_context) as smtp:
        pop_pin, smtp_pin = inspect(engine, pop.port).fingerprint, inspect(engine, smtp.port, kind="smtp").fingerprint
        only_pop = check(engine, pop.port, [f"127.0.0.1:{pop.port}={pop_pin}"], smtp_port=smtp.port)
        assert only_pop.incoming_ok and not only_pop.outgoing_ok
        assert "certificate" in only_pop.outgoing_message and "not the certificate you accepted" not in only_pop.outgoing_message  # the usual checks apply to it
        both = check(engine, pop.port, [f"127.0.0.1:{pop.port}={pop_pin}", f"127.0.0.1:{smtp.port}={smtp_pin}"], smtp_port=smtp.port)
        assert both.incoming_ok and both.outgoing_ok
        wrong_way = check(engine, pop.port, [f"127.0.0.1:{pop.port}={smtp_pin}", f"127.0.0.1:{smtp.port}={pop_pin}"], smtp_port=smtp.port)
        assert "not the certificate you accepted" in wrong_way.incoming_message and "not the certificate you accepted" in wrong_way.outgoing_message
        other_port = check(engine, pop.port, [f"127.0.0.1:{pop.port + 1}={pop_pin}"], smtp_port=smtp.port)
        assert not other_port.incoming_ok and "not the certificate you accepted" not in other_port.incoming_message  # a pin for another port is not used here
        older_form = check(engine, pop.port, [pop_pin], smtp_port=smtp.port)  # a bare fingerprint still applies to every server of the account
        assert older_form.incoming_ok and "not the certificate you accepted" in older_form.outgoing_message


def test_pins_are_written_in_one_form_and_checked() -> None:
    from chrono_mail.config import fingerprints_for, server_pin, valid_pin

    pin = server_pin(" Mail.Example.COM ", 995, "a" * 64)
    assert pin == "mail.example.com:995=" + "a" * 64 and valid_pin(pin) and valid_pin("a" * 64) and valid_pin(server_pin("::1", 993, "b" * 64))
    for bad in ("", "a" * 63, "mail.example.com=" + "a" * 64, ":995=" + "a" * 64, "mail:0=" + "a" * 64, "mail:99999=" + "a" * 64, "Mail:995=" + "a" * 64, "mail:995=" + "A" * 64, "mail:995=" + "a" * 64 + "=x"):
        assert not valid_pin(bad)
    pins = (pin, "c" * 64, server_pin("other.example.com", 995, "d" * 64))
    assert fingerprints_for(pins, "MAIL.example.com", 995) == {"a" * 64, "c" * 64}
    assert fingerprints_for(pins, "mail.example.com", 465) == {"c" * 64} and fingerprints_for((pin,), "mail.example.com", 465) == frozenset()


def test_the_pinned_context_trusts_only_what_it_is_told_to(tmp_path: Path) -> None:
    server_context, _ = make_certificate(tmp_path)
    with FakePop3Server({}, implicit_tls=server_context) as server:
        import socket

        for trusted, ok in ((frozenset({"0" * 64}), False), (frozenset(), False)):
            context = PinnedContext(trusted)
            raw = socket.create_connection(("127.0.0.1", server.port), timeout=3)
            with pytest.raises(Exception, match="accepted"):
                context.wrap_socket(raw, server_hostname="127.0.0.1")
            assert not ok
        assert PinnedContext(frozenset({"a" * 64})).check_hostname is False


def test_the_settings_file_keeps_only_valid_fingerprints() -> None:
    from dataclasses import replace

    from chrono_mail.config import AccountSettings, parse_config
    from tests.test_config import make_config

    base = make_config()
    settings = base.accounts[0]
    for bad in (("xyz",), ("A" * 64,), ("a" * 64, "a" * 64), tuple(f"{index:064x}" for index in range(11))):
        with pytest.raises(ConfigurationError):
            replace(settings, trusted_certificates=bad)
    good = replace(settings, trusted_certificates=("a" * 64,))
    config = base.with_account(good)
    from chrono_mail.config import config_to_text

    assert parse_config(config_to_text(config)).accounts[0].trusted_certificates == ("a" * 64,) and isinstance(good, AccountSettings)
    import json

    data = json.loads(config_to_text(config))
    data["accounts"][0]["trusted_certificates"] = "no"
    with pytest.raises(ConfigurationError, match="trusted_certificates"):
        parse_config(json.dumps(data))
