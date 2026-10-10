import io
import json
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from chrono_mail.cli import (
    EXIT_AUTHENTICATION,
    EXIT_CONFIGURATION,
    EXIT_ERROR,
    EXIT_MESSAGE,
    EXIT_NETWORK,
    EXIT_OK,
    EXIT_STORAGE,
    EXIT_USAGE,
    build_parser,
    exit_code,
    main,
    render_text,
)
from chrono_mail.config import AppDirectories, InMemoryPasswordStore, ServerRole
from chrono_mail.core import AuthenticationError, ChronoMailError, ConfigurationError, CryptoError, MessageFormatError, ProtocolError, StorageError
from chrono_mail.crypto import InMemoryKeyStore
from chrono_mail.engine import Engine, UsageError, build_registry
from chrono_mail.engine import commands_accounts
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_cli.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial command line tests


@dataclass
class Session:
    passwords: InMemoryPasswordStore
    keys: InMemoryKeyStore
    tmp_path: Path
    pop: FakePop3Server
    smtp: FakeSmtpServer

    def run(self, *arguments: str, stdin: str = "") -> tuple[int, str, str]:
        def factory(directories: AppDirectories) -> Engine:
            return Engine(directories, self.passwords, self.keys, durable=False)

        out, err = io.StringIO(), io.StringIO()
        code = main(["--config-dir", str(self.tmp_path / "c"), "--data-dir", str(self.tmp_path / "d"), *arguments], factory, io.StringIO(stdin), out, err)
        return code, out.getvalue(), err.getvalue()

    def json(self, *arguments: str, stdin: str = "") -> Any:
        code, out, err = self.run("--json", *arguments, stdin=stdin)
        assert code == EXIT_OK, (out, err)
        return json.loads(out)

    def add_account(self) -> str:
        data = self.json(
            "account.add", "--display-name", "Me", "--email-address", "me@example.com", "--username", "user", "--password-stdin",
            "--incoming-host", "127.0.0.1", "--incoming-port", str(self.pop.port), "--incoming-security", "none",
            "--outgoing-host", "127.0.0.1", "--outgoing-port", str(self.smtp.port), "--outgoing-security", "none",
            stdin="secret\n",
        )
        return str(data["account_id"])


@pytest.fixture
def session(tmp_path: Path) -> Generator[Session]:
    messages = {f"uid{n}": raw_message(subject=f"Message {n}", body=f"body {n}\r\n", message_id=f"<m{n}@x>") for n in (1, 2, 3)}
    with FakePop3Server(messages) as pop, FakeSmtpServer() as smtp:
        yield Session(InMemoryPasswordStore(), InMemoryKeyStore(), tmp_path, pop, smtp)


# --- the parser -----------------------------------------------------------------------------------------


def test_every_command_has_a_subcommand_with_help(capsys: pytest.CaptureFixture[str]) -> None:
    registry = build_registry()
    for command in registry.commands():
        assert main([command.id, "--help"]) == EXIT_OK
        assert command.description[:20] in capsys.readouterr().out


