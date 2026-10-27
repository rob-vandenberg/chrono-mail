import shutil
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chrono_mail.config import load_tags
from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind, FolderRole, MessageFlag, MessageRef, StorageError
from chrono_mail.engine import Engine, UsageError, commands_outlook
from chrono_mail.engine.commands_addressbook import ContactList
from chrono_mail.engine.commands_outlook import JOURNAL_FOLDER, ImportResult, ScanResult
from chrono_mail.engine.groups import groups_of_book
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.store.store import Store
from chrono_mail.pimstore import ItemQuery
from chrono_mail.pst.model import PstContact, PstError, PstEvent, PstFailure, PstFolder, PstGroup, PstItem, PstMail, PstStore, PstTask
from tests.enginefactory import make_engine
from tests.mailfactory import raw_message
from tests.test_engine_commands import REGISTRY

# --- Version ------------------------------------------------------------
__version__ = "test_import_outlook.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: the reader's note that not every contact could be looked through is told once per file, not once per batch
# v4.2.385: Tags ticked in a later run: what was imported before gets its categories, once
# v4.2.385: the review: never into Chrono Mail's own folders; a blank folder name; one bad item fails only itself; an item number given twice;
#           a changed choice keeps the journal; two files with the same message; the journal only after the batch is safe; empty folders
#           of notes are not listed
# v4.2.385: Initial tests: the import from a data file (.pst) with made-up data files: where the mail goes in all three cases (by role, never
#           by name), calendars, contacts, groups, tasks and categories, batches and resume, nothing twice, failures without contents

MOMENT = datetime(2024, 3, 4, 5, 6, 7, tzinfo=UTC)
SEEN = frozenset({MessageFlag.SEEN})


def mail(number: int, folder: tuple[str, ...], categories: tuple[str, ...] = (), flags: frozenset[MessageFlag] = SEEN) -> PstMail:
    raw = raw_message(subject=f"Message {number}", body=f"body {number}\r\n", date=MOMENT, message_id=f"<p{number}@example.com>")
    return PstMail(folder, raw, flags, categories)


def event(uid: str, summary: str, folder: tuple[str, ...], categories: tuple[str, ...] = ()) -> PstEvent:
    uid_line = f"UID:{uid}\r\n" if uid else ""
    text = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//EN\r\nBEGIN:VEVENT\r\n" + uid_line
        + f"DTSTAMP:20240301T100000Z\r\nDTSTART:20240305T100000Z\r\nDTEND:20240305T110000Z\r\nSUMMARY:{summary}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    return PstEvent(folder, text, categories)


def task(uid: str, summary: str, folder: tuple[str, ...]) -> PstTask:
    text = f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//EN\r\nBEGIN:VTODO\r\nUID:{uid}\r\nDTSTAMP:20240301T100000Z\r\nSUMMARY:{summary}\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
    return PstTask(folder, text)


def contact(name: str, address: str, folder: tuple[str, ...], categories: tuple[str, ...] = ()) -> PstContact:
    return PstContact(folder, f"BEGIN:VCARD\r\nVERSION:4.0\r\nFN:{name}\r\nN:{name};;;;\r\nEMAIL:{address}\r\nEND:VCARD\r\n", categories)


INBOX = ("Postvak IN",)  # a standard folder under a name of its own: found by its role, never by its name
SENT = ("Verzonden items",)
OUTBOX = ("Postvak UIT",)
FOLDERS = (
    PstFolder(INBOX, None, FolderRole.INBOX, items=2),
    PstFolder(INBOX + ("Clients",), None, None, items=1),
    PstFolder(SENT, None, FolderRole.SENT, items=1),
    PstFolder(OUTBOX, None, FolderRole.OUTBOX, items=1),
    PstFolder(("Projects",), None, None, items=1),
    PstFolder(("Agenda",), CollectionKind.EVENTS, None, main=True, items=1),
    PstFolder(("Holidays",), CollectionKind.EVENTS, None, items=1),
    PstFolder(("Contactpersonen",), CollectionKind.CONTACTS, None, main=True, items=3),
    PstFolder(("Taken",), CollectionKind.TASKS, None, main=True, items=1),
    PstFolder(("Notities",), None, None, unsupported="notes", items=4),
)


def items() -> list[PstItem]:
    return [
        mail(1, INBOX, categories=("Work",)),
        mail(2, INBOX, flags=frozenset()),
        mail(3, INBOX + ("Clients",)),
        mail(4, SENT),
        mail(5, OUTBOX),
        mail(6, ("Projects",)),
        event("ev-1@example.com", "Meeting", ("Agenda",), categories=("Work",)),
        event("", "Beach", ("Holidays",)),
        contact("Anna Example", "anna@example.com", ("Contactpersonen",), categories=("Family",)),
        contact("Ben Example", "ben@example.com", ("Contactpersonen",)),
        PstGroup(("Contactpersonen",), "Team", (("Anna Example", "anna@example.com"), ("Carl", "carl@example.com"))),
        task("task-1@example.com", "Write the plan", ("Taken",)),
    ]


@dataclass
class FakeFile:
    store: PstStore
    items: list[PstItem]
    starts: list[int] = field(default_factory=list)
    broken_at: int | None = None  # the reading process stops with an error at this item
    note_each_call: str = ""  # a file-wide note the reader gives once per reading (so once per batch), with the first item's number
    numbered: list[tuple[int, PstItem]] | None = None  # the items with the numbers the reader gives them (else numbered in order)


