from collections.abc import Generator
from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.config import load_tags
from chrono_mail.engine import Engine, UsageError
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message
from tests.test_engine_commands import add_account, call

# --- Version ------------------------------------------------------------
__version__ = "test_engine_tags.py 0.5.135"

# --- Version history ----------------------------------------------------
# v0.5.135: tag.edit (shortcut, use for, place), tag.list in the person's order, the combined view of one tag
# v0.5.134: Initial tests: tags for mail (message.tag, tag.create, tag.delete, tag.list, tag.rename, finding by tag)


class Setup:
    def __init__(self, engine: Engine, account: str) -> None:
        self.engine, self.account = engine, account

    def ids(self, folder: str = "Inbox") -> list[str]:
        return [row.message_id for row in call(self.engine, "message.list", account=self.account, folder=folder, oldest_first=True).messages]

    def tags(self, folder: str = "Inbox") -> dict[str, tuple[str, ...]]:
        return {row.subject: row.tags for row in call(self.engine, "message.list", account=self.account, folder=folder).messages}

    def tag(self, messages: list[str], folder: str = "Inbox", **values: object) -> Any:  # noqa: ANN401
        return call(self.engine, "message.tag", account=self.account, folder=folder, messages=tuple(messages), **values)


@pytest.fixture
def setup(tmp_path: Path) -> Generator[Setup]:
    engine = make_engine(tmp_path)
    mail = {f"u{n}": raw_message(subject=f"Message {n}", body=f"body {n}\r\n", message_id=f"<t{n}@x>") for n in (1, 2, 3)}
    with FakePop3Server(mail) as pop, FakeSmtpServer() as smtp:
        account = add_account(engine, pop, smtp)
        call(engine, "mail.fetch", account=account)
        yield Setup(engine, account)


def test_tags_are_added_taken_away_and_replaced(setup: Setup) -> None:
    first, second, _ = setup.ids()
    result = setup.tag([first, second], add=("Work", "Family"))
    assert [item.tags for item in result] == [("Work", "Family"), ("Work", "Family")]
    setup.tag([first], remove=("family",))
    setup.tag([second], add=("Home",), replace=True)
    assert setup.tags() == {"Message 1": ("Work",), "Message 2": ("Home",), "Message 3": ()}


def test_a_new_tag_gets_a_colour_and_is_listed_with_its_mail(setup: Setup) -> None:
    first, second, _ = setup.ids()
    setup.tag([first, second], add=("Work",))
    assert load_tags(setup.engine.directories.tags_file).colour_of("work") == "#808080"
    listed = {item.name: item for item in call(setup.engine, "tag.list")}
    assert listed["Work"].mails == 2


def test_mail_is_found_by_tag_in_a_list_and_a_search(setup: Setup) -> None:
    first, _, third = setup.ids()
    setup.tag([first, third], add=("Work",))
    listed = call(setup.engine, "message.list", account=setup.account, folder="Inbox", tag="work")
    assert sorted(row.subject for row in listed.messages) == ["Message 1", "Message 3"]
    found = call(setup.engine, "message.search", account=setup.account, query="tag:work message")
    assert sorted(row.subject for row in found) == ["Message 1", "Message 3"]
    assert [row.subject for row in call(setup.engine, "message.search", account=setup.account, query="-tag:work message")] == ["Message 2"]


def test_tags_go_along_with_a_move_and_a_copy_and_go_with_a_deletion_for_good(setup: Setup) -> None:
    first, second, _ = setup.ids()
    setup.tag([first, second], add=("Work",))
    call(setup.engine, "folder.create", account=setup.account, folder="Kept")
    call(setup.engine, "message.move", account=setup.account, folder="Inbox", message=first, target="Kept")
    call(setup.engine, "message.copy", account=setup.account, folder="Inbox", message=second, target="Kept")
    assert setup.tags("Kept") == {"Message 1": ("Work",), "Message 2": ("Work",)}
    call(setup.engine, "message.delete", account=setup.account, folder="Inbox", message=second, permanent=True)
    assert [item.mails for item in call(setup.engine, "tag.list") if item.name == "Work"] == [2]  # the two in Kept


def test_rename_and_delete_a_tag_in_all_mail(setup: Setup) -> None:
    first, second, _ = setup.ids()
    setup.tag([first, second], add=("Work",))
    call(setup.engine, "tag.colour", name="Work", colour="#ff0000")
    assert call(setup.engine, "tag.rename", name="work", new_name="Job").items == 2
    assert setup.tags()["Message 1"] == ("Job",)
    assert load_tags(setup.engine.directories.tags_file).colour_of("job") == "#ff0000"
    assert call(setup.engine, "tag.delete", name="JOB").messages == 2
    assert setup.tags() == {"Message 1": (), "Message 2": (), "Message 3": ()}
    assert "job" not in {item.name.lower() for item in call(setup.engine, "tag.list")}


def test_a_tag_can_be_made_before_any_mail_carries_it(setup: Setup) -> None:
    created = call(setup.engine, "tag.create", name="  Later  ", colour="#00FF00")
    assert (created.name, created.colour) == ("Later", "#00ff00")
    assert [(item.name, item.colour, item.mails) for item in call(setup.engine, "tag.list")] == [("Later", "#00ff00", 0)]


def test_wrong_input_is_refused(setup: Setup) -> None:
    first = setup.ids()[0]
    with pytest.raises(UsageError):
        setup.tag([first])  # nothing to do
    with pytest.raises(UsageError):
        setup.tag(["nonsense"], add=("Work",))
    with pytest.raises(UsageError):
        setup.tag([first], add=("x" * 101,))
    with pytest.raises(UsageError):
        call(setup.engine, "tag.create", name="Bad", colour="red")


def test_a_tag_gets_a_shortcut_kinds_and_a_place_and_is_listed_in_that_order(setup: Setup) -> None:
    for name in ("Home", "Work", "School"):
        call(setup.engine, "tag.create", name=name)
    edited = call(setup.engine, "tag.edit", name="School", shortcut="Ctrl+Alt+2", use_for=("mail", "tasks"), position=0)
    assert (edited.name, edited.shortcut, edited.use_for) == ("School", "Ctrl+Alt+2", ("mail", "tasks"))
    call(setup.engine, "tag.edit", name="Home", move="down")
    listed = [(item.name, item.shortcut, item.kinds) for item in call(setup.engine, "tag.list")]
    assert listed == [("School", "Ctrl+Alt+2", ("mail", "tasks")), ("Work", "", ("mail", "events", "contacts", "tasks")), ("Home", "", ("mail", "events", "contacts", "tasks"))]
    with pytest.raises(UsageError):
        call(setup.engine, "tag.edit", name="Work", shortcut="F5")


def test_the_combined_view_of_one_tag_shows_its_mail_from_every_folder(setup: Setup) -> None:
    first, second, _ = setup.ids()
    call(setup.engine, "folder.create", account=setup.account, folder="Kept")
    call(setup.engine, "message.move", account=setup.account, folder="Inbox", message=second, target="Kept")
    setup.tag([first], add=("Work",))
    setup.tag([second], folder="Kept", add=("work",))
    rows = call(setup.engine, "message.combined", view="tagged", tag="WORK")
    assert sorted((row.subject, row.folder) for row in rows) == [("Message 1", "Inbox"), ("Message 2", "Kept")]
    with pytest.raises(UsageError):
        call(setup.engine, "message.combined", view="tagged")

