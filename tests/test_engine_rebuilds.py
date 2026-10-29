import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chrono_mail.core import RebuildAborted
from chrono_mail.engine import commands_folders
from chrono_mail.engine.changes import RebuildStep
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.index import Index
from chrono_mail.index.schema import SENT_FILL_KEY, UPGRADE_FROM_13
from chrono_mail.sync import rebuild_index
from tests.mailfactory import raw_message
from tests.test_mime_parse import PICTURE_IN_A_PLAIN_TEXT_MESSAGE
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is the fixture
from tests.test_index_rebuild import make_store

# --- Version ------------------------------------------------------------
__version__ = "test_engine_rebuilds.py 4.2.418"

# --- Version history ----------------------------------------------------
# v4.2.418: the helpers that make an index of format 11 or 12 take away what format 14 added first (D-291, D-292)
# v2.5.335: index.prepare tells how many messages it indexed (it said 0), also when no rebuild was needed
# v2.0.301: a picture labelled inline in a plain-text message: listed, shown below the text, its paperclip filled in by the upgrade from format 12 (D-235)
# v2.0.281: Initial version: a rebuild counts first, can be aborted, keeps the old index usable meanwhile; format 11 is upgraded in place
#           and its Sent dates filled in, resumable; the start check tells which places need what (D-229)


def index_path(setup: Setup) -> Path:
    return setup.engine.directories.index_file(setup.account_id)


def fetched(setup: Setup) -> list[RebuildStep]:
    """The account with its three messages, and the rebuild reports from now on."""
    call(setup.engine, "mail.fetch", account=setup.account_id)
    steps: list[RebuildStep] = []
    setup.engine.rebuilding = steps.append
    return steps


def without_format_14(connection: sqlite3.Connection) -> None:
    """Take away what format 14 added (the people columns, the receiving account and their indexes, D-291, D-292)."""
    for statement in UPGRADE_FROM_13:
        if statement.startswith("CREATE INDEX"):
            connection.execute(f"DROP INDEX {statement.split()[2]}")
    for statement in UPGRADE_FROM_13:
        if statement.startswith("ALTER TABLE"):
            connection.execute(f"ALTER TABLE messages DROP COLUMN {statement.split()[5]}")


def as_format_11(path: Path) -> None:
    """Turn a current index back into format 11, as 1.9.276 to 1.10.279 left it."""
    connection = sqlite3.connect(path)
    without_format_14(connection)
    connection.execute("DROP INDEX messages_by_sent")
    connection.execute("ALTER TABLE messages DROP COLUMN sent")
    connection.execute("UPDATE meta SET value = '11' WHERE key = 'schema'")
    connection.commit()
    connection.close()


