"""Outlook's rules and its junk list in Chrono Mail's terms, and Chrono Mail's rules in Outlook's: pure mapping, no Windows needed.

A rule is taken over whole or not at all: a condition, an exception or an action that has no exact counterpart leaves the rule out, and
its name is reported with the reason. Outlook joins its conditions with "and", the values of one condition with "or", and an exception
leaves a message out; Chrono Mail's rule matches all or any of its conditions."""

from collections.abc import Callable
from dataclasses import dataclass

from chrono_mail.config import Rule, new_rule_id
from chrono_mail.config.rules import MAXIMUM_CONDITIONS, Action, Condition
from chrono_mail.core import ConfigurationError

# --- Version ------------------------------------------------------------
__version__ = "outlook_rules.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial version: Outlook's rules to Chrono Mail's and back (whole or not at all), its junk list (MS-OXCSPAM) to blocked-sender rules

BLOCKED_RULE_NAME: str = "Blocked senders"


@dataclass(frozen=True, slots=True)
class OutlookPart:
    """One condition, exception or action of an Outlook rule: the name of its property in Outlook's object model, and its values."""

    kind: str
    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OutlookRule:
    name: str
    enabled: bool = True
    receive: bool = True  # False: a rule on mail being sent
    conditions: tuple[OutlookPart, ...] = ()
    exceptions: tuple[OutlookPart, ...] = ()
    actions: tuple[OutlookPart, ...] = ()


# --- Outlook -> Chrono Mail --------------------------------------------------------------------------------------------------------------

# Outlook's condition -> the Chrono Mail fields that together hold it (more than one: any of them)
_FIELDS: dict[str, tuple[str, ...]] = {
    "SenderAddress": ("from",), "From": ("from",), "Subject": ("subject",), "Body": ("body",), "SentTo": ("to",),
    "BodyOrSubject": ("subject", "body"), "RecipientAddress": ("to", "cc"),
}
_NAMES: dict[str, str] = {
    "SenderAddress": "words in the sender's address", "From": "from people", "Subject": "words in the subject", "Body": "words in the body",
    "SentTo": "sent to people", "BodyOrSubject": "words in the subject or body", "RecipientAddress": "words in the recipient's address",
    "HasAttachment": "with an attachment", "Category": "a category", "Account": "through an account",
    "MoveToFolder": "move to a folder", "Delete": "delete", "AssignToCategory": "assign a category", "MarkAsTask": "flag", "Stop": "stop processing more rules",
}


def _words(kind: str) -> str:
    return _NAMES.get(kind, kind)


def _group(part: OutlookPart) -> list[Condition]:
    """One Outlook condition as Chrono Mail conditions of which any one must hold."""
    if part.kind in _FIELDS:
        values = [value for value in part.values if value.strip()]
        if not values:
            raise ValueError(f"the condition '{_words(part.kind)}' has no value")
        return [Condition(field, "contains", value) for value in values for field in _FIELDS[part.kind]]
    if part.kind == "HasAttachment":
        return [Condition("attachment", "is", "yes")]
    if part.kind == "Category":
        return [Condition("tag", "is", value) for value in part.values if value.strip()]
    raise ValueError(f"Chrono Mail has no condition like '{_words(part.kind)}'")


def _negated(part: OutlookPart) -> list[Condition]:
    """An exception as conditions that must all hold (not any of its values)."""
    found: list[Condition] = []
    for condition in _group(part):
        if condition.operator == "contains":
            found.append(Condition(condition.field, "not_contains", condition.value))
        elif condition.field == "attachment":
            found.append(Condition("attachment", "is", "no"))
        elif condition.field == "tag":
            found.append(Condition("tag", "is_not", condition.value))
    return found


def _actions(parts: tuple[OutlookPart, ...]) -> list[Action]:
    found: list[Action] = []
    for part in parts:
        if part.kind == "MoveToFolder" and part.values and part.values[0].strip():
            found.append(Action("move_to", part.values[0]))
        elif part.kind == "Delete":
            found.append(Action("delete"))
        elif part.kind == "AssignToCategory" and part.values:
            found.extend(Action("add_tag", value) for value in part.values if value.strip())
        elif part.kind == "MarkAsTask":
            found.append(Action("flag"))
        elif part.kind == "Stop":
            found.append(Action("stop"))
        else:
            raise ValueError(f"Chrono Mail has no action like '{_words(part.kind)}'")
    return found


