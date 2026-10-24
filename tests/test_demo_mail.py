import re
from datetime import datetime, timedelta
from email import message_from_bytes
from email.policy import default
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

import pytest

from chrono_mail.config import InMemoryPasswordStore, profile_directories
from chrono_mail.core import LOCAL_ACCOUNT_ID
from chrono_mail.crypto import InMemoryKeyStore
from chrono_mail.engine import Engine
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_conflicts import sync_waiting
from chrono_mail.engine.commands_pim import ListParameters, collection_list
from chrono_mail.engine.commands_schedule import AccountOptional, mail_scheduled, message_snoozed
from tools.demo.build import BuiltDemo, build_demo
from tools.demo.mail import SPAM_SHARE
from tools.demo.pim import TOTAL_CONTACTS, TOTAL_EVENTS, TOTAL_TASKS

# --- Version ------------------------------------------------------------
__version__ = "test_demo_mail.py 3.4.345"

# --- Version history ----------------------------------------------------
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
    assert len(messages) == MESSAGES + 3 + 1 + 3  # with the drafts, the one in the Outbox and the scheduled ones
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
    assert built.events >= TOTAL_EVENTS and built.tasks == TOTAL_TASKS and built.contacts == TOTAL_CONTACTS
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
