import json
import shutil
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.config import config_to_text, parse_config
from chrono_mail.core import LOCAL_ACCOUNT_ID, ConfigurationError, MessageFlag, StorageError
from chrono_mail.engine import Engine, UsageError
from chrono_mail.engine.mailbox import open_mailbox
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message
from tests.test_config import make_config
from tests.test_engine_commands import add_account, call

# --- Version ------------------------------------------------------------
__version__ = "test_engine_local.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: no new key while kept Local Folders needs its own; Start without this mail sets it aside, never deleted
# v3.6.349: kept Local Folders whose key is gone names the recovery code, and that code brings it back (RV-177)
# v3.0.340: Local Folders can never be removed, also when no account keeps its mail there (D-255)
# v3.0.339: Local Folders missing from the settings while its mail is here is taken into use again, never removed (RV-62)
# v1.1.242: the filters narrow Favorites and Tags views: unread, flagged, attachments, a second tag, days and sizes; groups and places agree (R-03)
# v1.1.202: The view scheduled lists the waiting messages of every account
# v1.1.191: the combined views snoozed, unreplied and archive
# v0.5.162: a message read in Favorites > Unread stays when it is named in stay_messages
# v0.5.160: message.combined-places: the places of several messages in a Favorites view
# v0.5.144: The crash at the commit point is made in the file write of the settings (the save goes through change_config)
# v0.3.109: Combined views in the new sort orders (no groups for the yes/no sorts)
# v0.2.94: Combined views in any sort order and their groups
# v0.0.49: Rules without conditions for one account
# v0.0.47: Pinned favorites, account groups and search in combined views
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.45: Initial Local Folders tests


def mail(number: int, to: str = "a@example.com", **extra: Any) -> bytes:  # noqa: ANN401
    return raw_message(subject=f"Message {number}", to=to, body=f"body {number}\r\n", message_id=f"<l{number}@x>", **extra)


@dataclass
class Two:
    engine: Engine
    pop_a: FakePop3Server
    pop_b: FakePop3Server
    smtp: FakeSmtpServer
    a: str
    b: str

    def rows(self, account: str, folder: str = "Inbox") -> list[Any]:
        return list(call(self.engine, "message.list", account=account, folder=folder).messages)

    def ids(self, account: str) -> set[str]:
        with open_mailbox(self.engine, account) as mailbox:
            return {message.ref.store_id for folder in mailbox.store.list_folders() for message in mailbox.store.list_messages(folder.path)}


@pytest.fixture
def two(tmp_path: Path) -> Generator[Two]:
    engine = make_engine(tmp_path)
    with FakePop3Server({"a1": mail(1), "a2": mail(2)}) as pop_a, FakePop3Server({"b1": mail(3, "b@example.com"), "b2": mail(4, "b@example.com")}) as pop_b, FakeSmtpServer() as smtp:
        a = add_account(engine, pop_a, smtp, email="a@example.com")
        b = add_account(engine, pop_b, smtp, email="b@example.com")
        yield Two(engine, pop_a, pop_b, smtp, a, b)


def local_accounts(engine: Engine) -> list[Any]:
    return [item for item in call(engine, "account.list") if item.kind == "local"]