def rule_from_outlook(source: OutlookRule, account_of: Callable[[str], str | None]) -> Rule | str:
    """The rule in Chrono Mail's terms, or why it is not imported. account_of: the id of the account here with this address (or None)."""
    if not source.receive:
        return "it acts on mail being sent"
    try:
        accounts: tuple[str, ...] = ()
        groups: list[list[Condition]] = []
        for part in source.conditions:
            if part.kind == "Account":
                account = account_of(part.values[0]) if part.values else None
                if account is None:
                    return "its account is not here"
                accounts = (account,)
                continue
            groups.append(_group(part))
        exceptions = [condition for part in source.exceptions for condition in _negated(part)]
        if all(len(group) == 1 for group in groups):
            match, conditions = "all", [group[0] for group in groups]
        elif len(groups) == 1 and not exceptions:
            match, conditions = "any", groups[0]
        elif len(groups) == 1:
            return "an exception cannot go with conditions of which any one is enough"
        else:
            return "its conditions need both 'all' and 'any'"
        conditions += exceptions
        if len(conditions) > MAXIMUM_CONDITIONS:
            return f"it has more than {MAXIMUM_CONDITIONS} conditions"
        return Rule(new_rule_id(), source.name.strip()[:100] or "Imported rule", tuple(conditions), tuple(_actions(source.actions)), match, source.enabled, accounts)
    except (ValueError, ConfigurationError) as error:
        return str(error)


# --- Chrono Mail -> Outlook --------------------------------------------------------------------------------------------------------------

_TO_OUTLOOK: dict[tuple[str, str], tuple[str, bool]] = {  # (field, operator) -> (Outlook's condition, as an exception)
    ("from", "contains"): ("SenderAddress", False), ("from", "not_contains"): ("SenderAddress", True),
    ("subject", "contains"): ("Subject", False), ("subject", "not_contains"): ("Subject", True),
    ("body", "contains"): ("Body", False), ("body", "not_contains"): ("Body", True),
    ("tag", "is"): ("Category", False), ("tag", "is_not"): ("Category", True),
}
_ACTION_WORDS: dict[str, str] = {
    "mark_read": "mark as read", "mark_unread": "mark as unread", "unflag": "remove the flag", "remove_tag": "remove a tag", "mark_junk": "mark as junk",
}


@dataclass(frozen=True, slots=True)
class OutlookRuleSpec:
    """A rule as Outlook's object model makes it; account: the address of the account it is for ("" every account)."""

    name: str
    enabled: bool
    conditions: tuple[OutlookPart, ...]
    exceptions: tuple[OutlookPart, ...]
    actions: tuple[OutlookPart, ...]
    account: str = ""


def _outlook_place(condition: Condition) -> tuple[str, bool, str]:
    if condition.field == "attachment":
        return "HasAttachment", condition.value == "no", ""
    place = _TO_OUTLOOK.get((condition.field, condition.operator))
    if place is None:
        raise ValueError(f"Outlook has no condition like '{condition.field} {condition.operator.replace('_', ' ')}'")
    return place[0], place[1], condition.value


def rule_to_outlook(rule: Rule, address_of: Callable[[str], str | None]) -> OutlookRuleSpec | str:
    """The rule as Outlook can hold it, or why it cannot. address_of: the e-mail address of an account id here."""
    try:
        positive: dict[str, list[str]] = {}
        negative: dict[str, list[str]] = {}
        for condition in rule.conditions:
            kind, exception, value = _outlook_place(condition)
            values = (negative if exception else positive).setdefault(kind, [])
            if value:
                values.append(value)
        if rule.match == "any" and len(rule.conditions) > 1:
            if negative or len(positive) != 1:
                return "Outlook cannot hold 'any' of different conditions"
        elif any(len(values) > 1 for values in positive.values()):
            return "Outlook cannot require two values of one condition together"
        account = ""
        if len(rule.accounts) > 1:
            return "it is for more than one account"
        if rule.accounts:
            account = address_of(rule.accounts[0]) or ""
            if not account:
                return "its account is not here"
        actions: list[OutlookPart] = []
        tags: list[str] = []
        for action in rule.actions:
            if action.type == "move_to":
                actions.append(OutlookPart("MoveToFolder", (action.value,)))
            elif action.type == "delete":
                actions.append(OutlookPart("Delete"))
            elif action.type == "add_tag":
                tags.append(action.value)
            elif action.type == "flag":
                actions.append(OutlookPart("MarkAsTask"))
            elif action.type == "stop":
                actions.append(OutlookPart("Stop"))
            else:
                return f"Outlook cannot take the action '{_ACTION_WORDS.get(action.type, action.type)}' from another program"
        if tags:
            actions.append(OutlookPart("AssignToCategory", tuple(tags)))
        if not positive and not account:
            return "Outlook would apply it to all mail"
        return OutlookRuleSpec(
            rule.name, rule.enabled, tuple(OutlookPart(kind, tuple(values)) for kind, values in positive.items()),
            tuple(OutlookPart(kind, tuple(values)) for kind, values in negative.items()), tuple(actions), account,
        )
    except ValueError as error:
        return str(error)


# --- the junk list (MS-OXCSPAM: the restriction in PR_EXTENDED_RULE_MSG_CONDITION) ------------------------------------------------------

