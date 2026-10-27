from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind, FolderRole, MessageFlag
from chrono_mail.engine import Engine, commands_outlook
from chrono_mail.engine.commands_addressbook import ImportParameters as ContactsImport, contact_import
from chrono_mail.engine.commands_calendar import ImportParameters as EventsImport, event_import
from chrono_mail.engine.commands_outlook_export import ExportResult, _members
from chrono_mail.engine.commands_tasklist import ImportParameters as TasksImport, task_import
from chrono_mail.engine.import_common import fingerprint
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.pim.contact import parse_contacts
from chrono_mail.pim.ical import parse_events, parse_tasks
from chrono_mail.pst.isolated import items_in_child, store_in_child
from tests.enginefactory import make_engine
from tests.mailfactory import raw_message
from tests.test_engine_commands import REGISTRY

# --- Version ------------------------------------------------------------
__version__ = "test_export_outlook.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial test: the round trip of a made-up profile through the export to data files and the import back into a fresh profile

POP3 = ("anna@example.com", "ben@example.com")
IMAP = ("carl@example.com", "dora@example.com")
MOMENT = datetime(2024, 3, 4, 5, 6, 7, tzinfo=UTC)
EVENT = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//EN\r\nBEGIN:VEVENT\r\nUID:weekly@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
    "DTSTART;TZID=Europe/Amsterdam:20240305T100000\r\nDTEND;TZID=Europe/Amsterdam:20240305T110000\r\nSUMMARY:Weekly meeting\r\nLOCATION:Room 1\r\n"
    "RRULE:FREQ=WEEKLY;COUNT=10;BYDAY=TU\r\nEXDATE;TZID=Europe/Amsterdam:20240312T100000\r\nCATEGORIES:Work\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:weekly@example.com\r\nDTSTAMP:20240101T000000Z\r\nRECURRENCE-ID;TZID=Europe/Amsterdam:20240319T100000\r\n"
    "DTSTART;TZID=Europe/Amsterdam:20240319T140000\r\nDTEND;TZID=Europe/Amsterdam:20240319T150000\r\nSUMMARY:Weekly meeting (moved)\r\n"
    "LOCATION:Room 1\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)
DAY = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//EN\r\nBEGIN:VEVENT\r\nUID:day@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
    "DTSTART;VALUE=DATE:20240401\r\nDTEND;VALUE=DATE:20240402\r\nSUMMARY:Holiday\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)
TASK = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//EN\r\nBEGIN:VTODO\r\nUID:task@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
    "SUMMARY:Write the plan\r\nDUE;VALUE=DATE:20240310\r\nSTATUS:IN-PROCESS\r\nPERCENT-COMPLETE:40\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
)
CONTACTS = (
    "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:urn:uuid:11111111-1111-1111-1111-111111111111\r\nFN:Erik Example\r\nN:Example;Erik;;;\r\n"
    "EMAIL:erik@example.com\r\nTEL;TYPE=cell:+31 6 12345678\r\nORG:Example Ltd\r\nCATEGORIES:Family\r\nEND:VCARD\r\n",
    "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:urn:uuid:22222222-2222-2222-2222-222222222222\r\nKIND:group\r\nFN:Team\r\n"
    "MEMBER:urn:uuid:11111111-1111-1111-1111-111111111111\r\nMEMBER:mailto:fay@example.com\r\nEND:VCARD\r\n",
)

type Mail = dict[tuple[str, str], tuple[str, frozenset[MessageFlag], tuple[str, ...]]]


def html_mail() -> bytes:
    message = EmailMessage()
    message["From"] = "Gina <gina@example.com>"
    message["To"] = "anna@example.com"
    message["Subject"] = "Pictures"
    message["Date"] = "Mon, 04 Mar 2024 05:06:07 +0000"
    message["Message-ID"] = "<html-1@example.com>"
    message.set_content("See the plan")
    message.add_alternative("<p>See <b>the plan</b></p>", subtype="html")
    message.add_attachment(b"%PDF-1.4 made up" + bytes(20_000), maintype="application", subtype="pdf", filename="plan.pdf")
    return message.as_bytes()


def add_account(engine: Engine, address: str, kind: str) -> str:
    added = REGISTRY.run(engine, "account.add", {
        "kind": kind, "display_name": "x", "email_address": address, "username": address, "password": "p", "incoming_host": "mail.example.com",
        "outgoing_host": "smtp.example.com",
    })
    account = getattr(added, "account_id")
    assert isinstance(account, str)
    return account


def profile(engine: Engine) -> dict[str, str]:
    accounts = {address: add_account(engine, address, "pop3") for address in POP3}
    for address in IMAP:
        add_account(engine, address, "imap")
    ensure_local_pim(engine)
    return accounts


