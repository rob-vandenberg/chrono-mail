import ssl
from pathlib import Path

import pytest

from chrono_mail.core import AuthenticationError, ConnectionSecurity, ProtocolError, ServerSettings
from chrono_mail.protocols.imap import ImapClient, ImapLimits, ImapRefused, decode_folder_name, encode_folder_name, quote
from tests.certs import make_certificate
from tests.fakeimap import FakeImapServer
from tests.fakeserver import Conversation, ScriptedServer

# --- Version ------------------------------------------------------------
__version__ = "test_protocols_imap.py 0.0.28"

# --- Version history ----------------------------------------------------
# v0.0.28: Initial IMAP client tests

MESSAGE_A = b"From: a@example.com\r\nSubject: one\r\n\r\nfirst body\r\n"
MESSAGE_B = b"From: b@example.com\r\nSubject: two\r\n\r\nsecond body\r\n"
FAST = ImapLimits(maximum_line_bytes=4096, maximum_literal_bytes=20_000, maximum_response_bytes=50_000, maximum_tokens=500, maximum_depth=6, connect_timeout_seconds=3, read_timeout_seconds=1.0)


def settings(port: int, security: ConnectionSecurity = ConnectionSecurity.NONE) -> ServerSettings:
    return ServerSettings("127.0.0.1", port, security, "user")


def client(server: ScriptedServer, limits: ImapLimits = FAST, security: ConnectionSecurity = ConnectionSecurity.NONE, context: ssl.SSLContext | None = None, password: str = "secret") -> ImapClient:
    return ImapClient(settings(server.port, security), password, limits, context)


# --- folder names ------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["INBOX", "Überschrift", "日本語/メール", "A&B", "Tom & Jerry", "100%", "naïve café", "emoji 😀", "&", "-&-", "a/b/c", ""])
def test_folder_names_survive_the_wire_format(name: str) -> None:
    wire = encode_folder_name(name)
    assert wire.isascii() and decode_folder_name(wire) == name


def test_known_wire_forms() -> None:
    assert encode_folder_name("A&B") == "A&-B"
    assert encode_folder_name("Entwürfe") == "Entw&APw-rfe"
    assert decode_folder_name("Entw&APw-rfe") == "Entwürfe"
    assert decode_folder_name("&AOQ-") == "ä"


@pytest.mark.parametrize("wire", ["&", "&abc", "&!!!-", "&AOQ", "a&-&b-c", "&&-"])
def test_broken_wire_names_never_raise(wire: str) -> None:
    decode_folder_name(wire)


def test_quote_escapes_and_refuses_line_breaks() -> None:
    assert quote('a"b\\c') == '"a\\"b\\\\c"'
    for bad in ("a\nb", "a\rb", "a\x00b"):
        with pytest.raises(ProtocolError):
            quote(bad)


# --- connecting and logging in -----------------------------------------------------------------------------------------------------


def test_login_with_plain_and_the_initial_response() -> None:
    with FakeImapServer() as server, client(server) as imap:
        assert "UIDPLUS" in imap.capabilities and "AUTH=PLAIN" in imap.capabilities
        assert imap.list_folders()
    assert [command for command in server.commands if command.startswith("AUTHENTICATE")] == ["AUTHENTICATE ***"]


def test_login_with_plain_in_two_steps_and_with_the_login_command() -> None:
    with FakeImapServer(sasl_ir=False) as server, client(server):
        pass
    assert "AUTHENTICATE ***" in server.commands
    with FakeImapServer(plain=False) as server, client(server):
        pass
    assert "LOGIN ***" in server.commands


def test_a_wrong_password_is_an_authentication_error() -> None:
    with FakeImapServer() as server:
        with pytest.raises(AuthenticationError, match="refused the login"):
            client(server, password="wrong").connect()
    with FakeImapServer(plain=False) as server:
        with pytest.raises(AuthenticationError):
            client(server, password="wrong").connect()


def test_passwords_with_special_characters_work() -> None:
    for password in ('pa"ss\\word', "pässwörd 日本", "with space"):
        with FakeImapServer(password=password) as server, client(server, password=password):
            pass
        with FakeImapServer(password=password, plain=False) as server:
            if password.isascii():
                with client(server, password=password):
                    pass


def test_implicit_tls(tmp_path: Path) -> None:
    server_context, client_context = make_certificate(tmp_path)
    with FakeImapServer(implicit_tls=server_context) as server, client(server, security=ConnectionSecurity.TLS, context=client_context) as imap:
        assert imap.list_folders()


