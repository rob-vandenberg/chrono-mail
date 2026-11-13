from dataclasses import replace
from pathlib import Path

import pytest

from chrono_mail.config import AccountSettings, AppConfig, new_account_id
from chrono_mail.config.files import config_to_text, parse_config
from chrono_mail.core import AccountKind, CollectionKind, ProtocolError
from chrono_mail.pim.contact import Contact
from chrono_mail.core.account import Account, ConnectionSecurity, ServerSettings
from chrono_mail.engine import Engine, commands_dav
from chrono_mail.engine.commands_accounts import account_label, holds, list_name, shown_name
from tests.enginefactory import make_engine
from tests.test_engine_commands import call

# --- Version ------------------------------------------------------------
__version__ = "test_account_names_google.py 4.4.608"

# --- Version history ----------------------------------------------------
# v4.4.608: Initial tests: an account's name and what it holds, Google's name read again at every synchronisation, a calendar named only by
#           an address shown by a name (D-346); reviews: no lookup for a changed appointment, only the contacts with the address read,
#           a name found kept until something changes, an unreadable contact names nobody, a big address book paged through

CALDAV = "https://apidata.googleusercontent.com/caldav/v2/ann%40gmail.com/user"
CARDDAV = "https://www.googleapis.com/.well-known/carddav"


def google(url: str, account_name: str = "", google_name: str = "Ann Lee") -> AccountSettings:
    host = url.split("/")[2]
    server = ServerSettings(host, 443, ConnectionSecurity.TLS, "ann@gmail.com")
    return AccountSettings(Account(new_account_id(), "Ann Lee", "", AccountKind.DAV, server, server), dav_url=url, google=True,
                           account_name=account_name, google_name=google_name)


def mail(address: str, host: str, kind: AccountKind = AccountKind.POP3, account_name: str = "", dav_url: str = "") -> AccountSettings:
    server = ServerSettings(host, 995 if kind is AccountKind.POP3 else 993, ConnectionSecurity.TLS, address)
    return AccountSettings(Account(new_account_id(), "Ann Lee", address, kind, server, server), account_name=account_name, dav_url=dav_url)


def test_an_account_is_listed_by_its_name_and_address_then_what_it_holds() -> None:
    """The owner (D-346): Settings > Accounts shows the name and address, below it what the account holds; no server."""
    calendar, contacts = google(CALDAV, account_name="Rob"), google(CARDDAV)
    pop3 = mail("ann@example.com", "mail.example.com")
    kerio = mail("ann@example.org", "mailserver.example.org", AccountKind.IMAP, dav_url="https://mailserver.example.org:11443")
    accounts = (calendar, contacts, pop3, kerio)
    assert [(list_name(item), holds(item, accounts)) for item in accounts] == [
        ("Rob <ann@gmail.com>", "Google Calendar"),
        ("Ann Lee <ann@gmail.com>", "Google Contacts"),  # no Shown as name: Google's name for the person
        ("ann@example.com", "Mail (POP3)"),
        ("ann@example.org", "Mail (IMAP), Calendars and contacts"),
    ]


def test_the_server_shows_only_when_two_accounts_would_look_the_same() -> None:
    first, second = mail("ann@example.com", "mail.example.com", AccountKind.IMAP), mail("ann@example.com", "mailserver.example.com", AccountKind.IMAP)
    with_calendars = mail("ann@example.com", "mail.example.org", AccountKind.IMAP, dav_url="https://mail.example.org/dav")
    assert holds(first, (first, second)) == "Mail (IMAP) - mail.example.com" and holds(second, (first, second)) == "Mail (IMAP) - mailserver.example.com"
    assert holds(first, (first, with_calendars)) == "Mail (IMAP)"  # the second line already tells them apart


def test_google_s_two_parts_of_one_name_are_told_apart_by_what_they_hold() -> None:
    calendar, contacts = google(CALDAV, account_name="Rob"), google(CARDDAV, account_name="Rob")
    accounts = (calendar, contacts)
    assert [shown_name(item, accounts) for item in accounts] == ["Rob - Google Calendar", "Rob - Google Contacts"]
    assert [account_label(item, accounts) for item in accounts] == ["Rob - Google Calendar", "Rob - Google Contacts"]


def test_an_account_without_a_shown_as_name_is_named_by_google_and_by_its_address_without_one() -> None:
    named, unnamed = google(CALDAV), google(CARDDAV, google_name="")
    assert shown_name(named, ()) == "Ann Lee" and list_name(unnamed) == "ann@gmail.com"


