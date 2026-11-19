"""The import of Outlook's accounts, rules and signatures on the Windows computer where Outlook keeps them (import.outlook calls it).

Accounts are made as the other import makes them: their passwords into the credential store (never asked again, OR-19); an account that
is here already (the same kind, server and user name) is skipped. Rules are taken whole or not at all, and the junk list's blocked senders
become rules that mark their mail as junk. Every signature comes into Settings > Templates and Signatures as it is, and each account here
gets its New and Reply-Forward Signature as its choice when it has none yet (D-361)."""

from dataclasses import dataclass, replace

from chrono_mail.config import Rule, load_rules, save_rules
from chrono_mail.core import AccountKind, ChronoMailError, ConfigurationError
from chrono_mail.engine import outlook_windows
from chrono_mail.engine.commands_accounts import AccountAddParameters, Policy, account_add
from chrono_mail.engine.commands_signatures import add_texts, with_pictures_inside
from chrono_mail.engine.outlook_rules import blocked_rules, junk_senders, rule_from_outlook
from chrono_mail.engine.outlook_windows import Profile, ProfileAccount
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "outlook_settings.py 4.6.632"

# --- Version history ----------------------------------------------------
# v4.6.632: Every signature into Settings > Templates and Signatures as it is, its pictures inside; each account gets its New and
#           Reply-Forward Signature as its choice for new mail, replies and forwards; the old signature field is no longer written (D-361)
# v4.2.453: the imported rules are added under engine.rules_lock (D-302)
# v4.2.420: the rules that could not be imported are collected in a list (Pyrefly could not settle the tuple's type in the loop)
# v4.2.386: Initial version: accounts with their passwords, rules with the junk list's blocked senders, and signatures from Outlook's profile

WINDOWS_ONLY: str = "Only on the Windows computer where Outlook keeps these accounts"
OPEN_OUTLOOK: str = "Open Outlook to import its rules"


@dataclass(frozen=True, slots=True)
class ProfileScan:
    available: bool = False  # accounts and signatures can be imported
    reason: str = WINDOWS_ONLY  # why not
    rules_available: bool = False
    rules_reason: str = WINDOWS_ONLY
    accounts: tuple[str, ...] = ()  # address (kind) of each account of the profile
    accounts_here: int = 0
    signatures: int = 0
    rules: int = 0  # rules that can be imported (blocked senders included)
    rules_not_imported: tuple[str, ...] = ()  # name: why
    blocked: int = 0
    safe: int = 0
    files: tuple[str, ...] = ()  # the profile's data files
    coming: tuple[str, ...] = ()  # the addresses of the profile's POP3 accounts (their data file goes into the account)


@dataclass(frozen=True, slots=True)
class ProfileImport:
    accounts_added: int = 0
    accounts_existing: int = 0
    accounts_without_password: tuple[str, ...] = ()
    rules_added: int = 0
    rules_existing: int = 0
    rules_not_imported: tuple[str, ...] = ()
    signatures_added: int = 0
    signatures_existing: int = 0  # a signature of a name already in Settings > Templates and Signatures, left as it is
    signatures_given: int = 0  # the accounts that got a signature chosen
    notes: tuple[str, ...] = ()


def _same_account(engine: Engine, source: ProfileAccount) -> str | None:
    kind = AccountKind.POP3 if source.kind == "pop3" else AccountKind.IMAP
    for settings in engine.config().accounts:
        account = settings.account
        if account.kind is kind and account.incoming.username.lower() == source.incoming_user.lower() and account.incoming.host.lower() == source.incoming_host.lower():
            return account.account_id
    return None


def _account_of(engine: Engine, address: str) -> str | None:
    return next((settings.account.account_id for settings in engine.config().accounts if settings.account.email_address.lower() == address.strip().lower()), None)


@dataclass(frozen=True, slots=True)
class _Rules:
    rules: tuple[Rule, ...]
    not_imported: tuple[str, ...]
    blocked: int
    safe: int


