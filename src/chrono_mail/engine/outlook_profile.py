"""The export of accounts and rules to Outlook: the accounts as a profile file (.prf, Outlook's documented format, which Outlook reads with
outlook.exe /importprf), never with their passwords; the rules made in a running Outlook (Windows only, never started), each whole or not
at all, and every rule Outlook cannot hold named with why."""

from collections.abc import Sequence
from pathlib import Path

from chrono_mail.config import AccountSettings, Pop3DeletePolicy, load_rules
from chrono_mail.core import AccountKind, ConnectionSecurity
from chrono_mail.engine import outlook_windows
from chrono_mail.engine.outlook_rules import OutlookRuleSpec, rule_to_outlook
from chrono_mail.engine.outlook_windows import ProfileAccount, Security
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import Engine
from chrono_mail.store.atomic import replace_file_atomically

# --- Version ------------------------------------------------------------
__version__ = "outlook_profile.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial version: accounts to an Outlook profile file (.prf, no passwords), rules into a running Outlook

PROFILE_NAME: str = "Chrono Mail"
OPEN_OUTLOOK_EXPORT: str = "Open Outlook to export the rules to it"
_SECURITY: dict[ConnectionSecurity, Security] = {ConnectionSecurity.TLS: "tls", ConnectionSecurity.STARTTLS: "starttls", ConnectionSecurity.NONE: "none"}
_SSL_VALUE: dict[Security, int] = {"none": 0, "tls": 1, "starttls": 2}  # Use SSL / Secure Connection: none, SSL/TLS, STARTTLS
_MAPPING: str = """[I_Mail]
AccountType=POP3
;--- POP3 and IMAP account settings ---
AccountName=PT_UNICODE,0x0002
DisplayName=PT_UNICODE,0x000B
EmailAddress=PT_UNICODE,0x000C
POP3Server=PT_UNICODE,0x0100
POP3UserName=PT_UNICODE,0x0101
POP3UseSPA=PT_LONG,0x0108
LeaveOnServer=PT_LONG,0x1000
POP3Port=PT_LONG,0x0104
POP3UseSSL=PT_LONG,0x0105
IMAPServer=PT_UNICODE,0x0100
IMAPUserName=PT_UNICODE,0x0101
IMAPUseSPA=PT_LONG,0x0108
IMAPPort=PT_LONG,0x0104
IMAPUseSSL=PT_LONG,0x0105
;--- SMTP settings ---
SMTPServer=PT_UNICODE,0x0200
SMTPUseAuth=PT_LONG,0x0203
SMTPAuthMethod=PT_LONG,0x0208
SMTPUserName=PT_UNICODE,0x0204
SMTPUseSPA=PT_LONG,0x0207
SMTPPort=PT_LONG,0x0201
SMTPSecureConnection=PT_LONG,0x020A
ServerTimeOut=PT_LONG,0x0209
ConnectionType=PT_LONG,0x000F
"""


def _value(text: str) -> str:
    """One line's value: no line break can end the line early."""
    return " ".join(text.split())


def _account_section(number: int, account: ProfileAccount) -> list[str]:
    prefix = "POP3" if account.kind == "pop3" else "IMAP"
    lines = [
        f"[Account{number}]", "UniqueService=No", f"AccountType={prefix}", f"AccountName={_value(account.name or account.email)}",
        f"DisplayName={_value(account.display_name)}", f"EmailAddress={_value(account.email)}", f"{prefix}Server={_value(account.incoming_host)}",
        f"{prefix}UserName={_value(account.incoming_user)}", f"{prefix}UseSPA=0", f"{prefix}Port={account.incoming_port}",
        f"{prefix}UseSSL={_SSL_VALUE[account.incoming_security]}",
    ]
    if account.kind == "pop3":
        lines.append(f"LeaveOnServer=0x{(account.leave_days << 16) | (1 if account.leave_on_server else 0):08X}")
    lines += [
        f"SMTPServer={_value(account.outgoing_host)}", f"SMTPUseAuth={1 if account.outgoing_auth else 0}",
        f"SMTPAuthMethod={2 if account.outgoing_user else 0}", f"SMTPUserName={_value(account.outgoing_user)}", "SMTPUseSPA=0",
        f"SMTPPort={account.outgoing_port}", f"SMTPSecureConnection={_SSL_VALUE[account.outgoing_security]}", "ServerTimeOut=60", "ConnectionType=0", "",
    ]
    return lines