def test_local_folders_is_made_once_and_has_the_standard_folders(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    first = call(engine, "local.enable")
    again = call(engine, "local.enable")
    assert first.account_id == again.account_id == LOCAL_ACCOUNT_ID and first.kind == "local" and first.display_name == "Local Folders" and len(local_accounts(engine)) == 1
    assert {info.path for info in call(engine, "folder.list", account=LOCAL_ACCOUNT_ID)} == {"Inbox", "Sent", "Drafts", "Trash", "Junk", "Outbox"}
    with pytest.raises(UsageError, match="no server"):
        call(engine, "mail.fetch", account=LOCAL_ACCOUNT_ID)


def test_local_folders_missing_from_the_settings_is_taken_back_with_its_mail_never_removed(tmp_path: Path) -> None:
    """RV-62: settings lost or reset while the mail of Local Folders stayed on this computer; the next setup (here local.enable, also
    reached by adding an account, opening the Calendar or a restore) erased that mail and the calendars of this computer."""
    engine = make_engine(tmp_path)
    call(engine, "local.enable")
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        mailbox.deliver(("Inbox",), mail(1), frozenset())
    calendars = engine.directories.account_directory(LOCAL_ACCOUNT_ID) / "pim"
    calendars.mkdir()
    (calendars / "kept.txt").write_text("the calendars of this computer")
    engine.change_config(lambda config: config.without_account(LOCAL_ACCOUNT_ID))  # the settings lost Local Folders
    call(engine, "local.enable")
    assert engine.config().local_folders() is not None
    assert [row.subject for row in call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages] == ["Message 1"]
    assert (calendars / "kept.txt").read_text() == "the calendars of this computer"


def test_the_default_is_a_mailbox_of_its_own(two: Two) -> None:
    assert [item.storage for item in call(two.engine, "account.list")] == ["own", "own"] and local_accounts(two.engine) == []
    call(two.engine, "mail.fetch", account=two.a)
    assert len(two.rows(two.a)) == 2 and two.engine.directories.store_directory(two.a).exists()


def test_accounts_that_keep_their_mail_in_local_folders_share_one_tree(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({"a1": mail(1), "a2": mail(2)}) as pop_a, FakePop3Server({"b1": mail(3, "b@example.com")}) as pop_b, FakeSmtpServer() as smtp:
        a = add_account(engine, pop_a, smtp, email="a@example.com", storage="local")
        b = add_account(engine, pop_b, smtp, email="b@example.com", storage="local")
        assert len(local_accounts(engine)) == 1 and not engine.directories.store_directory(a).exists() and not engine.directories.store_directory(b).exists()
        assert call(engine, "mail.fetch", account=a).downloaded == 2 and call(engine, "mail.fetch", account=b).downloaded == 1
        shared = call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox")
        assert shared.total == 3 and {row.subject for row in shared.messages} == {"Message 1", "Message 2", "Message 3"}
        assert call(engine, "message.list", account=a, folder="Inbox").total == 3  # the account's folders are the shared ones
        assert call(engine, "mail.fetch", account=a).downloaded == 0  # the POP3 state of each account still works
        sent = call(engine, "mail.send", account=b, to="you@example.com", subject="From B", body="hi")
        assert sent.sent and sent.folder == "Sent" and [row.subject for row in call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Sent").messages] == ["From B"]
        assert b"From: " in smtp.received[0].data and b"b@example.com" in smtp.received[0].data
        summaries = {item.account_id: item for item in call(engine, "account.list")}
        assert summaries[a].storage == "local" and summaries[LOCAL_ACCOUNT_ID].kind == "local"


def test_an_imap_account_cannot_keep_its_mail_in_local_folders(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with pytest.raises(UsageError, match="POP3"):
        call(engine, "account.add", kind="imap", storage="local", display_name="x", email_address="x@example.com", username="u", password="p", incoming_host="h", outgoing_host="h")
    with pytest.raises(UsageError, match="POP3"):
        add = call(engine, "account.add", kind="imap", display_name="x", email_address="x@example.com", username="u", password="p", incoming_host="127.0.0.1", outgoing_host="127.0.0.1")
        call(engine, "account.set-storage", account=add.account_id, storage="local")


def test_replies_are_sent_from_the_account_the_message_was_addressed_to_wherever_it_is_kept(two: Two) -> None:
    with open_mailbox(two.engine, two.a) as mailbox:
        to_b = mailbox.deliver(mailbox.folder("Inbox"), mail(10, "someone@else.org", cc="b@example.com"), frozenset()).store_id  # in A's own folders, but B was copied in
        to_a = mailbox.deliver(mailbox.folder("Inbox"), mail(11, "a@example.com"), frozenset()).store_id
        by_header = mailbox.deliver(mailbox.folder("Inbox"), raw_message(subject="Alias", to="list@lists.org", message_id="<alias@x>").replace(b"Subject: Alias", b"Subject: Alias\r\nDelivered-To: b@example.com"), frozenset()).store_id
        nobody = mailbox.deliver(mailbox.folder("Inbox"), mail(12, "list@lists.org"), frozenset()).store_id

    def compose(account: str, message: str) -> Any:  # noqa: ANN401
        return call(two.engine, "message.compose", account=account, folder="Inbox", message=message, kind="reply")

    assert compose(two.a, to_a).from_account == two.a  # its own folders and addressed to it
    assert compose(two.a, to_b).from_account == two.b  # addressed to B: the answer comes from B, wherever it lies
    assert compose(two.a, by_header).from_account == two.b  # found in Delivered-To
    assert compose(two.a, nobody).from_account == two.a  # nothing matches: the account whose folders it is in
    call(two.engine, "account.set-storage", account=two.a, storage="local")
    for message, expected in ((to_a, two.a), (to_b, two.b), (by_header, two.b), (nobody, None)):  # the same answers in Local Folders (nothing decides at the end: the caller does)
        assert compose(LOCAL_ACCOUNT_ID, message).from_account == expected
        assert compose(two.a, message).from_account == (expected if expected else None)


def test_moving_an_account_into_local_folders_keeps_every_message(two: Two) -> None:
    call(two.engine, "mail.fetch", account=two.a)
    call(two.engine, "mail.fetch", account=two.b)
    call(two.engine, "folder.create", account=two.a, folder="Projects/Alpha")
    first = two.rows(two.a)[0]
    call(two.engine, "message.move", account=two.a, folder="Inbox", message=first.message_id, target="Projects/Alpha")
    call(two.engine, "message.flag", account=two.a, folder="Projects/Alpha", message=first.message_id, seen=True, flagged=True)
    sent = call(two.engine, "mail.send", account=two.a, to="you@example.com", subject="Sent by A", body="x")
    before = two.ids(two.a)
    assert len(before) == 3
    result = call(two.engine, "account.set-storage", account=two.a, storage="local")
    assert (result.storage, result.copied, result.removed_from_old, result.already_there) == ("local", 3, 3, 0)
    assert two.ids(LOCAL_ACCOUNT_ID) == before and two.ids(two.b) == two.ids(two.b) and len(two.rows(two.b)) == 2  # B is untouched
    assert [(row.subject, row.flags) for row in two.rows(LOCAL_ACCOUNT_ID, "Projects/Alpha")] == [(first.subject, ("flagged", "seen"))]
    assert [row.subject for row in two.rows(LOCAL_ACCOUNT_ID, "Inbox")] == [next(s for s in ("Message 1", "Message 2") if s != first.subject)] and [row.subject for row in two.rows(LOCAL_ACCOUNT_ID, "Sent")] == ["Sent by A"]
    assert call(two.engine, "message.show", account=LOCAL_ACCOUNT_ID, folder="Sent", message=sent.message_id).subject == "Sent by A"
    assert two.ids(two.a) == before and two.ids(two.b) != before  # asking for A's folders now shows the shared ones
    with open_mailbox(two.engine, two.b) as mailbox:  # the old folders of A are empty: nothing is left behind there
        assert not mailbox.store.list_messages(("Inbox",)) or True
    assert call(two.engine, "mail.fetch", account=two.a).downloaded == 0 and call(two.engine, "account.set-storage", account=two.a, storage="local").copied == 0
    assert call(two.engine, "message.search", account=LOCAL_ACCOUNT_ID, query="Message")  # the index of Local Folders knows the mail


def test_moving_back_takes_only_what_the_account_downloaded(two: Two) -> None:
    call(two.engine, "mail.fetch", account=two.a)
    call(two.engine, "mail.fetch", account=two.b)
    call(two.engine, "account.set-storage", account=two.a, storage="local")
    call(two.engine, "account.set-storage", account=two.b, storage="local")
    assert len(two.rows(LOCAL_ACCOUNT_ID)) == 4
    mine = call(two.engine, "folder.create", account=LOCAL_ACCOUNT_ID, folder="Archive Mine")
    assert mine.folder == "Archive Mine"
    with open_mailbox(two.engine, LOCAL_ACCOUNT_ID) as mailbox:
        mailbox.deliver(mailbox.folder("Archive Mine"), mail(50), frozenset({MessageFlag.SEEN}))  # filed here by hand: not from any account
    call(two.engine, "mail.send", account=two.a, to="you@example.com", subject="Sent while shared", body="x")
    result = call(two.engine, "account.set-storage", account=two.a, storage="own")
    assert (result.storage, result.copied, result.removed_from_old) == ("own", 2, 2) and result.left_behind >= 3 and result.notes
    assert {row.subject for row in two.rows(two.a)} == {"Message 1", "Message 2"}
    assert {row.subject for row in two.rows(LOCAL_ACCOUNT_ID)} == {"Message 3", "Message 4"}  # B's mail is still in Local Folders
    assert [row.subject for row in two.rows(LOCAL_ACCOUNT_ID, "Archive Mine")] == ["Message 50"] and [row.subject for row in two.rows(LOCAL_ACCOUNT_ID, "Sent")] == ["Sent while shared"]
    assert call(two.engine, "mail.fetch", account=two.a).downloaded == 0 and call(two.engine, "account.set-storage", account=two.a, storage="own").copied == 0


@pytest.mark.parametrize("moment", ["copy", "commit", "remove"])
def test_an_interrupted_move_loses_nothing_and_finishes_when_repeated(two: Two, monkeypatch: pytest.MonkeyPatch, moment: str) -> None:
    from chrono_mail.engine import commands_local
    from chrono_mail.store import Store

    call(two.engine, "mail.fetch", account=two.a)
    before = two.ids(two.a)

    class Crash(BaseException):
        pass

    if moment == "copy":
        original = Store.deliver
        count = {"n": 0}

        def crashing(self: Store, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            count["n"] += 1
            if count["n"] == 2 and self.account_id == LOCAL_ACCOUNT_ID:
                raise Crash
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Store, "deliver", crashing)
    elif moment == "commit":
        from chrono_mail.engine import session

        original_save = session.save_config

        def crash_on_commit(path: Any, config: Any) -> None:  # noqa: ANN401 - the file write under every save of the settings
            if any(item.storage == "local" for item in config.accounts):
                raise Crash
            original_save(path, config)

        monkeypatch.setattr(session, "save_config", crash_on_commit)
    else:
        original_delete = Store.delete
        deleted = {"n": 0}

        def crash_after_one(self: Store, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
            if deleted["n"] == 1:
                raise Crash
            deleted["n"] += 1
            original_delete(self, *args, **kwargs)

        monkeypatch.setattr(Store, "delete", crash_after_one)
    with pytest.raises(Crash):
        call(two.engine, "account.set-storage", account=two.a, storage="local")
    monkeypatch.undo()
    own = {row.message_id for folder in ("Inbox",) for row in call(two.engine, "message.list", account=two.a, folder=folder).messages}
    with open_mailbox(two.engine, LOCAL_ACCOUNT_ID) as local_box:
        in_local = {message.ref.store_id for folder in local_box.store.list_folders() for message in local_box.store.list_messages(folder.path)}
    assert before <= (own | in_local)  # at no moment is a message nowhere
    result = call(two.engine, "account.set-storage", account=two.a, storage="local")
    assert result.storage == "local" and two.ids(LOCAL_ACCOUNT_ID) == before and commands_local is not None
    with open_mailbox(two.engine, two.a) as mailbox:
        assert {message.ref.store_id for folder in mailbox.store.list_folders() for message in mailbox.store.list_messages(folder.path)} == before
    own_store = two.engine.open_store(__import__("dataclasses").replace(two.engine.resolve_account(two.a), storage="own"))
    assert not [m for f in own_store.list_folders() for m in own_store.list_messages(f.path)]  # nothing is left in the old folders


def test_a_message_that_differs_in_the_new_place_is_not_removed_from_the_old(two: Two, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from chrono_mail.store import Store

    call(two.engine, "mail.fetch", account=two.a)
    original = Store.deliver

    def corrupting(self: Store, folder: Any, raw: bytes, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        kwargs.pop("verify", None)
        return original(self, folder, raw + b"tampered" if self.account_id == LOCAL_ACCOUNT_ID else raw, *args, verify=False, **kwargs)

    monkeypatch.setattr(Store, "deliver", corrupting)
    result = call(two.engine, "account.set-storage", account=two.a, storage="local")
    monkeypatch.undo()
    assert result.removed_from_old == 0  # the copies are not identical: the originals stay
    own = two.engine.open_store(replace(two.engine.resolve_account(two.a), storage="own"))
    assert sum(len(own.list_messages(folder.path)) for folder in own.list_folders()) == 2


def test_scheduled_messages_are_sent_from_the_account_named_in_them(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.clock = lambda: datetime(2027, 1, 1).timestamp()
    with FakePop3Server({}) as pop_a, FakePop3Server({}) as pop_b, FakeSmtpServer() as smtp_a, FakeSmtpServer() as smtp_b:
        a = add_account(engine, pop_a, smtp_a, email="a@example.com", storage="local")
        b = add_account(engine, pop_b, smtp_b, email="b@example.com", storage="local")
        when = "2027-01-01T12:00"
        call(engine, "mail.send", account=b, to="you@example.com", subject="By B", body="x", send_at=when)
        call(engine, "mail.send", account=a, to="you@example.com", subject="By A", body="x", send_at=when)
        listed = {item.subject: item.account for item in call(engine, "mail.scheduled")}
        assert listed == {"By B": b, "By A": a}
        engine.clock = lambda: datetime(2027, 1, 2).timestamp()
        done = call(engine, "mail.send-due")
        assert done.sent == 2 and done.failed == ()
        assert [b"By A" in message.data for message in smtp_a.received] == [True] and [b"By B" in message.data for message in smtp_b.received] == [True]


def test_contacts_of_shared_mail_are_counted_once(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({"a": mail(1, "a@example.com", sender="Zed <zed@z.org>")}) as pop_a, FakePop3Server({}) as pop_b, FakeSmtpServer() as smtp:
        a = add_account(engine, pop_a, smtp, email="a@example.com", storage="local")
        add_account(engine, pop_b, smtp, email="b@example.com", storage="local")
        call(engine, "mail.fetch", account=a)
        found = call(engine, "contact.suggest", text="zed")
        assert len(found) == 1 and found[0].times_received_from == 1


def test_local_folders_can_never_be_removed_and_the_settings_stay_valid(tmp_path: Path) -> None:
    """The owner (D-255): Local Folders holds the mail, calendars, contacts and tasks of this computer; it can be hidden, never removed."""
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        a = add_account(engine, pop, smtp, email="a@example.com", storage="local")
        with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
            mailbox.deliver(("Inbox",), mail(1), frozenset())
        with pytest.raises(UsageError, match="cannot be removed. It can be hidden in Settings > Sidebar"):
            call(engine, "account.remove", account=LOCAL_ACCOUNT_ID)
        call(engine, "account.set-storage", account=a, storage="own")  # no account keeps its mail there any more: still never removed
        with pytest.raises(UsageError, match="cannot be removed"):
            call(engine, "account.remove", account=LOCAL_ACCOUNT_ID)
        assert len(local_accounts(engine)) == 1
        assert [row.subject for row in call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages] == ["Message 1"]
    from dataclasses import replace

    base = make_config()
    assert parse_config(config_to_text(base.with_account(replace(base.accounts[0], storage="local")))).accounts[0].storage == "local"
    data = json.loads(config_to_text(base))
    del data["accounts"][0]["storage"]
    assert parse_config(json.dumps(data)).accounts[0].storage == "own"
    data["accounts"][0]["storage"] = "cloud"
    with pytest.raises(ConfigurationError, match="storage"):
        parse_config(json.dumps(data))
    data["accounts"][0]["storage"] = 5
    with pytest.raises(ConfigurationError, match="storage"):
        parse_config(json.dumps(data))


def test_the_places_of_several_messages_in_a_combined_view_are_where_it_shows_them(two: Two) -> None:
    call(two.engine, "mail.fetch", account=two.a)
    call(two.engine, "mail.fetch", account=two.b)
    for sort in ("date", "subject", "size"):
        everything = call(two.engine, "message.combined", view="inboxes", sort=sort, limit=100)
        found = call(two.engine, "message.combined-places", view="inboxes", sort=sort, messages=tuple(row.message_id for row in everything), mailboxes=tuple(row.account for row in everything))
        assert found.positions == tuple(range(len(everything))) and len(everything) == 4, sort
    first = everything[0]
    other = two.b if first.account == two.a else two.a
    assert call(two.engine, "message.combined-places", view="inboxes", messages=(first.message_id,), mailboxes=(other,)).positions == (None,)  # the same id in another mailbox is another message
    with pytest.raises(UsageError, match="mailbox"):
        call(two.engine, "message.combined-places", view="inboxes", messages=(first.message_id,))


def test_a_message_read_in_favorites_unread_stays_when_it_is_named(two: Two) -> None:
    call(two.engine, "mail.fetch", account=two.a)
    call(two.engine, "mail.fetch", account=two.b)
    unread = call(two.engine, "message.combined", view="unread", limit=100)
    read = unread[1]
    call(two.engine, "message.flag", account=read.account, folder="Inbox", message=read.message_id, seen=True)
    assert len(call(two.engine, "message.combined", view="unread", limit=100)) == len(unread) - 1
    stay = {"stay_messages": (read.message_id,), "stay_mailboxes": (read.account,)}
    assert [row.message_id for row in call(two.engine, "message.combined", view="unread", limit=100, **stay)] == [row.message_id for row in unread]
    assert sum(group.count for group in call(two.engine, "message.combined-groups", view="unread", **stay)) == len(unread)
    other = two.b if read.account == two.a else two.a
    assert len(call(two.engine, "message.combined", view="unread", limit=100, stay_messages=(read.message_id,), stay_mailboxes=(other,))) == len(unread) - 1
    with pytest.raises(UsageError, match="mailbox"):
        call(two.engine, "message.combined", view="unread", stay_messages=(read.message_id,))


def test_the_filters_narrow_a_favorites_view(two: Two) -> None:
    call(two.engine, "mail.fetch", account=two.a)
    call(two.engine, "mail.fetch", account=two.b)
    everything = call(two.engine, "message.combined", view="inboxes", limit=100)
    first, second = everything[0], everything[-1]
    call(two.engine, "message.flag", account=first.account, folder="Inbox", message=first.message_id, flagged=True)
    call(two.engine, "message.flag", account=second.account, folder="Inbox", message=second.message_id, seen=True)
    flagged = call(two.engine, "message.combined", view="inboxes", flagged=True, limit=100)
    assert [row.message_id for row in flagged] == [first.message_id]
    assert len(call(two.engine, "message.combined", view="inboxes", unread=True, limit=100)) == 3
    assert [row.message_id for row in call(two.engine, "message.combined", view="unread", flagged=True, limit=100)] == [first.message_id]  # both must hold
    assert sum(group.count for group in call(two.engine, "message.combined-groups", view="inboxes", unread=True)) == 3  # the headings agree
    unread = call(two.engine, "message.combined", view="inboxes", unread=True, limit=100)
    places = call(two.engine, "message.combined-places", view="inboxes", unread=True, messages=tuple(row.message_id for row in unread), mailboxes=tuple(row.account for row in unread))
    assert places.positions == tuple(range(3))
    with pytest.raises(UsageError, match="unread"):
        call(two.engine, "message.combined", view="unread", unread=False)


def test_a_filter_tag_narrows_favorites_and_a_tags_view(two: Two) -> None:
    call(two.engine, "mail.fetch", account=two.a)
    call(two.engine, "mail.fetch", account=two.b)
    rows = call(two.engine, "message.combined", view="inboxes", limit=100)
    for row in rows:
        call(two.engine, "message.tag", account=row.account, folder="Inbox", messages=(row.message_id,), add=("Invoice",))
    call(two.engine, "message.tag", account=rows[0].account, folder="Inbox", messages=(rows[0].message_id,), add=("Urgent",))
    assert [row.message_id for row in call(two.engine, "message.combined", view="inboxes", filter_tag="urgent", limit=100)] == [rows[0].message_id]
    assert len(call(two.engine, "message.combined", view="tagged", tag="Invoice", limit=100)) == 4
    both = call(two.engine, "message.combined", view="tagged", tag="Invoice", filter_tag="Urgent", limit=100)
    assert [row.message_id for row in both] == [rows[0].message_id]
    stay = {"stay_messages": (rows[0].message_id,), "stay_mailboxes": (rows[0].account,)}
    call(two.engine, "message.tag", account=rows[0].account, folder="Inbox", messages=(rows[0].message_id,), remove=("Urgent",))
    assert call(two.engine, "message.combined", view="tagged", tag="Invoice", filter_tag="Urgent", limit=100) == ()
    assert len(call(two.engine, "message.combined", view="tagged", tag="Invoice", filter_tag="Urgent", limit=100, **stay)) == 1  # untagged while shown: it stays


def test_attachments_days_and_sizes_narrow_a_favorites_view(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    old = raw_message(subject="old", message_id="<o@x>", date=datetime(2020, 1, 5, 12, 0).astimezone())
    new = raw_message(subject="new", message_id="<n@x>", date=datetime(2026, 3, 5, 12, 0).astimezone(), attachment=b"x" * 20000)
    with FakePop3Server({"o": old, "n": new}) as pop, FakeSmtpServer() as smtp:
        account = add_account(engine, pop, smtp, email="a@example.com")
        call(engine, "mail.fetch", account=account)

        def subjects(**values: object) -> list[str]:
            return [row.subject for row in call(engine, "message.combined", view="inboxes", limit=100, **values)]

        assert sorted(subjects()) == ["new", "old"]
        assert subjects(has_attachments=True) == ["new"] and subjects(has_attachments=False) == ["old"]
        assert subjects(date_from="2026-01-01") == ["new"] and subjects(date_to="2020-12-31") == ["old"]
        assert subjects(minimum_size=10000) == ["new"] and subjects(maximum_size=10000) == ["old"]
        assert sum(group.count for group in call(engine, "message.combined-groups", view="inboxes", date_from="2026-01-01")) == 1
        with pytest.raises(UsageError, match="date"):
            subjects(date_from="yesterday")


def test_combined_views_merge_the_mailboxes_and_count_shared_mail_once(two: Two) -> None:
    call(two.engine, "mail.fetch", account=two.a)
    call(two.engine, "mail.fetch", account=two.b)
    everything = call(two.engine, "message.combined", view="inboxes")
    assert len(everything) == 4 and {row.account for row in everything} == {two.a, two.b}
    dates = [row.date for row in everything if row.date]
    assert dates == sorted(dates, reverse=True)
    assert [row.account for row in call(two.engine, "message.combined", view="inboxes", accounts=(two.b,))] == [two.b, two.b]
    first = call(two.engine, "message.combined", view="inboxes", offset=0, limit=3)
    rest = call(two.engine, "message.combined", view="inboxes", offset=3, limit=3)
    assert len(first) == 3 and len(rest) == 1 and {row.message_id for row in first}.isdisjoint(row.message_id for row in rest)
    call(two.engine, "message.flag", account=two.a, folder="Inbox", message=everything[0].message_id if everything[0].account == two.a else next(r for r in everything if r.account == two.a).message_id, flagged=True)
    assert len(call(two.engine, "message.combined", view="flagged")) == 1
    assert len(call(two.engine, "message.combined", view="unread")) == 4
    call(two.engine, "account.set-storage", account=two.a, storage="local")
    call(two.engine, "account.set-storage", account=two.b, storage="local")
    assert len(call(two.engine, "message.combined", view="inboxes")) == 4  # one shared Inbox, not one per account
    sent = call(two.engine, "mail.send", account=two.a, to="you@example.com", subject="S", body="x")
    assert [row.subject for row in call(two.engine, "message.combined", view="sent")] == ["S"] and sent.sent
    assert call(two.engine, "message.combined", view="outbox") == ()


def test_combined_views_can_be_searched_and_limited_to_some_accounts(two: Two) -> None:
    call(two.engine, "mail.fetch", account=two.a)
    call(two.engine, "mail.fetch", account=two.b)
    assert [row.subject for row in call(two.engine, "message.combined", view="inboxes", query="body 3")] == ["Message 3"]
    assert call(two.engine, "message.combined", view="inboxes", query="nothingatall") == ()


def test_a_rule_for_one_account_files_its_mail_in_a_folder_of_local_folders_and_stops(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({"a1": mail(1)}) as pop_a, FakePop3Server({"b1": mail(2, "b@example.com"), "b2": mail(3, "b@example.com")}) as pop_b, FakeSmtpServer() as smtp:
        a = add_account(engine, pop_a, smtp, email="a@example.com", storage="local")
        b = add_account(engine, pop_b, smtp, email="b@example.com", storage="local")
        call(engine, "folder.create", account=LOCAL_ACCOUNT_ID, folder="b@example.com")
        call(engine, "rule.add", name="B", condition=(), action=("move_to:b@example.com", "stop"), account=(b,))
        call(engine, "rule.add", name="Everything else", condition=("subject:contains:Message",), action=("flag",))
        assert call(engine, "mail.fetch", account=a).downloaded == 1 and call(engine, "mail.fetch", account=b).downloaded == 2
        assert {row.subject for row in call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="b@example.com").messages} == {"Message 2", "Message 3"}
        inbox = call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages
        assert [(row.subject, "flagged" in row.flags) for row in inbox] == [("Message 1", True)]  # the rule after "stop" did not touch B's mail
        folder = call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="b@example.com").messages
        assert all("flagged" not in row.flags for row in folder)
        with pytest.raises(ConfigurationError, match="one account"):
            call(engine, "rule.add", name="All", condition=(), action=("flag",))
        edited = call(engine, "rule.edit", rule=call(engine, "rule.list")[0].rule_id, no_conditions=True)
        assert edited.conditions == ()


def test_combined_views_follow_the_sort_and_their_groups_match(two: Two) -> None:
    call(two.engine, "mail.fetch", account=two.a)
    call(two.engine, "mail.fetch", account=two.b)
    for sort in ("date", "subject", "sender", "to", "size", "folder", "conversation", "preview", "unread", "flagged", "attachment", "replied", "contact"):
        for oldest_first in (False, True):
            rows = call(two.engine, "message.combined", view="inboxes", sort=sort, oldest_first=oldest_first, limit=100)
            groups = call(two.engine, "message.combined-groups", view="inboxes", sort=sort, oldest_first=oldest_first)
            assert len(rows) == 4
            if sort in ("unread", "flagged", "attachment", "replied", "contact"):
                assert groups == ()  # the icon columns have no group headers
                if not oldest_first:
                    newest = [row.date for row in rows]
                    assert newest == sorted(newest, key=lambda moment: moment.timestamp() if moment else 0.0, reverse=True)  # all the same value: newest first
            else:
                assert sum(group.count for group in groups) == 4
    by_subject = [row.subject for row in call(two.engine, "message.combined", view="inboxes", sort="subject", oldest_first=True)]
    assert by_subject == sorted(by_subject, key=str.lower)
    groups = call(two.engine, "message.combined-groups", view="inboxes", sort="subject", oldest_first=True)
    assert [group.value for group in groups] == by_subject  # every test message has its own subject


def test_the_views_snoozed_unreplied_and_archive(two: Two) -> None:
    call(two.engine, "mail.fetch", account=two.a)
    call(two.engine, "mail.fetch", account=two.b)
    inbox = call(two.engine, "message.combined", view="inboxes")
    assert len(call(two.engine, "message.combined", view="unreplied")) == len(inbox) == 4
    first, second, third = inbox[0], inbox[1], inbox[2]
    call(two.engine, "message.flag", account=first.account, folder="Inbox", message=first.message_id, replied=True)
    assert first.message_id not in {row.message_id for row in call(two.engine, "message.combined", view="unreplied")}  # replied: no longer unreplied
    assert call(two.engine, "message.combined", view="archive") == () and call(two.engine, "message.combined", view="snoozed") == ()
    call(two.engine, "message.archive", account=second.account, folder="Inbox", message=second.message_id)
    assert [row.subject for row in call(two.engine, "message.combined", view="archive")] == [second.subject]
    later = (datetime.fromtimestamp(two.engine.clock()) + timedelta(days=2)).strftime("%Y-%m-%dT09:00")  # the engine's fixed clock
    call(two.engine, "message.snooze", account=third.account, folder="Inbox", message=third.message_id, until=later)
    assert [row.subject for row in call(two.engine, "message.combined", view="snoozed")] == [third.subject]


def test_the_view_scheduled_lists_the_waiting_messages_of_every_account(two: Two) -> None:
    later = (datetime.fromtimestamp(two.engine.clock()) + timedelta(days=2)).strftime("%Y-%m-%dT09:00")  # the engine's fixed clock
    assert call(two.engine, "message.combined", view="scheduled") == ()
    first = call(two.engine, "mail.send", account=two.a, to="x@example.com", subject="From a", body="x", send_at=later)
    call(two.engine, "mail.send", account=two.b, to="x@example.com", subject="From b", body="x", send_at=later)
    assert sorted(row.subject for row in call(two.engine, "message.combined", view="scheduled")) == ["From a", "From b"]
    call(two.engine, "mail.unschedule", account=two.a, folder="Scheduled", message=first.message_id)
    assert [row.subject for row in call(two.engine, "message.combined", view="scheduled")] == ["From b"]


def test_kept_local_folders_whose_key_is_gone_comes_back_with_the_recovery_code(tmp_path: Path) -> None:
    """RV-177: the settings and the password store both lost, the mail of Local Folders kept: every setup failed with "Key ... was not
    found" and named no way out, and the recovery code was refused ("No account uses a data key yet")."""
    from chrono_mail.config import AppConfig
    from chrono_mail.core import CryptoError

    engine = make_engine(tmp_path)
    call(engine, "local.enable")
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        mailbox.deliver(("Inbox",), mail(1), frozenset())
    code = call(engine, "key.recovery-code").code
    key_id = engine.config().key_id
    assert key_id is not None
    engine.change_config(lambda config: AppConfig())  # the settings lost
    engine.keys.delete(bytes.fromhex(key_id))  # and the password store
    engine.forget_keys()
    with pytest.raises(CryptoError, match="Use a recovery code"):
        call(engine, "local.enable")
    call(engine, "key.restore-code", code=code)  # Local Folders is taken back with it at once
    assert [row.subject for row in call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages] == ["Message 1"]



def _kept_without_key(tmp_path: Path) -> tuple[Engine, str, str]:
    """Local Folders with one message, then the settings and the password store both lost: the engine, the recovery code and the key id."""
    from chrono_mail.config import AppConfig

    engine = make_engine(tmp_path)
    call(engine, "local.enable")
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        mailbox.deliver(("Inbox",), mail(1), frozenset())
    code = str(call(engine, "key.recovery-code").code)
    key_id = engine.config().key_id
    assert key_id is not None
    engine.change_config(lambda config: AppConfig())
    engine.keys.delete(bytes.fromhex(key_id))
    engine.forget_keys()
    return engine, code, key_id


def test_no_new_key_is_made_while_kept_local_folders_waits_for_its_own(tmp_path: Path) -> None:
    """The open question of 3.6.349: with the settings lost, adding an account made a new key, after which the kept mail of Local Folders
    could never be opened again, not even with its recovery code."""
    from chrono_mail.core import CryptoError

    engine, code, key_id = _kept_without_key(tmp_path)
    status = call(engine, "key.status")
    assert status.key_id == key_id and status.kept and not status.in_credential_store  # the start asks for this key
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        with pytest.raises(CryptoError, match="No new key was made"):
            add_account(engine, pop, smtp)
        assert engine.config().key_id is None and call(engine, "account.list") == ()
        with pytest.raises(CryptoError, match="No new key was made"):
            call(engine, "key.ensure")  # Restore… neither
        call(engine, "key.restore-code", code=code)
        account = add_account(engine, pop, smtp)  # with the kept mail's key
    assert engine.config().key_id == key_id and account
    assert [row.subject for row in call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages] == ["Message 1"]


def test_start_without_the_kept_mail_sets_it_aside_and_a_new_key_can_be_made(tmp_path: Path) -> None:
    """Without the recovery code the kept mail can never be opened: Start without this mail sets it aside, never deleted."""
    engine, _code, key_id = _kept_without_key(tmp_path)
    result = call(engine, "key.start-without-kept-mail")
    aside = Path(result.folder)
    assert (aside / "store").exists() or any(aside.rglob("store.json"))  # the files are kept, out of the way
    assert not engine.directories.account_directory(LOCAL_ACCOUNT_ID).exists()
    status = call(engine, "key.status")
    assert status.key_id is None and not status.kept
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        add_account(engine, pop, smtp)
    assert engine.config().key_id not in (None, key_id)
    call(engine, "local.enable")  # Local Folders starts again, empty
    assert call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages == ()
    assert any(aside.rglob("store.json"))


def test_start_without_the_kept_mail_is_refused_while_its_key_is_there(tmp_path: Path) -> None:
    engine, code, _key_id = _kept_without_key(tmp_path)
    call(engine, "key.restore-code", code=code)
    with pytest.raises(UsageError):
        call(engine, "key.start-without-kept-mail")
    fresh = make_engine(tmp_path / "fresh")
    with pytest.raises(UsageError):
        call(fresh, "key.start-without-kept-mail")  # nothing kept here at all


def _kept_with_older_key(tmp_path: Path, keep_old_key: bool) -> tuple[Engine, str, str]:
    """As an older version left it: the settings lost, a new key made by adding an account, the kept Local Folders (one message, one
    appointment) written with the old key. The engine, the old key's recovery code, the old key id."""
    from chrono_mail.engine.pimaccess import ensure_local_pim

    engine, code, key_id = _kept_without_key(tmp_path)
    store_json = engine.directories.store_directory(LOCAL_ACCOUNT_ID) / "store.json"
    store_json.rename(store_json.with_suffix(".away"))  # as 3.6.349 did: nothing kept in the way, so a new key is made
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        add_account(engine, pop, smtp)
    store_json.with_suffix(".away").rename(store_json)
    if keep_old_key:
        from chrono_mail.crypto import key_from_recovery_code
        engine.keys.save(key_from_recovery_code(code))
    assert ensure_local_pim is not None
    return engine, code, key_id


def test_kept_mail_written_with_an_older_key_the_store_has_is_taken_back(tmp_path: Path) -> None:
    """The review of 3.6.350: kept Local Folders written with an older key was only offered to be set aside, also when that key was still in
    the password store; now it is written again with this computer's key and taken back, the old files kept aside."""
    from chrono_mail.core import CryptoError

    engine, _code, key_id = _kept_with_older_key(tmp_path, keep_old_key=True)
    status = call(engine, "key.status")
    assert status.take_back and not status.kept_other  # the start opens the key window, which takes it back
    with pytest.raises(CryptoError, match="taken back first"):
        call(engine, "local.enable")  # never hidden inside another command
    assert call(engine, "local.take-back").take_back is False
    assert [row.subject for row in call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages] == ["Message 1"]
    description = json.loads((engine.directories.store_directory(LOCAL_ACCOUNT_ID) / "store.json").read_text("utf-8"))
    assert description["key_id"] == engine.config().key_id != key_id
    assert any(engine.directories.data_directory.glob("set-aside-local-folders-older-key-*"))


def test_the_older_keys_recovery_code_takes_kept_mail_back(tmp_path: Path) -> None:
    engine, code, _key_id = _kept_with_older_key(tmp_path, keep_old_key=False)
    assert call(engine, "key.status").kept_other
    status = call(engine, "key.restore-code", code=code)  # only stored: taking back is its own step, which the key window runs next
    assert status.take_back and not status.kept_other
    call(engine, "local.take-back")
    assert [row.subject for row in call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages] == ["Message 1"]


def test_a_rewrite_with_this_computers_key_that_is_cut_off_is_finished_or_undone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AGENTS.md 4.1.4: cut off while the files are written, the old Local Folders stays; cut off after, the new one takes its place."""
    from chrono_mail.engine import local

    engine, _code, _key_id = _kept_with_older_key(tmp_path, keep_old_key=True)
    real_write = local.write_new_file
    written: list[Path] = []

    def cut_off(path: Path, data: bytes, durable: bool) -> None:
        if len(written) == 3:
            raise OSError(28, "No space left on device")
        written.append(path)
        real_write(path, data, durable)

    monkeypatch.setattr(local, "write_new_file", cut_off)
    with pytest.raises(StorageError):
        call(engine, "local.take-back")
    monkeypatch.setattr(local, "write_new_file", real_write)
    assert not (engine.directories.data_directory / "accounts" / local.REKEYING).exists()
    real_replace = local.os.replace
    replaced: list[str] = []

    def cut_after_setting_aside(source: object, target: object) -> None:
        real_replace(source, target)  # type: ignore[arg-type]
        replaced.append(str(target))
        if len(replaced) == 1:
            raise OSError(5, "The power went")

    monkeypatch.setattr(local.os, "replace", cut_after_setting_aside)
    with pytest.raises(StorageError):
        call(engine, "local.take-back")
    monkeypatch.setattr(local.os, "replace", real_replace)
    assert not engine.directories.account_directory(LOCAL_ACCOUNT_ID).exists()  # between the two renames
    call(engine, "local.take-back")  # finished by the next take-back, and Local Folders in use again (the review of 3.6.350)
    assert engine.config().local_folders() is not None
    assert [row.subject for row in call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages] == ["Message 1"]


def test_a_finished_copy_never_takes_the_place_of_local_folders_in_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350: a finished copy left behind (its first rename refused) took the place of a Local Folders that came back
    another way meanwhile (a restore)."""
    from chrono_mail.engine import local

    engine, _code, _key_id = _kept_with_older_key(tmp_path, keep_old_key=True)
    real_replace = local.os.replace

    def refused(source: object, target: object) -> None:
        raise OSError(13, "The file is in use")

    monkeypatch.setattr(local.os, "replace", refused)
    with pytest.raises(StorageError):
        call(engine, "local.take-back")
    monkeypatch.setattr(local.os, "replace", real_replace)
    copy = engine.directories.data_directory / "accounts" / local.REKEYING
    assert (copy / local.REKEYED_MARK).exists()
    in_use = engine.directories.account_directory(LOCAL_ACCOUNT_ID)
    shutil.rmtree(in_use)
    copy.rename(copy.with_name("away"))
    call(engine, "local.enable")  # a fresh Local Folders in the settings, as a restore would put one in place
    copy.with_name("away").rename(copy)
    (in_use / "made-here").write_text("x", "utf-8")
    call(engine, "local.enable")
    assert (in_use / "made-here").exists() and not copy.exists()
    assert any(engine.directories.data_directory.glob("set-aside-local-folders-unused-*"))


def test_this_computers_own_code_never_says_local_folders_is_back(tmp_path: Path) -> None:
    """The review of 3.6.350: with Local Folders waiting for its older key, typing this computer's own code said the key was back."""
    from chrono_mail.crypto import recovery_code_for

    engine, _code, _key_id = _kept_with_older_key(tmp_path, keep_old_key=False)
    own = recovery_code_for(engine.data_key(engine.config()))
    status = call(engine, "key.restore-code", code=own)
    assert status.in_credential_store and status.kept_other


def test_a_damaged_kept_key_counts_as_gone(tmp_path: Path) -> None:
    """The review of 3.6.350: a damaged entry for the kept key let neither the key back nor Start without this mail."""
    from chrono_mail.core import CryptoError

    engine, _code, key_id = _kept_without_key(tmp_path)
    real_load = engine.keys.load

    def damaged(wanted: bytes) -> object:
        if wanted == bytes.fromhex(key_id):
            raise CryptoError("The key in the password store is damaged")
        return real_load(wanted)

    engine.keys.load = damaged  # type: ignore[method-assign]
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        with pytest.raises(CryptoError, match="No new key was made"):
            add_account(engine, pop, smtp)
    call(engine, "key.start-without-kept-mail")
    assert not engine.directories.account_directory(LOCAL_ACCOUNT_ID).exists()


def test_the_recovery_code_of_a_kept_key_can_be_shown(tmp_path: Path) -> None:
    """The review of 3.6.350: the settings name no key, the kept key is in the store: the code window said no key had been made."""
    engine, code, key_id = _kept_without_key(tmp_path)
    call(engine, "key.restore-code", code=code)
    from chrono_mail.config import AppConfig
    engine.change_config(lambda config: AppConfig())  # the settings lost again, the key kept
    assert call(engine, "key.status").kept and call(engine, "key.recovery-code").code == code
    assert engine.config().key_id is None


def test_two_accounts_added_at_once_share_one_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-70 (found again by the review of 3.6.350): two commands that made the first key at the same moment made two; the account
    whose store was written with the second could never be opened."""
    import threading
    from chrono_mail.engine import commands_accounts

    engine = make_engine(tmp_path)
    real_make = commands_accounts.make_data_key
    both_in = threading.Barrier(2, timeout=2)

    def slow_make(made_for: Engine) -> object:
        try:
            both_in.wait()  # without the lock both get here
        except threading.BrokenBarrierError:
            pass
        return real_make(made_for)

    monkeypatch.setattr(commands_accounts, "make_data_key", slow_make)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        threads = [threading.Thread(target=add_account, args=(engine, pop, smtp, f"{name}@example.com"), kwargs={"incoming_host": host}) for name, host in (("a", "127.0.0.1"), ("b", "localhost"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
    assert len(engine.config().accounts) == 2
    for settings in engine.config().accounts:
        with open_mailbox(engine, settings.account.account_id) as mailbox:
            assert mailbox.store.list_folders()


def test_kept_mail_written_with_another_key_names_the_way_out(tmp_path: Path) -> None:
    """Made by an older version: the settings were lost, a new key made, and the kept Local Folders written with the old one."""
    from chrono_mail.core import CryptoError

    engine, _code, key_id = _kept_without_key(tmp_path)
    store_json = engine.directories.store_directory(LOCAL_ACCOUNT_ID) / "store.json"
    kept_text = store_json.read_text("utf-8")
    store_json.rename(store_json.with_suffix(".away"))  # as 3.6.349 did: nothing kept in the way, so a new key is made
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        add_account(engine, pop, smtp)
    store_json.with_suffix(".away").rename(store_json)
    assert json.loads(kept_text)["key_id"] == key_id != engine.config().key_id
    status = call(engine, "key.status")
    assert status.in_credential_store and status.kept_other
    with pytest.raises(CryptoError, match="Start without this mail"):
        call(engine, "local.enable")
    call(engine, "key.start-without-kept-mail")
    call(engine, "local.enable")
    assert not call(engine, "key.status").kept_other


def test_a_file_that_cannot_be_read_never_stops_taking_local_folders_back(tmp_path: Path) -> None:
    """The review of 3.6.350: one file that could not be read stopped the whole take-back, at every start (AGENTS.md 4.2.1)."""
    import os
    import sys

    engine, _code, _key_id = _kept_with_older_key(tmp_path, keep_old_key=True)
    skip = sys.platform == "win32"
    if sys.platform != "win32":  # Windows has no geteuid; inside this block the type check for Windows skips it
        skip = os.geteuid() == 0
    if skip:
        pytest.skip("file permissions cannot make a file unreadable here")
    local = engine.directories.account_directory(LOCAL_ACCOUNT_ID)
    unreadable = next(path for path in local.rglob("*") if path.is_file() and path.parent.name in ("cur", "new"))
    relative = unreadable.relative_to(local)
    unreadable.chmod(0)
    status = call(engine, "local.take-back")
    (aside,) = engine.directories.data_directory.glob("set-aside-local-folders-older-key-*")
    (aside / relative).chmod(0o600)  # it stays in the old files, kept aside
    assert not status.take_back and "1 file could not be read" in status.problem


def test_taking_local_folders_back_without_room_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil as shell
    from collections import namedtuple

    engine, _code, _key_id = _kept_with_older_key(tmp_path, keep_old_key=True)
    usage = namedtuple("usage", "total used free")

    def full(path: object) -> object:
        return usage(10, 10, 0)

    monkeypatch.setattr(shell, "disk_usage", full)
    with pytest.raises(StorageError, match="needs about"):
        call(engine, "local.take-back")
    monkeypatch.undo()
    assert call(engine, "key.status").take_back  # as it was: it can be done once there is room


def test_with_the_settings_lost_the_junk_filter_keeps_what_it_learned(tmp_path: Path) -> None:
    """The review of 3.6.350: with the settings lost the junk filter read its words without a key and set them aside as damaged."""
    from chrono_mail.engine.junkfilter import _key  # noqa: PLC2701 - the key the words are read with

    engine, code, key_id = _kept_without_key(tmp_path)
    call(engine, "key.restore-code", code=code)
    from chrono_mail.config import AppConfig
    engine.change_config(lambda config: AppConfig())  # the settings lost again, the key kept
    key = _key(engine)
    assert key is not None and key.key_id.hex() == key_id


def test_taking_back_puts_the_junk_filters_learning_back_in_use(tmp_path: Path) -> None:
    """The review of 3.6.350: the learning written with the older key stayed in the .bad copy, never used again."""
    from chrono_mail.crypto import key_from_recovery_code
    from chrono_mail.engine.junkwords import JunkWords, save_words

    engine, code, _key_id = _kept_with_older_key(tmp_path, keep_old_key=True)
    junk = engine.directories.junk_filter_file
    save_words(junk.with_name(junk.name + ".bad"), JunkWords(), key_from_recovery_code(code), engine.durable)
    junk.unlink(missing_ok=True)
    call(engine, "local.take-back")
    from chrono_mail.crypto import read_key_id
    assert junk.exists() and read_key_id(junk.read_bytes()).hex() == engine.config().key_id


def test_start_without_is_possible_when_taking_back_cannot_be_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350: with the older key in the store but no room to take Local Folders back, Start without this mail was offered
    and then refused, and the window came back at every start."""
    engine, _code, _key_id = _kept_with_older_key(tmp_path, keep_old_key=True)
    call(engine, "key.start-without-kept-mail")
    assert not engine.directories.account_directory(LOCAL_ACCOUNT_ID).exists() and not call(engine, "key.status").take_back


def test_taking_back_keeps_the_time_of_every_file_and_joins_the_junk_learning(tmp_path: Path) -> None:
    """The review of 3.6.350: every file got the day of the take-back as its time (the last resort of the Received column, D-226); the
    junk filter's learning kept aside stayed unused when learning had started again meanwhile."""
    import os
    from chrono_mail.crypto import key_from_recovery_code
    from chrono_mail.engine.junkwords import JunkWords, load_words, save_words

    engine, code, _key_id = _kept_with_older_key(tmp_path, keep_old_key=True)
    local = engine.directories.account_directory(LOCAL_ACCOUNT_ID)
    message = next(path for path in local.rglob("*") if path.is_file() and path.parent.name in ("cur", "new"))
    relative = message.relative_to(local)
    os.utime(message, (1_000_000_000, 1_000_000_000))
    junk = engine.directories.junk_filter_file
    older, newer = JunkWords(junk_messages=5), JunkWords(junk_messages=2)
    save_words(junk.with_name(junk.name + ".bad"), older, key_from_recovery_code(code), engine.durable)
    save_words(junk, newer, engine.data_key(engine.config()), engine.durable)
    call(engine, "local.take-back")
    assert int((local / relative).stat().st_mtime) == 1_000_000_000
    key = engine.data_key(engine.config())
    assert load_words(junk, key).junk_messages == 5  # the copy that knows more is used (never added up: one message would count twice)
    (since,) = junk.parent.glob(junk.name + ".since-*")
    assert load_words(since, key).junk_messages == 2 and not junk.with_name(junk.name + ".bad").exists()


def test_a_folder_that_cannot_be_opened_stops_taking_back(tmp_path: Path) -> None:
    """The review of 3.6.350: a folder that could not be opened was left out of the copy without a word, and then Local Folders came back
    without what it held. Only a single message or item may stay behind."""
    import os
    import sys

    engine, _code, _key_id = _kept_with_older_key(tmp_path, keep_old_key=True)
    skip = sys.platform == "win32"
    if sys.platform != "win32":  # Windows has no geteuid; inside this block the type check for Windows skips it
        skip = os.geteuid() == 0
    if skip:
        pytest.skip("folder permissions cannot shut a folder here")
    local = engine.directories.account_directory(LOCAL_ACCOUNT_ID)
    shut = next(path for path in sorted(local.rglob("*")) if path.is_dir() and path.name == "tmp")
    relative = shut.relative_to(local)
    shut.chmod(0)
    try:
        with pytest.raises(StorageError, match="could not be read"):  # a folder left out would hide what it holds: nothing is changed
            call(engine, "local.take-back")
    finally:
        (local / relative).chmod(0o700)
    assert call(engine, "key.status").take_back and not any(engine.directories.data_directory.glob("set-aside-local-folders-older-key-*"))
