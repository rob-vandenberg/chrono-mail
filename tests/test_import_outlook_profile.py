from pathlib import Path

import pytest

from chrono_mail.config import ServerRole, load_rules
from chrono_mail.config.rules import Action, Condition, Rule, RuleSet, save_rules
from chrono_mail.core import AccountKind, FolderRole
from chrono_mail.engine import UsageError, outlook_profile, outlook_windows
from chrono_mail.engine.commands_outlook import ImportResult, ScanResult
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.outlook_rules import OutlookPart, OutlookRule, OutlookRuleSpec
from chrono_mail.engine.outlook_windows import Machine, RegistryValue
from tests.enginefactory import make_engine
from tests.test_engine_commands import REGISTRY
from tests.test_import_outlook import FakeFile, data_file, files  # noqa: F401 - files is the fixture of the data files the reader knows

# --- Version ------------------------------------------------------------
__version__ = "test_import_outlook_profile.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial tests: accounts, passwords, signatures, rules and blocked senders from a made-up profile; the export to a profile file
#           and of rules into a made-up Outlook

_ = files


def secret(text: str) -> bytes:
    return b"\x02" + b"BLOB:" + text.encode("utf-16-le") + b"\x00\x00"


def entries() -> list[dict[str, RegistryValue]]:
    pop3: dict[str, RegistryValue] = {
        "Account Name": "Anna at home", "Display Name": "Anna", "Email": "anna@example.com", "POP3 Server": "pop.example.com", "POP3 User": "anna@example.com",
        "POP3 Port": 995, "POP3 Use SSL": 1, "SMTP Server": "smtp.example.com", "SMTP Port": 465, "SMTP Use SSL": 1, "SMTP Secure Connection": 1,
        "SMTP Use Auth": 1, "Leave on Server": 0x000E0001, "POP3 Password": secret("Pop-Secret1"), "New Signature": "Home (anna@example.com)",
    }
    imap: dict[str, RegistryValue] = {
        "Account Name": "bert@example.org", "Display Name": "Bert", "Email": "bert@example.org", "IMAP Server": "imap.example.org", "IMAP User": "bert",
        "IMAP Port": 993, "IMAP Use SSL": 1, "SMTP Server": "smtp.example.org", "SMTP Port": 587, "SMTP Secure Connection": 2, "SMTP Use Auth": 1,
        "IMAP Password": secret("Imap-Secret2"),
    }
    exchange: dict[str, RegistryValue] = {"Account Name": "Work", "Service Name": "MSEMS"}
    return [exchange, pop3, imap]


def junk_blob() -> bytes:
    def content(text: str) -> bytes:
        return b"\x03" + b"\x01\x00\x01\x00" + (0x0C1F001F).to_bytes(4, "little") + (0x0C1F001F).to_bytes(4, "little") + text.encode("utf-16-le") + b"\x00\x00"

    blocked = b"\x01" + (2).to_bytes(4, "little") + content("spam@example.net") + content("example.biz")
    safe = b"\x02" + content("friend@example.com")
    return b"\x00\x00" + b"\x00" + (2).to_bytes(4, "little") + blocked + safe


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    signatures = tmp_path / "Signatures"
    signatures.mkdir()
    (signatures / "Home (anna@example.com).txt").write_bytes("Greetings,\r\nAnna".encode("utf-16"))
    folder = tmp_path / "Outlook Files"
    folder.mkdir()
    made = Machine(lambda: ("Outlook", entries()), lambda blob: blob[5:] if blob.startswith(b"BLOB:") else None, lambda: object(), signatures, (folder,))
    monkeypatch.setattr(outlook_windows, "machine", lambda: made)
    rules = (
        OutlookRule("Newsletters", conditions=(OutlookPart("Subject", ("newsletter",)),), exceptions=(OutlookPart("SenderAddress", ("boss@example.com",)),),
                    actions=(OutlookPart("MoveToFolder", ("Inbox/News",)), OutlookPart("Stop"))),
        OutlookRule("Play a sound", conditions=(OutlookPart("Subject", ("bell",)),), actions=(OutlookPart("PlaySound"),)),
    )
    def outlook_rules(outlook: object) -> tuple[OutlookRule, ...]:
        return rules

    def junk_list(outlook: object) -> bytes:
        return junk_blob()

    monkeypatch.setattr(outlook_windows, "outlook_rules", outlook_rules)
    monkeypatch.setattr(outlook_windows, "junk_list", junk_list)
    return folder