def test_google_s_name_is_kept_in_the_settings_file_apart_from_shown_as() -> None:
    config = AppConfig(accounts=(google(CALDAV, google_name="Ann Lee"),))
    again = parse_config(config_to_text(config)).accounts[0]
    assert (again.google_name, again.account_name) == ("Ann Lee", "")


def with_account(engine: Engine, settings: AccountSettings) -> str:
    engine.change_config(lambda fresh: fresh.with_account(settings))
    return settings.account.account_id


def test_google_s_name_is_read_again_at_every_synchronisation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-346, D-193: a name changed at Google shows here; a failed question or an empty answer keeps the name kept."""
    engine = make_engine(tmp_path)
    account = with_account(engine, google(CALDAV, google_name="Ann Lee"))
    answers: list[str | Exception] = ["Ann Smith", ProtocolError("Google could not be reached"), ""]

    def profile_name(access: str) -> str:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(commands_dav, "profile_name", profile_name)
    for expected in ("Ann Smith", "Ann Smith", "Ann Smith"):
        commands_dav._record_google_name(engine, account, "access-1")  # noqa: SLF001 - the step every synchronisation of a Google account takes
        settings = engine.config().find(account)
        assert (settings.google_name, settings.account_name) == (expected, "")


def test_a_calendar_named_only_by_an_address_is_shown_by_a_name(tmp_path: Path) -> None:
    """The owner (D-346): your own address shows your account's name, someone else's the name of the contact; else the address."""
    engine = make_engine(tmp_path)
    collections = {summary.kind: summary.collection for summary in call(engine, "pim.enable")}
    with_account(engine, google(CALDAV, account_name="", google_name="Ann Lee"))
    call(engine, "contact.add", collection=collections["contacts"], name="Bob Stone", email=("bob@example.com",))
    for name in ("ann@gmail.com", "bob@example.com", "carol@example.com", "Robedoes"):
        call(engine, "collection.create", kind="events", name=name)
    shown = {item.name for item in call(engine, "collection.list") if item.kind == "events"}
    assert {"Ann Lee", "Bob Stone", "carol@example.com", "Robedoes"} <= shown
    assert not shown & {"ann@gmail.com", "bob@example.com"}


def test_an_unnamed_account_s_calendar_keeps_its_address(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    call(engine, "pim.enable")
    with_account(engine, replace(google(CALDAV), google_name=""))  # no Shown as name and no name from Google: the address is all there is
    call(engine, "collection.create", kind="events", name="ann@gmail.com")
    assert "ann@gmail.com" in {item.name for item in call(engine, "collection.list") if item.kind == "events"}


def test_a_changed_item_looks_no_name_up_and_a_list_reads_only_the_contacts_with_the_address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reviews of 4.4.608: the name of a calendar named by an address is looked up for the facts about the calendar itself, never for each
    changed appointment; a lookup reads only the contacts the index finds for that address, never the whole address book."""
    from chrono_mail.engine import calendarnames

    engine = make_engine(tmp_path)
    collections = {summary.kind: summary.collection for summary in call(engine, "pim.enable")}
    for name, address in (("Bob Stone", "bob@example.com"), ("Dan Hill", "dan@example.net"), ("Eve Moss", "eve@example.org")):
        call(engine, "contact.add", collection=collections["contacts"], name=name, email=(address,))
    calendar = call(engine, "collection.create", kind="events", name="bob@example.com").collection
    call(engine, "collection.create", kind="events", name="carol@example.com")
    read: list[str] = []
    original = calendarnames.parse_contacts

    def parse_contacts(text: str) -> list[Contact]:
        found = original(text)
        read.extend(contact.full_name() for contact in found)
        return found

    monkeypatch.setattr(calendarnames, "parse_contacts", parse_contacts)
    call(engine, "event.add", calendar=calendar, summary="Lunch", start="2026-11-20T12:00", end="2026-11-20T13:00")
    assert read == []  # an appointment added: no name looked up
    engine.contact_names.forget()  # as after a change of a contact: the list looks the addresses up
    shown = {item.name for item in call(engine, "collection.list") if item.kind == "events"}
    assert {"Bob Stone", "carol@example.com"} <= shown and read == ["Bob Stone"]  # only the contact that has the address was read


