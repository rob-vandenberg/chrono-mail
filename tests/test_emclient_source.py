import base64
import sqlite3
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chrono_mail.config import Action, Condition
from chrono_mail.core import MessageFlag
from chrono_mail.engine import emclient_source
from chrono_mail.engine.emclient_source import ImportSourceError, SourceRule, decode_password, message_files, message_keys, read_backup, read_settings
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_emclient_source.py 0.5.137"

# --- Version history ----------------------------------------------------
# v0.5.137: categories, the categories of messages in the backup, category parts of rules
# v0.5.132: Initial tests: reading eM Client's settings export, mail export and backup (all made-up data); every rule as it is

NAMESPACE = "http://licensing.emclient.com/XSD/accounts"
TICKS_AT_1970 = 621_355_968_000_000_000


def encode_password(password: str) -> str:
    key = emclient_source._key()
    return base64.b64encode(bytes(byte ^ mask for byte, mask in zip(password.encode(), key))).decode()


# --- made-up settings exports ---------------------------------------------------------------------


@dataclass
class FakeAccount:
    uid: str
    email: str
    login: str
    password: str = "Secret12"
    incoming_host: str = "pop.example.com"
    kind: str = "POP3"
    port: int = 110
    encryption: str = "TLS"
    leave: str = "true"
    days: int = 30
    remove: str = "false"
    smtp_login: str = ""  # empty: the account's own login and password (UseBindingAccount)

    def xml(self) -> str:
        smtp_credentials = f"<LoginName>{self.smtp_login}</LoginName><Password>{encode_password('Other123')}</Password>" if self.smtp_login else ""
        return f"""<account><AccountName>{self.email}</AccountName>
  <Account xmlns="{NAMESPACE}" uid="{self.uid}">
    <PersonName>Person {self.uid}</PersonName><LoginName>{self.login}</LoginName><Password>{encode_password(self.password)}</Password>
    <Addresses><Address>{self.email}</Address></Addresses>
    <Protocol uid="i{self.uid}"><Type>{self.kind}</Type><Server>{self.incoming_host}</Server><Port>{self.port}</Port><Encryption>{self.encryption}</Encryption>
      <CredentialsMode>UseAccountDefaults</CredentialsMode><LoginName>{self.login}</LoginName><Password>{encode_password(self.password)}</Password>
      <Enabled>true</Enabled><LeaveMessagesOnServer>{self.leave}</LeaveMessagesOnServer><DeleteMessageInDays>{self.days}</DeleteMessageInDays>
      <RemoveWhenDeleted>{self.remove}</RemoveWhenDeleted></Protocol>
    <Protocol uid="o{self.uid}"><Type>SMTP</Type><Server>smtp.example.com</Server><Port>25</Port><Encryption>AUTO</Encryption>
      <CredentialsMode>{"UseAccountDefaults" if self.smtp_login else "UseBindingAccount"}</CredentialsMode>{smtp_credentials}<Enabled>true</Enabled></Protocol>
  </Account></account>"""


def ui_rule(switched_on: dict[str, str]) -> str:
    """The rule editor's XML: every element is there; the ones switched on are true, with their data."""
    elements = ["wordsInSubject", "fromPeople", "sentToPeople", "wordsInBody", "hasAttachment", "usingAccount", "moveToFolder", "markAsRead",
                "markAsFlagged", "deleteMail", "permanentlyDeleteMail", "moveToJunk", "terminateProcessing", "forwardToPeople", "skipNotification", "wordsInHeader",
                "hasCategory", "addCategories", "removeCategories"]
    parts = ["<active>true</active>", "<dataEntered>true</dataEntered>"]
    for name in elements:
        parts.append(f"<{name}>{'true' if name in switched_on else 'false'}</{name}>")
    for name, data in switched_on.items():
        if data:
            parts.append(f"<{name}Data>{data}</{name}Data>")
    return "<UIRule>" + "".join(parts) + "</UIRule>"


