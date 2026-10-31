"""The rules follow the folders they move mail to (D-302). A folder renamed or moved (here, on the server first, or followed from the
server) takes along every rule whose Move to folder names it or a folder inside it; a folder deleted switches those rules off. Only a
rule that reaches the place where the folder was is changed. A rule that also reaches another place where mail is kept, which has a
folder of that name too, is split: a copy for the accounts of this place follows the folder, the original keeps the other accounts
and its folder. The rules are this computer's own data: nothing goes to a server (D-193)."""

from collections.abc import Callable
from dataclasses import replace

from chrono_mail.config import Action, AccountSettings, AppConfig, Rule, RuleSet, load_rules, new_rule_id, save_rules
from chrono_mail.config.rules import MAXIMUM_RULES
from chrono_mail.core import AccountKind, ChronoMailError, ConfigurationError
from chrono_mail.engine.commands_accounts import shown_name
from chrono_mail.engine.session import Engine
from chrono_mail.store import folder_paths

# --- Version ------------------------------------------------------------
__version__ = "rule_folders.py 4.2.453"

# --- Version history ----------------------------------------------------
# v4.2.453: Initial version: rules follow a folder that is renamed or moved, and are switched off when it is deleted; a rule that
#           another place shares the folder with is split, the copy named "<name> (Copy n)" (the owner, D-302)

Path = tuple[str, ...]
NAME_LIMIT: int = 100  # the longest rule name (config.rules)


def _folded(path: Path) -> Path:
    return tuple(part.lower() for part in path)


def _rule_path(action: Action) -> Path:
    """The folder of a Move to action, as the rules find it (mailbox.parse_folder)."""
    return tuple(part.strip() for part in action.value.split("/"))


def _names(target: Path, path: Path, folders: set[Path]) -> bool:
    """Whether a rule's folder is this path or inside it, as the mailbox finds a rule's folder: by its exact path, else by the one
    path that differs only in letter case (Mailbox.folder). An exact folder of the rule's own spelling that is still there is its own."""
    if len(target) < len(path):
        return False
    if target[: len(path)] == path:
        return True
    return _folded(target[: len(path)]) == _folded(path) and target not in folders


def _uses(rule: Rule, path: Path, folders: set[Path]) -> bool:
    return any(action.type == "move_to" and _names(_rule_path(action), path, folders) for action in rule.actions)


def _reaches(rule: Rule, accounts: list[str]) -> list[str]:
    """The accounts of this list the rule works for (no accounts in a rule: every account)."""
    return list(accounts) if not rule.accounts else [account for account in accounts if account in rule.accounts]


def _has_folder(engine: Engine, settings: AccountSettings, path: Path) -> bool:
    """Whether the place of these settings has a folder at this path, found as a rule finds it. Unknown (unreadable): yes, so the
    rule is split and nothing that works now stops working."""
    try:
        paths = folder_paths(engine.store_directory(settings))
    except ChronoMailError:
        return True
    return path in paths or any(_folded(candidate) == _folded(path) for candidate in paths)


def _copy_name(name: str, taken: set[str]) -> str:
    """The name of a rule split off: "<name> (Copy n)" with the lowest n no rule has (the owner); a long name is cut and ends with "…"."""
    number = 1
    while True:
        suffix = f" (Copy {number})"
        base = name if len(name) + len(suffix) <= NAME_LIMIT else name[: NAME_LIMIT - len(suffix) - 1].rstrip() + "…"
        candidate = base + suffix
        if candidate not in taken:
            return candidate
        number += 1


def _place_name(config: AppConfig, settings: AccountSettings) -> str:
    """The place as the folder list names it: Local Folders, else the account's name or address."""
    return settings.account.display_name if settings.account.kind is AccountKind.LOCAL else shown_name(settings, config.accounts)


