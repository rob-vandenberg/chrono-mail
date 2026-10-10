from dataclasses import dataclass, field, replace

from chrono_mail.config import Action, Condition, Rule, RuleSet, load_rules, new_rule_id, save_rules
from chrono_mail.core import ConfigurationError, MessageRef
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.rule_engine import RuleReport, Subject, apply_rules, applies_to, rule_matches
from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "commands_rules.py 0.0.33"

# --- Version history ----------------------------------------------------
# v0.0.33: Initial rule commands

HEADER_PREFIX: str = "header."


def parse_condition(text: str) -> Condition:
    """'from:contains:news@example.org', 'header.List-Id:contains:lists.example', 'size:greater_than:100', 'attachment:is:yes'."""
    parts = text.split(":", 2)
    if len(parts) != 3:
        raise UsageError(f"A condition is written field:operator:value, for example subject:contains:invoice (got '{text}')")
    field_name, operator, value = parts[0].strip(), parts[1].strip(), parts[2].strip()
    name = ""
    if field_name.lower().startswith(HEADER_PREFIX):
        name, field_name = field_name[len(HEADER_PREFIX) :], "header"
    return Condition(field_name.lower(), operator.lower(), value, name)


def format_condition(condition: Condition) -> str:
    field_name = f"{HEADER_PREFIX}{condition.name}" if condition.field == "header" else condition.field
    return f"{field_name}:{condition.operator}:{condition.value}"


def parse_action(text: str) -> Action:
    """'move_to:Archive/2026', 'mark_read', 'stop'."""
    kind, _, value = text.partition(":")
    return Action(kind.strip().lower(), value.strip())


def format_action(action: Action) -> str:
    return f"{action.type}:{action.value}" if action.value else action.type


@dataclass(frozen=True, slots=True)
class RuleInfo:
    rule_id: str
    name: str
    enabled: bool
    match: str
    accounts: tuple[str, ...]
    conditions: tuple[str, ...]
    actions: tuple[str, ...]


def _info(rule: Rule) -> RuleInfo:
    return RuleInfo(rule.rule_id, rule.name, rule.enabled, rule.match, rule.accounts, tuple(format_condition(c) for c in rule.conditions), tuple(format_action(a) for a in rule.actions))