def _escaped(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rule_xml(name: str, compose: str | None, scope: str = "AllAccounts", account: str | None = None, kind: str = "Composed", active: str = "true", script: str = "") -> str:
    escaped = "" if compose is None else _escaped(compose)
    account_element = f'<account uid="{account}" />' if account else ""
    sieve = f"<sieve>{_escaped(script)}</sieve>" if compose is None else ""
    return (f"<rule><name>{name}</name><active>{active}</active><invocation>OnReceive</invocation><scope>{scope}</scope><type>{kind}</type>"
            f"{account_element}{sieve}<composeXml>{escaped}</composeXml></rule>")


SPAM_SCRIPT = 'require ["fileinto"];\r\nif allof(anyof(header :matches "X-Spam-Flag" "YES*",header :matches "X-Spam-Status" "Yes*"))\r\n{\r\n  fileinto "special://spam";\r\n}\r\n'


def words(*values: str) -> str:
    return "".join(f"<string>{value}</string>" for value in values)


def category_xml(name: str, colour: str, types: str) -> str:
    return (f"<category><name>{name}</name><color>{colour}</color><priority>0</priority><shortcut>None</shortcut><isSystemCategory>false</isSystemCategory>"
            f"<isLocalizable>false</isLocalizable><isUsedFirstTime>false</isUsedFirstTime><parentTypes>{types}</parentTypes></category>")


def settings_xml(accounts: list[FakeAccount], rules: list[str], crypted: str = "yes", categories: list[str] | None = None) -> str:
    return (f'<?xml version="1.0" encoding="utf-8"?><settings><format-version>1</format-version><general-settings />'
            f"<categories>{''.join(categories or [])}</categories>"
            f"<rules>{''.join(rules)}</rules><accounts><crypted>{crypted}</crypted>{''.join(a.xml() for a in accounts)}</accounts></settings>")


def standard_rules() -> list[str]:
    return [
        rule_xml("To clients", ui_rule({"usingAccount": "a1@example.com", "moveToFolder": "/Local Folders/Clients/Rob", "terminateProcessing": ""}), "SpecifiedAccount", "a1"),
        rule_xml("Newsletters", ui_rule({"wordsInSubject": words("news", "offer"), "moveToFolder": "/Local Folders/News"})),
        rule_xml("Boss", ui_rule({"fromPeople": words("boss@example.com"), "markAsFlagged": "", "skipNotification": ""})),
        rule_xml("Blocked", ui_rule({"fromPeople": words("spam@example.com"), "moveToFolder": "special://spam", "skipNotification": "", "terminateProcessing": ""}), kind="Blacklist"),
        rule_xml("Script", None, "AllLocalAccounts", kind="None", script=SPAM_SCRIPT),
        rule_xml("Empty", ui_rule({})),
        rule_xml("Header", ui_rule({"wordsInHeader": words("x"), "markAsRead": ""})),
        rule_xml("Elsewhere", ui_rule({"fromPeople": words("x@example.com"), "moveToFolder": "imap://server/Inbox"})),
        rule_xml("Gone account", ui_rule({"usingAccount": "x", "markAsRead": ""}), "SpecifiedAccount", "zz"),
        rule_xml("Two lists", ui_rule({"fromPeople": words("a@x", "b@x"), "wordsInSubject": words("c"), "markAsRead": ""})),
    ]


def write_settings(folder: Path, accounts: list[FakeAccount] | None = None, rules: list[str] | None = None, categories: list[str] | None = None) -> Path:
    path = folder / "settings.xml"
    path.write_text(settings_xml(accounts if accounts is not None else [FakeAccount("a1", "a1@example.com", "a1login")], rules if rules is not None else standard_rules(), categories=categories), "utf-8")
    return path


# --- made-up mail exports and backups -------------------------------------------------------------


def write_mail_export(folder: Path, messages: dict[str, bytes]) -> Path:
    """relative path (folder/…/file.eml) -> raw message."""
    root = folder / "mail"
    for relative, raw in messages.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass
class FakeItem:
    folder: str  # as the backup writes it: /Inbox/Sub
    message_id: str
    date: datetime | None
    flags: int = 2  # read
    replied: bool = False
    tags: tuple[str, ...] = ()  # its categories


@dataclass
class FakeContact:
    name: str
    emails: tuple[str, ...]
    phones: tuple[str, ...] = ()
    organization: str = ""
    birthday: datetime | None = None
    extra: dict[str, str] = field(default_factory=dict)


def _ticks(moment: datetime | None) -> int:
    return 0 if moment is None else TICKS_AT_1970 + int(moment.timestamp()) * 10_000_000


def write_backup(folder: Path, items: list[FakeItem], contacts: list[FakeContact] | None = None, leave_out: str | None = None) -> Path:
    work = folder / "backup-work"
    work.mkdir(parents=True, exist_ok=True)
    folders = sorted({item.folder for item in items} | {"/Inbox"})
    numbers = {path: number for number, path in enumerate(folders, 1)}
    connection = sqlite3.connect(work / "folders.dat")
    connection.execute("CREATE TABLE Folders (id INTEGER PRIMARY KEY, name TEXT, path TEXT)")
    connection.executemany("INSERT INTO Folders VALUES (?, ?, ?)", [(number, path.rsplit("/", 1)[1], path) for path, number in numbers.items()])
    connection.commit()
    connection.close()
    connection = sqlite3.connect(work / "mail_index.dat")
    connection.execute("CREATE TABLE MailItems (id INTEGER PRIMARY KEY, folder INTEGER, date TIMESTAMP, messageId TEXT, flags INTEGER, replyDate TIMESTAMP)")
    connection.executemany(
        "INSERT INTO MailItems (id, folder, date, messageId, flags, replyDate) VALUES (?, ?, ?, ?, ?, ?)",
        [(number, numbers[item.folder], _ticks(item.date), item.message_id, item.flags, _ticks(datetime(2020, 1, 1, tzinfo=UTC)) if item.replied else 0) for number, item in enumerate(items, 1)],
    )
    connection.execute("CREATE TABLE MailCategoryNames (categoryId INTEGER, id INTEGER, categoryName TEXT)")
    connection.executemany(
        "INSERT INTO MailCategoryNames VALUES (?, ?, ?)", [(position, number, tag) for number, item in enumerate(items, 1) for position, tag in enumerate(item.tags)],
    )
    connection.commit()
    connection.close()
    connection = sqlite3.connect(work / "contact_index.dat")
    connection.execute(
        "CREATE TABLE ContactItems (id INTEGER PRIMARY KEY, displayName TEXT, firstName TEXT, middleName TEXT, lastName TEXT, nickName TEXT,"
        " organization TEXT, department TEXT, title TEXT, comment TEXT, birthday TIMESTAMP)"
    )
    connection.execute("CREATE TABLE ContactEmails (id INTEGER PRIMARY KEY, parentId INTEGER, email TEXT, isPrimary INTEGER)")
    connection.execute("CREATE TABLE ContactTelephones (id INTEGER PRIMARY KEY, parentId INTEGER, telephone TEXT, isPrimary INTEGER)")
    for number, contact in enumerate(contacts or [], 1):
        connection.execute(
            "INSERT INTO ContactItems VALUES (?, ?, ?, '', ?, '', ?, '', '', ?, ?)",
            (number, contact.name, contact.extra.get("given", ""), contact.extra.get("family", ""), contact.organization, contact.extra.get("note", ""), _ticks(contact.birthday)),
        )
        connection.executemany("INSERT INTO ContactEmails (parentId, email, isPrimary) VALUES (?, ?, 0)", [(number, email) for email in contact.emails])
        connection.executemany("INSERT INTO ContactTelephones (parentId, telephone, isPrimary) VALUES (?, ?, 0)", [(number, phone) for phone in contact.phones])
    connection.commit()
    connection.close()
    path = folder / "backup.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Manifest.xml", "<manifest />")
        archive.writestr("accounts.dat", b"not used")
        for name in ("folders.dat", "mail_index.dat", "contact_index.dat"):
            if name != leave_out:
                archive.write(work / name, f"Local Folders/{name}")
    return path


