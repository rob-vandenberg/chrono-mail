from dataclasses import replace
from pathlib import Path

import pytest

from chrono_mail.config import Action, Rule, RuleSet, load_rules, new_rule_id, save_rules
from chrono_mail.config.rules import MAXIMUM_RULES
from chrono_mail.engine.changes import Change
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.rule_folders import _copy_name
from chrono_mail.engine.session import Engine
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.test_engine_commands import add_account, call
from tests.test_engine_imap import World, world  # noqa: F401 - world is a fixture
from tests.test_engine_imap_online import filled, in_step
from tests.test_engine_mail_conflicts import folder_with_a_change_here

__all__ = ["world"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_rule_folders.py 4.2.453"

# --- Version history ----------------------------------------------------
# v4.2.453: Initial version: the rules follow a folder renamed or moved and are switched off when it is deleted; a rule another place
#           shares the folder with is split into "<name> (Copy n)" (the owner, D-302)


def rules(engine: Engine) -> list[Rule]:
    return list(load_rules(engine.directories.rules_file).rules)


def by_name(engine: Engine) -> dict[str, Rule]:
    return {rule.name: rule for rule in rules(engine)}


def add_rule(engine: Engine, name: str, folder: str, accounts: tuple[str, ...] = (), enabled: bool = True) -> None:
    call(engine, "rule.add", name=name, condition=["subject:contains:x"], action=[f"move_to:{folder}", "mark_read"], account=list(accounts), disabled=not enabled)


def moves_to(rule: Rule) -> str:
    return next(action.value for action in rule.actions if action.type == "move_to")


def listen(engine: Engine) -> list[Change]:
    told: list[Change] = []
    engine.changed = told.append
    return told


def mail_accounts(engine: Engine) -> set[str]:
    return {item.account.account_id for items in engine.storage_groups().values() for item in items}


# --- one place where mail is kept ---------------------------------------------------------------------------------------------------------


def test_a_renamed_folder_takes_its_rules_along_with_the_folders_inside_it(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        account = add_account(engine, pop, smtp, email="a@example.com")
        for folder in ("Projects", "Projects/Alpha", "Other"):
            call(engine, "folder.create", account=account, folder=folder)
        add_rule(engine, "Top", "Projects")
        add_rule(engine, "Inside", "Projects/Alpha")
        add_rule(engine, "Spelled", "projects/Alpha")  # found by the mailbox in any letter case
        add_rule(engine, "Elsewhere", "Other")
        add_rule(engine, "Switched off", "Projects", enabled=False)  # follows too: it works again when switched on
        told = listen(engine)
        result = call(engine, "folder.rename", account=account, folder="Projects", new_name="Work")
        shown = by_name(engine)
        assert moves_to(shown["Top"]) == "Work" and moves_to(shown["Inside"]) == "Work/Alpha" and moves_to(shown["Spelled"]) == "Work/Alpha"
        assert moves_to(shown["Elsewhere"]) == "Other" and moves_to(shown["Switched off"]) == "Work" and not shown["Switched off"].enabled
        assert [rule.name for rule in rules(engine)] == ["Top", "Inside", "Spelled", "Elsewhere", "Switched off"]  # the order stays
        assert all(rule.actions[1] == Action("mark_read") for rule in rules(engine))  # the other actions stay
        assert result.rules[0] == "Rule “Top” now moves mail to Work" and len(result.rules) == 4
        facts = [change for change in told if change.kind == "rules_changed"]
        assert len(facts) == 1 and facts[0].notes == result.rules and facts[0].mailbox == account
        assert [change.kind for change in told].index("folder_renamed") < [change.kind for change in told].index("rules_changed")


def test_a_moved_folder_takes_its_rules_to_its_new_place(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        account = add_account(engine, pop, smtp, email="a@example.com")
        for folder in ("Clients", "Clients/2026", "Archive"):
            call(engine, "folder.create", account=account, folder=folder)
        add_rule(engine, "Year", "Clients/2026")
        call(engine, "folder.rename", account=account, folder="Clients/2026", new_name="Archive/2026")
        assert moves_to(by_name(engine)["Year"]) == "Archive/2026"


def test_a_deleted_folder_switches_its_rules_off(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        account = add_account(engine, pop, smtp, email="a@example.com")
        for folder in ("Work", "Work/Sub", "Other"):
            call(engine, "folder.create", account=account, folder=folder)
        add_rule(engine, "Top", "Work")
        add_rule(engine, "Inside", "Work/Sub")
        add_rule(engine, "Elsewhere", "Other")
        add_rule(engine, "Off already", "Work", enabled=False)
        result = call(engine, "folder.delete", account=account, folder="Work", with_messages=True, include_subfolders=True)
        shown = by_name(engine)
        assert not shown["Top"].enabled and not shown["Inside"].enabled and shown["Elsewhere"].enabled and not shown["Off already"].enabled
        assert moves_to(shown["Top"]) == "Work" and moves_to(shown["Inside"]) == "Work/Sub"  # the folder stays, to see what it was
        assert set(result.rules) == {"Rule “Top” is switched off: its folder Work was deleted", "Rule “Inside” is switched off: its folder Work/Sub was deleted"}


def test_a_rule_for_other_accounts_only_is_left_alone(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        first = add_account(engine, pop, smtp, email="a@example.com")
        second = add_account(engine, pop, smtp, email="b@example.com")
        call(engine, "folder.create", account=first, folder="Clients")
        add_rule(engine, "Theirs", "Clients", accounts=(second,))
        result = call(engine, "folder.rename", account=first, folder="Clients", new_name="Customers")
        assert moves_to(by_name(engine)["Theirs"]) == "Clients" and result.rules == ()
        call(engine, "folder.delete", account=first, folder="Customers", with_messages=True, include_subfolders=True)
        assert by_name(engine)["Theirs"].enabled


def test_the_accounts_that_keep_their_mail_in_local_folders_share_the_folder(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        first = add_account(engine, pop, smtp, email="a@example.com", storage="local")
        add_account(engine, pop, smtp, email="b@example.com", storage="local")
        call(engine, "folder.create", account=first, folder="Clients")
        add_rule(engine, "Every account", "Clients")
        call(engine, "folder.rename", account=first, folder="Clients", new_name="Customers")
        assert [(rule.name, moves_to(rule), rule.accounts) for rule in rules(engine)] == [("Every account", "Customers", ())]  # one place: no split


# --- a rule that reaches another place with the same folder: split (the owner's choice A) --------------------------------------------------


def test_a_rule_another_account_shares_the_folder_with_is_split(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        first = add_account(engine, pop, smtp, email="a@example.com")
        second = add_account(engine, pop, smtp, email="b@example.com")
        for account in (first, second):
            call(engine, "folder.create", account=account, folder="Clients")
        add_rule(engine, "SPAM", "Clients")
        add_rule(engine, "After", "Clients", accounts=(second,))
        result = call(engine, "folder.rename", account=first, folder="Clients", new_name="Customers")
        original, copy, after = rules(engine)
        assert (original.name, moves_to(original), set(original.accounts)) == ("SPAM", "Clients", mail_accounts(engine) - {first})
        assert (copy.name, moves_to(copy), copy.accounts) == ("SPAM (Copy 1)", "Customers", (first,)) and copy.rule_id != original.rule_id
        assert (copy.conditions, copy.match, copy.enabled, copy.actions[1:]) == (original.conditions, original.match, original.enabled, original.actions[1:])
        assert after.name == "After" and moves_to(after) == "Clients"
        assert result.rules == ("Rule “SPAM (Copy 1)” is made for a@example.com: it moves mail to Customers",)
        call(engine, "folder.create", account=first, folder="Clients")
        call(engine, "folder.rename", account=second, folder="Clients", new_name="Old")  # the first account has Clients again, but SPAM no longer reaches it
        assert [(rule.name, moves_to(rule)) for rule in rules(engine)] == [("SPAM", "Old"), ("SPAM (Copy 1)", "Customers"), ("After", "Old")]


def test_a_second_split_takes_the_next_copy_number(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        accounts = [add_account(engine, pop, smtp, email=f"{letter}@example.com") for letter in "abc"]
        for account in accounts:
            call(engine, "folder.create", account=account, folder="Clients")
        add_rule(engine, "SPAM", "Clients")
        call(engine, "folder.rename", account=accounts[0], folder="Clients", new_name="First")
        call(engine, "folder.rename", account=accounts[1], folder="Clients", new_name="Second")
        assert [(rule.name, moves_to(rule), rule.accounts) for rule in rules(engine)] == [
            ("SPAM", "Clients", tuple(sorted(mail_accounts(engine) - set(accounts[:2]), key=[item.account.account_id for item in engine.config().accounts].index))),
            ("SPAM (Copy 2)", "Second", (accounts[1],)), ("SPAM (Copy 1)", "First", (accounts[0],)),
        ]


def test_a_rule_whose_other_account_has_no_such_folder_just_follows(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        first = add_account(engine, pop, smtp, email="a@example.com")
        add_account(engine, pop, smtp, email="b@example.com")
        call(engine, "folder.create", account=first, folder="Clients")
        add_rule(engine, "SPAM", "Clients")
        call(engine, "folder.rename", account=first, folder="Clients", new_name="Customers")
        assert [(rule.name, moves_to(rule), rule.accounts) for rule in rules(engine)] == [("SPAM", "Customers", ())]


def test_a_delete_switches_off_only_the_copy_for_that_account(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        first = add_account(engine, pop, smtp, email="a@example.com")
        second = add_account(engine, pop, smtp, email="b@example.com")
        for account in (first, second):
            call(engine, "folder.create", account=account, folder="Clients")
        add_rule(engine, "SPAM", "Clients", accounts=(first, second))
        result = call(engine, "folder.delete", account=first, folder="Clients", with_messages=True, include_subfolders=True)
        original, copy = rules(engine)
        assert (original.name, original.enabled, original.accounts) == ("SPAM", True, (second,))
        assert (copy.name, copy.enabled, copy.accounts, moves_to(copy)) == ("SPAM (Copy 1)", False, (first,), "Clients")
        assert result.rules == ("Rule “SPAM (Copy 1)” is made for a@example.com and switched off: its folder Clients was deleted there",)


def test_no_split_past_the_most_rules_there_may_be(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        first = add_account(engine, pop, smtp, email="a@example.com")
        second = add_account(engine, pop, smtp, email="b@example.com")
        for account in (first, second):
            call(engine, "folder.create", account=account, folder="Clients")
        add_rule(engine, "SPAM", "Clients")
        spam = rules(engine)[0]
        filler = tuple(replace(spam, rule_id=new_rule_id(), name=f"Rule {number}", actions=(Action("move_to", "Elsewhere"),)) for number in range(MAXIMUM_RULES - 1))
        save_rules(engine.directories.rules_file, RuleSet((spam, *filler)))
        result = call(engine, "folder.rename", account=first, folder="Clients", new_name="Customers")
        assert len(rules(engine)) == MAXIMUM_RULES and moves_to(rules(engine)[0]) == "Clients"
        assert result.rules == (f"Rule “SPAM” still moves mail to Clients: another account still has its folder Clients, and there are already {MAXIMUM_RULES} rules, so it cannot be split",)
        call(engine, "folder.create", account=first, folder="Clients")
        result = call(engine, "folder.delete", account=first, folder="Clients", with_messages=True, include_subfolders=True)
        assert rules(engine)[0].enabled  # the second account still has Clients: switching it off would stop its mail there
        assert result.rules == (f"Rule “SPAM” stays on: another account still has its folder Clients, and there are already {MAXIMUM_RULES} rules, so it cannot be split",)


def test_a_new_folder_path_too_long_for_a_rule_keeps_the_rule_and_the_rename(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        account = add_account(engine, pop, smtp, email="a@example.com")
        call(engine, "folder.create", account=account, folder="P")
        add_rule(engine, "Short", "P")
        told = listen(engine)
        long = "/".join(["x" * 60] * 9)  # 548 characters: a folder may have it, a rule's folder may not (500)
        result = call(engine, "folder.rename", account=account, folder="P", new_name=long)
        assert result.folder == long and "folder_renamed" in [change.kind for change in told]  # the rename is done and told
        assert moves_to(by_name(engine)["Short"]) == "P" and result.rules == ("Rule “Short” still moves mail to P",)


def test_a_folder_renamed_to_a_name_with_a_space_at_its_end_keeps_the_rule_and_says_so(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        first = add_account(engine, pop, smtp, email="a@example.com")
        second = add_account(engine, pop, smtp, email="b@example.com")
        for account in (first, second):
            call(engine, "folder.create", account=account, folder="Clients")
        add_rule(engine, "SPAM", "Clients")
        told = listen(engine)
        with open_mailbox(engine, first) as mailbox:
            mailbox.rename_folder(("Clients",), ("Foo ",))  # a server may name a folder so; a rule finds a folder by its name without the space
        assert "folder_renamed" in [change.kind for change in told]
        assert [(rule.name, moves_to(rule)) for rule in rules(engine)] == [("SPAM", "Clients")]
        assert [change.notes for change in told if change.kind == "rules_changed"] == [("Rule “SPAM” still moves mail to Clients",)]


def test_whatever_goes_wrong_with_the_rules_the_rename_is_told_and_so_is_that(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        account = add_account(engine, pop, smtp, email="a@example.com")
        call(engine, "folder.create", account=account, folder="Clients")
        add_rule(engine, "SPAM", "Clients")

        def broken(*arguments: object) -> tuple[str, ...]:
            raise RuntimeError("a fault of the program")

        monkeypatch.setattr("chrono_mail.engine.mailbox.follow_rename", broken)
        told = listen(engine)
        result = call(engine, "folder.rename", account=account, folder="Clients", new_name="Customers")
        assert result.folder == "Customers" and "folder_renamed" in [change.kind for change in told]
        assert result.rules == ("The rules could not follow the folder Clients: please look at them in Settings > Rules",)


def test_the_name_of_a_copy() -> None:
    assert _copy_name("SPAM", {"SPAM"}) == "SPAM (Copy 1)"
    assert _copy_name("SPAM", {"SPAM", "SPAM (Copy 1)", "SPAM (Copy 2)"}) == "SPAM (Copy 3)"
    assert _copy_name("SPAM", {"SPAM", "SPAM (Copy 2)"}) == "SPAM (Copy 1)"  # the lowest number no rule has
    assert _copy_name("SPAM (Copy 1)", set()) == "SPAM (Copy 1) (Copy 1)"
    long = _copy_name("x" * 100, set())
    assert len(long) == 100 and long.endswith("… (Copy 1)")
    Rule(new_rule_id(), long, (), (Action("mark_read"),), accounts=("a" * 32,))  # a name a rule may have


# --- IMAP: followed from the server; a folder only hidden here changes no rule -------------------------------------------------------------


def solo(world: World) -> None:
    """Solo, an IMAP folder with one message, known to the sync, and a rule that moves mail to it."""
    (row,) = filled(world, 1)
    call(world.engine, "folder.create", account=world.account, folder="Solo")
    call(world.engine, "message.move", account=world.account, folder="Inbox", message=row.message_id, target="Solo")
    in_step(world, ("Solo", "Solo"))
    add_rule(world.engine, "Solo rule", "Solo")


def test_a_folder_the_server_renamed_takes_its_rules_along(world: World) -> None:
    solo(world)
    world.imap.folders["Duo"] = world.imap.folders.pop("Solo")  # another program renamed it
    told = listen(world.engine)
    world.fetch()
    assert moves_to(by_name(world.engine)["Solo rule"]) == "Duo"
    assert [change.notes for change in told if change.kind == "rules_changed"] == [("Rule “Solo rule” now moves mail to Duo",)]  # told during the check too


def test_a_folder_renamed_here_on_the_server_first_takes_its_rules_along(world: World) -> None:
    solo(world)
    call(world.engine, "folder.rename", account=world.account, folder="Solo", new_name="Duo")
    assert moves_to(by_name(world.engine)["Solo rule"]) == "Duo"


def test_a_folder_the_server_deleted_switches_its_rules_off(world: World) -> None:
    solo(world)
    world.imap.folders.pop("Solo")
    world.fetch()
    assert "Solo" not in {item.path for item in call(world.engine, "folder.list", account=world.account)}
    assert not by_name(world.engine)["Solo rule"].enabled


def test_a_folder_hidden_here_changes_no_rule(world: World) -> None:
    solo(world)
    call(world.engine, "account.set-folders", account=world.account, hide=["Solo"])
    assert "Solo" not in {item.path for item in call(world.engine, "folder.list", account=world.account)}
    assert by_name(world.engine)["Solo rule"].enabled and moves_to(by_name(world.engine)["Solo rule"]) == "Solo"


def test_a_folder_deleted_on_the_server_settled_with_the_servers_side_switches_its_rules_off(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    conflict = folder_with_a_change_here(world, monkeypatch)
    add_rule(world.engine, "Projects rule", "Projects")
    assert by_name(world.engine)["Projects rule"].enabled  # while the conflict is open the folder is still here
    call(world.engine, "conflict.settle", conflicts=(conflict.conflict,), side="server")
    assert not by_name(world.engine)["Projects rule"].enabled
