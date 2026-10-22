import json
from pathlib import Path

import pytest
import sys

from chrono_mail.config import load_sidebar, parse_sidebar, save_sidebar, sidebar_to_text, Sidebar
from chrono_mail.core import ConfigurationError
from chrono_mail.engine import UsageError
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.test_engine_commands import add_account, call

# --- Version ------------------------------------------------------------
__version__ = "test_engine_sidebar.py 2.0.287"

# --- Version history ----------------------------------------------------
# v2.0.287: the permission check not on Windows (the Windows computer's report)
# v1.1.252: a renamed folder takes its pins along, with the folders inside it; their names and order stay; other accounts untouched (R-41)
# v1.1.204: sidebar.show-local-folder hides and shows a standard folder of Local Folders; the file refuses another name
# v1.1.191: sidebar.show-tree hides and shows an account's folder tree
# v1.1.190: A group always holds an account: group.add needs one, an emptied group goes, an empty one in the file is dropped
# v0.0.47: Initial tests of pinned favorites and account groups


def test_pinning_renaming_ordering_and_unpinning_folders(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        account = add_account(engine, pop, smtp, email="a@example.com")
        call(engine, "folder.create", account=account, folder="Projects/Alpha")
        call(engine, "folder.create", account=account, folder="Projects/Beta")
        call(engine, "favorite.pin", account=account, folder="Projects/Alpha")
        call(engine, "favorite.pin", account=account, folder="Projects/Alpha")  # again: still one
        shown = call(engine, "favorite.pin", account=account, folder="Projects/Beta")
        assert [(p.folder, p.name) for p in shown.pins] == [("Projects/Alpha", ""), ("Projects/Beta", "")]
        alpha, beta = (p.pin_id for p in shown.pins)
        assert [p.name for p in call(engine, "favorite.rename", pin=beta, name="  The   second  ").pins] == ["", "The second"]
        assert [p.pin_id for p in call(engine, "favorite.reorder", pin=(beta, alpha)).pins] == [beta, alpha]
        assert [p.pin_id for p in call(engine, "favorite.reorder", pin=(beta,)).pins] == [beta, alpha]  # unnamed ones follow
        assert [p.pin_id for p in call(engine, "favorite.unpin", pin=beta).pins] == [alpha]
        assert [p.pin_id for p in call(engine, "sidebar.show").pins] == [alpha]
        with pytest.raises(UsageError, match="no pinned folder"):
            call(engine, "favorite.unpin", pin="0" * 16)
        with pytest.raises(UsageError):
            call(engine, "favorite.pin", account=account, folder="No/Such")
        assert call(engine, "favorite.show", view="trash", visible=False).hidden == ("trash",)
        assert call(engine, "favorite.show", view="trash", visible=True).hidden == ()
        with pytest.raises(UsageError):
            call(engine, "favorite.show", view="everything")
        call(engine, "account.remove", account=account)  # the pins of a removed account go with it
        assert call(engine, "sidebar.show").pins == ()


def test_groups_hold_an_account_at_most_once(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop_a, FakePop3Server({}) as pop_b, FakeSmtpServer() as smtp:
        a = add_account(engine, pop_a, smtp, email="a@example.com")
        b = add_account(engine, pop_b, smtp, email="b@example.com")
        made = call(engine, "group.add", name="Work", account=(a, b))
        work = made.groups[0]
        assert work.name == "Work" and set(work.accounts) == {a, b}
        with pytest.raises(UsageError, match="at least one account"):
            call(engine, "group.add", name="Empty")  # a group without accounts could never be shown
        call(engine, "group.assign", account=a)  # out of Work, into no group: Work keeps b
        other = call(engine, "group.add", name="Home", account=(a,)).groups[1]
        moved = call(engine, "group.assign", account=b, group=other.group_id)  # Work's last account leaves: Work is gone
        assert {g.name: set(g.accounts) for g in moved.groups} == {"Home": {a, b}}
        work = call(engine, "group.add", name="Work", account=(b,)).groups[1]
        assert {g.name: set(g.accounts) for g in call(engine, "group.assign", account=a).groups} == {"Work": {b}}  # Home's last one left
        assert call(engine, "group.rename", group=work.group_id, name="Job").groups[0].name == "Job"
        with pytest.raises(UsageError, match="needs a name"):
            call(engine, "group.add", name="   ", account=(a,))
        with pytest.raises(UsageError, match="no group"):
            call(engine, "group.assign", account=a, group="f" * 16)
        assert call(engine, "group.remove", group=work.group_id).groups == ()
        assert call(engine, "group.add", name="Again", account=(b,)).groups[0].accounts == (b,)
        call(engine, "account.remove", account=b)
        assert call(engine, "sidebar.show").groups == ()  # its only account was removed: the group went with it


def test_a_standard_folder_of_local_folders_is_hidden_and_shown(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    assert call(engine, "sidebar.show-local-folder", folder="trash", visible=False).hidden_local_folders == ("trash",)
    assert call(engine, "sidebar.show-local-folder", folder="snoozed", visible=False).hidden_local_folders == ("trash", "snoozed")
    assert call(engine, "sidebar.show").hidden_local_folders == ("trash", "snoozed")
    assert call(engine, "sidebar.show-local-folder", folder="trash", visible=True).hidden_local_folders == ("snoozed",)
    with pytest.raises(UsageError, match="standard folders of Local Folders"):
        call(engine, "sidebar.show-local-folder", folder="Projects", visible=False)  # the person's own folders have no tick
    data = json.loads(sidebar_to_text(load_sidebar(engine.directories.sidebar_file)))
    assert data["hidden_local_folders"] == ["snoozed"]
    with pytest.raises(ConfigurationError, match="Local Folders"):
        parse_sidebar(json.dumps({**data, "hidden_local_folders": ["Projects"]}))


def test_the_sidebar_file_is_checked_and_a_damaged_one_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "sidebar.json"
    assert load_sidebar(path) == Sidebar()
    sidebar, group = Sidebar().with_group("G")
    save_sidebar(path, sidebar.assigned("a" * 32, group.group_id).pin("a" * 32, "Inbox"))
    assert parse_sidebar(path.read_text()) == load_sidebar(path) and (sys.platform == "win32" or (path.stat().st_mode & 0o077) == 0)  # Windows has no such permissions
    data = json.loads(sidebar_to_text(load_sidebar(path)))
    cases: list[dict[str, object]] = [{"format": 2}, {**data, "pins": [{"id": "x", "account": "a" * 32, "folder": "Inbox"}]}, {**data, "groups": [{"id": "1" * 16, "name": "", "accounts": []}]}, {**data, "pins": "no"}, {**data, "groups": [{"id": "1" * 16, "name": "x", "accounts": ["bad"]}]}]
    for broken in cases:
        with pytest.raises(ConfigurationError):
            parse_sidebar(json.dumps(broken))
    path.write_text("{ not json")
    with pytest.raises(ConfigurationError, match="copied"):
        load_sidebar(path)
    assert (tmp_path / "sidebar.json.bad").read_text() == "{ not json"


def test_an_empty_group_in_the_file_is_dropped(tmp_path: Path) -> None:
    sidebar, group = Sidebar().with_group("Kept")
    data = json.loads(sidebar_to_text(sidebar.assigned("a" * 32, group.group_id)))
    data["groups"].append({"id": "2" * 16, "name": "Test", "accounts": []})  # made by an older version, never shown
    assert [item.name for item in parse_sidebar(json.dumps(data)).groups] == ["Kept"]


def test_an_account_tree_can_be_hidden_and_shown(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        account = add_account(engine, pop, smtp)
        assert call(engine, "sidebar.show-tree", account=account, visible=False).hidden_trees == (account,)
        assert call(engine, "sidebar.show").hidden_trees == (account,)
        assert call(engine, "sidebar.show-tree", account=account, visible=True).hidden_trees == ()
        call(engine, "sidebar.show-tree", account=account, visible=False)
        call(engine, "account.remove", account=account)  # a removed account is forgotten
        assert call(engine, "sidebar.show").hidden_trees == ()



def test_a_renamed_folder_takes_its_pins_along(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        account = add_account(engine, pop, smtp, email="a@example.com")
        call(engine, "folder.create", account=account, folder="Projects")
        call(engine, "folder.create", account=account, folder="Projects/Alpha")
        call(engine, "folder.create", account=account, folder="Other")
        call(engine, "favorite.pin", account=account, folder="Other")
        pins = call(engine, "favorite.pin", account=account, folder="Projects/Alpha").pins
        call(engine, "favorite.pin", account=account, folder="Projects")
        other, alpha = (p.pin_id for p in pins)
        call(engine, "favorite.rename", pin=alpha, name="My alpha")
        call(engine, "folder.rename", account=account, folder="Projects", new_name="Work")
        shown = {p.pin_id: (p.folder, p.name) for p in call(engine, "sidebar.show").pins}
        assert shown[alpha] == ("Work/Alpha", "My alpha") and shown[other] == ("Other", "")  # the folder inside it too; its own name kept
        assert sorted(folder for folder, _ in shown.values()) == ["Other", "Work", "Work/Alpha"]
        assert [p.pin_id for p in call(engine, "sidebar.show").pins][:2] == [other, alpha]  # the order stays