# --- passwords ------------------------------------------------------------------------------------


def test_a_password_comes_back_from_its_stored_form() -> None:
    assert decode_password(encode_password("Pass_word9")) == "Pass_word9"
    assert decode_password(encode_password("Twelve-char!")) == "Twelve-char!"  # 12 characters of the key are known
    assert len(emclient_source._key()) == 12


def test_a_password_that_cannot_be_decoded_is_none() -> None:
    assert decode_password("") is None
    assert decode_password("not base64 at all!") is None
    assert decode_password(base64.b64encode(b"x" * 40).decode()) is None  # longer than the part of the key that is known
    assert decode_password(base64.b64encode(bytes(byte ^ mask for byte, mask in zip(b"\x01\x02", emclient_source._key()))).decode()) is None  # not text


def test_the_key_is_not_a_readable_string_in_the_source() -> None:
    source = Path(emclient_source.__file__).read_text("utf-8")
    assert emclient_source._key().decode("latin-1") not in source
    assert "DefaultA" not in source


# --- the settings export --------------------------------------------------------------------------


def test_accounts_are_read_with_their_servers_and_passwords(tmp_path: Path) -> None:
    accounts = [
        FakeAccount("a1", "a1@example.com", "a1login"),
        FakeAccount("a2", "a2@example.com", "a2login", port=995, encryption="SSL", leave="false", smtp_login="smtpuser"),
        FakeAccount("a3", "a3@example.com", "a3login", kind="IMAP", days=-1, remove="true"),
    ]
    export = read_settings(write_settings(tmp_path, accounts, []))
    first, second, third = export.accounts
    assert export.passwords_encrypted
    assert (first.kind, first.email_address, first.person_name, first.name) == ("pop3", "a1@example.com", "Person a1", "a1@example.com")
    assert (first.incoming.host, first.incoming.port, first.incoming.security, first.incoming.username, first.incoming.password) == ("pop.example.com", 110, "starttls", "a1login", "Secret12")
    assert (first.outgoing.host, first.outgoing.port, first.outgoing.security, first.outgoing.username, first.outgoing.password) == ("smtp.example.com", 25, "starttls", "a1login", "Secret12")
    assert (first.leave_on_server, first.delete_after_days, first.remove_when_deleted) == (True, 30, False)
    assert (second.incoming.security, second.leave_on_server, second.outgoing.username, second.outgoing.password) == ("tls", False, "smtpuser", "Other123")
    assert (third.kind, third.delete_after_days, third.remove_when_deleted) == ("imap", -1, True)


