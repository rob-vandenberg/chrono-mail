import json
from pathlib import Path

import pytest

from chrono_mail.config import Action, Condition, Rule, RuleSet, load_rules, new_rule_id, parse_rules, rules_to_text, save_rules
from chrono_mail.core import ConfigurationError

# --- Version ------------------------------------------------------------
__version__ = "test_config_rules.py 0.0.33"

# --- Version history ----------------------------------------------------
# v0.0.33: Initial rules file tests


def rule(name: str = "Newsletters", rule_id: str | None = None) -> Rule:
    return Rule(
        rule_id or new_rule_id(), name,
        (Condition("from", "contains", "news@example.org"), Condition("header", "contains", "bulk", "Precedence"), Condition("size", "greater_than", "100"), Condition("attachment", "is", "yes")),
        (Action("move_to", "Newsletters/2026"), Action("mark_read"), Action("stop")),
        "any", True, ("a" * 32,),
    )


def test_a_rule_set_survives_the_file(tmp_path: Path) -> None:
    rules = RuleSet((rule("One"), rule("Two")))
    assert parse_rules(rules_to_text(rules)) == rules
    save_rules(tmp_path / "rules.json", rules)
    assert load_rules(tmp_path / "rules.json") == rules
    assert oct((tmp_path / "rules.json").stat().st_mode & 0o777) == "0o600"
    assert load_rules(tmp_path / "none.json") == RuleSet()


def test_the_set_adds_replaces_moves_toggles_and_removes() -> None:
    one, two, three = rule("One"), rule("Two"), rule("Three")
    rules = RuleSet().with_rule(one).with_rule(two).with_rule(three)
    assert [item.name for item in rules.rules] == ["One", "Two", "Three"]
    from dataclasses import replace

    assert [item.name for item in rules.with_rule(replace(two, name="Second")).rules] == ["One", "Second", "Three"]
    assert [item.name for item in rules.moved(three.rule_id, 0).rules] == ["Three", "One", "Two"]
    assert [item.name for item in rules.moved(one.rule_id, 99).rules] == ["Two", "Three", "One"]
    assert not rules.toggled(two.rule_id, False).find(two.rule_id).enabled
    assert [item.name for item in rules.without(two.rule_id).rules] == ["One", "Three"]
    for operation in (lambda: rules.find("0" * 16), lambda: rules.without("0" * 16), lambda: rules.moved("0" * 16, 0)):
        with pytest.raises(ConfigurationError, match="Unknown rule"):
            operation()


@pytest.mark.parametrize(
    "build",
    [
        lambda: Condition("colour", "contains", "x"),
        lambda: Condition("from", "greater_than", "5"),
        lambda: Condition("size", "contains", "5"),
        lambda: Condition("from", "contains", ""),
        lambda: Condition("from", "contains", "x" * 501),
        lambda: Condition("from", "contains", "a\x00b"),
        lambda: Condition("header", "contains", "x"),
        lambda: Condition("header", "contains", "x", "Bad Name"),
        lambda: Condition("from", "contains", "x", "Header"),
        lambda: Condition("size", "greater_than", "big"),
        lambda: Condition("size", "greater_than", "-5"),
        lambda: Condition("size", "greater_than", "1" * 10),
        lambda: Condition("attachment", "is", "maybe"),
        lambda: Action("shout"),
        lambda: Action("move_to"),
        lambda: Action("move_to", "a//b"),
        lambda: Action("move_to", "a/ /b"),
        lambda: Action("mark_read", "Inbox"),
        lambda: Rule("x", "n", (Condition("from", "contains", "a"),), (Action("stop"),)),
        lambda: Rule(new_rule_id(), "  ", (Condition("from", "contains", "a"),), (Action("stop"),)),
        lambda: Rule(new_rule_id(), "n" * 101, (Condition("from", "contains", "a"),), (Action("stop"),)),
        lambda: Rule(new_rule_id(), "n", (), (Action("stop"),)),
        lambda: Rule(new_rule_id(), "n", (Condition("from", "contains", "a"),), ()),
        lambda: Rule(new_rule_id(), "n", (Condition("from", "contains", "a"),) * 21, (Action("stop"),)),
        lambda: Rule(new_rule_id(), "n", (Condition("from", "contains", "a"),), (Action("stop"),) * 11),
        lambda: Rule(new_rule_id(), "n", (Condition("from", "contains", "a"),), (Action("stop"),), "some"),
        lambda: Rule(new_rule_id(), "n", (Condition("from", "contains", "a"),), (Action("stop"),), "all", True, ("../x",)),
        lambda: RuleSet((rule(rule_id="a" * 16), rule(rule_id="a" * 16))),
        lambda: RuleSet(tuple(rule() for _ in range(201))),
    ],
)
def test_invalid_rules_are_refused(build: object) -> None:
    assert callable(build)
    with pytest.raises(ConfigurationError):
        build()


@pytest.mark.parametrize(
    "text",
    ["", "[]", "{}", '{"version": 2, "rules": []}', '{"version": 1, "rules": {}}', '{"version": 1, "rules": [5]}',
     '{"version": 1, "rules": [{"id": "a", "name": "x"}]}', json.dumps({"version": 1, "rules": [{"id": "a" * 16, "name": "x", "conditions": [{"field": 3}], "actions": [{"type": "stop"}]}]}),
     json.dumps({"version": 1, "rules": [{"id": "a" * 16, "name": "x", "conditions": [{"field": "from", "operator": "contains", "value": "a"}], "actions": [{"type": "stop"}], "enabled": "yes"}]}),
     json.dumps({"version": 1, "rules": [{"id": "a" * 16, "name": "x", "conditions": [{"field": "from", "operator": "contains", "value": "a"}], "actions": [{"type": "stop"}], "accounts": [5]}]})],
)
def test_wrong_files_are_a_configuration_error(text: str) -> None:
    with pytest.raises(ConfigurationError):
        parse_rules(text)


def test_unknown_settings_are_ignored_and_a_damaged_file_is_backed_up(tmp_path: Path) -> None:
    data = json.loads(rules_to_text(RuleSet((rule(),))))
    data["future"] = 1
    data["rules"][0]["future"] = True
    assert len(parse_rules(json.dumps(data)).rules) == 1
    broken = tmp_path / "rules.json"
    broken.write_text("{not json")
    with pytest.raises(ConfigurationError, match="rules.json.bad"):
        load_rules(broken)
    assert (tmp_path / "rules.json.bad").read_text() == "{not json" and broken.read_text() == "{not json"
    broken.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ConfigurationError):
        load_rules(broken)
    broken.write_bytes(b" " * (4 * 1024 * 1024 + 1))
    with pytest.raises(ConfigurationError, match="too large"):
        load_rules(broken)


def test_saving_where_it_cannot_be_done_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "blocked").write_text("a file, not a folder")
    with pytest.raises(ConfigurationError, match="could not be saved"):
        save_rules(tmp_path / "blocked" / "rules.json", RuleSet())