def test_starttls_and_no_login_before_it(tmp_path: Path) -> None:
    server_context, client_context = make_certificate(tmp_path)
    with FakeImapServer(starttls_context=server_context) as server:
        with client(server, security=ConnectionSecurity.STARTTLS, context=client_context) as imap:
            assert imap.list_folders()
        with pytest.raises(AuthenticationError, match="encrypted"):
            client(server, security=ConnectionSecurity.NONE).connect()  # the server forbids a login in the clear
    assert not any(command.startswith("LOGIN") for command in server.commands[:2])


def test_an_untrusted_certificate_is_refused(tmp_path: Path) -> None:
    server_context, _ = make_certificate(tmp_path)
    with FakeImapServer(implicit_tls=server_context) as server:
        with pytest.raises(ProtocolError, match="certificate"):
            client(server, security=ConnectionSecurity.TLS).connect()


def test_starttls_asked_for_but_not_offered_is_an_error() -> None:
    with FakeImapServer() as server:
        with pytest.raises(ProtocolError, match="does not offer STARTTLS"):
            client(server, security=ConnectionSecurity.STARTTLS).connect()


def test_nothing_listening_is_a_protocol_error() -> None:
    with FakeImapServer() as server:
        port = server.port
    with pytest.raises(ProtocolError, match="Connection failed"):
        ImapClient(settings(port), "x", FAST).connect()


# --- folders and messages -------------------------------------------------------------------------------------------------------------


def test_listing_folders_with_attributes_and_unicode_names() -> None:
    with FakeImapServer() as server:
        server.add("Entwürfe/Neu", MESSAGE_A)
        with client(server) as imap:
            folders = {entry.name: entry for entry in imap.list_folders()}
            assert set(folders) >= {"INBOX", "Sent", "Drafts", "Trash", "Junk", "Entwürfe/Neu"}
            assert "\\sent" in folders["Sent"].attributes and folders["INBOX"].delimiter == "/" and folders["INBOX"].attributes == frozenset()
            assert {entry.name for entry in imap.list_folders(subscribed_only=True)} == set(folders)


def test_select_tells_validity_next_uid_and_count() -> None:
    with FakeImapServer() as server:
        server.add("INBOX", MESSAGE_A)
        server.add("INBOX", MESSAGE_B)
        with client(server) as imap:
            info = imap.select("INBOX")
            assert (info.exists, info.uid_validity, info.uid_next, info.read_only) == (2, 1000, 3, False)
            assert "\\Seen" in info.flags and imap.selected == "INBOX"
            assert imap.select("INBOX", read_only=True).read_only
            with pytest.raises(ImapRefused, match="no such folder"):
                imap.select("Nowhere")


def test_search_fetch_and_flags() -> None:
    with FakeImapServer() as server:
        first = server.add("INBOX", MESSAGE_A, {"\\Seen"})
        second = server.add("INBOX", MESSAGE_B)
        with client(server) as imap:
            imap.select("INBOX")
            assert imap.uid_search() == (first, second) and imap.uid_search("UNSEEN") == (second,)
            summary = {item.uid: item for item in imap.uid_fetch("1:*")}
            assert summary[first].flags == {"\\Seen"} and summary[first].size == len(MESSAGE_A) and summary[second].flags == frozenset() and summary[first].data is None
            (full,) = imap.uid_fetch(str(second), "(UID FLAGS BODY.PEEK[])")
            assert full.data == MESSAGE_B and full.uid == second
            assert imap.uid_fetch("99") == ()


def test_changing_flags() -> None:
    with FakeImapServer() as server:
        uid = server.add("INBOX", MESSAGE_A)
        with client(server) as imap:
            imap.select("INBOX")
            imap.uid_store(str(uid), ["\\Seen", "\\Flagged"], "add")
            assert server.flags_of("INBOX", uid) == {"\\Seen", "\\Flagged"}
            imap.uid_store(str(uid), ["\\Flagged"], "remove")
            assert server.flags_of("INBOX", uid) == {"\\Seen"}
            imap.uid_store(str(uid), ["\\Answered"], "set")
            assert server.flags_of("INBOX", uid) == {"\\Answered"}
            with pytest.raises(ProtocolError, match="flag"):
                imap.uid_store(str(uid), ["bad flag\r\nA1 LOGOUT"], "add")