def test_accounts_passwords_signatures_rules_and_blocked_senders_come_from_the_profile(tmp_path: Path, machine: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path)
    pst = data_file(files, machine, "anna.pst", "anna@example.com")
    scanned = REGISTRY.run(engine, "import.outlook-scan", {"profile": True})
    assert isinstance(scanned, ScanResult)
    assert scanned.profile.available and scanned.profile.rules_available and scanned.profile.files == (str(pst),)
    assert scanned.profile.accounts == ("anna@example.com (POP3)", "bert@example.org (IMAP)") and scanned.profile.blocked == 2 and scanned.profile.safe == 1
    assert scanned.files[0].place.startswith("the account anna@example.com when Accounts is chosen")

    result = REGISTRY.run(engine, "import.outlook", {"files": (str(pst),), "accounts": True, "rules": True, "signatures": True})
    assert isinstance(result, ImportResult)
    profile = result.profile
    assert (profile.accounts_added, profile.signatures, profile.rules_added) == (2, 1, 2)  # Newsletters and one rule of blocked senders
    assert profile.rules_not_imported == ("Play a sound: Chrono Mail has no action like 'PlaySound'",)
    accounts = {settings.account.email_address: settings for settings in engine.config().accounts}
    anna, bert = accounts["anna@example.com"], accounts["bert@example.org"]
    assert anna.account.kind is AccountKind.POP3 and anna.account.incoming.port == 995 and anna.skip_known_mail and anna.signature == "Greetings,\nAnna"
    assert bert.account.kind is AccountKind.IMAP and bert.account.outgoing.security.value == "starttls"
    assert engine.passwords.get(anna.account.account_id, ServerRole.INCOMING) == "Pop-Secret1"
    assert engine.passwords.get(bert.account.account_id, ServerRole.INCOMING) == "Imap-Secret2"
    rules = {rule.name: rule for rule in load_rules(engine.directories.rules_file).rules}
    assert rules["Newsletters"].conditions == (Condition("subject", "contains", "newsletter"), Condition("from", "not_contains", "boss@example.com"))
    assert rules["Blocked senders"].conditions == (Condition("from", "contains", "spam@example.net"), Condition("from", "contains", "example.biz"))
    with open_mailbox(engine, anna.account.account_id) as mailbox:  # the data file named after the account went into it
        assert mailbox.index.count(mailbox.role_folder(FolderRole.INBOX)) > 0


def test_the_accounts_go_into_a_profile_file_without_passwords(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    added = REGISTRY.run(
        engine, "account.add", {"display_name": "Anna", "email_address": "anna@example.com", "username": "anna", "password": "Never-In-File1",
                                "incoming_host": "pop.example.com", "outgoing_host": "smtp.example.com"},
    )
    path = tmp_path / "accounts.prf"
    notes = outlook_profile.export_accounts_prf(engine, (getattr(added, "account_id"),), path)
    text = path.read_text("utf-16")
    assert "[Internet Account List]" in text and "AccountType=POP3" in text and "POP3Server=pop.example.com" in text and "POP3UseSSL=1" in text
    assert "Never-In-File1" not in text and notes


def test_rules_go_into_a_running_outlook_and_what_it_cannot_hold_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path)
    save_rules(engine.directories.rules_file, RuleSet((
        Rule("0000000000000001", "Invoices", (Condition("subject", "contains", "invoice"),), (Action("move_to", "Inbox/Invoices"),)),
        Rule("0000000000000002", "Read it", (Condition("from", "contains", "news@example.com"),), (Action("mark_read"),)),
    )))
    made: list[OutlookRuleSpec] = []
    monkeypatch.setattr(outlook_windows, "machine", lambda: Machine(lambda: None, lambda blob: None, lambda: None, None, ()))
    with pytest.raises(UsageError):
        outlook_profile.export_rules_to_outlook(engine, None)
    monkeypatch.setattr(outlook_windows, "machine", lambda: Machine(lambda: ("Outlook", []), lambda blob: None, lambda: object(), None, ()))
    def names(outlook: object) -> set[str]:
        return set()

    def make_rules(outlook: object, specs: list[OutlookRuleSpec]) -> list[str]:
        made.extend(specs)
        return []

    monkeypatch.setattr(outlook_windows, "outlook_rule_names", names)
    monkeypatch.setattr(outlook_windows, "make_rules", make_rules)
    count, notes = outlook_profile.export_rules_to_outlook(engine, None)
    assert count == 1 and [spec.name for spec in made] == ["Invoices"] and made[0].actions == (OutlookPart("MoveToFolder", ("Inbox/Invoices",)),)
    assert notes == ["Read it: Outlook cannot take the action 'mark as read' from another program"]