def test_a_changed_google_name_renames_a_calendar_named_by_its_address_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A review of 4.4.608: the synchronisation that finds a new name tells the calendars, so the window shows it without a Refresh."""
    from chrono_mail.engine.changes import Change
    from chrono_mail.engine.pimaccess import open_pim

    engine = make_engine(tmp_path)
    call(engine, "pim.enable")  # the data key the calendars are kept with
    account = with_account(engine, google(CALDAV, google_name="Ann Lee"))
    with open_pim(engine, account, create=True) as store:
        store.create_collection(CollectionKind.EVENTS, "ann@gmail.com", "")
    told: list[Change] = []
    monkeypatch.setattr(engine, "report", told.append)
    def renamed(access: str) -> str:
        return "Ann Smith"

    monkeypatch.setattr(commands_dav, "profile_name", renamed)
    with open_pim(engine, account) as store:
        if commands_dav._record_google_name(engine, account, "access-1"):  # noqa: SLF001 - as the synchronisation does
            commands_dav.tell_palettes(engine, account, store)
    assert [change.name for change in told if change.kind == "collection_edited"] == ["Ann Smith"]


def test_a_name_found_is_kept_until_something_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review rounds 3 and 4 of 4.4.608: the views and a changed appointment do not make the address be looked up again; a changed contact does."""
    from chrono_mail.engine import calendarnames

    engine = make_engine(tmp_path)
    collections = {summary.kind: summary.collection for summary in call(engine, "pim.enable")}
    bob = call(engine, "contact.add", collection=collections["contacts"], name="Bob Stone", email=("bob@example.com",))
    looked: list[str] = []
    original = calendarnames.CollectionNamer._contact_name  # noqa: SLF001

    def counted(self: calendarnames.CollectionNamer, address: str) -> str:
        looked.append(address)
        return original(self, address)

    monkeypatch.setattr(calendarnames.CollectionNamer, "_contact_name", counted)
    calendar = call(engine, "collection.create", kind="events", name="bob@example.com").collection  # its fact names it
    for _ in range(3):
        call(engine, "event.add", calendar=calendar, summary="Lunch", start="2026-11-20T12:00", end="2026-11-20T13:00")  # never lets it go
        assert "Bob Stone" in {item.name for item in call(engine, "collection.list")}
    assert looked == ["bob@example.com"]  # looked up once: the lists use the name kept
    call(engine, "contact.edit", item=bob.item, name="Robert Stone")  # a contact changed: looked up anew
    assert "Robert Stone" in {item.name for item in call(engine, "collection.list")} and len(looked) == 2


def test_a_contact_that_cannot_be_read_names_nobody_and_breaks_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.core import CryptoError
    from chrono_mail.engine import calendarnames

    engine = make_engine(tmp_path)
    collections = {summary.kind: summary.collection for summary in call(engine, "pim.enable")}
    call(engine, "contact.add", collection=collections["contacts"], name="Bob Stone", email=("bob@example.com",))

    def damaged(text: str) -> list[Contact]:
        raise CryptoError("this contact could not be decrypted")

    monkeypatch.setattr(calendarnames, "parse_contacts", damaged)
    call(engine, "collection.create", kind="events", name="bob@example.com")
    assert "bob@example.com" in {item.name for item in call(engine, "collection.list")}


def test_the_contact_is_found_in_a_big_address_book_whose_words_match_often(tmp_path: Path) -> None:
    """Review round 3 of 4.4.608: the index finds every contact with the address's words; the one with the address is read even after 50 others."""
    engine = make_engine(tmp_path)
    collections = {summary.kind: summary.collection for summary in call(engine, "pim.enable")}
    for number in range(60):
        call(engine, "contact.add", collection=collections["contacts"], name=f"Aa Jansen {number:02}", email=(f"jansen{number:02}@company.nl",))
    call(engine, "contact.add", collection=collections["contacts"], name="Zz Jan", email=("jan@company.nl",))  # sorts after all of them
    call(engine, "collection.create", kind="events", name="jan@company.nl")
    assert "Zz Jan" in {item.name for item in call(engine, "collection.list")}


def test_a_busy_index_or_a_contact_without_a_name_names_nobody(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review round 4 of 4.4.608: a database that is busy now never makes a list fail; a contact without a name never names a calendar by
    another of its addresses."""
    import sqlite3

    from chrono_mail.engine import calendarnames

    engine = make_engine(tmp_path)
    collections = {summary.kind: summary.collection for summary in call(engine, "pim.enable")}
    call(engine, "contact.add", collection=collections["contacts"], email=("first@example.com", "second@example.com"), organization="")
    call(engine, "collection.create", kind="events", name="second@example.com")
    assert "second@example.com" in {item.name for item in call(engine, "collection.list")}  # never "first@example.com"
    engine.contact_names.forget()

    def busy(*args: object, **kwargs: object) -> object:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(calendarnames, "open_pim", busy)
    assert "second@example.com" in {item.name for item in call(engine, "collection.list")}
