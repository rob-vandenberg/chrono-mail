import re
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email.policy import default
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from chrono_mail.config import InMemoryPasswordStore, profile_directories
from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind, MessageFlag
from chrono_mail.crypto import InMemoryKeyStore
from chrono_mail.engine import Engine
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_combined import EVERYWHERE
from chrono_mail.engine.commands_conflicts import conflict_list, sync_waiting
from chrono_mail.engine.commands_pim import ListParameters, collection_list, tag_list
from chrono_mail.engine.commands_schedule import AccountOptional, mail_scheduled, message_snoozed
from chrono_mail.engine.commands_sidebar import sidebar_show
from chrono_mail.engine.invitations import invitation_of
from chrono_mail.engine.pimaccess import open_pim, pim_account_ids
from chrono_mail.mime.parser import parse_message
from chrono_mail.pim.ical import occurrences, parse_events, parse_tasks
from chrono_mail.pimstore import ItemQuery
from chrono_mail.ui.app import WINDOW_SETTINGS_FILE
from tools.demo.build import BuiltDemo, build_demo
from tools.demo.mail import SPAM_SHARE
from tools.demo.meetings import coming_week, tasks_with_reminders
from tools.demo.pim import TOTAL_CONTACTS, TOTAL_EVENTS, TOTAL_TASKS
from tools.demo.tagging import DEMO_TAGS

# --- Version ------------------------------------------------------------
__version__ = "test_demo_mail.py 4.4.614"

# --- Version history ----------------------------------------------------
# v4.4.614: (review) nothing of the coming week that starts today carries a reminder (no pop-up on the screenshots)
# v4.4.614: the tags with their Show in choices, more calendars with the blue plus, the coming week, reminders, the invitations of the Work
#           Inbox, Favorites with All Messages, three address books, two sync conflicts, and every address and server on an example domain
# v3.4.345: TODAY is the hour the test runs: the engine checks scheduled and snoozed times against the real clock, so a fixed day
#           made the demo's scheduled message lie in the past from the next day on
# v2.3.321: Initial: the demo's mail, calendars, tasks and contacts, built small (D-243)

MESSAGES: int = 800
TODAY: datetime = datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)  # the same for every test of a run
FAKE_WORDS: re.Pattern[str] = re.compile(r"\b(dummy|test|sample|lorem|ipsum|placeholder|foo|bar)\b", re.IGNORECASE)
EXAMPLE_DOMAIN: re.Pattern[str] = re.compile(r"(^|\.)example\.(com|org|net)$")


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> tuple[BuiltDemo, Engine]:
    """One small demo for every test of this file (built once: a few seconds)."""
    passwords, keys = InMemoryPasswordStore(), InMemoryKeyStore()
    built = build_demo(tmp_path_factory.mktemp("demo") / "profile", passwords, keys, MESSAGES, today=TODAY)
    return built, Engine(profile_directories(built.folder), passwords, keys)


def every_message(engine: Engine) -> list[tuple[tuple[str, ...], bytes]]:
    found: list[tuple[tuple[str, ...], bytes]] = []
    for place in [settings for settings in engine.config().accounts if settings.storage != "local"]:
        store = engine.open_store(engine.storage_settings(place))
        for folder in store.list_folders():
            found.extend((folder.path, store.read(message.ref)) for message in store.list_messages(folder.path))
    return found


def test_the_demo_has_the_messages_asked_for_with_its_share_of_spam(demo: tuple[BuiltDemo, Engine]) -> None:
    built, engine = demo
    messages = every_message(engine)
    assert built.messages == MESSAGES
    assert len(messages) == MESSAGES + 3 + 1 + 3 + 5  # with the drafts, the one in the Outbox, the scheduled ones and the calendar mail of Work
    junk = [raw for folder, raw in messages if folder == ("Junk",)]
    assert round(MESSAGES * SPAM_SHARE) - 2 <= len(junk) <= round(MESSAGES * SPAM_SHARE) + 2  # its share, not more


def test_the_mail_spans_five_years_up_to_today(demo: tuple[BuiltDemo, Engine]) -> None:
    _, engine = demo
    dates = [parsedate_to_datetime(str(message_from_bytes(raw, policy=default)["Date"])) for _, raw in every_message(engine)]
    received = [date for date in dates if date <= TODAY]
    assert min(received) >= TODAY - timedelta(days=5 * 365 + 1)
    assert min(received) <= TODAY - timedelta(days=4 * 365)  # really five years, not a few months
    assert max(received) >= TODAY - timedelta(days=30)


