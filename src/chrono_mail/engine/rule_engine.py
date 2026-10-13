from dataclasses import dataclass, field
from email.parser import BytesHeaderParser

from chrono_mail.config import Action, Condition, Rule
from chrono_mail.core import ChronoMailError, FolderRole, MessageFlag, MessageRef, StorageError
from chrono_mail.engine.mailbox import Mailbox, parse_folder
from chrono_mail.index import html_to_text
from chrono_mail.mime import Address, ParsedMessage, parse_message

# --- Version ------------------------------------------------------------
__version__ = "rule_engine.py 0.2.101"

# --- Version history ----------------------------------------------------
# v0.2.101: The folder of a rule is made safely (one folder when rules run at the same time)
# v0.0.49: Rules without conditions for one account
# v0.0.33: Initial rule matching and actions

MAXIMUM_BODY_CHARACTERS: int = 200_000
MAXIMUM_HEADER_BYTES: int = 256 * 1024


def _address_texts(address: Address) -> tuple[str, ...]:
    return (address.address, address.name, f"{address.name} <{address.address}>" if address.name else address.address)


def _holds(operator: str, candidate: str, wanted: str) -> bool:
    candidate, wanted = candidate.casefold(), wanted.casefold()
    if operator in ("contains", "not_contains"):
        return wanted in candidate
    if operator in ("equals", "not_equals"):
        return candidate == wanted
    if operator == "starts_with":
        return candidate.startswith(wanted)
    if operator == "ends_with":
        return candidate.endswith(wanted)
    return False


def _any_holds(operator: str, candidates: tuple[str, ...], wanted: str) -> bool:
    """The negative operators hold when the positive one holds for none of the candidates."""
    positive = {"not_contains": "contains", "not_equals": "equals"}.get(operator, operator)
    found = any(_holds(positive, candidate, wanted) for candidate in candidates)
    return not found if operator.startswith("not_") else found


def header_values(raw: bytes, name: str) -> tuple[str, ...]:
    """Every value of a header of the message (a header may occur several times). Never raises."""
    end = raw.find(b"\r\n\r\n")
    end = raw.find(b"\n\n") if end == -1 else end
    head = raw[: end if end != -1 else MAXIMUM_HEADER_BYTES][:MAXIMUM_HEADER_BYTES]
    try:
        message = BytesHeaderParser().parsebytes(head)
        return tuple(str(value).encode("utf-8", "surrogateescape").decode("utf-8", "replace") for value in message.get_all(name, []))
    except Exception:  # a hostile header must never stop the mail from arriving
        return ()


@dataclass(frozen=True, slots=True)
class Subject:
    """A message as the rules see it."""

    raw: bytes
    parsed: ParsedMessage

    @staticmethod
    def of(raw: bytes) -> "Subject":
        return Subject(raw, parse_message(raw))

    def body(self) -> str:
        text = self.parsed.text_body or (html_to_text(self.parsed.html_body) if self.parsed.html_body else "")
        return text[:MAXIMUM_BODY_CHARACTERS]


def condition_holds(condition: Condition, subject: Subject) -> bool:
    field_name, operator, value = condition.field, condition.operator, condition.value
    parsed = subject.parsed
    if field_name in ("from", "to", "cc"):
        addresses = {"from": parsed.from_addresses, "to": parsed.to_addresses, "cc": parsed.cc_addresses}[field_name]
        return _any_holds(operator, tuple(text for address in addresses for text in _address_texts(address)), value) if addresses else operator.startswith("not_")
    if field_name == "subject":
        return _any_holds(operator, (parsed.subject,), value)
    if field_name == "body":
        return _any_holds(operator, (subject.body(),), value)
    if field_name == "header":
        values = header_values(subject.raw, condition.name)
        return _any_holds(operator, values, value) if values else operator.startswith("not_")
    if field_name == "size":
        kilobytes = int(value) * 1024
        return len(subject.raw) > kilobytes if operator == "greater_than" else len(subject.raw) < kilobytes
    if field_name == "attachment":
        has = any(not attachment.inline for attachment in parsed.attachments)
        return has == (value == "yes")
    return False


def rule_matches(rule: Rule, subject: Subject) -> bool:
    if not rule.conditions:
        return True  # a rule for an account alone: all the mail of that account
    results = (condition_holds(condition, subject) for condition in rule.conditions)
    return all(results) if rule.match == "all" else any(results)


def applies_to(rule: Rule, account_id: str) -> bool:
    return rule.enabled and (not rule.accounts or account_id in rule.accounts)


@dataclass
class RuleReport:
    examined: int = 0
    matched: int = 0
    actions: int = 0
    failed: list[str] = field(default_factory=list)


def _folder_for(mailbox: Mailbox, path: str) -> tuple[str, ...]:
    """The folder named in a rule; made when it does not exist yet."""
    try:
        return mailbox.folder(path)
    except ChronoMailError:
        folder = parse_folder(path)
        mailbox.store.ensure_folder(folder)
        return folder


def execute_action(mailbox: Mailbox, reference: MessageRef, action: Action) -> MessageRef:
    """One action on one message. Returns where the message is afterwards."""
    if action.type == "move_to":
        target = _folder_for(mailbox, action.value)
        return reference if target == reference.folder_path else mailbox.move(reference, target)
    if action.type == "delete":
        mailbox.delete(reference, permanent=False)  # to Trash: never for good
        try:
            trash = mailbox.role_folder(FolderRole.TRASH)
        except StorageError:
            return reference
        return MessageRef(reference.account_id, trash, reference.store_id) if trash != reference.folder_path else reference
    if action.type == "mark_junk":
        junk = mailbox.role_folder(FolderRole.JUNK)
        return reference if junk == reference.folder_path else mailbox.move(reference, junk)
    flags = set(mailbox.current_flags(reference))
    change = {"mark_read": (MessageFlag.SEEN, True), "mark_unread": (MessageFlag.SEEN, False), "flag": (MessageFlag.FLAGGED, True), "unflag": (MessageFlag.FLAGGED, False)}.get(action.type)
    if change is not None:
        if change[1]:
            flags.add(change[0])
        else:
            flags.discard(change[0])
        mailbox.set_flags(reference, frozenset(flags))
    return reference


def apply_rules(mailbox: Mailbox, rules: tuple[Rule, ...], references: list[MessageRef], report: RuleReport | None = None) -> RuleReport:
    """Run the rules (in order) on these messages. The first rule that says stop ends the rules for that message.
    A failure with one message or one action is reported and the rest goes on."""
    outcome = report if report is not None else RuleReport()
    account_id = mailbox.account_id
    active = tuple(rule for rule in rules if applies_to(rule, account_id))
    if not active:
        return outcome
    for original in references:
        outcome.examined += 1
        try:
            subject = Subject.of(mailbox.store.read(original, hint=mailbox.hint(original.store_id)))
        except ChronoMailError as error:
            outcome.failed.append(f"A message could not be read for the rules: {error}")
            continue
        reference, matched = original, False
        for rule in active:
            if not rule_matches(rule, subject):
                continue
            matched = True
            stop = False
            for action in rule.actions:
                if action.type == "stop":
                    stop = True
                    break
                try:
                    reference = execute_action(mailbox, reference, action)
                    outcome.actions += 1
                except ChronoMailError as error:
                    outcome.failed.append(f"Rule '{rule.name}': {action.type} failed: {error}")
            if stop:
                break
        outcome.matched += 1 if matched else 0
    return outcome
