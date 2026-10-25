import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any  # json.load returns untyped data; every value is checked below

from chrono_mail.config.model import ACCOUNT_ID_PATTERN
from chrono_mail.core import ConfigurationError
from chrono_mail.core.jsontext import load_json
from chrono_mail.core.words import digits

# --- Version ------------------------------------------------------------
__version__ = "rules.py 3.6.351"

# --- Version history ----------------------------------------------------
# v3.6.351: numbers are read only from the digits 0 to 9 (core.words.digits): "²" passed isdigit() and stopped the work with a ValueError
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash
# v0.5.136: Tags: the condition tag is / is not, the actions add_tag and remove_tag
# v0.0.49: Rules without conditions for one account
# v0.0.33: Initial mail rules

FORMAT_VERSION: int = 1
MAXIMUM_RULES: int = 200
MAXIMUM_CONDITIONS: int = 20
MAXIMUM_ACTIONS: int = 10
MAXIMUM_TEXT: int = 500
MAXIMUM_FILE_BYTES: int = 4 * 1024 * 1024
RULE_ID: re.Pattern[str] = re.compile(r"[0-9a-f]{16}")
HEADER_NAME: re.Pattern[str] = re.compile(r"[A-Za-z0-9-]{1,60}")

TEXT_OPERATORS: tuple[str, ...] = ("contains", "not_contains", "equals", "not_equals", "starts_with", "ends_with")
FIELD_OPERATORS: dict[str, tuple[str, ...]] = {
    "from": TEXT_OPERATORS,
    "to": TEXT_OPERATORS,
    "cc": TEXT_OPERATORS,
    "subject": TEXT_OPERATORS,
    "body": TEXT_OPERATORS,
    "header": TEXT_OPERATORS,  # the name of the header goes with the condition
    "size": ("greater_than", "less_than"),  # the value is a number of kilobytes
    "attachment": ("is",),  # the value is yes or no
    "tag": ("is", "is_not"),  # the message has (or has not) this tag
}
ACTIONS: tuple[str, ...] = ("move_to", "mark_read", "mark_unread", "flag", "unflag", "delete", "mark_junk", "add_tag", "remove_tag", "stop")
TAG_ACTIONS: tuple[str, ...] = ("add_tag", "remove_tag")  # their value is the tag


@dataclass(frozen=True, slots=True)
class Condition:
    field: str
    operator: str
    value: str
    name: str = ""  # only for the field "header"

    def __post_init__(self) -> None:
        if self.field not in FIELD_OPERATORS:
            raise ConfigurationError(f"Unknown rule field '{self.field}'")
        if self.operator not in FIELD_OPERATORS[self.field]:
            raise ConfigurationError(f"The operator '{self.operator}' cannot be used with '{self.field}'")
        if len(self.value) > MAXIMUM_TEXT or "\x00" in self.value:
            raise ConfigurationError("A rule value is too long")
        if self.field == "header" and HEADER_NAME.fullmatch(self.name) is None:
            raise ConfigurationError("A header condition needs a header name such as List-Id")
        if self.field != "header" and self.name:
            raise ConfigurationError("Only a header condition has a name")
        if self.field == "size" and not (digits(self.value) and 0 < len(self.value) <= 9):
            raise ConfigurationError("A size condition needs a number of kilobytes")
        if self.field == "attachment" and self.value not in ("yes", "no"):
            raise ConfigurationError("An attachment condition needs yes or no")
        if self.field not in ("size", "attachment") and not self.value:
            raise ConfigurationError("A rule condition needs a value")


@dataclass(frozen=True, slots=True)
class Action:
    type: str
    value: str = ""  # the folder for move_to

    def __post_init__(self) -> None:
        if self.type not in ACTIONS:
            raise ConfigurationError(f"Unknown rule action '{self.type}'")
        if self.type == "move_to":
            parts = self.value.split("/")
            if not self.value or len(self.value) > MAXIMUM_TEXT or any(not part.strip() for part in parts) or "\x00" in self.value:
                raise ConfigurationError("The action move_to needs a folder such as Archive/2026")
        elif self.type in TAG_ACTIONS:
            if not self.value.strip() or len(self.value) > 100 or "\x00" in self.value:
                raise ConfigurationError(f"The action {self.type} needs a tag of up to 100 characters")
        elif self.value:
            raise ConfigurationError(f"The action '{self.type}' takes no value")


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    name: str
    conditions: tuple[Condition, ...]
    actions: tuple[Action, ...]
    match: str = "all"  # all conditions must hold, or any one of them
    enabled: bool = True
    accounts: tuple[str, ...] = ()  # account ids; empty means every account

    def __post_init__(self) -> None:
        if RULE_ID.fullmatch(self.rule_id) is None:
            raise ConfigurationError("A rule id is 16 lower-case hexadecimal characters")
        if not self.name.strip() or len(self.name) > 100:
            raise ConfigurationError("A rule needs a name of up to 100 characters")
        if self.match not in ("all", "any"):
            raise ConfigurationError("A rule matches 'all' or 'any' of its conditions")
        if len(self.conditions) > MAXIMUM_CONDITIONS:
            raise ConfigurationError(f"A rule has at most {MAXIMUM_CONDITIONS} conditions")
        if not self.conditions and not self.accounts:
            raise ConfigurationError("A rule without conditions must be for one account, or it would act on all mail")
        if not 1 <= len(self.actions) <= MAXIMUM_ACTIONS:
            raise ConfigurationError(f"A rule needs between 1 and {MAXIMUM_ACTIONS} actions")
        if any(ACCOUNT_ID_PATTERN.fullmatch(account) is None for account in self.accounts):
            raise ConfigurationError("A rule names an account that is not valid")