def test_only_made_up_people_on_example_domains_and_no_test_words(demo: tuple[BuiltDemo, Engine]) -> None:
    _, engine = demo
    for _, raw in every_message(engine):
        message = message_from_bytes(raw, policy=default)
        for _, address in getaddresses([str(message.get("From", "")), str(message.get("To", ""))]):
            if address:
                assert EXAMPLE_DOMAIN.search(address.rpartition("@")[2]), address
        text = str(message["Subject"]) + " " + " ".join(str(part.get_content()) for part in message.walk() if part.get_content_type() == "text/plain")
        assert FAKE_WORDS.search(text) is None, text[:200]


def test_nothing_waits_for_a_server(demo: tuple[BuiltDemo, Engine]) -> None:
    _, engine = demo
    assert sync_waiting(engine, NoParameters()).accounts == ()


def test_scheduled_snoozed_drafts_and_outbox_are_there(demo: tuple[BuiltDemo, Engine]) -> None:
    _, engine = demo
    scheduled = mail_scheduled(engine, AccountOptional())
    assert len(scheduled) == 3 and sum(1 for item in scheduled if item.repeat) == 2
    assert len(message_snoozed(engine, AccountOptional())) == 5
    folders = [folder for folder, _ in every_message(engine)]
    assert folders.count(("Drafts",)) == 3 and folders.count(("Outbox",)) == 1


def test_calendars_tasks_and_contacts(demo: tuple[BuiltDemo, Engine]) -> None:
    built, engine = demo
    assert built.events >= TOTAL_EVENTS and built.tasks == TOTAL_TASKS + len(tasks_with_reminders(TODAY)) and built.contacts == TOTAL_CONTACTS
    collections = collection_list(engine, ListParameters())
    shared = [collection for collection in collections if collection.read_only]
    assert len(shared) == 2 and len({collection.account for collection in shared}) == 2 and len({collection.name for collection in shared}) == 1
    assert any(collection.account == LOCAL_ACCOUNT_ID and collection.name == "Birthdays" for collection in collections)
    assert {collection.kind for collection in collections} == {"events", "tasks", "contacts"}


def test_the_junk_filter_has_learned(demo: tuple[BuiltDemo, Engine]) -> None:
    built, _ = demo
    assert (built.folder / "data" / "junk-filter.bin").stat().st_size > 0


def test_the_same_demo_every_time(tmp_path: Path) -> None:
    subjects: list[list[str]] = []
    for name in ("first", "second"):
        passwords, keys = InMemoryPasswordStore(), InMemoryKeyStore()
        built = build_demo(tmp_path / name, passwords, keys, 120, today=TODAY)
        engine = Engine(profile_directories(built.folder), passwords, keys)
        subjects.append(sorted(str(message_from_bytes(raw, policy=default)["Subject"]) for _, raw in every_message(engine)))
    assert subjects[0] == subjects[1]


ADDRESS: re.Pattern[str] = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
SERVER: re.Pattern[str] = re.compile(r"https?://([\w.-]+)")


def every_item(engine: Engine, kind: CollectionKind) -> list[tuple[str, str]]:
    """(collection, text) of every item of this kind in every account."""
    found: list[tuple[str, str]] = []
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            found.extend((item.collection, store.get(item.collection, item.item_id)) for item in store.index.query(ItemQuery(kind, limit=100_000)))
    return found


def window_setting(engine: Engine, key: str) -> str:
    return str(QSettings(str(engine.directories.config_directory / WINDOW_SETTINGS_FILE), QSettings.Format.IniFormat).value(key, ""))


