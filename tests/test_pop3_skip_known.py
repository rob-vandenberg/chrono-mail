from dataclasses import replace
from pathlib import Path

from chrono_mail.config import Pop3DeletePolicy, Pop3Options
from chrono_mail.core import LOCAL_ACCOUNT_ID, ConnectionSecurity, MessageFlag, ServerSettings
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.protocols.pop3 import Pop3Client
from chrono_mail.sync import fetch_pop3
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message
from tests.test_engine_commands import add_account, call
from tests.test_sync_pop3 import CLIENT_LIMITS, INBOX, LIMIT, World, make_world, messages

# --- Version ------------------------------------------------------------
__version__ = "test_pop3_skip_known.py 0.5.132"

# --- Version history ----------------------------------------------------
# v0.5.132: Initial tests: a message that is already here is recorded as downloaded, not stored twice


def _world_with_first_message_stored(tmp_path: Path) -> tuple[World, str]:
    world = make_world(tmp_path)
    reference = world.store.deliver(INBOX, messages()["uid1"], verify=True)
    return world, reference.store_id


def test_a_message_that_is_already_here_is_recorded_not_stored_again(tmp_path: Path) -> None:
    world, present = _world_with_first_message_stored(tmp_path)
    try:
        settings_options = Pop3Options()

        def already_here(raw: bytes) -> str | None:
            return present if raw == messages()["uid1"] else None

        server = ServerSettings("127.0.0.1", world.server.port, ConnectionSecurity.NONE, "user")
        with Pop3Client(server, "secret", CLIENT_LIMITS) as client:
            report = fetch_pop3(client, world.store, world.index, world.state, INBOX, settings_options, LIMIT, lambda: world.clock[0], already_here=already_here)
        assert (report.downloaded, report.already_here) == (2, 1)
        assert len(world.store.list_messages(INBOX)) == 3  # not four: the copy that was here is the one
        assert world.state.records["uid1"].store_id == present
    finally:
        world.server.__exit__(None, None, None)


def test_the_delete_policy_checks_the_copy_that_was_already_here(tmp_path: Path) -> None:
    world, present = _world_with_first_message_stored(tmp_path)
    try:
        def already_here(raw: bytes) -> str | None:
            if raw == messages()["uid1"]:
                return present
            if raw == messages()["uid2"]:
                return "0" * 32  # a copy that is not in the store: its server copy must stay
            return None

        server = ServerSettings("127.0.0.1", world.server.port, ConnectionSecurity.NONE, "user")
        with Pop3Client(server, "secret", CLIENT_LIMITS) as client:
            fetch_pop3(
                client, world.store, world.index, world.state, INBOX, Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD), LIMIT, lambda: world.clock[0],
                already_here=already_here,
            )
        assert set(world.server.messages) == {"uid2"}  # uid1 verified here and deleted; uid3 downloaded and deleted; uid2 kept
    finally:
        world.server.__exit__(None, None, None)


def test_the_first_check_of_an_imported_account_skips_mail_that_is_here_and_then_stops_skipping(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    old = raw_message(subject="Old", body="old\r\n", message_id="<old@x>")
    new = raw_message(subject="New", body="new\r\n", message_id="<new@x>")
    with FakePop3Server({"u1": old, "u2": new}) as pop, FakeSmtpServer() as smtp:
        account = add_account(engine, pop, smtp, storage="local")
        config = engine.config()
        engine.save_config(config.with_account(replace(config.find(account), skip_known_mail=True)))
        with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
            mailbox.ensure_folder(("Imported",))
            mailbox.deliver(("Imported",), old, frozenset({MessageFlag.SEEN}))
        result = call(engine, "mail.fetch", account=account)
        assert result.downloaded == 1
        with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
            subjects = sorted(m.subject for folder in mailbox.store.list_folders() for m in mailbox.index.page(folder.path))
        assert subjects == ["New", "Old"]
        assert engine.config().find(account).skip_known_mail is False


def test_without_the_setting_the_same_message_is_downloaded_again(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    old = raw_message(subject="Old", body="old\r\n", message_id="<old@x>")
    with FakePop3Server({"u1": old}) as pop, FakeSmtpServer() as smtp:
        account = add_account(engine, pop, smtp, storage="local")
        with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
            mailbox.ensure_folder(("Imported",))
            mailbox.deliver(("Imported",), old, frozenset({MessageFlag.SEEN}))
        assert call(engine, "mail.fetch", account=account).downloaded == 1  # the normal behaviour: a POP3 download knows nothing of other copies
