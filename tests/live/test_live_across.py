import time

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, MessageFlag
from chrono_mail.engine import Engine
from chrono_mail.engine.local import ensure_local_folders
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.protocols.imap import ImapClient
from tests.live.test_live_imap_online import live_account, message, on_server, run, setup, token  # noqa: F401 - fixtures

__all__ = ["live_account", "setup", "token"]

# --- Version ------------------------------------------------------------
__version__ = "test_live_across.py 3.6.366"

# --- Version history ----------------------------------------------------
# v3.6.366: Initial: messages moved from Local Folders to a folder of the real server and back, message by message, timed (D-272)

pytestmark = pytest.mark.live
COUNT: int = 20


def test_messages_go_to_a_real_server_folder_and_back(setup: tuple[Engine, str, ImapClient, list[str]], token: str) -> None:
    engine, account, imap, (folder, _second, _third) = setup
    imap.create_folder(folder)
    imap.subscribe(folder)
    run(engine, "mail.fetch", account=account)
    ensure_local_folders(engine)
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        ids = [mailbox.deliver(mailbox.folder("Inbox"), message(token, number), frozenset({MessageFlag.SEEN})).store_id for number in range(COUNT)]
    started = time.monotonic()
    there = run(engine, "message.move-across", account=LOCAL_ACCOUNT_ID, folder="Inbox", target_account=account, target=folder, messages=tuple(ids))
    upload = time.monotonic() - started
    assert set(there.done) == set(ids) and there.failed == ()
    assert len(on_server(imap, folder)) == COUNT and len(on_server(imap, folder, "SEEN")) == COUNT
    started = time.monotonic()
    back = run(engine, "message.move-across", account=account, folder=folder, target_account=LOCAL_ACCOUNT_ID, target="Inbox", messages=tuple(ids))
    download = time.monotonic() - started
    assert set(back.done) == set(ids) and on_server(imap, folder) == ()
    print(f"\n{COUNT} messages to the server: {upload:.2f} s ({upload / COUNT * 1000:.0f} ms each); back: {download:.2f} s ({download / COUNT * 1000:.0f} ms each)")