_STRING_TYPES: frozenset[int] = frozenset({0x001F, 0x001E})
_FIXED_SIZES: dict[int, int] = {0x0002: 2, 0x0003: 4, 0x0004: 4, 0x0005: 8, 0x0006: 8, 0x0007: 8, 0x000A: 4, 0x000B: 2, 0x0014: 8, 0x0040: 8, 0x0048: 16}
_MAXIMUM_DEPTH: int = 50


class _Reader:
    def __init__(self, data: bytes, position: int) -> None:
        self.data, self.position = data, position

    def take(self, size: int) -> bytes:
        if size < 0 or self.position + size > len(self.data):
            raise ValueError("the junk list ends too early")
        part = self.data[self.position:self.position + size]
        self.position += size
        return part

    def number(self, size: int) -> int:
        return int.from_bytes(self.take(size), "little")

    def string(self, wide: bool) -> str:
        step = 2 if wide else 1
        end = self.position
        while end + step <= len(self.data) and self.data[end:end + step] != b"\x00" * step:
            end += step
        text = self.data[self.position:end].decode("utf-16-le" if wide else "cp1252", "replace")
        self.position = end + step
        return text

    def value(self, kind: int) -> str | None:
        """A tagged value's value: its text for a string, else skipped (None)."""
        if kind in _STRING_TYPES:
            return self.string(kind == 0x001F)
        if kind in _FIXED_SIZES:
            self.take(_FIXED_SIZES[kind])
        elif kind == 0x0102:
            self.take(self.number(4))
        elif kind & 0x1000:
            for _ in range(min(self.number(4), len(self.data))):
                self.value(kind & 0x0FFF)
        else:
            raise ValueError("the junk list holds a value of an unknown kind")
        return None


def _restriction(reader: _Reader, negated: bool, depth: int, blocked: list[str], safe: list[str]) -> None:
    if depth > _MAXIMUM_DEPTH:
        raise ValueError("the junk list is nested too deeply")
    kind = reader.number(1)
    if kind in (0x00, 0x01):  # and, or
        for _ in range(min(reader.number(4), len(reader.data))):
            _restriction(reader, negated, depth + 1, blocked, safe)
    elif kind == 0x02:  # not
        _restriction(reader, not negated, depth + 1, blocked, safe)
    elif kind in (0x03, 0x04):  # content, property
        reader.take(4 if kind == 0x03 else 1)
        reader.take(4)  # the property compared
        value = reader.value(reader.number(4) & 0xFFFF)
        if value is not None and value.strip():
            (safe if negated else blocked).append(value.strip())
    elif kind == 0x05:
        reader.take(9)
    elif kind in (0x06, 0x07):
        reader.take(9)
    elif kind == 0x08:
        reader.take(4)
    elif kind == 0x09:
        reader.take(4)
        _restriction(reader, negated, depth + 1, blocked, safe)
    elif kind == 0x0A:  # comment: its values, then perhaps a restriction
        for _ in range(reader.number(1)):
            reader.value(reader.number(4) & 0xFFFF)
        if reader.number(1):
            _restriction(reader, negated, depth + 1, blocked, safe)
    elif kind == 0x0B:
        reader.take(4)
        _restriction(reader, negated, depth + 1, blocked, safe)
    else:
        raise ValueError("the junk list holds a test of an unknown kind")


def _skip_named_properties(data: bytes) -> int:
    reader = _Reader(data, 0)
    count = reader.number(2)
    reader.take(2 * count)
    if count:
        reader.take(reader.number(4))
    return reader.position


def junk_senders(data: bytes) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The blocked and the safe senders (addresses and domains) of the junk list; ValueError when it cannot be read."""
    start = _skip_named_properties(data)
    for offset in (start, start + 4):  # with or without the rule's version number before the restriction
        blocked: list[str] = []
        safe: list[str] = []
        try:
            _restriction(_Reader(data, offset), False, 0, blocked, safe)
        except ValueError:
            continue
        return tuple(dict.fromkeys(blocked)), tuple(dict.fromkeys(safe))
    raise ValueError("the junk list could not be read")


def blocked_rules(blocked: tuple[str, ...]) -> tuple[Rule, ...]:
    """Blocked senders as rules: a message from any of them is junk; at most MAXIMUM_CONDITIONS senders per rule."""
    values = [value[:500] for value in blocked if value.strip()]
    rules: list[Rule] = []
    for number, start in enumerate(range(0, len(values), MAXIMUM_CONDITIONS)):
        name = BLOCKED_RULE_NAME if number == 0 else f"{BLOCKED_RULE_NAME} {number + 1}"
        chunk = values[start:start + MAXIMUM_CONDITIONS]
        rules.append(Rule(new_rule_id(), name, tuple(Condition("from", "contains", value) for value in chunk), (Action("mark_junk"),), "any"))
    return tuple(rules)
