import json
from collections.abc import Generator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, StorageError
from chrono_mail.engine import Engine
from chrono_mail.engine import old_files
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.mime import with_extra_headers
from chrono_mail.store import Store
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message
from tests.test_engine_commands import add_account, call

# --- Version ------------------------------------------------------------
__version__ = "test_engine_properties.py 1.1.208"

# --- Version history ----------------------------------------------------
# v1.1.208: Moving back to drafts through Local Folders keeps the sender (D-178)
# v1.1.206: Initial tests: what a message needs beyond itself is kept with the mail, whichever account acted (D-176). The owner's
#           report: a repeating message from an account that keeps its mail in Local Folders was missing from Settings >
#           Scheduled messages and was never sent.

START = datetime(2027, 1, 4, 8, 0)


class Local:
    """One POP3 account that keeps its mail in Local Folders, with a clock that can be moved on."""

    def __init__(self, engine: Engine, smtp: FakeSmtpServer, account: str) -> None:
        self.engine, self.smtp, self.account = engine, smtp, account
        self.at(timedelta())

    def at(self, later: timedelta) -> None:
        moment = (START + later).timestamp()
        self.engine.clock = lambda: moment

    def when(self, later: timedelta) -> str:
        return (START + later).strftime("%Y-%m-%dT%H:%M")

    def ids(self, folder: str) -> list[str]:
        return [row.message_id for row in call(self.engine, "message.list", account=LOCAL_ACCOUNT_ID, folder=folder).messages]

    def old_files_beside_the_account(self) -> list[str]:
        directory = self.engine.directories.account_directory(self.account)
        return sorted(path.name for path in directory.iterdir() if path.name in ("recurring.json", "snoozed.json", "archived.log")) if directory.exists() else []


@pytest.fixture
def local(tmp_path: Path) -> Generator[Local]:
    engine = make_engine(tmp_path)
    mail = {f"u{number}": raw_message(subject=f"Message {number}", to="a@example.com", body="x\r\n", message_id=f"<p{number}@x>") for number in (1, 2)}
    with FakePop3Server(mail) as pop, FakeSmtpServer() as smtp:
        old_files._SETTLED.clear()  # noqa: SLF001 - every test is a new run of the program
        yield Local(engine, smtp, add_account(engine, pop, smtp, email="a@example.com", storage="local"))


def repeat_daily(local: Local) -> Any:  # noqa: ANN401
    return call(
        local.engine, "mail.send", account=local.account, to="you@example.com", subject="Daily", body="again", send_at=local.when(timedelta(hours=1)), repeat_days=1,
    )


def test_a_repeating_message_from_an_account_in_local_folders_is_listed_and_sent(local: Local) -> None:
    result = repeat_daily(local)
    assert local.ids("Scheduled") == [result.message_id]
    (info,) = call(local.engine, "mail.scheduled")  # the owner's report: Settings > Scheduled messages showed nothing
    assert info.message_id == result.message_id and info.repeat == "every 1 day" and info.account == local.account
    local.at(timedelta(hours=2))
    assert call(local.engine, "mail.send-due").sent == 1 and len(local.smtp.received) == 1  # and it was never sent
    local.at(timedelta(days=1, hours=2))
    assert call(local.engine, "mail.send-due").sent == 1 and len(local.smtp.received) == 2
    (info,) = call(local.engine, "mail.scheduled")
    assert info.times_sent == 2 and info.seconds == int((START + timedelta(days=2, hours=1)).timestamp())
    assert local.old_files_beside_the_account() == []  # nothing is kept beside the account any more