@pytest.mark.parametrize("move", [True, False])
@pytest.mark.parametrize("uidplus", [True, False])
def test_move_copy_and_expunge_with_and_without_the_extensions(move: bool, uidplus: bool) -> None:
    with FakeImapServer(move=move, uidplus=uidplus) as server:
        first = server.add("INBOX", MESSAGE_A)
        server.add("INBOX", MESSAGE_B)
        with client(server) as imap:
            imap.select("INBOX")
            copied = imap.uid_copy(str(first), "Sent")
            assert (copied.uid is not None) is uidplus and server.uids("INBOX") == [1, 2] and len(server.uids("Sent")) == 1
            imap.uid_move(str(first), "Trash")
            assert server.uids("INBOX") == [2] and server.raw_of("Trash", server.uids("Trash")[0]) == MESSAGE_A
        # without UIDPLUS an expunge takes every message marked deleted: here only the moved one was marked
        assert server.uids("INBOX") == [2]


def test_append_returns_the_new_uid_when_the_server_tells() -> None:
    with FakeImapServer() as server, client(server) as imap:
        result = imap.append("Sent", MESSAGE_A, ["\\Seen"])
        assert result.uid is not None and result.uid == server.uids("Sent")[0] and result.uid_validity == 1000
        assert server.flags_of("Sent", result.uid) == {"\\Seen"} and server.raw_of("Sent", result.uid) == MESSAGE_A
        with pytest.raises(ImapRefused, match="no such folder"):
            imap.append("Nowhere", MESSAGE_A)
    with FakeImapServer(uidplus=False) as server, client(server) as imap:
        assert imap.append("Sent", MESSAGE_A).uid is None and len(server.uids("Sent")) == 1


def test_append_of_a_large_message_with_binary_content() -> None:
    big = b"Subject: big\r\n\r\n" + bytes(range(256)) * 60
    with FakeImapServer() as server, client(server, limits=ImapLimits(maximum_literal_bytes=100_000, read_timeout_seconds=2)) as imap:
        uid = imap.append("INBOX", big).uid
        assert uid is not None
        imap.select("INBOX")
        (fetched,) = imap.uid_fetch(str(uid), "(UID BODY.PEEK[])")
        assert fetched.data == big


def test_creating_renaming_and_deleting_folders_with_special_names() -> None:
    with FakeImapServer() as server, client(server) as imap:
        imap.create_folder("Projekt Übung")
        assert "Projekt Übung" in server.folders
        imap.rename_folder("Projekt Übung", "Archiv & mehr")
        assert "Archiv & mehr" in server.folders and "Projekt Übung" not in server.folders
        imap.subscribe("Archiv & mehr")
        imap.delete_folder("Archiv & mehr")
        assert "Archiv & mehr" not in server.folders
        with pytest.raises(ImapRefused):
            imap.delete_folder("INBOX")


def test_commands_cannot_be_injected_through_a_folder_name() -> None:
    with FakeImapServer() as server, client(server) as imap:
        imap.create_folder("x\r\nA9 DELETE INBOX")  # the line breaks are encoded in the folder name: they never reach the wire as such
        assert "INBOX" in server.folders and not any(command.startswith("DELETE") for command in server.commands)
        assert any(name.startswith("x") and "\n" in name for name in server.folders)


def test_noop_and_logout() -> None:
    with FakeImapServer() as server, client(server) as imap:
        imap.noop()
        imap.logout()
        with pytest.raises(ProtocolError, match="Not connected"):
            imap.noop()


# --- hostile servers -----------------------------------------------------------------------------------------------------------------------------


def answer_with(*lines: bytes, greeting: bytes = b"* OK [CAPABILITY IMAP4rev1 AUTH=PLAIN] hello") -> ScriptedServer:
    """A server that greets, accepts the login and then answers the next command with these raw lines."""

    def script(conversation: Conversation) -> None:
        conversation.send(greeting + b"\r\n")
        while True:
            raw = conversation.read_line()
            if raw is None:
                return
            tag = raw.split(b" ", 1)[0]
            command = raw.split(b" ")[1].upper() if b" " in raw else b""
            if command in (b"AUTHENTICATE", b"CAPABILITY"):
                conversation.send(b"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n" + tag + b" OK done\r\n")
            else:
                for line in lines:
                    conversation.send(line.replace(b"TAG", tag) + b"\r\n")
                if not lines or lines[-1].startswith(b"TAG") is False and b"TAG " not in lines[-1]:
                    conversation.send(tag + b" OK done\r\n")

    return ScriptedServer(script)