@pytest.fixture
def files(monkeypatch: pytest.MonkeyPatch) -> Generator[dict[Path, FakeFile]]:
    """The data files the reader knows (in place of the separate process); the import forgets its run after the test."""
    known: dict[Path, FakeFile] = {}

    def read_store(path: Path) -> PstStore:
        found = known.get(path.resolve())
        if found is None:
            raise PstError("This is not a data file that can be read")
        return found.store

    def read_items(path: Path, start: int) -> Iterator[tuple[int, PstItem]]:
        found = known[path.resolve()]
        found.starts.append(start)
        noted = False
        for number, item in found.numbered if found.numbered is not None else list(enumerate(found.items)):
            if number < start:
                continue
            if found.broken_at == number:
                raise PstError("The data file could not be read further")
            if found.note_each_call and not noted:
                noted = True
                yield number, PstFailure(("Contactpersonen",), found.note_each_call)
            yield number, item

    monkeypatch.setattr(commands_outlook, "read_store", read_store)
    monkeypatch.setattr(commands_outlook, "read_items", read_items)
    commands_outlook.finished_run()
    yield known
    commands_outlook.finished_run()


def data_file(files: dict[Path, FakeFile], folder: Path, name: str, display: str, content: list[PstItem] | None = None) -> Path:
    path = folder / name
    path.write_bytes(b"made-up data file " + name.encode())
    files[path.resolve()] = FakeFile(PstStore(display, FOLDERS), items() if content is None else content)
    return path


def run(engine: Engine, path: Path | tuple[Path, ...], **values: object) -> ImportResult:
    paths = path if isinstance(path, tuple) else (path,)
    result = REGISTRY.run(engine, "import.outlook", {"files": tuple(str(each) for each in paths), **values})
    assert isinstance(result, ImportResult)
    return result


def scan(engine: Engine, *paths: str) -> ScanResult:
    result = REGISTRY.run(engine, "import.outlook-scan", {"files": paths})
    assert isinstance(result, ScanResult)
    return result


def messages(engine: Engine, account: str) -> dict[str, tuple[tuple[str, ...], frozenset[MessageFlag], tuple[str, ...]]]:
    """subject -> (folder, marks, tags) of everything in a mailbox; nothing may be there twice."""
    found: dict[str, tuple[tuple[str, ...], frozenset[MessageFlag], tuple[str, ...]]] = {}
    with open_mailbox(engine, account) as mailbox:
        for folder in mailbox.store.list_folders():
            for message in mailbox.index.page(folder.path, limit=1000):
                assert message.subject not in found, f"{message.subject} is there twice"
                found[message.subject] = (message.folder, message.flags, mailbox.tags_of(message.store_id))
    return found


def role_folders(engine: Engine, account: str) -> dict[FolderRole, tuple[str, ...]]:
    with open_mailbox(engine, account) as mailbox:
        return {role: mailbox.role_folder(role) for role in (FolderRole.INBOX, FolderRole.SENT, FolderRole.DRAFTS)}


def pop3_account(engine: Engine, address: str) -> str:
    added = REGISTRY.run(
        engine, "account.add", {"display_name": "Anna", "email_address": address, "username": "anna", "password": "x", "incoming_host": "pop.example.com", "outgoing_host": "smtp.example.com"},
    )
    account = getattr(added, "account_id")
    assert isinstance(account, str)
    return account