def _accounts(engine: Engine, identifiers: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(engine.resolve_account(identifier).account.account_id for identifier in identifiers)


@dataclass(frozen=True, slots=True)
class NoRuleParameters:
    pass


def rule_list(engine: Engine, parameters: NoRuleParameters) -> tuple[RuleInfo, ...]:
    return tuple(_info(rule) for rule in load_rules(engine.directories.rules_file).rules)


@dataclass(frozen=True, slots=True)
class RuleAddParameters:
    name: str = field(metadata=option("What the rule is called"))
    condition: tuple[str, ...] = field(metadata=option("A condition field:operator:value (repeat for more); fields from, to, cc, subject, body, header.<Name>, size, attachment"))
    action: tuple[str, ...] = field(metadata=option("An action (repeat for more): move_to:<folder>, mark_read, mark_unread, flag, unflag, delete, mark_junk, stop"))
    match: str = field(default="all", metadata=option("all: every condition must hold; any: one is enough"))
    account: tuple[str, ...] = field(default=(), metadata=option("Only for this account (repeat for more); default: every account"))
    disabled: bool = field(default=False, metadata=option("Add the rule switched off"))
    position: int | None = field(default=None, metadata=option("Where in the order of the rules (0 is first); default: last"))


def rule_add(engine: Engine, parameters: RuleAddParameters) -> RuleInfo:
    rule = Rule(
        new_rule_id(), parameters.name.strip(), tuple(parse_condition(text) for text in parameters.condition), tuple(parse_action(text) for text in parameters.action),
        parameters.match.strip().lower(), not parameters.disabled, _accounts(engine, parameters.account),
    )
    rules = load_rules(engine.directories.rules_file).with_rule(rule)
    if parameters.position is not None:
        rules = rules.moved(rule.rule_id, parameters.position)
    save_rules(engine.directories.rules_file, rules)
    return _info(rule)


@dataclass(frozen=True, slots=True)
class RuleEditParameters:
    rule: str = field(metadata=option("The rule id (as shown by rule.list)"))
    name: str | None = field(default=None, metadata=option("A new name"))
    condition: tuple[str, ...] = field(default=(), metadata=option("The conditions, replacing the old ones (repeat for more)"))
    action: tuple[str, ...] = field(default=(), metadata=option("The actions, replacing the old ones (repeat for more)"))
    match: str | None = field(default=None, metadata=option("all or any"))
    account: tuple[str, ...] = field(default=(), metadata=option("The accounts, replacing the old ones (repeat for more)"))
    every_account: bool = field(default=False, metadata=option("Make the rule apply to every account"))


def rule_edit(engine: Engine, parameters: RuleEditParameters) -> RuleInfo:
    rules = load_rules(engine.directories.rules_file)
    rule = rules.find(parameters.rule)
    changed = replace(
        rule,
        name=rule.name if parameters.name is None else parameters.name.strip(),
        conditions=tuple(parse_condition(text) for text in parameters.condition) or rule.conditions,
        actions=tuple(parse_action(text) for text in parameters.action) or rule.actions,
        match=rule.match if parameters.match is None else parameters.match.strip().lower(),
        accounts=() if parameters.every_account else (_accounts(engine, parameters.account) or rule.accounts),
    )
    save_rules(engine.directories.rules_file, rules.with_rule(changed))
    return _info(changed)


@dataclass(frozen=True, slots=True)
class RuleIdParameters:
    rule: str = field(metadata=option("The rule id (as shown by rule.list)"))


def rule_remove(engine: Engine, parameters: RuleIdParameters) -> RuleInfo:
    rules = load_rules(engine.directories.rules_file)
    removed = rules.find(parameters.rule)
    save_rules(engine.directories.rules_file, rules.without(parameters.rule))
    return _info(removed)


@dataclass(frozen=True, slots=True)
class RuleEnableParameters:
    rule: str = field(metadata=option("The rule id (as shown by rule.list)"))
    enabled: bool = field(default=True, metadata=option("true switches the rule on, false off"))


def rule_enable(engine: Engine, parameters: RuleEnableParameters) -> RuleInfo:
    rules = load_rules(engine.directories.rules_file).toggled(parameters.rule, parameters.enabled)
    save_rules(engine.directories.rules_file, rules)
    return _info(rules.find(parameters.rule))


@dataclass(frozen=True, slots=True)
class RuleMoveParameters:
    rule: str = field(metadata=option("The rule id (as shown by rule.list)"))
    position: int = field(metadata=option("The new place in the order (0 is first)"))


def rule_move(engine: Engine, parameters: RuleMoveParameters) -> tuple[RuleInfo, ...]:
    rules = load_rules(engine.directories.rules_file).moved(parameters.rule, parameters.position)
    save_rules(engine.directories.rules_file, rules)
    return tuple(_info(rule) for rule in rules.rules)


@dataclass(frozen=True, slots=True)
class RuleRunParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(default="Inbox", metadata=option("The folder whose messages the rules are applied to"))
    rule: str | None = field(default=None, metadata=option("Only this rule (default: all rules that are switched on)"))
    test: bool = field(default=False, metadata=option("Only count the messages that would be changed; change nothing"))


@dataclass(frozen=True, slots=True)
class RuleRunResult:
    examined: int
    matched: int
    actions: int
    failed: tuple[str, ...]


def rule_run(engine: Engine, parameters: RuleRunParameters) -> RuleRunResult:
    """Apply the rules to the messages already in a folder."""
    rule_set = load_rules(engine.directories.rules_file)
    chosen: tuple[Rule, ...] = (rule_set.find(parameters.rule),) if parameters.rule else rule_set.rules
    with open_mailbox(engine, parameters.account) as mailbox:
        folder = mailbox.folder(parameters.folder)
        references = [message.ref for message in mailbox.store.list_messages(folder)]
        if parameters.test:
            active = [rule for rule in chosen if applies_to(rule, mailbox.account_id) or (parameters.rule and not rule.enabled)]
            matched = 0
            failed: list[str] = []
            for reference in references:
                try:
                    subject = Subject.of(mailbox.store.read(reference, hint=mailbox.hint(reference.store_id)))
                except Exception as error:  # a message that cannot be read is reported, never fatal
                    failed.append(f"A message could not be read: {type(error).__name__}")
                    continue
                matched += any(rule_matches(rule, subject) for rule in active)
            return RuleRunResult(len(references), matched, 0, tuple(failed))
        if parameters.rule:
            chosen = tuple(replace_enabled(rule) for rule in chosen)
        report = apply_rules(mailbox, chosen, references)
    return RuleRunResult(report.examined, report.matched, report.actions, tuple(report.failed))


def replace_enabled(rule: Rule) -> Rule:
    """A rule named explicitly runs even when it is switched off."""
    return Rule(rule.rule_id, rule.name, rule.conditions, rule.actions, rule.match, True, rule.accounts)


def apply_to_new(engine: Engine, mailbox: Mailbox, references: list[MessageRef]) -> RuleReport:
    """The rules for messages that have just arrived. Never raises: rules must not stand in the way of mail."""
    report = RuleReport()
    try:
        rule_set = load_rules(engine.directories.rules_file)
        if not rule_set.rules or not references:
            return report
        return apply_rules(mailbox, rule_set.rules, references)
    except (ConfigurationError, OSError) as error:
        report.failed.append(f"The rules could not be applied: {error}")
        return report


def register(registry: CommandRegistry) -> None:
    registry.register("rule.list", "List the rules, in the order they are applied", NoRuleParameters, rule_list)
    registry.register("rule.add", "Add a rule: conditions on a message and what to do with it", RuleAddParameters, rule_add)
    registry.register("rule.edit", "Change a rule", RuleEditParameters, rule_edit)
    registry.register("rule.remove", "Remove a rule", RuleIdParameters, rule_remove, destructive=True)
    registry.register("rule.enable", "Switch a rule on or off", RuleEnableParameters, rule_enable)
    registry.register("rule.move", "Change the place of a rule in the order", RuleMoveParameters, rule_move)
    registry.register("rule.run", "Apply the rules to the messages already in a folder", RuleRunParameters, rule_run)