def test_a_repeating_message_carries_its_timetable_and_pause_resume_change_and_cancel_work_on_it(local: Local) -> None:
    result = repeat_daily(local)
    paused = call(local.engine, "mail.repeat-pause", folder="Scheduled", account=local.account, message=result.message_id)
    assert paused.paused and call(local.engine, "mail.scheduled")[0].paused
    local.at(timedelta(hours=2))
    assert call(local.engine, "mail.send-due").sent == 0
    call(local.engine, "mail.repeat-resume", folder="Scheduled", account=LOCAL_ACCOUNT_ID, message=result.message_id)  # by either account: the message is the same
    changed = call(local.engine, "mail.repeat-change", folder="Scheduled", account=local.account, message=result.message_id, days=3)
    assert changed.repeat == "every 3 days" and not changed.paused
    with open_mailbox(local.engine, LOCAL_ACCOUNT_ID) as mailbox:
        raw = mailbox.store.read(mailbox.reference(("Scheduled",), result.message_id))
    assert b"X-Chrono-Mail-Repeat: start=" in raw and b"days=3" in raw  # in the message itself
    call(local.engine, "mail.repeat-cancel", folder="Scheduled", account=local.account, message=result.message_id)
    assert local.ids("Scheduled") == [] and call(local.engine, "mail.scheduled") == ()