def test_passwords_of_an_export_without_encryption_are_taken_as_they_are(tmp_path: Path) -> None:
    path = tmp_path / "plain.xml"
    path.write_text(settings_xml([FakeAccount("a1", "a1@example.com", "a1login")], [], crypted="no").replace(encode_password("Secret12"), "Plain99"), "utf-8")
    assert read_settings(path).accounts[0].incoming.password == "Plain99"


def test_rules_are_mapped_or_refused_with_a_reason(tmp_path: Path) -> None:
    rules = {rule.name: rule for rule in read_settings(write_settings(tmp_path)).rules}
    clients = rules["To clients"]
    assert (clients.problem, clients.account_uid, clients.conditions, clients.actions) == (None, "a1", (), (Action("move_to", "Clients/Rob"), Action("stop")))
    news = rules["Newsletters"]
    assert (news.match, news.conditions) == ("any", (Condition("subject", "contains", "news"), Condition("subject", "contains", "offer")))
    assert news.actions == (Action("move_to", "News"),) and news.account_uid is None
    boss = rules["Boss"]
    assert (boss.conditions, boss.actions, boss.left_out) == ((Condition("from", "contains", "boss@example.com"),), (Action("flag"),), ("no notification",))
    blocked = rules["Blocked"]
    assert (blocked.problem, blocked.conditions, blocked.actions, blocked.left_out) == (
        None, (Condition("from", "contains", "spam@example.com"),), (Action("mark_junk"), Action("stop")), ("no notification",)
    )
    script = rules["Script"]
    assert (script.problem, script.match, script.actions) == (None, "any", (Action("mark_junk"),))
    assert script.conditions == (Condition("header", "starts_with", "YES", "X-Spam-Flag"), Condition("header", "starts_with", "Yes", "X-Spam-Status"))
    assert rules["Empty"].problem == "no action that Chrono Mail has"
    assert rules["Header"].problem is not None and "wordsInHeader" in rules["Header"].problem
    assert rules["Elsewhere"].problem == "it moves to a folder outside Local Folders"
    assert rules["Gone account"].problem == "its account is not in the settings file"
    assert rules["Two lists"].problem == "several words in more than one condition"


def test_a_file_that_is_not_a_settings_export_is_refused(tmp_path: Path) -> None:
    (tmp_path / "a.xml").write_text("<other />", "utf-8")
    (tmp_path / "b.xml").write_text("not xml <", "utf-8")
    for name in ("a.xml", "b.xml", "missing.xml"):
        with pytest.raises(ImportSourceError):
            read_settings(tmp_path / name)


# --- the mail export ------------------------------------------------------------------------------


def test_message_files_come_in_a_fixed_order_with_their_folder(tmp_path: Path) -> None:
    root = write_mail_export(tmp_path, {"Inbox/b.eml": b"x", "Inbox/a.eml": b"x", "Inbox/Sub/c.eml": b"x", "Archive/d.eml": b"x", "notes.txt": b"x", "top.eml": b"x"})
    found = [(item.relative, item.folder) for item in message_files(root)]
    assert found == [("top.eml", ()), ("Archive/d.eml", ("Archive",)), ("Inbox/a.eml", ("Inbox",)), ("Inbox/b.eml", ("Inbox",)), ("Inbox/Sub/c.eml", ("Inbox", "Sub"))]