def test_global_help_lists_the_commands(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == EXIT_OK
    output = capsys.readouterr().out
    assert "account.add" in output and "message.list" in output and "--json" in output


@pytest.mark.parametrize("arguments", [[], ["no.such.command"], ["account.list", "--bogus"], ["message.list", "--sort", "colour"], ["message.list", "--limit", "many"], ["--json"]])
def test_usage_errors_exit_with_2(arguments: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(arguments) == EXIT_USAGE
    capsys.readouterr()


def test_secrets_cannot_be_given_as_arguments(session: Session, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = session.run("account.add", "--password", "hunter2")
    assert code == EXIT_USAGE and "hunter2" not in err.replace("--password", "")
    assert main(["account.add", "--help"]) == EXIT_OK
    help_text = capsys.readouterr().out
    assert "--password-stdin" in help_text and "--outgoing-password-stdin" in help_text
    assert "--password " not in help_text and "--password," not in help_text
    assert build_parser(build_registry()).prog == "chrono-mail-cli"


def test_missing_secret_without_a_terminal_is_a_usage_error(session: Session) -> None:
    code, _, err = session.run("account.add", "--display-name", "Me", "--email-address", "me@example.com", "--username", "u", "--incoming-host", "h", "--outgoing-host", "h")
    assert code == EXIT_USAGE and "stdin" in err


def test_missing_required_parameter(session: Session) -> None:
    code, _, err = session.run("message.list")
    assert code == EXIT_USAGE and "account" in err


# --- exit codes ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (UsageError("x"), EXIT_USAGE), (AuthenticationError("x"), EXIT_AUTHENTICATION), (ProtocolError("x"), EXIT_NETWORK),
        (StorageError("x"), EXIT_STORAGE), (CryptoError("x"), EXIT_STORAGE), (ConfigurationError("x"), EXIT_CONFIGURATION),
        (MessageFormatError("x"), EXIT_MESSAGE), (ChronoMailError("x"), EXIT_ERROR),
    ],
)
def test_exit_code_per_kind_of_error(error: ChronoMailError, code: int) -> None:
    assert exit_code(error) == code


def test_exit_codes_through_real_failures(session: Session) -> None:
    account = session.add_account()
    session.passwords.set(account, ServerRole.INCOMING, "wrong")
    assert session.run("mail.fetch", "--account", account)[0] == EXIT_AUTHENTICATION
    session.pop.__exit__(None, None, None)
    session.passwords.set(account, ServerRole.INCOMING, "secret")
    assert session.run("mail.fetch", "--account", account)[0] == EXIT_NETWORK
    assert session.run("message.show", "--account", account, "--folder", "Inbox", "--message", "0" * 32)[0] == EXIT_STORAGE
    assert session.run("folder.list", "--account", "nobody@example.com")[0] == EXIT_CONFIGURATION
    assert session.run("mail.send", "--account", account, "--to", "a@example.com", "--subject", "x\r\nBcc: e@example.com")[0] == EXIT_MESSAGE


def test_errors_are_json_with_the_json_option(session: Session) -> None:
    code, out, err = session.run("--json", "folder.list", "--account", "nobody@example.com")
    assert code == EXIT_CONFIGURATION and err == ""
    assert json.loads(out) == {"error": "Unknown account", "exit_code": EXIT_CONFIGURATION}


def test_destructive_commands_need_yes(session: Session) -> None:
    account = session.add_account()
    code, _, err = session.run("account.remove", "--account", account)
    assert code == EXIT_USAGE and "--yes" in err
    assert session.run("--yes", "account.remove", "--account", account)[0] == EXIT_OK
    assert session.json("account.list") == []


# --- a whole session through the command line -------------------------------------------------------------------


def test_a_full_session(session: Session, tmp_path: Path) -> None:
    account = session.add_account()
    assert session.json("account.list")[0]["email_address"] == "me@example.com"
    fetched = session.json("mail.fetch", "--account", account)
    assert fetched["downloaded"] == 3 and fetched["aborted"] is None
    listing = session.json("message.list", "--account", account)
    assert listing["total"] == 3 and listing["unread"] == 3
    message = listing["messages"][0]["message_id"]
    session.json("message.flag", "--account", account, "--folder", "Inbox", "--message", message, "--seen")
    assert session.json("message.list", "--account", account)["unread"] == 2
    session.json("message.flag", "--account", account, "--folder", "Inbox", "--message", message, "--no-seen")
    assert session.json("message.list", "--account", account)["unread"] == 3
    shown = session.json("message.show", "--account", account, "--folder", "inbox", "--message", message)
    assert shown["text_body"].startswith("body")
    assert len(session.json("message.search", "--account", account, "--query", "message")) == 3
    sent = session.json("mail.send", "--account", account, "--to", "you@example.com", "--cc", "cc@example.com", "--subject", "Hi", "--body", "Hello")
    assert sent["sent"] is True and sent["folder"] == "Sent"
    session.json("folder.create", "--account", account, "--folder", "Archive")
    session.json("message.move", "--account", account, "--folder", "Inbox", "--message", message, "--target", "Archive")
    status = session.json("key.status")
    assert status["in_credential_store"] is True
    recovery = tmp_path / "recovery.key"
    assert session.run("key.export-file", "--path", str(recovery), "--passphrase-stdin", stdin="my passphrase\n")[0] == EXIT_OK and recovery.exists()


def test_message_delete_through_the_command_line(session: Session) -> None:
    account = session.add_account()
    session.json("mail.fetch", "--account", account)
    message = session.json("message.list", "--account", account)["messages"][0]["message_id"]
    assert session.run("message.delete", "--account", account, "--folder", "Inbox", "--message", message)[0] == EXIT_USAGE
    result = session.json("--yes", "message.delete", "--account", account, "--folder", "Inbox", "--message", message)
    assert result["outcome"] == "moved to Trash"


def test_text_output_is_readable(session: Session) -> None:
    account = session.add_account()
    session.run("mail.fetch", "--account", account)
    code, out, _ = session.run("account.list")
    assert code == EXIT_OK and "display name: Me" in out and "encrypted: yes" in out
    code, out, _ = session.run("message.list", "--account", account)
    assert code == EXIT_OK and "total: 3" in out and "subject: Message" in out
    code, out, _ = session.run("message.search", "--account", account, "--query", "zzzzzz")
    assert code == EXIT_OK and out.strip() == "(nothing)"


def test_the_two_directory_options_are_used(session: Session) -> None:
    session.add_account()
    assert (session.tmp_path / "c" / "config.json").exists() and (session.tmp_path / "d" / "accounts").is_dir()


def test_render_text() -> None:
    assert render_text(None) == "(nothing)" and render_text(()) == "(nothing)" and render_text(7) == "7" and render_text(True) == "yes"


def test_an_unexpected_file_error_is_reported_not_a_traceback(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(engine: Engine, parameters: object) -> object:
        raise PermissionError(13, "Permission denied", "/some/file")

    monkeypatch.setattr(commands_accounts, "account_list", failing)  # the registry binds handlers when it is built
    code, out, err = session.run("account.list")
    assert code == EXIT_STORAGE and out == "" and err == "Error: Permission denied: /some/file\n"
    code, out, err = session.run("--json", "account.list")
    assert code == EXIT_STORAGE and err == "" and json.loads(out) == {"error": "Permission denied: /some/file", "exit_code": EXIT_STORAGE}
