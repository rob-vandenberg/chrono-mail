import os
import time
from collections.abc import Generator
from contextlib import suppress
from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.engine import Engine, build_registry
from chrono_mail.protocols.imap import ImapClient
from tests.enginefactory import make_engine
from tests.live.account import LiveAccount
from tests.live.test_live_imap import connect

# --- Version ------------------------------------------------------------
__version__ = "test_live_imap_online.py 0.4.121"

# --- Version history ----------------------------------------------------
# v0.4.121: Initial live tests: every action of an IMAP account is on the real server at once, before any sync (D-117)

pytestmark = pytest.mark.live
REGISTRY = build_registry()


def message(token: str, number: int) -> bytes:
    return (
        f"From: live@example.com\r\nTo: live@example.com\r\nSubject: Online {token} {number:02d}\r\n"
        f"Message-ID: <online-{token}-{number}@example.com>\r\n\r\nBody {number}.\r\n"
    ).encode()


def run(engine: Engine, command: str, **values: object) -> Any:  # noqa: ANN401 - a command result
    return REGISTRY.run(engine, command, values)


def on_server(imap: ImapClient, folder: str, criteria: str = "UNDELETED") -> tuple[int, ...]:
    """The UIDs in a folder of the real server, seen over a connection of its own (not the program's)."""
    imap.select(folder, read_only=True)
    return imap.uid_search(criteria)


def rows(engine: Engine, account: str, folder: str) -> list[Any]:
    return sorted(run(engine, "message.list", account=account, folder=folder, limit=500).messages, key=lambda row: row.subject)


@pytest.fixture
def token() -> str:
    return os.urandom(4).hex()


@pytest.fixture
def setup(live_account: LiveAccount, token: str, tmp_path: Path) -> Generator[tuple[Engine, str, ImapClient, list[str]]]:
    engine = make_engine(tmp_path)
    created = run(
        engine, "account.add", kind="imap", display_name="Live", email_address=live_account.user, username=live_account.user, password=live_account.password,
        incoming_host=live_account.host, incoming_port=live_account.imap_ssl_port, incoming_security="tls",
        outgoing_host=live_account.host, outgoing_port=live_account.smtp_ssl_port, outgoing_security="tls",
    )
    imap = connect(live_account, secure=True)
    folders = [f"ChronoMailOnline-{token}-A", f"ChronoMailOnline-{token}-B", f"ChronoMailOnline-{token}-C"]
    try:
        yield engine, str(created.account_id), imap, folders
    finally:
        engine.close_imap_links()
        for name in folders:
            with suppress(Exception):
                imap.select("INBOX", read_only=True)
                imap.delete_folder(name)
        for name in ("INBOX", "Sent", "Drafts", "Trash"):  # whatever this test left in the account's own folders
            with suppress(Exception):
                imap.select(name)
                found = imap.uid_search(f"SUBJECT {token}")
                if found:
                    uids = ",".join(str(uid) for uid in found)
                    imap.uid_store(uids, ["\\Deleted"], "add")
                    imap.uid_expunge(uids)
        imap.close()


def test_every_action_is_on_the_real_server_before_any_sync(setup: tuple[Engine, str, ImapClient, list[str]], token: str) -> None:
    engine, account, imap, (first, second, third) = setup
    for name in (first, second):
        imap.create_folder(name)
        imap.subscribe(name)
    for number in range(1, 13):
        imap.append(first, message(token, number))
    fetched = run(engine, "mail.fetch", account=account)
    assert fetched.aborted is None and fetched.blocked == ()
    listed = rows(engine, account, first)
    assert len(listed) == 12

    # marks
    run(engine, "message.flag", account=account, folder=first, message=listed[0].message_id, seen=True, flagged=True)
    assert len(on_server(imap, first, "FLAGGED SEEN")) == 1
    run(engine, "message.flag", account=account, folder=first, message=listed[0].message_id, seen=False, flagged=False)
    assert on_server(imap, first, "FLAGGED") == () and len(on_server(imap, first, "UNSEEN")) == 12

    # move and copy between server folders
    run(engine, "message.move", account=account, folder=first, message=listed[1].message_id, target=second)
    assert len(on_server(imap, first)) == 11 and len(on_server(imap, second)) == 1
    run(engine, "message.copy", account=account, folder=first, message=listed[2].message_id, target=second)
    assert len(on_server(imap, first)) == 11 and len(on_server(imap, second)) == 2

    # a folder made in Chrono Mail, and a move into it and back out of it
    run(engine, "folder.create", account=account, folder=third)
    moved = run(engine, "message.move", account=account, folder=first, message=listed[3].message_id, target=third)
    assert len(on_server(imap, third)) == 1 and len(on_server(imap, first)) == 10
    run(engine, "message.move", account=account, folder=third, message=moved.message_id, target=first)
    assert on_server(imap, third) == () and len(on_server(imap, first)) == 11

    # deleting most of a folder yourself: at once, never held back
    remaining = rows(engine, account, first)
    for row in remaining[:9]:
        run(engine, "message.delete", account=account, folder=first, message=row.message_id, permanent=True)
    assert len(on_server(imap, first)) == 2 and len(on_server(imap, first, "ALL")) == 2  # removed, not only marked

    # a sync finds nothing left to do, and both sides agree
    synced = run(engine, "mail.fetch", account=account)
    assert synced.blocked == () and not [text for text in synced.failed if first in text or second in text or third in text]
    for name in (first, second, third):
        assert len(rows(engine, account, name)) == len(on_server(imap, name)), name


def test_drafts_and_the_sent_copy_are_on_the_real_server_at_once(setup: tuple[Engine, str, ImapClient, list[str]], live_account: LiveAccount, token: str) -> None:
    engine, account, imap, _ = setup
    run(engine, "mail.fetch", account=account)
    draft = run(engine, "draft.save", account=account, subject=f"Draft {token} v1", body="one")
    assert len(on_server(imap, "Drafts", f"SUBJECT {token}")) == 1
    replaced = run(engine, "draft.save", account=account, subject=f"Draft {token} v2", body="two", replace_message=draft.message_id)
    assert len(on_server(imap, "Drafts", f"SUBJECT {token}")) == 1 and len(on_server(imap, "Drafts", f"SUBJECT {token} SUBJECT v2")) == 1
    run(engine, "message.delete", account=account, folder="Drafts", message=replaced.message_id, permanent=True)
    assert on_server(imap, "Drafts", f"SUBJECT {token}") == ()

    sent = run(engine, "mail.send", account=account, to=live_account.user, subject=f"Sent {token}", body="hello")
    assert sent.sent and len(on_server(imap, "Sent", f"SUBJECT {token}")) == 1  # in Sent on the server at once
    deadline = time.monotonic() + 60
    while not on_server(imap, "INBOX", f"SUBJECT {token}") and time.monotonic() < deadline:
        time.sleep(2)
    synced = run(engine, "mail.fetch", account=account)
    assert synced.blocked == ()
    sent_rows = [row for row in rows(engine, account, "Sent") if token in row.subject]
    assert len(sent_rows) == 1  # no second copy from the sync
    run(engine, "message.delete", account=account, folder="Sent", message=sent_rows[0].message_id, permanent=True)
    assert on_server(imap, "Sent", f"SUBJECT {token}") == ()