def test_message_keys_read_the_id_and_the_date_from_the_header_only(tmp_path: Path) -> None:
    moment = datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)
    assert message_keys(raw_message(message_id="<AbC@Example>", date=moment)) == ("abc@example", int(moment.timestamp()))
    assert message_keys(b"Subject: x\r\nDate: nonsense\r\n\r\nMessage-ID: <in-body@x>\r\n") == (None, None)
    assert message_keys(b"Message-ID:\r\n <folded@x>\r\n\r\n") == ("folded@x", None)


# --- the backup -----------------------------------------------------------------------------------


def test_the_backup_gives_the_marks_of_each_message_once(tmp_path: Path) -> None:
    moment = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    items = [
        FakeItem("/Inbox", "<one@x>", moment, flags=0),
        FakeItem("/Inbox/Sub", "two@x", moment, flags=2 | 4, replied=True),
        FakeItem("/Inbox", "", moment, flags=2),  # no Message-ID: found by its date
        FakeItem("/Inbox", "dup@x", moment, flags=0),
        FakeItem("/Inbox", "dup@x", moment, flags=4),
    ]
    data = read_backup(write_backup(tmp_path, items), tmp_path / "work")
    seconds = int(moment.timestamp())
    assert data.take_marks(("Inbox",), "one@x", seconds) == frozenset()
    assert data.take_marks(("INBOX", "sub"), "two@x", seconds) == frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED, MessageFlag.REPLIED})
    assert data.take_marks(("Inbox",), None, seconds) == frozenset({MessageFlag.SEEN})
    assert data.take_marks(("Inbox",), "dup@x", seconds) == frozenset()
    assert data.take_marks(("Inbox",), "dup@x", seconds) == frozenset({MessageFlag.FLAGGED})
    assert data.take_marks(("Inbox",), "dup@x", None) is None
    assert data.take_marks(("Other",), "one@x", seconds) is None
    assert list((tmp_path / "work").iterdir()) == []  # what was unpacked is gone


def test_the_backup_gives_the_contacts(tmp_path: Path) -> None:
    contacts = [
        FakeContact("Ann Example", ("ann@example.com", "ann@work.example"), ("+31 6 1234",), "Example Ltd", datetime(1980, 4, 15, tzinfo=UTC), {"given": "Ann", "family": "Example", "note": "met 2019"}),
        FakeContact("", ()),  # nothing to know it by: left out
    ]
    data = read_backup(write_backup(tmp_path, [], contacts), tmp_path / "work")
    (ann,) = data.contacts
    assert (ann.name, ann.given, ann.family, ann.organization, ann.note, ann.birthday) == ("Ann Example", "Ann", "Example", "Example Ltd", "met 2019", "1980-04-15")
    assert (ann.emails, ann.phones) == (("ann@example.com", "ann@work.example"), ("+31 6 1234",))


def test_a_backup_without_its_message_list_or_not_a_zip_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ImportSourceError):
        read_backup(write_backup(tmp_path, [], leave_out="mail_index.dat"), tmp_path / "work")
    (tmp_path / "fake.zip").write_bytes(b"not a zip")
    with pytest.raises(ImportSourceError):
        read_backup(tmp_path / "fake.zip", tmp_path / "work")
    assert list((tmp_path / "work").iterdir()) == []


def test_a_backup_without_contacts_still_gives_the_marks(tmp_path: Path) -> None:
    data = read_backup(write_backup(tmp_path, [FakeItem("/Inbox", "a@x", None)], leave_out="contact_index.dat"), tmp_path / "work")
    assert data.contacts == () and data.take_marks(("Inbox",), "a@x", None) == frozenset({MessageFlag.SEEN})



def scripted(tmp_path: Path, script: str) -> SourceRule:
    path = write_settings(tmp_path, [], [rule_xml("S", None, kind="None", script=script)])
    return read_settings(path).rules[0]