def test_one_list_of_tags_each_shown_in_its_sections_and_in_use_in_every_section(demo: tuple[BuiltDemo, Engine]) -> None:
    """D-332: one list, each tag with its colour and Show in choices; D-327, D-334: every section's Tags block lists several tags with counts."""
    _, engine = demo
    tags = {tag.name: tag for tag in tag_list(engine, NoParameters())}
    for name, colour, kinds, shortcut in DEMO_TAGS:
        assert (tags[name].colour, tags[name].kinds, tags[name].shortcut) == (colour, kinds, shortcut)
    assert sum(1 for tag in tags.values() if tag.mails) >= 3
    assert sum(1 for tag in tags.values() if tag.events) >= 5
    assert sum(1 for tag in tags.values() if tag.tasks) >= 5
    assert sum(1 for tag in tags.values() if tag.contacts) >= 5
    assert all(tag.mails == 0 for tag in tags.values() if "mail" not in tag.kinds)  # a tag is used only where it is shown


def test_an_appointment_has_a_colour_tag_and_other_tags(demo: tuple[BuiltDemo, Engine]) -> None:
    """D-329: the first tag gives the colour; an appointment with more tags shows them all."""
    _, engine = demo
    tagged = [series.master.categories for _, text in every_item(engine, CollectionKind.EVENTS) for series in parse_events(text)]
    assert ("Clients", "Important") in tagged and ("Family", "Important") in tagged


def test_several_calendars_a_read_only_one_and_the_blue_plus(demo: tuple[BuiltDemo, Engine]) -> None:
    """D-326: new appointments go to Work, new tasks to Personal; a read-only calendar can take none."""
    built, engine = demo
    collections = collection_list(engine, ListParameters())
    calendars = [collection for collection in collections if collection.kind == "events"]
    for account in ("work", "personal"):
        assert len([calendar for calendar in calendars if calendar.account == built.account_ids[account]]) >= 3
    plus = {collection.collection: collection for collection in collections}
    assert plus[window_setting(engine, "new_items_events")].name == "Work" and not plus[window_setting(engine, "new_items_events")].read_only
    assert plus[window_setting(engine, "new_items_tasks")].name == "Personal"
    assert [collection.name for collection in collections if collection.kind == "tasks"].count("Shopping list") == 1


def test_the_coming_week_is_busy_with_repeats_a_cancelled_appointment_and_reminders(demo: tuple[BuiltDemo, Engine]) -> None:
    """D-316, D-317: the Week and Day views from the build date look real."""
    _, engine = demo
    texts = [text for _, text in every_item(engine, CollectionKind.EVENTS)]
    midnight = TODAY.replace(hour=0)
    week = occurrences(texts, midnight.astimezone(UTC), (midnight + timedelta(days=8)).astimezone(UTC))  # today and the seven days after it, whole
    assert len(week) >= 15
    assert any(occurrence.event.status == "CANCELLED" and not occurrence.event.rrule for occurrence in week)  # the print check
    assert any(occurrence.event.status == "CANCELLED" and occurrence.changed for occurrence in week)  # one date of the weekly yoga
    repeating = {series.master.uid for text in texts for series in parse_events(text) if series.master.repeats}
    assert sum(1 for occurrence in week if occurrence.event.uid in repeating) >= 5  # tennis, yoga, swimming, football, the studio meeting …
    assert sum(1 for occurrence in week if occurrence.event.reminders) >= 5
    tasks = [task for _, text in every_item(engine, CollectionKind.TASKS) for task in parse_tasks(text)]
    reminded = [task for task in tasks if task.reminders and not task.done]
    assert len(reminded) >= 3 and any(reminder.at is not None for task in reminded for reminder in task.reminders)


def test_the_work_inbox_has_invitations_to_answer_answered_and_from_her_own_meeting(demo: tuple[BuiltDemo, Engine]) -> None:
    """D-310, D-311: the cards of the Work Inbox, read by Chrono Mail's own code."""
    built, engine = demo
    store = engine.open_store(engine.resolve_account(built.account_ids["work"]))
    cards: list[tuple[str, str, int, frozenset[MessageFlag], str]] = []
    for message in store.list_messages(("Inbox",)):
        parsed = parse_message(store.read(message.ref))
        card = invitation_of(engine, parsed.attachments)
        if card is not None:
            cards.append((card.method, card.your_answer, card.items, message.flags, parsed.attachments[card.position].filename or ""))
    methods = sorted((method, answer) for method, answer, _, _, _ in cards)
    assert methods == [("PUBLISH", ""), ("REPLY", "ACCEPTED"), ("REQUEST", ""), ("REQUEST", "ACCEPTED"), ("REQUEST", "DECLINED")]
    assert all(MessageFlag.REPLIED in flags for method, answer, _, flags, _ in cards if method == "REQUEST" and answer)
    assert all(MessageFlag.REPLIED not in flags for method, answer, _, flags, _ in cards if method == "REQUEST" and not answer)
    assert [items for method, _, items, _, _ in cards if method == "PUBLISH"] == [4]
    assert all(name.endswith(".ics") for *_, name in cards)