def _read_rules(engine: Engine, outlook: object) -> _Rules:
    rules: list[Rule] = []
    not_imported: list[str] = []
    for source in outlook_windows.outlook_rules(outlook):
        found = rule_from_outlook(source, lambda address: _account_of(engine, address))
        if isinstance(found, Rule):
            rules.append(found)
        else:
            not_imported.append(f"{source.name}: {found}")
    blocked: tuple[str, ...] = ()
    safe: tuple[str, ...] = ()
    data = outlook_windows.junk_list(outlook)
    if data is not None:
        try:
            blocked, safe = junk_senders(data)
        except ValueError as error:
            not_imported.append(f"Junk list: {error}")
    rules.extend(blocked_rules(blocked))
    return _Rules(tuple(rules), tuple(not_imported), len(blocked), len(safe))


def scan_profile(engine: Engine) -> ProfileScan:
    """What Outlook's profile on this computer holds; changes nothing."""
    machine = outlook_windows.machine()
    profile = outlook_windows.read_profile(machine)
    if profile is None:
        return ProfileScan()
    outlook = machine.outlook()
    rules = _read_rules(engine, outlook) if outlook is not None else None
    return ProfileScan(
        True, "", rules is not None, "" if rules is not None else OPEN_OUTLOOK, tuple(f"{account.email} ({account.kind.upper()})" for account in profile.accounts),
        sum(1 for account in profile.accounts if _same_account(engine, account) is not None), len(profile.signatures),
        len(rules.rules) if rules else 0, rules.not_imported if rules else (), rules.blocked if rules else 0, rules.safe if rules else 0,
        tuple(str(path) for path in profile.files), tuple(account.email for account in profile.accounts if account.kind == "pop3"),
    )


def _policy(source: ProfileAccount) -> tuple[Policy, int]:
    """Only what is sure: a copy stays on the server, or not. Outlook's later removal is not taken over (the copy is kept)."""
    return ("keep", 30) if source.leave_on_server else ("delete_after_download", 30)


def _add_account(engine: Engine, source: ProfileAccount, notes: list[str]) -> str | None:
    policy, days = _policy(source)
    try:
        result = account_add(engine, AccountAddParameters(
            display_name=source.display_name or source.email, email_address=source.email, username=source.incoming_user, password=source.password or "",
            incoming_host=source.incoming_host, outgoing_host=source.outgoing_host, incoming_port=source.incoming_port, incoming_security=source.incoming_security,
            outgoing_port=source.outgoing_port, outgoing_security=source.outgoing_security, outgoing_username=source.outgoing_user or None,
            outgoing_password=source.outgoing_password if source.outgoing_user else None, delete_policy=policy, delete_days=days, kind=source.kind,
            account_name=source.name if source.name != source.email else "",
        ))
    except ChronoMailError as error:
        notes.append(f"{source.email}: the account was not made (Import again makes it): {error}")
        return None
    if source.kind == "pop3":  # its mail comes from its data file: the first check records what is here instead of downloading it again
        engine.change_config(lambda config: config.with_account(replace(config.find(result.account_id), skip_known_mail=True)))
        if source.leave_on_server and source.leave_other_bits:
            notes.append(f"{source.email}: copies stay on the server; set when they are removed in Edit account")
    return result.account_id