def profile_text(accounts: Sequence[ProfileAccount], profile_name: str = PROFILE_NAME) -> str:
    """An Outlook profile file for these POP3 and IMAP accounts; never their passwords (Outlook asks for them once)."""
    lines = [
        "; An Outlook profile file made by Chrono Mail: the accounts without their passwords.", "", "[General]", "Custom=1",
        f"ProfileName={_value(profile_name)}", "DefaultProfile=No", "OverwriteProfile=Append", "ModifyDefaultProfileIfPresent=FALSE", "",
        "[Service List]", "ServiceX=Microsoft Outlook Client", "", "[ServiceX]", "", "[ServiceEXT]", "", "[Internet Account List]",
    ]
    lines += [f"Account{number}=I_Mail" for number in range(1, len(accounts) + 1)]
    lines.append("")
    for number, account in enumerate(accounts, start=1):
        lines += _account_section(number, account)
    lines.append(_MAPPING)
    return "\r\n".join(lines)


def profile_account(settings: AccountSettings) -> ProfileAccount | None:
    """A POP3 or IMAP account here as a profile account (None for Local Folders and calendar-only accounts)."""
    account = settings.account
    if account.kind not in (AccountKind.POP3, AccountKind.IMAP):
        return None
    pop3 = account.kind is AccountKind.POP3
    leave = not pop3 or settings.pop3.policy is not Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD
    days = settings.pop3.days if pop3 and settings.pop3.policy is Pop3DeletePolicy.DELETE_AFTER_DAYS else 0
    different = not settings.outgoing_same_login and account.outgoing.username != account.incoming.username
    return ProfileAccount(
        "pop3" if pop3 else "imap", settings.account_name or account.email_address, account.email_address, account.display_name, account.incoming.host,
        account.incoming.port, _SECURITY[account.incoming.security], account.incoming.username, account.outgoing.host, account.outgoing.port,
        _SECURITY[account.outgoing.security], account.outgoing.username if different else "", True, leave, days,
    )


def export_accounts_prf(engine: Engine, account_ids: Sequence[str], path: Path) -> list[str]:
    """Write these accounts as an Outlook profile file, in one atomic step. Returns notes (what was left out, what Outlook asks)."""
    notes: list[str] = []
    accounts: list[ProfileAccount] = []
    config = engine.config()
    for account_id in account_ids:
        settings = config.find(account_id)
        found = profile_account(settings)
        if found is None:
            notes.append(f"{settings.account.email_address or settings.account_name or 'Local Folders'}: only POP3 and IMAP accounts go into the profile file")
            continue
        accounts.append(found)
        if settings.pop3.policy in (Pop3DeletePolicy.DELETE_AFTER_DAYS, Pop3DeletePolicy.DELETE_WHEN_DELETED_LOCALLY) and found.kind == "pop3":
            notes.append(f"{found.email}: copies stay on the server; set when Outlook removes them in its account settings")
    if not accounts:
        raise UsageError("Choose a POP3 or IMAP account to export")
    try:
        replace_file_atomically(path, profile_text(accounts).encode("utf-16"), True)
    except OSError as error:
        raise UsageError(f"The profile file could not be written: {error.strerror}") from None
    notes.append("Outlook asks for each account's password once (passwords are never written to the file)")
    return notes


def export_rules_to_outlook(engine: Engine, rule_ids: Sequence[str] | None) -> tuple[int, list[str]]:
    """Make the rules (None: all) in Outlook's default store; Windows, while Outlook runs. Returns how many were made and notes naming
    each rule that was not, with why."""
    outlook = outlook_windows.machine().outlook()
    if outlook is None:
        raise UsageError(OPEN_OUTLOOK_EXPORT if outlook_windows.read_profile() is not None else "Only on a Windows computer with Outlook open")
    rule_set = load_rules(engine.directories.rules_file)
    chosen = rule_set.rules if rule_ids is None else tuple(rule_set.find(rule_id) for rule_id in rule_ids)
    addresses = {settings.account.account_id: settings.account.email_address for settings in engine.config().accounts}
    existing = outlook_windows.outlook_rule_names(outlook)
    notes: list[str] = []
    specs: list[OutlookRuleSpec] = []
    for rule in chosen:
        if rule.name.casefold() in existing:
            notes.append(f"{rule.name}: Outlook has a rule of this name already")
            continue
        found = rule_to_outlook(rule, addresses.get)
        if isinstance(found, str):
            notes.append(f"{rule.name}: {found}")
        else:
            specs.append(found)
    refused = outlook_windows.make_rules(outlook, specs) if specs else []
    notes += refused
    return len(specs) - len(refused), notes
