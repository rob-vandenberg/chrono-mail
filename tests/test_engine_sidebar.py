import json
from pathlib import Path

import pytest

from chrono_mail.config import load_sidebar, parse_sidebar, save_sidebar, sidebar_to_text, Sidebar
from chrono_mail.core import ConfigurationError
from chrono_mail.engine import UsageError
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.test_engine_commands import add_account, call

# --- Version ------------------------------------------------------------
__version__ = "test_engine_sidebar.py 0.0.47"

# --- Version history ----------------------------------------------------
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
        other = call(engine, "group.add", name="Home").groups[1]
        moved = call(engine, "group.assign", account=a, group=other.group_id)
        assert {g.name: set(g.accounts) for g in moved.groups} == {"Work": {b}, "Home": {a}}
        assert {g.name: set(g.accounts) for g in call(engine, "group.assign", account=a).groups} == {"Work": {b}, "Home": set()}
        assert call(engine, "group.rename", group=work.group_id, name="Job").groups[0].name == "Job"
        with pytest.raises(UsageError, match="needs a name"):
            call(engine, "group.add", name="   ")
        with pytest.raises(UsageError, match="no group"):
            call(engine, "group.assign", account=a, group="f" * 16)
        assert [g.name for g in call(engine, "group.remove", group=work.group_id).groups] == ["Home"]
        assert call(engine, "group.add", name="Again", account=(b,)).groups[1].accounts == (b,)
        call(engine, "account.remove", account=b)
        assert all(not g.accounts for g in call(engine, "sidebar.show").groups)


def test_the_sidebar_file_is_checked_and_a_damaged_one_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "sidebar.json"
    assert load_sidebar(path) == Sidebar()
    sidebar, group = Sidebar().with_group("G")
    save_sidebar(path, sidebar.assigned("a" * 32, group.group_id).pin("a" * 32, "Inbox"))
    assert parse_sidebar(path.read_text()) == load_sidebar(path) and (path.stat().st_mode & 0o077) == 0
    data = json.loads(sidebar_to_text(load_sidebar(path)))
    cases: list[dict[str, object]] = [{"format": 2}, {**data, "pins": [{"id": "x", "account": "a" * 32, "folder": "Inbox"}]}, {**data, "groups": [{"id": "1" * 16, "name": "", "accounts": []}]}, {**data, "pins": "no"}, {**data, "groups": [{"id": "1" * 16, "name": "x", "accounts": ["bad"]}]}]
    for broken in cases:
        with pytest.raises(ConfigurationError):
            parse_sidebar(json.dumps(broken))
    path.write_text("{ not json")
    with pytest.raises(ConfigurationError, match="copied"):
        load_sidebar(path)
    assert (tmp_path / "sidebar.json.bad").read_text() == "{ not json"