def test_favorites_show_all_messages_and_contacts_have_three_address_books_with_groups(demo: tuple[BuiltDemo, Engine]) -> None:
    """D-334: All Messages first in Favorites (not hidden); a tick per address book in Contacts."""
    _, engine = demo
    sidebar = sidebar_show(engine, NoParameters())
    assert EVERYWHERE not in sidebar.hidden and len(sidebar.pins) == 2
    books = {collection.collection: collection.name for collection in collection_list(engine, ListParameters()) if collection.kind == "contacts"}
    assert sorted(books.values()) == ["Members", "Personal contacts", "Studio contacts"]
    cards = every_item(engine, CollectionKind.CONTACTS)
    for book in books:
        texts = [text for collection, text in cards if collection == book]
        assert any("KIND:group" in text for text in texts) and sum(1 for text in texts if "KIND:group" not in text) >= 2


def test_two_sync_conflicts_an_appointment_and_a_contact(demo: tuple[BuiltDemo, Engine]) -> None:
    """D-194, D-308: each changed here and on the server in a field the person sees; nothing else waits for a server."""
    _, engine = demo
    conflicts = conflict_list(engine, NoParameters()).conflicts
    assert sorted(conflict.kind for conflict in conflicts) == ["contacts", "events"]
    assert all(conflict.fields and conflict.local_allowed and conflict.local_text and conflict.server_text and conflict.base_text for conflict in conflicts)
    assert sync_waiting(engine, NoParameters()).accounts == ()


def test_every_address_domain_and_server_of_the_demo_is_on_an_example_domain(demo: tuple[BuiltDemo, Engine]) -> None:
    """OR-40, D-243: the screenshots show only example.com, example.org and example.net (RFC 2606), so no real person or company appears."""
    _, engine = demo
    domains: set[str] = set()
    for settings in engine.config().accounts:
        account = settings.account
        if account.account_id == LOCAL_ACCOUNT_ID:
            continue  # Local Folders: no server, only the program's own stand-in
        domains.update(ADDRESS.findall(account.email_address))
        domains.update(host for host in (account.incoming.host, account.outgoing.host) if host)
        domains.update(SERVER.findall(settings.dav_url))
    for _, raw in every_message(engine):
        message = message_from_bytes(raw, policy=default)
        for name, value in message.items():
            if name.lower() in ("from", "to", "cc", "reply-to", "message-id", "in-reply-to", "references", "received"):
                domains.update(ADDRESS.findall(str(value)))
                domains.update(re.findall(r"(?:from|by) ([\w.-]+\.[a-z]+)", str(value)) if name.lower() == "received" else ())
        for part in message.walk():
            if part.get_content_maintype() == "text":
                text = re.sub(r"\r?\n[ \t]", "", str(part.get_content()))  # a calendar part's long lines are folded (RFC 5545)
                domains.update(ADDRESS.findall(text))
                domains.update(SERVER.findall(text))
    for kind in CollectionKind:
        for _, folded in every_item(engine, kind):
            text = re.sub(r"\r?\n[ \t]", "", folded)  # long lines are folded (RFC 5545, RFC 6350): read as one
            domains.update(ADDRESS.findall(text))
            domains.update(SERVER.findall(text))
    assert len(domains) > 20
    assert [domain for domain in sorted(domains) if not EXAMPLE_DOMAIN.search(domain.lower())] == []


def test_nothing_of_the_coming_week_that_starts_today_has_a_reminder() -> None:
    """D-243: a reminder of today would pop up while the screenshots are taken; today is a working day and a Wednesday in turn."""
    for today in (TODAY, *(TODAY + timedelta(days=offset) for offset in range(1, 7))):
        for _, text in coming_week(today):
            for series in parse_events(text):
                start = series.master.start
                if isinstance(start, datetime) and not series.master.repeats and start.astimezone().date() == today.date():
                    assert series.master.reminders == (), series.master.summary

