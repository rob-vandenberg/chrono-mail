from collections.abc import Generator
from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.core import MessageFlag, ProtocolError, StorageError
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.protocols.imap import ImapClient, keyword_to_tag, tag_to_keyword
from chrono_mail.core import ConnectionSecurity, ServerSettings
from chrono_mail.store.properties import MessageProperties
from tests.enginefactory import make_engine
from tests.fakeimap import FakeImapServer
from tests.fakesmtp import FakeSmtpServer
from tests.test_engine_commands import call
from tests.test_engine_imap import World, mail

# --- Version ------------------------------------------------------------
__version__ = "test_imap_tags.py 4.4.517"

# --- Version history ----------------------------------------------------
# v4.4.517: The answer marks of an invitation travel as keywords, a folder without keywords keeps them here (D-310)
# v3.2.342: tags that cannot be read here stay on the server (RV-48)
# v0.5.138: Initial tests: tags as IMAP keywords (server first, both ways, a server without keywords)


def make_world(tmp_path: Path, keywords: bool = True) -> Generator[World]:
    engine = make_engine(tmp_path)
    with FakeImapServer(plain=False, keywords=keywords) as imap, FakeSmtpServer() as smtp:
        result = call(
            engine, "account.add", kind="imap", display_name="Me", email_address="me@example.com", username="user", password="secret",
            incoming_host="127.0.0.1", incoming_port=imap.port, incoming_security="none",
            outgoing_host="127.0.0.1", outgoing_port=smtp.port, outgoing_security="none",
        )
        yield World(engine, imap, smtp, str(result.account_id))


@pytest.fixture
def world(tmp_path: Path) -> Generator[World]:
    yield from make_world(tmp_path)


@pytest.fixture
def plain_world(tmp_path: Path) -> Generator[World]:
    yield from make_world(tmp_path, keywords=False)


def server_flags(world: World, number: int = 0) -> set[str]:
    return set(world.imap.folders["INBOX"].messages[number].flags)


def tags_here(world: World) -> dict[str, tuple[str, ...]]:
    return {row.subject: row.tags for row in world.rows("Inbox")}


def tag(world: World, subject: str, **values: object) -> Any:  # noqa: ANN401
    message = next(row.message_id for row in world.rows("Inbox") if row.subject == subject)
    return call(world.engine, "message.tag", account=world.account, folder="Inbox", messages=(message,), **values)


# --- keywords --------------------------------------------------------------------------------------


def test_a_tag_and_its_keyword_turn_into_each_other() -> None:
    for name in ("Work", "Family matters", "Café €", "$money", "a&b", "x_y.z-1"):
        keyword = tag_to_keyword(name)
        assert not keyword.startswith("$") and " " not in keyword and keyword_to_tag(keyword) == name
    for other in ("\\Seen", "$Forwarded", "Junk", "NonJunk", "$label1", "bad*keyword"):
        assert keyword_to_tag(other) is None
    with pytest.raises(ProtocolError):
        tag_to_keyword("€" * 100)


@pytest.mark.parametrize("keywords", [True, False])
def test_select_tells_whether_the_server_keeps_keywords(keywords: bool) -> None:
    with FakeImapServer(plain=False, keywords=keywords) as imap:
        with ImapClient(ServerSettings("127.0.0.1", imap.port, ConnectionSecurity.NONE, "user"), "secret") as client:
            info = client.select("INBOX")
    assert info.keeps_keywords is keywords and info.permanent_flags is not None and "\\Seen" in info.permanent_flags


# --- the two ways --------------------------------------------------------------------------------


def test_a_tag_goes_to_the_server_first(world: World) -> None:
    world.imap.add("INBOX", mail(1))
    world.fetch()
    tag(world, "Message 1", add=("Work", "Family matters"))
    assert server_flags(world) == {"Work", "Family&ACA-matters"}
    tag(world, "Message 1", remove=("work",))
    assert server_flags(world) == {"Family&ACA-matters"}
    assert world.fetch().failed == () and tags_here(world) == {"Message 1": ("Family matters",)}


def test_tags_given_by_another_program_come_in_and_go_again(world: World) -> None:
    world.imap.add("INBOX", mail(1), {"\\Seen", "Home", "$Forwarded", "NonJunk"})
    world.fetch()
    assert tags_here(world) == {"Message 1": ("Home",)}  # a new message comes with its tags; the others' marks are no tags
    world.imap.folders["INBOX"].messages[0].flags |= {"Work"}
    world.fetch()
    assert tags_here(world) == {"Message 1": ("Home", "Work")}
    world.imap.folders["INBOX"].messages[0].flags -= {"Home", "Work"}
    world.fetch()
    assert tags_here(world) == {"Message 1": ()}
    assert server_flags(world) == {"\\Seen", "$Forwarded", "NonJunk"}  # never touched


def test_a_tag_given_here_while_offline_goes_to_the_server_at_the_next_check(world: World) -> None:
    world.imap.add("INBOX", mail(1))
    world.fetch()
    with open_mailbox(world.engine, world.account) as mailbox:
        store_id = world.rows("Inbox")[0].message_id
        row = mailbox.index.get(store_id)
        assert row is not None
        mailbox._set_tags_here(mailbox.reference(row.folder, store_id), ("Later",))  # what an offline change does: here only
    world.fetch()
    assert server_flags(world) == {"Later"}
    world.fetch()
    assert tags_here(world) == {"Message 1": ("Later",)}  # and not taken back