def fill(engine: Engine, accounts: dict[str, str]) -> None:
    for number, (address, account) in enumerate(accounts.items()):
        with open_mailbox(engine, account) as mailbox:
            inbox = mailbox.role_folder(FolderRole.INBOX)
            first = mailbox.deliver(inbox, raw_message(subject=f"First {number}", date=MOMENT, message_id=f"<a{number}@example.com>"), frozenset({MessageFlag.SEEN}))
            mailbox.set_tags(first, ("Work",))
            mailbox.deliver(inbox, raw_message(subject=f"Second {number}", date=MOMENT, message_id=f"<b{number}@example.com>"), frozenset({MessageFlag.FLAGGED, MessageFlag.REPLIED}))
            mailbox.deliver(mailbox.role_folder(FolderRole.SENT), raw_message(subject=f"Sent {number}", sender=address, date=MOMENT, message_id=f"<c{number}@example.com>"), frozenset({MessageFlag.SEEN}))
            mailbox.ensure_folder(("Projects",))
            mailbox.deliver(("Projects",), html_mail() if number == 0 else raw_message(subject="Project", date=MOMENT, message_id="<p@example.com>"), frozenset())
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        mailbox.deliver(mailbox.role_folder(FolderRole.INBOX), raw_message(subject="Kept here", date=MOMENT, message_id="<local@example.com>"), frozenset({MessageFlag.SEEN}))
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store:
        calendar = store.collections(CollectionKind.EVENTS)[0].collection_id
        book = store.collections(CollectionKind.CONTACTS)[0].collection_id
        tasks = store.collections(CollectionKind.TASKS)[0].collection_id
    event_import(engine, EventsImport(calendar, EVENT))
    event_import(engine, EventsImport(calendar, DAY))
    task_import(engine, TasksImport(tasks, TASK))
    for card in CONTACTS:
        contact_import(engine, ContactsImport(book, card))


def mail_of(engine: Engine, account: str) -> Mail:
    """(fingerprint, role or path) -> (subject, marks, tags) of every message of a mailbox."""
    found: Mail = {}
    with open_mailbox(engine, account) as mailbox:
        for folder in mailbox.folders():
            place = folder.role.value if folder.role is not FolderRole.OTHER else "/".join(folder.path)
            for row in mailbox.index.page(folder.path, limit=1000):
                key = fingerprint(row.message_id, row.date, row.sender, row.subject) or ""
                found[(key, place)] = (row.subject, row.flags, tuple(sorted(mailbox.tags_of(row.store_id))))
    return found


def pim_of(engine: Engine) -> tuple[set[tuple[object, ...]], set[tuple[object, ...]], set[tuple[object, ...]], set[tuple[object, ...]]]:
    events: set[tuple[object, ...]] = set()
    tasks: set[tuple[object, ...]] = set()
    contacts: set[tuple[object, ...]] = set()
    groups: set[tuple[object, ...]] = set()
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store:
        for info in store.collections():
            texts = [store.get(info.collection_id, item) for item in store.item_ids(info.collection_id)]
            if info.kind is CollectionKind.EVENTS:
                for series in (series for text in texts for series in parse_events(text)):
                    master = series.master
                    moved = tuple(sorted((str(override.recurrence_id), str(override.start), override.summary) for override in series.overrides))
                    events.add((info.name, master.summary, _utc(master.start), _utc(master.end), master.rrule.replace("INTERVAL=1;", ""),
                                tuple(_utc(day) for day in master.exdates), master.location, master.categories, len(moved)))
            elif info.kind is CollectionKind.TASKS:
                tasks.update((info.name, task.summary, task.due, task.status, task.percent) for text in texts for task in parse_tasks(text))
            else:
                cards = [card for text in texts for card in parse_contacts(text)]
                people = [card for card in cards if card.group_form is None]
                contacts.update((card.full_name(), tuple(email.value for email in card.emails), card.organization, card.categories) for card in people)
                groups.update((card.full_name(), tuple(sorted(address for _, address in _members(people, card)))) for card in cards if card.group_form is not None)
    return events, tasks, contacts, groups


def _utc(moment: object) -> object:
    return moment.astimezone(UTC) if isinstance(moment, datetime) else moment


def test_a_profile_comes_back_the_same_through_the_data_files(tmp_path: Path) -> None:
    source = make_engine(tmp_path / "source")
    accounts = profile(source)
    fill(source, accounts)
    out = tmp_path / "out"
    out.mkdir()
    result = REGISTRY.run(source, "export.outlook", {"folder": str(out)})
    assert isinstance(result, ExportResult)
    assert sorted(Path(file).name for file in result.files) == ["Local Folders.pst", "anna@example.com.pst", "ben@example.com.pst"]
    assert (result.messages, result.events, result.contacts, result.groups, result.tasks, result.failed) == (9, 2, 1, 1, 1, 0)
    store = store_in_child(out / "anna@example.com.pst")  # what the reading library itself makes of the file
    assert store.display_name == "anna@example.com" and {folder.role for folder in store.folders} >= {FolderRole.INBOX, FolderRole.SENT, FolderRole.TRASH}
    assert sum(1 for _ in items_in_child(out / "anna@example.com.pst")) == 4

    target = make_engine(tmp_path / "target")
    again = profile(target)
    commands_outlook.finished_run()
    REGISTRY.run(target, "import.outlook", {"files": tuple(result.files)})
    commands_outlook.finished_run()
    for address in POP3:
        before = mail_of(source, accounts[address])
        assert len(before) == 4 and mail_of(target, again[address]) == before
    assert mail_of(target, LOCAL_ACCOUNT_ID) == mail_of(source, LOCAL_ACCOUNT_ID)
    events, tasks, contacts, groups = pim_of(source)
    assert (len(events), len(tasks), len(contacts), len(groups)) == (2, 1, 1, 1)
    assert next(event for event in events if event[1] == "Weekly meeting")[-1] == 1  # the moved occurrence came back
    assert pim_of(target) == (events, tasks, contacts, groups)