def test_the_scan_says_what_goes_where_and_changes_nothing(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    pop3_account(engine, "anna@example.com")
    own = data_file(files, tmp_path, "anna.pst", "ANNA@example.com")
    other = data_file(files, tmp_path, "ben.pst", "ben@example.com")
    personal = data_file(files, tmp_path, "personal.pst", "Personal Folders")
    offline = tmp_path / "anna.ost"
    offline.write_bytes(b"made up")
    result = scan(engine, str(own), str(other), str(personal), str(offline), str(tmp_path / "missing.pst"))
    first, second, third, fourth, fifth = result.files
    assert (first.name, first.place, first.mail, first.events, first.contacts, first.tasks) == ("ANNA@example.com", "the account anna@example.com", 6, 2, 3, 1)
    assert first.left_out == ("Notities: 4 notes",) and first.items == 16 and first.done == 0
    assert second.place == "Local Folders > ben@example.com" and third.place == "Local Folders"
    assert "keeps its mail on its server" in fourth.problem and fourth.mail == 0
    assert fifth.problem == "The data file is not there"
    assert not (engine.directories.data_directory / JOURNAL_FOLDER).exists()


def test_a_data_file_of_a_pop3_account_fills_its_own_folders_by_role(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    account = pop3_account(engine, "anna@example.com")
    result = run(engine, data_file(files, tmp_path, "anna.pst", "Anna@Example.com"), calendar=False, contacts=False, tasks=False)
    assert (result.mail_imported, result.mail_existing, result.mail_failed, result.left) == (6, 0, 0, 0)
    roles = role_folders(engine, account)
    found = messages(engine, account)
    assert found["Message 1"] == (roles[FolderRole.INBOX], SEEN, ("Work",))
    assert found["Message 2"][:2] == (roles[FolderRole.INBOX], frozenset())
    assert found["Message 3"][0] == roles[FolderRole.INBOX] + ("Clients",)
    assert found["Message 4"][0] == roles[FolderRole.SENT]
    assert found["Message 5"][0] == roles[FolderRole.DRAFTS]  # never into the Outbox: an import never sends
    assert found["Message 6"][0] == ("Projects",)
    assert not any(folder[0] in ("Postvak IN", "Verzonden items", "Postvak UIT") for folder, _, _ in found.values())


def test_a_data_file_of_an_address_without_an_account_goes_under_its_name(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    run(engine, data_file(files, tmp_path, "ben.pst", "ben@example.com"), calendar=False, contacts=False, tasks=False)
    found = messages(engine, LOCAL_ACCOUNT_ID)
    assert found["Message 1"][0] == ("ben@example.com", "Postvak IN")  # as Outlook showed it: there they are plain folders
    assert found["Message 3"][0] == ("ben@example.com", "Postvak IN", "Clients")
    assert found["Message 5"][0] == ("ben@example.com", "Postvak UIT")


def test_the_own_data_file_fills_local_folders_by_role(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    run(engine, data_file(files, tmp_path, "personal.pst", "Personal Folders"), calendar=False, contacts=False, tasks=False)
    roles = role_folders(engine, LOCAL_ACCOUNT_ID)
    found = messages(engine, LOCAL_ACCOUNT_ID)
    assert found["Message 1"][0] == roles[FolderRole.INBOX] and found["Message 4"][0] == roles[FolderRole.SENT]
    assert found["Message 5"][0] == roles[FolderRole.DRAFTS] and found["Message 6"][0] == ("Projects",)


def test_the_mail_of_an_account_kept_on_its_server_is_not_imported(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    REGISTRY.run(
        engine, "account.add",
        {"kind": "imap", "display_name": "x", "email_address": "anna@example.com", "username": "u", "password": "p", "incoming_host": "127.0.0.1", "outgoing_host": "127.0.0.1"},
    )
    path = data_file(files, tmp_path, "anna.pst", "anna@example.com")
    assert scan(engine, str(path)).files[0].place == ""
    result = run(engine, path, contacts=False, tasks=False)
    assert result.mail_imported == 0 and result.events_imported == 2
    assert any("comes from its server" in note for note in result.notes)
    assert messages(engine, LOCAL_ACCOUNT_ID) == {}


def test_calendars_contacts_groups_tasks_and_categories(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    result = run(engine, data_file(files, tmp_path, "personal.pst", "Personal Folders"))
    assert (result.events_imported, result.contacts_imported, result.groups_imported, result.tasks_imported) == (2, 2, 1, 1)
    assert result.tags_added == 2 and {entry.shown for entry in load_tags(engine.directories.tags_file).entries} >= {"Work", "Family"}
    assert any(note == "Not imported (Chrono Mail has no place for them): Notities: 4 notes" for note in result.notes)
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store:
        calendars = {info.name: info.collection_id for info in store.collections(CollectionKind.EVENTS)}
        assert set(calendars) == {"Calendar", "Holidays"}  # the data file's own calendar into Local Folders' own; another into one of its name
        meeting = store.index.find_uid(calendars["Calendar"], "ev-1@example.com")
        assert meeting is not None and "CATEGORIES:Work" in store.get(calendars["Calendar"], meeting.item_id)
        books = store.collections(CollectionKind.CONTACTS)
        assert [info.name for info in books] == ["Contacts"]
        team = groups_of_book(store, books[0].collection_id)
        assert [group.title for group in team] == ["Team"]
        card = store.get(books[0].collection_id, team[0].item_id)
        assert "mailto:carl@example.com" in card and "urn:uuid:" in card  # Anna is a contact of the book: by her card; Carl by his address
        lists = store.collections(CollectionKind.TASKS)
        assert store.index.find_uid(lists[0].collection_id, "task-1@example.com") is not None


def test_batches_continue_where_they_stopped_also_after_a_restart(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    path = data_file(files, tmp_path, "personal.pst", "Personal Folders")
    first = run(engine, path, batch=5)
    assert (first.done, first.mail_imported) == (5, 5) and first.left > 0
    assert scan(engine, str(path)).files[0].done == 5
    commands_outlook.finished_run()  # the program starts again: nothing of the import is in memory any more
    second = run(engine, path, batch=5)
    third = run(engine, path, batch=5)
    assert files[path.resolve()].starts == [0, 5, 10]
    assert (second.done, third.done, third.left) == (5, 2, 0)
    assert len(messages(engine, LOCAL_ACCOUNT_ID)) == 6
    again = run(engine, path)
    assert (again.done, again.mail_imported, again.left) == (0, 0, 0)  # the journal: the whole file is done


def test_an_item_stored_but_not_yet_in_the_journal_is_not_stored_twice(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    path = data_file(files, tmp_path, "personal.pst", "Personal Folders")
    run(engine, path, batch=8)
    journal = next((engine.directories.data_directory / JOURNAL_FOLDER).iterdir())
    lines = journal.read_text("utf-8").splitlines()
    journal.write_text("\n".join(lines[:-3]) + "\n" + lines[-3][:5], "utf-8")  # the crash: the last lines lost, one cut off
    commands_outlook.finished_run()
    result = run(engine, path)
    assert (result.mail_imported, result.mail_existing, result.events_existing) == (0, 1, 2)
    assert len(messages(engine, LOCAL_ACCOUNT_ID)) == 6


def test_a_second_import_without_its_journal_stores_nothing_twice(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    path = data_file(files, tmp_path, "personal.pst", "Personal Folders")
    run(engine, path)
    shutil.rmtree(engine.directories.data_directory / JOURNAL_FOLDER)
    commands_outlook.finished_run()
    result = run(engine, path)
    assert (result.mail_imported, result.events_imported, result.contacts_imported, result.groups_imported, result.tasks_imported) == (0, 0, 0, 0, 0)
    assert (result.mail_existing, result.events_existing, result.contacts_existing, result.groups_existing, result.tasks_existing) == (6, 2, 2, 1, 1)
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store:
        assert [info.name for info in store.collections(CollectionKind.EVENTS)] == ["Calendar", "Holidays"]  # the calendar of its name is found again


def test_failures_are_listed_without_contents_and_the_rest_goes_on(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    secret = "BEGIN:VCALENDAR\r\nSUMMARY:the secret plan\r\nEND:VCALENDAR\r\n"
    content: list[PstItem] = [
        mail(1, INBOX), PstFailure(("Projects",), "a message that could not be read"), PstEvent(("Agenda",), secret), mail(2, INBOX),
    ]
    result = run(engine, data_file(files, tmp_path, "personal.pst", "Personal Folders", content))
    assert (result.mail_imported, result.unreadable, result.events_failed) == (2, 1, 1)
    assert result.failures == ("Projects: a message that could not be read", "Agenda: an appointment that could not be read")
    assert not any("secret" in text for text in result.failures + result.notes)


def test_a_file_that_stops_being_readable_keeps_what_was_done(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    path = data_file(files, tmp_path, "personal.pst", "Personal Folders")
    files[path.resolve()].broken_at = 3
    result = run(engine, path, batch=100)
    assert result.mail_imported == 3 and result.left == 0 and any("could not be read further" in note for note in result.notes)
    assert scan(engine, str(path)).files[0].done == 3  # not finished: what was done is kept
    files[path.resolve()].broken_at = None
    again = run(engine, path)  # a later import tries again, from where it stopped
    assert again.mail_imported == 3 and files[path.resolve()].starts[-1] == 3


def test_what_cannot_be_imported_is_refused_plainly(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    offline = tmp_path / "anna.ost"
    offline.write_bytes(b"made up")
    with pytest.raises(UsageError, match="takes that mail from the server itself"):
        run(engine, offline)
    with pytest.raises(UsageError, match="Choose what to import"):
        run(engine, data_file(files, tmp_path, "p.pst", "Personal Folders"), mail=False, calendar=False, contacts=False, tasks=False)
    with pytest.raises(UsageError, match="Choose a data file"):
        REGISTRY.run(engine, "import.outlook", {"files": ()})


def where(engine: Engine, account: str) -> list[tuple[str, tuple[str, ...]]]:
    """(subject, folder) of every message of a mailbox, a message there twice listed twice."""
    found: list[tuple[str, tuple[str, ...]]] = []
    with open_mailbox(engine, account) as mailbox:
        for folder in mailbox.store.list_folders():
            found.extend((message.subject, message.folder) for message in mailbox.index.page(folder.path, limit=1000))
    return sorted(found)


@pytest.mark.parametrize("case", ["account", "address", "own file"])
@pytest.mark.parametrize("name", ["Outbox", "Scheduled", "Snoozed", "Recovered", "outbox"])
def test_imported_mail_never_goes_into_chrono_mails_own_folders(tmp_path: Path, files: dict[Path, FakeFile], case: str, name: str) -> None:
    """A folder of the data file named as one of Chrono Mail's own folders (Outbox: what is there is sent) gets a name of its own."""
    engine = make_engine(tmp_path / "profile")
    account = pop3_account(engine, "anna@example.com") if case == "account" else LOCAL_ACCOUNT_ID
    display = {"account": "anna@example.com", "address": "ben@example.com", "own file": "Personal Folders"}[case]
    path = tmp_path / "file.pst"
    path.write_bytes(b"made up")
    files[path.resolve()] = FakeFile(PstStore(display, (PstFolder((name,), None, None, items=1), PstFolder((name, "Sub"), None, None, items=1))), [mail(1, (name,)), mail(2, (name, "Sub"))])
    assert run(engine, path).mail_imported == 2
    with open_mailbox(engine, account) as mailbox:
        roles = {folder.path: folder.role for folder in mailbox.store.list_folders()}
    places = dict(where(engine, account))
    top = ("ben@example.com",) if case == "address" else ()
    assert places["Message 1"] == top + (f"{name} (Outlook)",) if case != "address" else places["Message 1"] == top + (name,)
    assert places["Message 2"] == places["Message 1"] + ("Sub",)
    assert roles[places["Message 1"]] is FolderRole.OTHER and roles[places["Message 2"]] is FolderRole.OTHER


def test_a_folder_with_a_blank_name_and_a_bad_item_fail_only_themselves(tmp_path: Path, files: dict[Path, FakeFile], monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path / "profile")
    content: list[PstItem] = [mail(1, ("   ",)), mail(2, ("Projects", " ")), mail(3, ("Projects",)), mail(4, ("Projects",))]
    real = commands_outlook.fingerprint_of_raw

    def failing(raw: bytes) -> str | None:
        if b"Message 3" in raw:
            raise ValueError("Message 3 is secret")
        return real(raw)

    monkeypatch.setattr(commands_outlook, "fingerprint_of_raw", failing)
    result = run(engine, data_file(files, tmp_path, "personal.pst", "Personal Folders", content))
    places = dict(where(engine, LOCAL_ACCOUNT_ID))
    assert places["Message 1"] == ("Unnamed folder",) and places["Message 2"] == ("Projects", "Unnamed folder") and "Message 4" in places
    assert (result.mail_imported, result.mail_failed) == (3, 1)
    assert result.failures == ("Projects: it could not be imported (ValueError)",)  # the kind of fault only, never what the item holds


def test_an_item_number_given_twice_keeps_what_was_stored(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    """The reader gives a message and then a failure with the same number: the message stays imported, and after a restart a copy of it in
    another folder of the same file is still imported (as Outlook had it twice)."""
    engine = make_engine(tmp_path / "profile")
    copy = PstMail(("Projects",), mail(2, INBOX).raw, SEEN)
    path = data_file(files, tmp_path, "personal.pst", "Personal Folders", [])
    files[path.resolve()].numbered = [(0, mail(1, INBOX)), (1, mail(2, INBOX)), (1, PstFailure(INBOX, "an attachment that could not be read")), (2, copy)]
    first = run(engine, path, batch=2)
    assert (first.done, first.mail_imported, first.unreadable) == (2, 2, 1) and first.left > 0
    assert first.failures == ("Postvak IN: an attachment that could not be read",)
    commands_outlook.finished_run()  # the program starts again
    second = run(engine, path, batch=2)
    assert (second.mail_imported, second.mail_existing, second.left) == (1, 0, 0)
    assert [place for subject, place in where(engine, LOCAL_ACCOUNT_ID) if subject == "Message 2"] == [("Inbox",), ("Projects",)]


def test_a_changed_choice_keeps_the_journal_and_imports_what_was_left_out(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    path = data_file(files, tmp_path, "personal.pst", "Personal Folders")
    first = run(engine, path, calendar=False, contacts=False, tasks=False, batch=8)
    assert (first.mail_imported, first.events_imported, first.done) == (6, 0, 8)
    assert scan(engine, str(path)).files[0].done == 8  # it continues, whatever is chosen
    commands_outlook.finished_run()
    second = run(engine, path, mail=False, tasks=False)
    assert (second.mail_imported, second.mail_existing, second.events_imported, second.contacts_imported, second.tasks_imported) == (0, 0, 2, 2, 0)
    third = run(engine, path)
    assert (third.mail_imported, third.mail_existing, third.events_existing, third.tasks_imported, third.left) == (0, 0, 0, 1, 0)
    assert len(where(engine, LOCAL_ACCOUNT_ID)) == 6
    assert run(engine, path).done == 0


@pytest.mark.parametrize("together", [True, False], ids=["one run", "one after the other"])
def test_a_message_in_two_data_files_is_stored_once_either_way(tmp_path: Path, files: dict[Path, FakeFile], together: bool) -> None:
    engine = make_engine(tmp_path / "profile")
    first = data_file(files, tmp_path, "first.pst", "Personal Folders", [mail(1, INBOX), mail(2, INBOX)])
    second = data_file(files, tmp_path, "second.pst", "Archive", [mail(1, ("Projects",)), mail(3, INBOX)])
    if together:
        result = run(engine, (first, second))
        assert (result.mail_imported, result.mail_existing) == (3, 1)
    else:
        run(engine, first)
        assert (run(engine, second).mail_imported, run(engine, (first, second)).mail_existing) == (1, 0)
    assert [subject for subject, _ in where(engine, LOCAL_ACCOUNT_ID)] == ["Message 1", "Message 2", "Message 3"]


def test_the_journal_is_written_only_when_the_batch_is_safe_on_disk(tmp_path: Path, files: dict[Path, FakeFile], monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path / "profile")
    path = data_file(files, tmp_path, "personal.pst", "Personal Folders")

    @contextmanager
    def failing(self: Store) -> Generator[None]:
        yield
        raise StorageError("The disk is full")

    monkeypatch.setattr(Store, "written_together", failing)
    with pytest.raises(StorageError):
        run(engine, path, calendar=False, contacts=False, tasks=False)
    journal = next((engine.directories.data_directory / JOURNAL_FOLDER).iterdir())
    assert not any(line.startswith('["mail"') for line in journal.read_text("utf-8").splitlines())


def test_an_empty_folder_of_notes_is_not_mentioned(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    path = tmp_path / "file.pst"
    path.write_bytes(b"made up")
    folders = (PstFolder(INBOX, None, FolderRole.INBOX, items=1), PstFolder(("Journaal",), None, None, unsupported="journal entries", items=0))
    files[path.resolve()] = FakeFile(PstStore("Personal Folders", folders), [mail(1, INBOX)])
    assert scan(engine, str(path)).files[0].left_out == ()
    assert not any("Journaal" in note for note in run(engine, path).notes)


def test_a_file_whose_reading_fails_before_any_item_is_tried_again_later(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    path = data_file(files, tmp_path, "personal.pst", "Personal Folders")
    files[path.resolve()].broken_at = 0
    result = run(engine, path, batch=5)
    assert (result.done, result.left) == (0, 0) and any("could not be read further" in note for note in result.notes)
    files[path.resolve()].broken_at = None
    again = run(engine, path)
    assert again.mail_imported == 6 and files[path.resolve()].starts == [0, 0]


def test_tags_ticked_later_give_the_items_imported_before_their_categories_once(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    content: list[PstItem] = [
        mail(1, INBOX, categories=("Work",)), mail(2, INBOX), event("ev-1@example.com", "Meeting", ("Agenda",), categories=("Work",)),
        PstTask(("Taken",), task("task-1@example.com", "Write the plan", ("Taken",)).ical, ("Home",)),
        contact("Anna Example", "anna@example.com", ("Contactpersonen",), categories=("Family",)),
    ]
    path = data_file(files, tmp_path, "personal.pst", "Personal Folders", content)
    first = run(engine, path, tags=False)
    assert first.tags_added == 0 and first.items_tagged == 0
    assert dict((subject, tags) for subject, (_, _, tags) in messages(engine, LOCAL_ACCOUNT_ID).items())["Message 1"] == ()
    second = run(engine, path)  # Tags ticked now: what came in before gets its categories
    assert (second.done, second.items_tagged, second.tags_added) == (4, 4, 3)  # giving tags counts as the work of the batch
    assert messages(engine, LOCAL_ACCOUNT_ID)["Message 1"][2] == ("Work",)
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store:
        texts = [store.get(info.collection_id, row.item_id) for info in store.collections() for row in [
            store.index.find_uid(info.collection_id, uid) for uid in ("ev-1@example.com", "task-1@example.com")] if row is not None]
    assert any("CATEGORIES:Work" in text for text in texts) and any("CATEGORIES:Home" in text for text in texts)
    listed = REGISTRY.run(engine, "contact.list", {})
    assert isinstance(listed, ContactList)
    assert [row.tags for row in listed.contacts if row.name == "Anna Example"] == [("Family",)]
    assert run(engine, path).items_tagged == 0  # once


def test_a_retry_after_a_batch_that_could_not_be_made_safe_stores_nothing_twice(tmp_path: Path, files: dict[Path, FakeFile], monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path / "profile")
    path = data_file(files, tmp_path, "personal.pst", "Personal Folders")
    real = Store.written_together
    failures = [1]

    @contextmanager
    def failing_once(self: Store) -> Generator[None]:
        with real(self):
            yield
        if failures:
            failures.pop()
            raise StorageError("The disk is full")

    monkeypatch.setattr(Store, "written_together", failing_once)
    with pytest.raises(StorageError):
        run(engine, path, calendar=False, contacts=False, tasks=False)
    again = run(engine, path, calendar=False, contacts=False, tasks=False)  # Import again in the same session: the run is not forgotten by hand
    assert (again.mail_imported, again.mail_existing) == (6, 0)  # what the failed batch stored is taken as these items' copies, none stored again
    assert len(where(engine, LOCAL_ACCOUNT_ID)) == 6


ZONED_EVENT = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//EN\r\nBEGIN:VTIMEZONE\r\nTZID:Made-up Standard Time\r\nBEGIN:STANDARD\r\nDTSTART:19700101T000000\r\n"
    "TZOFFSETFROM:+0530\r\nTZOFFSETTO:+0530\r\nTZNAME:MUT\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\nBEGIN:VEVENT\r\nUID:zoned-1@example.com\r\nDTSTAMP:20240301T100000Z\r\n"
    "CREATED:20200101T080000Z\r\nDTSTART;TZID=Made-up Standard Time:20240305T100000\r\nDTEND;TZID=Made-up Standard Time:20240305T110000\r\nSUMMARY:Call\r\n"
    "X-EXAMPLE-KEPT:yes\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)
ZONED_TASK = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//EN\r\nBEGIN:VTIMEZONE\r\nTZID:Made-up Standard Time\r\nBEGIN:STANDARD\r\nDTSTART:19700101T000000\r\n"
    "TZOFFSETFROM:+0530\r\nTZOFFSETTO:+0530\r\nTZNAME:MUT\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\nBEGIN:VTODO\r\nUID:zoned-2@example.com\r\nDTSTAMP:20240301T100000Z\r\n"
    "CREATED:20200101T080000Z\r\nDUE;TZID=Made-up Standard Time:20240305T100000\r\nSUMMARY:Report\r\nX-EXAMPLE-KEPT:yes\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
)


def test_an_event_and_a_task_keep_their_own_time_zone_and_what_this_program_does_not_know(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    from datetime import timedelta

    from chrono_mail.pim.ical import parse_events, parse_tasks

    engine = make_engine(tmp_path / "profile")
    content: list[PstItem] = [PstEvent(("Agenda",), ZONED_EVENT), PstTask(("Taken",), ZONED_TASK)]
    run(engine, data_file(files, tmp_path, "personal.pst", "Personal Folders", content))
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store:
        texts = {uid: store.get(info.collection_id, row.item_id) for info in store.collections() for uid in ("zoned-1@example.com", "zoned-2@example.com")
                 for row in [store.index.find_uid(info.collection_id, uid)] if row is not None}
    for text in texts.values():
        assert "BEGIN:VTIMEZONE" in text and "X-EXAMPLE-KEPT:yes" in text and "CREATED:20200101T080000Z" in text
    start = parse_events(texts["zoned-1@example.com"])[0].master.start
    due = parse_tasks(texts["zoned-2@example.com"])[0].due
    assert isinstance(start, datetime) and start.utcoffset() == timedelta(hours=5, minutes=30)
    assert isinstance(due, datetime) and due.utcoffset() == timedelta(hours=5, minutes=30)


def test_every_event_of_one_item_gets_its_categories_later(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    two = event("a@example.com", "First", ("Agenda",)).ical.replace("END:VCALENDAR\r\n", "") + "BEGIN:VEVENT\r\nUID:b@example.com\r\nDTSTAMP:20240301T100000Z\r\n" \
        "DTSTART:20240306T100000Z\r\nDTEND:20240306T110000Z\r\nSUMMARY:Second\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    path = data_file(files, tmp_path, "personal.pst", "Personal Folders", [PstEvent(("Agenda",), two, ("Work",))])
    assert run(engine, path, tags=False).events_imported == 1
    assert run(engine, path).items_tagged == 2
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store:
        info = store.collections(CollectionKind.EVENTS)[0]
        for uid in ("a@example.com", "b@example.com"):
            row = store.index.find_uid(info.collection_id, uid)
            assert row is not None and "CATEGORIES:Work" in store.get(info.collection_id, row.item_id)


def test_a_message_whose_tags_could_not_be_given_stays_imported_and_gets_them_later(tmp_path: Path, files: dict[Path, FakeFile], monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.engine.mailbox import Mailbox

    engine = make_engine(tmp_path / "profile")
    copy = PstMail(("Projects",), mail(1, INBOX).raw, SEEN, ("Work",))
    path = data_file(files, tmp_path, "personal.pst", "Personal Folders", [mail(1, INBOX, categories=("Work",)), copy])
    real = Mailbox.set_tags
    failing = [1]

    def set_tags(self: Mailbox, reference: MessageRef, tags: tuple[str, ...]) -> tuple[str, ...]:
        if failing:
            failing.pop()
            raise StorageError("The properties could not be written")
        return real(self, reference, tags)

    monkeypatch.setattr(Mailbox, "set_tags", set_tags)
    result = run(engine, path)
    assert (result.mail_imported, result.mail_failed) == (2, 0)  # the copy in another folder of the same file too, as Outlook had it
    assert sorted(place for _, place in where(engine, LOCAL_ACCOUNT_ID)) == [("Inbox",), ("Projects",)]
    assert run(engine, path).items_tagged == 1
    assert all(tags == ("Work",) for tags in tags_of_all(engine))


def tags_of_all(engine: Engine) -> list[tuple[str, ...]]:
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        return [mailbox.tags_of(message.store_id) for folder in mailbox.store.list_folders() for message in mailbox.index.page(folder.path, limit=1000)]


def test_giving_tags_later_goes_in_batches_with_progress(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    content: list[PstItem] = [mail(number, INBOX, categories=("Work",)) for number in range(1, 6)]
    path = data_file(files, tmp_path, "personal.pst", "Personal Folders", content)
    run(engine, path, tags=False)
    seen: list[tuple[str, int]] = []
    engine.progress = lambda what, done, total: seen.append((what, done))
    first = run(engine, path, batch=2)
    assert (first.items_tagged, first.done) == (2, 2) and first.left > 0 and [done for _, done in seen][-2:] == [6, 7]
    second = run(engine, path, batch=2)
    third = run(engine, path, batch=2)
    assert (second.items_tagged, third.items_tagged, third.left) == (2, 1, 0)


# --- event.import and task.import keep only what is safe of the text they are given ---------------------------------------------------


def stored_after_import(engine: Engine, command: str, kind: str, text: str) -> list[str]:
    """Import text with event.import or task.import into Local Folders' own calendar or task list; the texts stored there."""
    from chrono_mail.engine.commands_pim import CollectionSummary

    places = REGISTRY.run(engine, "pim.enable", {})
    assert isinstance(places, tuple)
    collection = next(place.collection for place in places if isinstance(place, CollectionSummary) and place.kind == kind)
    REGISTRY.run(engine, command, {"calendar" if command == "event.import" else "list": collection, "text": text})
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store:
        return [store.get(collection, row.item_id) for row in store.index.query(ItemQuery(None, (collection,)))]


EVENT_LINES = "BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20240301T100000Z\r\nDTSTART:{start}\r\nDTEND:20240305T110000Z\r\nSUMMARY:{summary}\r\nEND:VEVENT\r\n"


def calendar_text(*parts: str, method: str = "", version: bool = True) -> str:
    head = "BEGIN:VCALENDAR\r\n" + ("VERSION:2.0\r\n" if version else "") + "PRODID:-//Example//EN\r\n" + (f"METHOD:{method}\r\n" if method else "")
    return head + "".join(parts) + "END:VCALENDAR\r\n"


@pytest.mark.parametrize("method", ["REQUEST", "PUBLISH"])
def test_an_imported_event_is_stored_without_a_method(tmp_path: Path, method: str) -> None:
    engine = make_engine(tmp_path / "profile")
    texts = stored_after_import(engine, "event.import", "events", calendar_text(EVENT_LINES.format(uid="m@example.com", start="20240305T100000Z", summary="Call"), method=method))
    assert len(texts) == 1 and "METHOD" not in texts[0]


def test_a_mixed_text_stores_only_the_kind_imported(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    todo = "BEGIN:VTODO\r\nUID:t@example.com\r\nDTSTAMP:20240301T100000Z\r\nSUMMARY:Other\r\nEND:VTODO\r\n"
    event_text = EVENT_LINES.format(uid="e@example.com", start="20240305T100000Z", summary="Call")
    events = stored_after_import(engine, "event.import", "events", calendar_text(event_text, todo))
    tasks = stored_after_import(engine, "task.import", "tasks", calendar_text(todo, event_text))
    assert len(events) == 1 and "VTODO" not in events[0] and len(tasks) == 1 and "VEVENT" not in tasks[0]


def test_an_event_without_a_stamp_and_a_version_gets_them(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    bare = EVENT_LINES.format(uid="s@example.com", start="20240305T100000Z", summary="Call").replace("DTSTAMP:20240301T100000Z\r\n", "")
    texts = stored_after_import(engine, "event.import", "events", calendar_text(bare, version=False))
    assert "DTSTAMP:" in texts[0] and "VERSION:2.0" in texts[0]


def test_two_main_events_of_one_uid_and_a_damaged_one_give_one_main_event(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    damaged = EVENT_LINES.format(uid="d@example.com", start="2024XX05T100000", summary="Damaged")
    first = EVENT_LINES.format(uid="d@example.com", start="20240305T100000Z", summary="First")
    second = EVENT_LINES.format(uid="d@example.com", start="20240306T100000Z", summary="Second")
    texts = stored_after_import(engine, "event.import", "events", calendar_text(damaged, first, second))
    assert len(texts) == 1 and texts[0].count("BEGIN:VEVENT") == 1 and "SUMMARY:First" in texts[0]


def tag_notes(result: ImportResult) -> list[str]:
    return [note for note in result.notes if "tags" in note]


def import_until_done(engine: Engine, path: Path, **values: object) -> list[ImportResult]:
    """Import as the window does, batch after batch, until nothing is left (at most 10 runs: more is a loop that never ends)."""
    results = [run(engine, path, **values)]
    while results[-1].left > 0 and len(results) < 10:
        results.append(run(engine, path, **values))
    return results


def test_tags_for_mail_of_an_account_removed_since_end_the_import_with_one_note(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    engine = make_engine(tmp_path / "profile")
    account = pop3_account(engine, "anna@example.com")
    content: list[PstItem] = [mail(number, INBOX, categories=("Work",)) for number in range(1, 6)]
    path = data_file(files, tmp_path, "anna.pst", "anna@example.com", content)
    run(engine, path, tags=False)
    REGISTRY.run(engine, "account.remove", {"account": account})
    results = import_until_done(engine, path, batch=2)
    assert results[-1].left == 0 and len(results) == 3
    assert all(len(tag_notes(result)) == 1 for result in results) and "no longer here" in tag_notes(results[0])[0]
    assert run(engine, path).done == 0  # given up for good: never tried again


def test_tags_that_keep_failing_are_tried_once_per_import(tmp_path: Path, files: dict[Path, FakeFile], monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path / "profile")
    content: list[PstItem] = [mail(number, INBOX, categories=("Work",)) for number in range(1, 6)]
    path = data_file(files, tmp_path, "personal.pst", "Personal Folders", content)
    run(engine, path, tags=False)
    from chrono_mail.engine.mailbox import Mailbox

    real = Mailbox.set_tags

    def failing(self: Mailbox, reference: MessageRef, tags: tuple[str, ...]) -> tuple[str, ...]:
        raise StorageError("The properties could not be written")

    monkeypatch.setattr(Mailbox, "set_tags", failing)
    results = import_until_done(engine, path, batch=2)
    assert results[-1].left == 0 and len(results) == 3 and all(len(tag_notes(result)) == 1 for result in results)
    monkeypatch.setattr(Mailbox, "set_tags", real)
    commands_outlook.finished_run()  # a later import tries them again
    assert run(engine, path).items_tagged == 5


def test_a_failed_batch_keeps_what_it_stored_for_the_retry(tmp_path: Path, files: dict[Path, FakeFile], monkeypatch: pytest.MonkeyPatch) -> None:
    """The batch stored the message in Inbox but could not be made safe: Import again keeps that copy (no second one) and still imports the
    copy in Projects of the same file, as Outlook had it; Tags later tags both."""
    engine = make_engine(tmp_path / "profile")
    copy = PstMail(("Projects",), mail(1, INBOX).raw, SEEN, ("Work",))
    path = data_file(files, tmp_path, "personal.pst", "Personal Folders", [mail(1, INBOX, categories=("Work",)), copy])
    real = Store.written_together
    failures = [1]

    @contextmanager
    def failing_once(self: Store) -> Generator[None]:
        with real(self):
            yield
        if failures:
            failures.pop()
            raise StorageError("The disk is full")

    monkeypatch.setattr(Store, "written_together", failing_once)
    with pytest.raises(StorageError):
        run(engine, path, tags=False, batch=1)
    again = run(engine, path, tags=False)
    assert (again.mail_imported, again.mail_existing) == (2, 0)
    assert sorted(place for _, place in where(engine, LOCAL_ACCOUNT_ID)) == [("Inbox",), ("Projects",)]
    assert run(engine, path).items_tagged == 2
    assert tags_of_all(engine) == [("Work",), ("Work",)]


def test_a_changed_occurrence_keeps_its_own_properties_whatever_form_its_moment_is_written_in() -> None:
    from chrono_mail.pim.ical import given_parts, kept_from

    occurrence = "BEGIN:VEVENT\r\nUID:r@example.com\r\nDTSTAMP:20240301T100000Z\r\nRECURRENCE-ID{}\r\nDTSTART:20240312T110000Z\r\nSUMMARY:Moved\r\n{}END:VEVENT\r\n"
    main = "BEGIN:VEVENT\r\nUID:r@example.com\r\nDTSTAMP:20240301T100000Z\r\nDTSTART:20240305T090000Z\r\nRRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:Weekly\r\nEND:VEVENT\r\n"
    given = calendar_text(main, occurrence.format(";TZID=Europe/Amsterdam:20240312T100000", "X-EXAMPLE-OCCURRENCE:kept\r\n"))
    fresh = calendar_text(main, occurrence.format(":20240312T090000Z", ""))  # the same instant, written in UTC
    assert "X-EXAMPLE-OCCURRENCE:kept" in kept_from(fresh, given_parts(given, "VEVENT"), "r@example.com", "VEVENT")



def test_the_note_that_not_every_contact_was_looked_through_is_told_once_per_file(tmp_path: Path, files: dict[Path, FakeFile]) -> None:
    from chrono_mail.pst.reading import CONTACTS_PARTIAL

    engine = make_engine(tmp_path / "profile")
    content: list[PstItem] = [mail(number, INBOX) for number in range(1, 7)]
    first = data_file(files, tmp_path, "first.pst", "Personal Folders", content)
    files[first.resolve()].note_each_call = CONTACTS_PARTIAL
    results = import_until_done(engine, first, batch=2)
    assert len(results) == 3 and sum(result.unreadable for result in results) == 1
    assert [failure for result in results for failure in result.failures] == [f"Contactpersonen: {CONTACTS_PARTIAL}"]
    assert sum(result.mail_imported for result in results) == 6  # the note never takes an item's place
    second = data_file(files, tmp_path, "second.pst", "Archive", [mail(number, ("Old",)) for number in range(7, 9)])
    files[second.resolve()].note_each_call = CONTACTS_PARTIAL
    commands_outlook.finished_run()  # a resumed import of the first file, and a second file with its own note
    both = run(engine, (first, second))
    assert both.unreadable == 1 and both.failures == (f"Contactpersonen: {CONTACTS_PARTIAL}",) and both.mail_imported == 2