def test_scripts_are_read_as_rules(tmp_path: Path) -> None:
    rule = scripted(tmp_path, 'if header :contains ["subject", "From"] ["a", "b"] { addflag "\\\\Seen"; fileinto "/Local Folders/News"; stop; keep; }')
    assert rule.problem is None and rule.match == "any"
    assert rule.conditions == (Condition("subject", "contains", "a"), Condition("subject", "contains", "b"), Condition("from", "contains", "a"), Condition("from", "contains", "b"))
    assert rule.actions == (Action("mark_read"), Action("move_to", "News"), Action("stop"))
    rule = scripted(tmp_path, 'if allof(header :is "to" "me@x", header :matches "subject" "*report") { discard; }')
    assert (rule.match, rule.conditions, rule.actions) == ("all", (Condition("to", "equals", "me@x"), Condition("subject", "ends_with", "report")), (Action("delete"),))


@pytest.mark.parametrize(
    ("script", "problem"),
    [
        ("", "it is empty"),
        ('if not header :is "to" "x" { stop; }', "a test Chrono Mail does not have (not)"),
        ('if header :regex "to" "x" { stop; }', "a test Chrono Mail does not have (:regex)"),
        ('if header :matches "to" "a*b" { stop; }', "a pattern Chrono Mail cannot hold"),
        ('if allof(anyof(header :is "a-b" "1", header :is "a-b" "2"), anyof(header :is "c" "3", header :is "c" "4")) { stop; }', "its conditions are nested in a way Chrono Mail cannot hold"),
        ('if header :is "to" "x" { stop; } if header :is "to" "y" { stop; }', "the script has more than one part"),
        ('if header :is "to" "x" { redirect "a@b"; }', "an action Chrono Mail does not have (redirect)"),
        ('if header :is "to" "x" { fileinto "imap://server/Inbox"; }', "it moves to a folder outside Local Folders"),
        ('if header :is "to" "x" { keep; }', "no action that Chrono Mail has"),
        ('if header :is "to" "x" { stop; ', "the script has a form Chrono Mail does not know"),
        ('if header :is "to" "x" { stop; } @', "the script cannot be read"),
    ],
)
def test_a_script_that_cannot_be_held_is_reported(tmp_path: Path, script: str, problem: str) -> None:
    assert scripted(tmp_path, script).problem == problem


# --- categories and tags ----------------------------------------------------------------------------------


def test_categories_become_tags_with_colour_and_kinds(tmp_path: Path) -> None:
    categories = [
        category_xml("Home", "#FF0000", "Mail"), category_xml("All of it", "#00ff00", "All"), category_xml("Unused", "#0000ff", "None"),
        category_xml("Notes only", "#123456", "Note"), category_xml("People", "#abcdef", "Contact, Event, Task, Note"), category_xml("Odd", "red", "Mail"),
        category_xml("", "#000000", "Mail"),
    ]
    export = read_settings(write_settings(tmp_path, [], [], categories))
    found = [(item.name, item.colour, item.kinds) for item in export.categories]
    every = ("mail", "events", "contacts", "tasks")
    assert found == [
        ("Home", "#ff0000", ("mail",)), ("All of it", "#00ff00", every), ("Unused", "#0000ff", every), ("Notes only", "#123456", every),
        ("People", "#abcdef", ("events", "contacts", "tasks")), ("Odd", "#808080", ("mail",)),
    ]


def test_category_parts_of_rules_become_tag_conditions_and_actions(tmp_path: Path) -> None:
    rules = [
        rule_xml("Tagger", ui_rule({"fromPeople": words("a@x"), "addCategories": words("Work", "Money")})),
        rule_xml("Untagger", ui_rule({"hasCategory": words("Work"), "removeCategories": words("Old")})),
    ]
    found = {rule.name: rule for rule in read_settings(write_settings(tmp_path, [], rules)).rules}
    assert found["Tagger"].actions == (Action("add_tag", "Work"), Action("add_tag", "Money")) and found["Tagger"].problem is None
    assert found["Untagger"].conditions == (Condition("tag", "is", "Work"),) and found["Untagger"].actions == (Action("remove_tag", "Old"),)


def test_the_backup_gives_the_tags_of_each_message(tmp_path: Path) -> None:
    moment = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    items = [FakeItem("/Inbox", "a@x", moment, tags=("Work", "Home")), FakeItem("/Inbox", "b@x", moment)]
    data = read_backup(write_backup(tmp_path, items), tmp_path / "work")
    assert data.tagged == (("a@x", int(moment.timestamp()), ("Work", "Home")),)
    assert data.take(("Inbox",), "a@x", None) == (frozenset({MessageFlag.SEEN}), ("Work", "Home"))
    assert data.take(("Inbox",), "b@x", None) == (frozenset({MessageFlag.SEEN}), ())