def test_an_aborted_rebuild_leaves_the_old_index_and_no_work_file(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    path = tmp_path / "index.sqlite"
    first, _ = rebuild_index(store, path)
    first.close()
    with pytest.raises(RebuildAborted):
        rebuild_index(store, path, stop=lambda: True)
    assert not Path(f"{path}.new").exists()
    index = Index(path)
    assert index.message_count() == 12  # the old one, untouched
    index.close()


def test_the_old_index_stays_in_use_while_a_new_one_is_built_and_what_came_meanwhile_is_caught_up(setup: Setup) -> None:
    steps = fetched(setup)
    listed: list[int] = []

    def during(step: RebuildStep) -> None:
        steps.append(step)
        if not step.ended and step.done == 0 and not listed:  # in the middle of the rebuild: the account works as before
            listed.append(call(setup.engine, "message.list", account=setup.account_id, folder="Inbox").total)
            with open_mailbox(setup.engine, setup.account_id) as mailbox:
                mailbox.deliver(mailbox.folder("Inbox"), raw_message(subject="Came meanwhile", message_id="<late@x>"), frozenset())

    setup.engine.rebuilding = during
    result = call(setup.engine, "index.rebuild", account=setup.account_id)
    assert listed == [3] and result.messages == 3  # read from the old index while the new one was built
    assert call(setup.engine, "message.list", account=setup.account_id, folder="Inbox").total == 4  # caught up after the swap
    assert steps[0] == RebuildStep(setup.account_id, 0, 3, usable=True) and steps[-1].ended  # it may run in the background


def test_the_check_at_start_tells_which_places_need_a_rebuild_or_their_sent_dates(setup: Setup) -> None:
    fetched(setup)
    (state,) = call(setup.engine, "index.check")
    assert (state.account, state.need) == (setup.account_id, "") and state.name
    index_path(setup).unlink()
    assert call(setup.engine, "index.check")[0].need == "rebuild"  # missing
    index_path(setup).write_bytes(b"not a database")
    assert call(setup.engine, "index.check")[0].need == "rebuild"  # damaged
    call(setup.engine, "index.prepare", account=setup.account_id)  # rebuilt before anything uses it
    assert call(setup.engine, "index.check")[0].need == ""
    as_format_11(index_path(setup))
    assert call(setup.engine, "index.check")[0].need == "fill"  # upgraded in place: usable, its Sent dates still to come


def test_prepare_rebuilds_an_unusable_index_with_how_many_of_how_many_and_can_be_aborted(setup: Setup) -> None:
    steps = fetched(setup)
    index_path(setup).write_bytes(b"not a database")
    call(setup.engine, "index.prepare", account=setup.account_id)
    assert steps[0] == RebuildStep(setup.account_id, 0, 3) and RebuildStep(setup.account_id, 3, 3) in steps and steps[-1].ended
    assert not any(step.usable for step in steps)  # nothing can use the account until it is done
    index_path(setup).write_bytes(b"not a database")
    setup.engine.rebuilding = lambda step: call(setup.engine, "index.abort", account=setup.account_id) if not step.ended else None
    with pytest.raises(RebuildAborted):
        call(setup.engine, "index.prepare", account=setup.account_id)
    assert call(setup.engine, "index.check")[0].need == "rebuild"  # still unusable: it starts again next time


def test_prepare_tells_how_many_messages_it_indexed(setup: Setup) -> None:
    fetched(setup)
    index_path(setup).write_bytes(b"not a database")
    rebuilt = call(setup.engine, "index.prepare", account=setup.account_id)
    assert (rebuilt.messages, rebuilt.unreadable) == (3, 0) and rebuilt.seconds > 0  # the index window says "3 messages indexed"
    ready = call(setup.engine, "index.prepare", account=setup.account_id)
    assert (ready.messages, ready.unreadable, ready.seconds) == (3, 0, 0.0)  # nothing to rebuild: what the index holds


def test_format_11_is_upgraded_in_place_and_its_sent_dates_are_filled_in_from_where_they_stopped(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    fetched(setup)
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        for number in (4, 5):
            dated = raw_message(subject=f"Dated {number}", message_id=f"<d{number}@x>", date=datetime(2026, 1, number, tzinfo=UTC))
            mailbox.deliver(mailbox.folder("Inbox"), dated, frozenset())
    as_format_11(index_path(setup))
    before = call(setup.engine, "message.list", account=setup.account_id, folder="Inbox")  # usable at once, nothing rebuilt
    assert before.total == 5 and all(row.sent is None for row in before.messages)
    monkeypatch.setattr(commands_folders, "FILL_BATCH", 2)
    steps: list[RebuildStep] = []

    def abort_after_the_first_batch(step: RebuildStep) -> None:
        steps.append(step)
        if not step.ended:
            call(setup.engine, "index.abort", account=setup.account_id)

    setup.engine.rebuilding = abort_after_the_first_batch
    with pytest.raises(RebuildAborted):
        call(setup.engine, "index.fill-sent", account=setup.account_id)
    assert steps[0] == RebuildStep(setup.account_id, 2, 5, usable=True) and steps[-1].ended
    setup.engine.rebuilding = steps.append
    assert call(setup.engine, "index.fill-sent", account=setup.account_id).filled == 3  # the rest, not the first two again
    rows = {row.subject: row.sent for row in call(setup.engine, "message.list", account=setup.account_id, folder="Inbox").messages}
    assert rows["Dated 4"] == datetime(2026, 1, 4, tzinfo=UTC) and rows["Dated 5"] == datetime(2026, 1, 5, tzinfo=UTC)
    index = Index(index_path(setup))
    assert index.sent_filling() is None and index._connection.execute("SELECT value FROM meta WHERE key = ?", (SENT_FILL_KEY,)).fetchone() is None  # noqa: SLF001
    index.close()
    assert call(setup.engine, "index.fill-sent", account=setup.account_id).filled == 0  # nothing left


def test_a_picture_labelled_inline_is_listed_shown_below_the_text_and_its_paperclip_filled_in_after_the_upgrade(setup: Setup) -> None:
    """The owner's bug (D-235): the picture was shown nowhere and the list had no paperclip; B-11: it is shown below the text too."""
    fetched(setup)
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        mailbox.deliver(mailbox.folder("Inbox"), PICTURE_IN_A_PLAIN_TEXT_MESSAGE, frozenset())
    row = next(row for row in call(setup.engine, "message.list", account=setup.account_id, folder="Inbox").messages if row.subject == "A picture")
    assert row.attachments
    message = call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=row.message_id)
    assert [(a.suggested_name, a.inline) for a in message.attachments][0] == ("photo.png", False) and len(message.attachments) == 2
    assert [(picture.content_type, picture.data_base64) for picture in message.pictures] == [("image/png", "iVBORw==")]
    connection = sqlite3.connect(index_path(setup))  # as 2.0.300 left it: format 12, no paperclip for this message
    without_format_14(connection)
    connection.execute("UPDATE messages SET has_attachments = 0")
    connection.execute("UPDATE meta SET value = '12' WHERE key = 'schema'")
    connection.commit()
    connection.close()
    assert call(setup.engine, "index.check")[0].need == "fill"  # upgraded in place: usable at once, filled in afterwards
    assert call(setup.engine, "index.fill-sent", account=setup.account_id).filled == 4
    rows = {row.subject: row.attachments for row in call(setup.engine, "message.list", account=setup.account_id, folder="Inbox").messages}
    assert rows["A picture"] and sum(rows.values()) == 1  # only the one with an attachment