def _import_signatures(engine: Engine, profile: Profile, here: dict[str, str], notes: list[str]) -> tuple[int, int, int]:
    """Every signature into Settings > Templates and Signatures as it is, its pictures inside it; a name already there is skipped and
    left as it is, and the account then gets that one. Each account here gets its New Signature for new mail and its Reply-Forward
    Signature for replies and forwards, unless it has one chosen already (D-361). Added, already there, accounts given them."""
    notes.extend(f"Not imported: {problem}" for problem in profile.signatures_not_imported)
    if not profile.signatures:
        return 0, 0, 0
    try:
        found = add_texts(engine, [(signature.name, with_pictures_inside(signature.html, signature.folder)) for signature in profile.signatures])
    except ConfigurationError as error:
        notes.append(f"Signatures: {error}")
        return 0, 0, 0
    notes.extend(f"Not imported: {problem}" for problem in found.problems)
    given = 0
    kept: list[str] = []
    for source in profile.accounts:
        account_id = here.get(source.email)
        if account_id is None:
            continue
        new_id = found.ids.get(" ".join(source.signature_new.split()).casefold(), "") if source.signature_new else ""
        reply_id = found.ids.get(" ".join(source.signature_reply.split()).casefold(), "") if source.signature_reply else ""
        old = engine.config().find(account_id).texts
        chosen = {"signature_new": new_id, "signature_reply": reply_id, "signature_forward": reply_id}
        current = {"signature_new": old.signature_new, "signature_reply": old.signature_reply, "signature_forward": old.signature_forward}
        changed = {name: item_id for name, item_id in chosen.items() if item_id and not current[name]}
        if any(item_id and current[name] and current[name] != item_id for name, item_id in chosen.items()):
            kept.append(source.email)
        if changed:
            texts = replace(
                old, signature_new=changed.get("signature_new", old.signature_new), signature_reply=changed.get("signature_reply", old.signature_reply),
                signature_forward=changed.get("signature_forward", old.signature_forward),
            )
            engine.change_config(lambda config, account=account_id, new=texts: config.with_account(replace(config.find(account), texts=new)))
            given += 1
    if kept:
        notes.append(f"These accounts keep the signatures already chosen for them: {', '.join(dict.fromkeys(kept))}")
    return found.added, found.existing, given


def import_profile(engine: Engine, accounts: bool, rules: bool, signatures: bool) -> ProfileImport:
    machine = outlook_windows.machine()
    profile: Profile | None = outlook_windows.read_profile(machine)
    if profile is None:
        raise UsageError(WINDOWS_ONLY)
    outlook = machine.outlook() if rules else None
    if rules and outlook is None:
        raise UsageError(OPEN_OUTLOOK)
    notes: list[str] = []
    added = existing = 0
    without_password: list[str] = []
    here: dict[str, str] = {}  # email -> account id
    for source in profile.accounts:
        same = _same_account(engine, source)
        if same is not None:
            existing += 1
            here[source.email] = same
        elif accounts:
            made = _add_account(engine, source, notes)
            if made is not None:
                added += 1
                here[source.email] = made
                if source.password is None:
                    without_password.append(source.email)
    signatures_added = signatures_existing = given = 0
    if signatures:
        signatures_added, signatures_existing, given = _import_signatures(engine, profile, here, notes)
    rules_added = rules_existing = 0
    not_imported: list[str] = []
    if outlook is not None:
        found = _read_rules(engine, outlook)
        not_imported.extend(found.not_imported)
        with engine.rules_lock:  # one read-change-save at a time: a folder rename may move rules meanwhile (D-302)
            rule_set = load_rules(engine.directories.rules_file)
            for rule in found.rules:
                if any((known.name, known.conditions, known.actions, known.match, known.accounts) == (rule.name, rule.conditions, rule.actions, rule.match, rule.accounts) for known in rule_set.rules):
                    rules_existing += 1
                    continue
                try:
                    rule_set = rule_set.with_rule(rule)
                except ChronoMailError as error:
                    not_imported.append(f"{rule.name}: {error}")
                    continue
                rules_added += 1
            save_rules(engine.directories.rules_file, rule_set)
        if found.safe:
            notes.append(f"Safe senders: {found.safe:,}; Chrono Mail has no list like it, so they are not imported")
    return ProfileImport(
        added, existing, tuple(without_password), rules_added, rules_existing, tuple(not_imported), signatures_added, signatures_existing, given, tuple(notes),
    )