@dataclass(frozen=True, slots=True)
class RuleSet:
    rules: tuple[Rule, ...] = field(default=())

    def __post_init__(self) -> None:
        if len(self.rules) > MAXIMUM_RULES:
            raise ConfigurationError(f"There may be at most {MAXIMUM_RULES} rules")
        if len({rule.rule_id for rule in self.rules}) != len(self.rules):
            raise ConfigurationError("Two rules have the same id")

    def find(self, rule_id: str) -> Rule:
        for rule in self.rules:
            if rule.rule_id == rule_id:
                return rule
        raise ConfigurationError("Unknown rule")

    def with_rule(self, rule: Rule) -> "RuleSet":
        """Added at the end, or replacing the rule with the same id in its place."""
        if any(item.rule_id == rule.rule_id for item in self.rules):
            return RuleSet(tuple(rule if item.rule_id == rule.rule_id else item for item in self.rules))
        return RuleSet((*self.rules, rule))

    def without(self, rule_id: str) -> "RuleSet":
        self.find(rule_id)
        return RuleSet(tuple(rule for rule in self.rules if rule.rule_id != rule_id))

    def moved(self, rule_id: str, position: int) -> "RuleSet":
        rule = self.find(rule_id)
        others = [item for item in self.rules if item.rule_id != rule_id]
        others.insert(max(0, min(position, len(others))), rule)
        return RuleSet(tuple(others))

    def toggled(self, rule_id: str, enabled: bool) -> "RuleSet":
        return self.with_rule(replace(self.find(rule_id), enabled=enabled))


def new_rule_id() -> str:
    return os.urandom(8).hex()


# --- the file ----------------------------------------------------------------------------------------------------------------------------


def _text(data: dict[str, Any], key: str, where: str, default: str | None = None) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise ConfigurationError(f"Rule setting '{where}.{key}' is missing or not text")
    return value


def _rule(data: object, position: int) -> Rule:
    where = f"rules[{position}]"
    if not isinstance(data, dict):
        raise ConfigurationError(f"Rule setting '{where}' is not an object")
    conditions = data.get("conditions")
    actions = data.get("actions")
    accounts = data.get("accounts", [])
    enabled = data.get("enabled", True)
    if not isinstance(conditions, list) or not isinstance(actions, list) or not isinstance(accounts, list) or not isinstance(enabled, bool):
        raise ConfigurationError(f"Rule setting '{where}' is not valid")
    if not all(isinstance(item, dict) for item in (*conditions, *actions)) or not all(isinstance(item, str) for item in accounts):
        raise ConfigurationError(f"Rule setting '{where}' is not valid")
    try:
        return Rule(
            _text(data, "id", where),
            _text(data, "name", where),
            tuple(Condition(_text(item, "field", where), _text(item, "operator", where), _text(item, "value", where, ""), _text(item, "name", where, "")) for item in conditions),
            tuple(Action(_text(item, "type", where), _text(item, "value", where, "")) for item in actions),
            _text(data, "match", where, "all"),
            enabled,
            tuple(accounts),
        )
    except ConfigurationError as error:
        raise ConfigurationError(f"{where}: {error}") from None


def parse_rules(text: str) -> RuleSet:
    try:
        data = load_json(text)
    except ValueError:
        raise ConfigurationError("The rules file is not valid JSON") from None
    if not isinstance(data, dict) or data.get("version") != FORMAT_VERSION:
        raise ConfigurationError("The rules file has an unknown version")
    rules = data.get("rules", [])
    if not isinstance(rules, list):
        raise ConfigurationError("Setting 'rules' is not a list")
    return RuleSet(tuple(_rule(item, position) for position, item in enumerate(rules)))


def rules_to_text(rule_set: RuleSet) -> str:
    return json.dumps(
        {
            "version": FORMAT_VERSION,
            "rules": [
                {
                    "id": rule.rule_id,
                    "name": rule.name,
                    "enabled": rule.enabled,
                    "match": rule.match,
                    "accounts": list(rule.accounts),
                    "conditions": [{"field": c.field, "operator": c.operator, "value": c.value, **({"name": c.name} if c.name else {})} for c in rule.conditions],
                    "actions": [{"type": a.type, **({"value": a.value} if a.value else {})} for a in rule.actions],
                }
                for rule in rule_set.rules
            ],
        },
        indent=2,
        ensure_ascii=False,
    )


def load_rules(path: Path) -> RuleSet:
    """The rules in this file; none when the file does not exist yet. A damaged file is copied to <name>.bad and reported."""
    try:
        if not path.exists():
            return RuleSet()
        if path.stat().st_size > MAXIMUM_FILE_BYTES:
            raise ConfigurationError("The rules file is far too large")
        text = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        raise ConfigurationError("The rules file cannot be read") from None
    try:
        return parse_rules(text)
    except ConfigurationError as error:
        backup = path.with_name(path.name + ".bad")
        try:
            shutil.copyfile(path, backup)
        except OSError:
            raise ConfigurationError(f"{error} (and it could not be backed up)") from None
        raise ConfigurationError(f"{error}. The file was copied to {backup.name}.") from None


def save_rules(path: Path, rule_set: RuleSet) -> None:
    """Write the rules in one atomic step, readable by the owner only."""
    temporary = path.with_name(f".{path.name}.{os.urandom(6).hex()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            os.write(descriptor, rules_to_text(rule_set).encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        if sys.platform != "win32":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass  # nothing was written there
        raise ConfigurationError(f"The rules could not be saved: {error.strerror}") from None