def test_a_flag_change_keeps_the_tags_on_the_server(world: World) -> None:
    world.imap.add("INBOX", mail(1))
    world.fetch()
    tag(world, "Message 1", add=("Work",))
    message = world.rows("Inbox")[0].message_id
    call(world.engine, "message.flag", account=world.account, folder="Inbox", message=message, seen=True)
    world.fetch()
    assert server_flags(world) == {"\\Seen", "Work"} and tags_here(world) == {"Message 1": ("Work",)}


def test_tags_that_cannot_be_read_here_stay_on_the_server(world: World) -> None:
    """RV-48: a properties file that could not be read counted as "no tags": the check removed every tag on the server, and every other
    computer followed."""
    world.imap.add("INBOX", mail(1))
    world.fetch()
    tag(world, "Message 1", add=("Work",))
    message = world.rows("Inbox")[0].message_id
    with open_mailbox(world.engine, world.account) as mailbox:
        (mailbox.store.properties.directory / f"{message}.json").write_text("{ damaged")
    assert world.fetch().failed == ()
    assert server_flags(world) == {"Work"}  # left alone: not known here is not the same as none


def test_renaming_a_tag_renames_its_keyword_on_the_server(world: World) -> None:
    world.imap.add("INBOX", mail(1))
    world.fetch()
    tag(world, "Message 1", add=("Work",))
    call(world.engine, "tag.rename", name="work", new_name="Job")
    assert server_flags(world) == {"Job"}
    call(world.engine, "tag.delete", name="job")
    assert server_flags(world) == set()


def test_a_server_that_keeps_no_keywords_keeps_the_tags_here(plain_world: World) -> None:
    world = plain_world
    world.imap.add("INBOX", mail(1))
    world.fetch()
    tag(world, "Message 1", add=("Work",))
    assert server_flags(world) == set()
    result = world.fetch()
    assert tags_here(world) == {"Message 1": ("Work",)} and server_flags(world) == set()
    assert any("keeps no tags" in text for text in result.failed)


# --- the answer marks of an invitation ------------------------------------------------------------


def declined(world: World) -> None:
    with open_mailbox(world.engine, world.account) as mailbox:
        reference = mailbox.reference(mailbox.folder("Inbox"), world.rows("Inbox")[0].message_id)
        mailbox.set_flags(reference, mailbox.current_flags(reference) | {MessageFlag.INVITE_DECLINED})


def flags_here(world: World) -> tuple[str, ...]:
    return world.rows("Inbox")[0].flags


def test_an_answer_mark_goes_to_the_server_and_comes_from_it(world: World) -> None:
    world.imap.add("INBOX", mail(1))
    world.fetch()
    declined(world)
    assert "$InviteDeclined" in server_flags(world)
    world.imap.folders["INBOX"].messages[0].flags.discard("$InviteDeclined")  # Change my answer on another computer
    world.fetch()
    assert "invite_declined" not in flags_here(world)
    world.imap.folders["INBOX"].messages[0].flags.add("$InviteDeclined")  # a Decline on another computer
    world.fetch()
    assert "invite_declined" in flags_here(world)
    assert not tags_here(world)["Message 1"]  # it is no tag


def test_a_mark_made_offline_goes_to_the_server_with_the_next_check(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.imap.add("INBOX", mail(1))
    world.fetch()
    world.engine.close_imap_links()
    with monkeypatch.context() as offline:
        def unreachable(_: ImapClient) -> None:
            raise ProtocolError("no connection")

        offline.setattr(ImapClient, "connect", unreachable)
        declined(world)
    world.fetch()
    assert "$InviteDeclined" in server_flags(world) and "invite_declined" in flags_here(world)


def test_a_folder_that_keeps_no_keywords_keeps_the_answer_mark_here_without_a_conflict(plain_world: World) -> None:
    world = plain_world
    world.imap.add("INBOX", mail(1))
    world.fetch()
    world.imap.refuse_stores = True  # nothing may be sent for it: a real server could say no
    declined(world)
    world.imap.refuse_stores = False
    world.imap.folders["INBOX"].messages[0].flags.add("\\Flagged")  # the server changes something: its marks are taken over
    world.fetch()
    world.fetch()
    assert "$InviteDeclined" not in server_flags(world)
    assert {"invite_declined", "flagged"} <= set(flags_here(world))
    with open_mailbox(world.engine, world.account) as mailbox:
        assert mailbox.sync_conflicts() == [] and mailbox.waiting_for_server() == ([], 0)


def test_marking_many_at_once_records_no_answer_mark_in_a_folder_that_keeps_no_keywords(plain_world: World) -> None:
    world = plain_world
    world.imap.add("INBOX", mail(1))
    world.fetch()
    declined(world)
    with open_mailbox(world.engine, world.account) as mailbox:
        reference = mailbox.reference(mailbox.folder("Inbox"), world.rows("Inbox")[0].message_id)
        flags = mailbox.current_flags(reference) | {MessageFlag.SEEN}
        with mailbox.together():
            mailbox.prepare_flags({reference: flags})
            mailbox.set_flags(reference, flags)
        world.imap.refuse_stores = True  # taking the mark off sends nothing either
        mailbox.set_flags(reference, flags - {MessageFlag.INVITE_DECLINED})
    assert "invite_declined" not in flags_here(world)


def test_tags_that_could_not_be_kept_at_download_stay_on_the_server_and_come_later(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.imap.add("INBOX", mail(1), {"Home"})
    with monkeypatch.context() as broken:
        def refused(*_: object) -> tuple[str, ...]:
            raise StorageError("no room")

        broken.setattr(MessageProperties, "set_tags", refused)
        world.fetch()
    world.fetch()
    assert "Home" in server_flags(world) and tags_here(world) == {"Message 1": ("Home",)}