def test_snooze_works_through_an_account_in_local_folders(local: Local) -> None:
    call(local.engine, "mail.fetch", account=local.account)
    first = local.ids("Inbox")[0]
    call(local.engine, "message.snooze", account=local.account, folder="Inbox", message=first, until=local.when(timedelta(days=1)))
    (info,) = call(local.engine, "message.snoozed")
    assert (info.message_id, info.comes_back_to, info.account) == (first, "Inbox", LOCAL_ACCOUNT_ID)
    row = next(item for item in call(local.engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Snoozed").messages if item.message_id == first)
    assert row.snoozed_until is not None  # the Until column, read where the mail is
    local.at(timedelta(days=1, minutes=1))
    assert call(local.engine, "message.wake").woken == 1 and first in local.ids("Inbox")
    assert call(local.engine, "message.snoozed") == () and local.old_files_beside_the_account() == []


def test_unarchive_finds_where_a_message_came_from_whichever_account_archived_it(local: Local) -> None:
    call(local.engine, "mail.fetch", account=local.account)
    call(local.engine, "folder.create", account=LOCAL_ACCOUNT_ID, folder="Projects")
    first = local.ids("Inbox")[0]
    call(local.engine, "message.move", account=LOCAL_ACCOUNT_ID, folder="Inbox", message=first, target="Projects")
    call(local.engine, "message.archive", account=local.account, folder="Projects", message=first)
    back = call(local.engine, "message.unarchive", account=LOCAL_ACCOUNT_ID, folder="Archive", message=first)  # as Favorites > Archive does
    assert back.folder == "Projects" and local.ids("Projects") == [first]


def test_tags_are_kept_with_the_mail_and_counted_once(local: Local) -> None:
    call(local.engine, "mail.fetch", account=local.account)
    first = local.ids("Inbox")[0]
    call(local.engine, "message.tag", account=local.account, folder="Inbox", messages=[first], add=["Work"])
    store_root = local.engine.directories.store_directory(LOCAL_ACCOUNT_ID)
    assert json.loads((store_root / "properties" / f"{first}.json").read_text("utf-8")) == {"tags": ["Work"]}
    assert [row.tags for row in call(local.engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages if row.message_id == first] == [("Work",)]


# --- the files of before 1.1.206 ------------------------------------------------------------------------------------------------------------


def write_old_files(local: Local) -> tuple[str, str, str]:
    """The state the owner's computer was in: a repeating message whose timetable sat beside the account (not where the mail
    is), a snoozed and an archived message whose records did too, and tags in one file for the whole mailbox."""
    repeating = repeat_daily(local).message_id
    call(local.engine, "mail.fetch", account=local.account)
    first, second = local.ids("Inbox")[:2]
    call(local.engine, "message.tag", account=local.account, folder="Inbox", messages=[first], add=["Work"])  # the index copies it, as it did then
    call(local.engine, "message.snooze", account=local.account, folder="Inbox", message=first, until=local.when(timedelta(days=1)))
    call(local.engine, "message.archive", account=local.account, folder="Inbox", message=second)
    root = local.engine.directories.store_directory(LOCAL_ACCOUNT_ID)
    with open_mailbox(local.engine, LOCAL_ACCOUNT_ID) as mailbox:  # back to how 1.1.205 kept them
        reference = mailbox.reference(("Scheduled",), repeating)
        raw = mailbox.store.read(reference)
        mailbox.store.replace(reference, with_extra_headers(raw, {"X-Chrono-Mail-Repeat": None, "X-Chrono-Mail-Send-At": None}))
        mailbox.index.set_send_times({repeating: None})
        for store_id in (first, second):
            mailbox.store.properties.forget(store_id)
    beside = local.engine.directories.account_directory(local.account)
    start = int((START + timedelta(hours=1)).timestamp())
    (beside / "recurring.json").write_text(json.dumps({repeating: {"start": start, "end": None, "days": 1, "sent": 4}}), "utf-8")
    (beside / "snoozed.json").write_text(json.dumps({first: {"until": int((START + timedelta(days=1)).timestamp()), "folder": "Inbox"}, "f" * 32: {"until": 1, "folder": "Gone"}}), "utf-8")
    (beside / "archived.log").write_text(f"{second}\tInbox\n{second}\tElsewhere\n{second}\tInbox\ncut off", "utf-8")
    (root / "tags.json").write_text(json.dumps({"format": 1, "messages": {first: ["Work"]}}), "utf-8")
    old_files._SETTLED.clear()  # noqa: SLF001 - the program starts again
    return repeating, first, second


def test_the_files_of_before_move_into_the_messages_and_their_properties_once(local: Local) -> None:
    repeating, first, second = write_old_files(local)
    (info,) = call(local.engine, "mail.scheduled")
    assert info.message_id == repeating and info.repeat == "every 1 day" and info.times_sent == 4
    (snoozed,) = call(local.engine, "message.snoozed")
    assert snoozed.message_id == first and snoozed.comes_back_to == "Inbox"
    assert call(local.engine, "message.unarchive", account=LOCAL_ACCOUNT_ID, folder="Archive", message=second).folder == "Inbox"
    assert [row.tags for row in call(local.engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Snoozed").messages] == [("Work",)]
    assert local.old_files_beside_the_account() == []
    assert not (local.engine.directories.store_directory(LOCAL_ACCOUNT_ID) / "tags.json").exists()
    local.at(timedelta(hours=2))
    assert call(local.engine, "mail.send-due").sent == 1 and call(local.engine, "mail.scheduled")[0].times_sent == 5


def test_an_interrupted_move_of_the_files_of_before_loses_nothing_and_finishes_later(local: Local, monkeypatch: pytest.MonkeyPatch) -> None:
    repeating, first, _ = write_old_files(local)

    def failing(self: Store, *arguments: object, **named: object) -> None:
        raise StorageError("the disk is full")

    monkeypatch.setattr(Store, "replace", failing)
    call(local.engine, "message.snoozed")  # opens the mailbox: the snooze and archive records move, the timetable cannot
    assert local.old_files_beside_the_account() == ["recurring.json"]
    monkeypatch.undo()
    (info,) = call(local.engine, "mail.scheduled")  # tried again at the next opening
    assert info.message_id == repeating and info.times_sent == 4 and local.old_files_beside_the_account() == []
    assert call(local.engine, "message.snoozed")[0].message_id == first


def test_moving_back_to_drafts_through_local_folders_keeps_the_sender_and_drops_the_schedule(local: Local) -> None:
    result = repeat_daily(local)
    call(local.engine, "mail.unschedule", account=LOCAL_ACCOUNT_ID, folder="Scheduled", message=result.message_id)  # as a drop on Drafts names it
    (draft,) = call(local.engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Drafts").messages
    source = call(local.engine, "message.source", account=LOCAL_ACCOUNT_ID, folder="Drafts", message=draft.message_id).text
    assert "a@example.com" in source.split("\n\n")[0] and "X-Chrono-Mail-Repeat" not in source and "X-Chrono-Mail-Send-At" not in source
    assert call(local.engine, "mail.scheduled") == () and local.ids("Scheduled") == []