def _follow(
    engine: Engine, place: str, path: Path, folders: set[Path], change: Callable[[Rule], Rule | None], said: Callable[[Rule, Rule], str],
    split_said: Callable[[Rule, Rule, str], str], kept_said: Callable[[Rule], str],
) -> tuple[str, ...]:
    """Change every rule that reaches this place and names this folder; returns one line per rule for the status line. change gives
    the rule as it is to be, or None when nothing changes; a rule it cannot make (a new folder path too long for a rule) stays as it is,
    and so does one that would have to be split past the most rules there may be: kept_said says so. Never raises for one rule: the
    folder change itself has happened, and a mail check goes on (OR-3). The rules file is read, changed and saved under engine.rules_lock."""
    with engine.rules_lock:
        try:
            rules = load_rules(engine.directories.rules_file)
        except ChronoMailError:
            return ()  # a damaged rules file is reported when the rules are next used; the folder change itself has happened
        if not any(_uses(rule, path, folders) for rule in rules.rules):
            return ()
        try:
            config = engine.config()
        except ChronoMailError:
            return ()
        groups = engine.storage_groups(config)
        here = [item.account.account_id for item in groups.get(place, [])]
        mail_accounts = [item.account.account_id for items in groups.values() for item in items]
        settings_of = {item.account.account_id: item for item in config.accounts}
        taken = {rule.name for rule in rules.rules}
        result: list[Rule] = []
        notes: list[str] = []
        copies = 0
        for rule in rules.rules:
            reached_here = _reaches(rule, here)
            try:
                changed = change(rule) if reached_here and _uses(rule, path, folders) else None
            except ChronoMailError:
                result.append(rule)
                notes.append(kept_said(rule))
                continue
            if changed is None:
                result.append(rule)
                continue
            shared = [
                other for other, items in groups.items()
                if other != place and _reaches(rule, [item.account.account_id for item in items]) and other in settings_of and _has_folder(engine, settings_of[other], path)
            ]
            if not shared:
                result.append(changed)
                notes.append(said(rule, changed))
                continue
            if len(rules.rules) + copies >= MAXIMUM_RULES:  # a copy more would pass the most rules there may be
                result.append(rule)
                notes.append(f"{kept_said(rule)}: another account still has its folder {'/'.join(path)}, and there are already {MAXIMUM_RULES} rules, so it cannot be split")
                continue
            copy = replace(changed, rule_id=new_rule_id(), name=_copy_name(rule.name, taken), accounts=tuple(reached_here))
            taken.add(copy.name)
            copies += 1
            result.append(replace(rule, accounts=tuple(account for account in (rule.accounts or tuple(mail_accounts)) if account not in here)))
            result.append(copy)
            notes.append(split_said(rule, copy, _place_name(config, settings_of[place]) if place in settings_of else place))
        if not notes:
            return ()
        try:
            save_rules(engine.directories.rules_file, RuleSet(tuple(result)))
        except ChronoMailError:
            return ()  # the rules stay as they were; nothing is said that did not happen
        return tuple(notes)


def follow_rename(engine: Engine, place: str, old: Path, new: Path, folders: set[Path]) -> tuple[str, ...]:
    """A folder of this place was renamed or moved (with everything inside it): the rules that move mail to it, or into it, move it to
    its new name. folders: the folder paths of the place after the rename."""

    def moved(action: Action) -> Action:
        if any(part != part.strip() for part in new):  # a rule cannot name a folder with a space at an end of its name (the rule finds it stripped)
            raise ConfigurationError("A rule's folder cannot start or end with a space")
        target = _rule_path(action)
        if action.type != "move_to" or not _names(target, old, folders):
            return action
        return Action("move_to", "/".join(new + target[len(old):]))

    def change(rule: Rule) -> Rule | None:
        actions = tuple(moved(action) for action in rule.actions)
        return None if actions == rule.actions else replace(rule, actions=actions)

    def target(rule: Rule, changed: Rule) -> str:
        return next(after.value for before, after in zip(rule.actions, changed.actions) if before != after)

    return _follow(
        engine, place, old, folders, change,
        lambda rule, changed: f"Rule “{rule.name}” now moves mail to {target(rule, changed)}",
        lambda rule, copy, where: f"Rule “{copy.name}” is made for {where}: it moves mail to {target(rule, copy)}",
        lambda rule: f"Rule “{rule.name}” still moves mail to {'/'.join(old)}",
    )


def follow_delete(engine: Engine, place: str, path: Path, folders: set[Path]) -> tuple[str, ...]:
    """A folder of this place was deleted: the rules that move mail to it, or into it, are switched off (a rule that is off stays as it
    is). folders: the folder paths of the place after the delete."""
    shown = "/".join(path)
    return _follow(
        engine, place, path, folders,
        lambda rule: replace(rule, enabled=False) if rule.enabled else None,
        lambda rule, changed: f"Rule “{rule.name}” is switched off: its folder {shown} was deleted",
        lambda rule, copy, where: f"Rule “{copy.name}” is made for {where} and switched off: its folder {shown} was deleted there",
        lambda rule: f"Rule “{rule.name}” stays on",
    )