def test_a_line_that_is_too_long_is_refused() -> None:
    with answer_with(b"* LIST () \"/\" \"" + b"x" * 10_000 + b"\"") as server, client(server) as imap:
        with pytest.raises(ProtocolError, match="too long"):
            imap.list_folders()


def test_a_literal_that_is_too_large_is_refused_before_it_is_read() -> None:
    with answer_with(b"* 1 FETCH (UID 1 BODY[] {999999999}") as server, client(server) as imap:
        with pytest.raises(ProtocolError, match="larger than allowed"):
            imap.uid_fetch("1", "(UID BODY.PEEK[])")


def test_an_answer_for_another_command_is_refused() -> None:
    with answer_with(b"C999 OK surprise") as server, client(server) as imap:
        with pytest.raises(ProtocolError, match="did not send"):
            imap.noop()


@pytest.mark.parametrize(
    "line",
    [
        b"* LIST (",
        b"* LIST (() \"/\" \"x\"",
        b"* SEARCH 1 2 )",
        b"* SEARCH \"unfinished",
        b"* FETCH (UID 1 BODY[] {5}",  # a literal that never comes: the connection stops
        b"",
        b"*",
        b"garbage without a tag",
    ],
)
def test_garbage_is_a_protocol_error_not_a_crash(line: bytes) -> None:
    with answer_with(line) as server, client(server) as imap:
        with pytest.raises(ProtocolError):
            imap.uid_search()


def test_nesting_and_token_counts_are_limited() -> None:
    with answer_with(b"* SEARCH " + b"(" * 50 + b")" * 50) as server, client(server) as imap:
        with pytest.raises(ProtocolError, match="nested too deeply"):
            imap.uid_search()
    with answer_with(b"* SEARCH " + b" ".join(b"1" for _ in range(2000))) as server, client(server) as imap:
        with pytest.raises(ProtocolError, match="too many parts"):
            imap.uid_search()


def test_the_server_ending_the_connection_is_reported() -> None:
    with answer_with(b"* BYE shutting down") as server, client(server) as imap:
        with pytest.raises(ProtocolError, match="ended the connection"):
            imap.noop()


def test_a_dropped_connection_in_the_middle_of_a_literal() -> None:
    def script(conversation: Conversation) -> None:
        conversation.send(b"* OK [CAPABILITY IMAP4rev1 AUTH=PLAIN] hi\r\n")
        while True:
            raw = conversation.read_line()
            if raw is None:
                return
            tag = raw.split(b" ", 1)[0]
            if b"AUTHENTICATE" in raw or b"CAPABILITY" in raw:
                conversation.send(b"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n" + tag + b" OK done\r\n")
            elif b"FETCH" in raw:
                conversation.send(b"* 1 FETCH (UID 1 BODY[] {100}\r\nonly a few bytes")
                return

    with ScriptedServer(script) as server, client(server) as imap:
        with pytest.raises(ProtocolError, match="closed the connection"):
            imap.uid_fetch("1", "(UID BODY.PEEK[])")


def test_a_silent_server_times_out() -> None:
    def script(conversation: Conversation) -> None:
        conversation.send(b"* OK [CAPABILITY IMAP4rev1 AUTH=PLAIN] hi\r\n")
        conversation.sleep(3)

    with ScriptedServer(script) as server:
        with pytest.raises(ProtocolError, match="timeout"):
            client(server).connect()


def test_a_greeting_that_is_not_ok_is_refused() -> None:
    with answer_with(greeting=b"* NO go away") as server:
        with pytest.raises(ProtocolError, match="greet"):
            client(server).connect()
    with answer_with(greeting=b"* BYE too busy") as server:
        with pytest.raises(ProtocolError):
            client(server).connect()


def test_nul_bytes_and_unexpected_continuations_are_refused() -> None:
    with answer_with(b"* SEARCH 1\x00 2") as server, client(server) as imap:
        with pytest.raises(ProtocolError, match="invalid character"):
            imap.uid_search()
    with answer_with(b"+ please send more") as server, client(server) as imap:
        with pytest.raises(ProtocolError, match="more input"):
            imap.uid_search()


def test_non_utf8_text_in_a_status_line_is_tolerated() -> None:
    with answer_with(b"* OK caf\xe9 still fine") as server, client(server) as imap:
        imap.noop()
