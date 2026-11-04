from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chrono_mail.config import AppConfig, load_config, save_config
from chrono_mail.engine.registry import UsageError
from chrono_mail.pim.itip import reply_text, without_organizer
from chrono_mail.sync.davitems import ItemGateway, ServerRefused
from tests.fakedav import FakeDav
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import answer, drafts
from tests.test_engine_invitations import calendar_text, deliver, invitation_mail

# --- Version ------------------------------------------------------------
__version__ = "test_engine_invitation_mailing.py 4.4.510"

# --- Version history ----------------------------------------------------
# v4.4.510: Initial tests: who mails the answer to an invitation (D-310): the server, Chrono Mail or nobody, never two and never none unless chosen


@pytest.fixture
def server(setup: Setup) -> Iterator[FakeDav]:  # noqa: F811 - the fixture of this module's import
    """A calendar server that mails by itself, linked to the account that has me@example.com."""
    with FakeDav() as fake:
        fake.announces = "calendar-auto-schedule"
        fake.user_addresses = ("mailto:me@example.com",)
        call(setup.engine, "account.set-dav", account=setup.account_id, url=fake.url, username="ann", password="secret")
        call(setup.engine, "dav.sync", account=setup.account_id)
        yield fake


def settings(setup: Setup, **values: object) -> None:  # noqa: F811
    def change(config: AppConfig) -> AppConfig:
        return config.with_account(replace(config.find(setup.account_id), **values))  # type: ignore[arg-type]

    setup.engine.change_config(change)


def calendar_id(setup: Setup) -> str:  # noqa: F811
    return next(item.collection for item in call(setup.engine, "collection.list") if item.kind == "events" and item.account == setup.account_id)


def on_server(fake: FakeDav) -> list[str]:
    return [text for _, text in fake.collections["events"].items.values()]


def writes(fake: FakeDav) -> list[str]:
    return [method for method, _ in fake.requests if method in ("PUT", "DELETE")]


def invited(setup: Setup) -> str:  # noqa: F811
    return deliver(setup, invitation_mail(calendar_text()))


def test_a_server_that_mails_gets_the_copy_as_it_is_and_chrono_mail_makes_no_mail(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    result = answer(setup, invited(setup), "ACCEPTED", calendar_id(setup))
    assert result.server_answers and result.draft_message is None and drafts(setup) == []
    assert "SCHEDULE-AGENT" not in on_server(server)[0]


def test_when_chrono_mail_sends_the_server_is_told_to_stay_silent(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    settings(setup, invitation_answers="chrono")
    result = answer(setup, invited(setup), "ACCEPTED", calendar_id(setup))
    assert not result.server_answers and len(drafts(setup)) == 1
    copy = on_server(server)[0].replace("\r\n ", "")
    assert "ORGANIZER;" in copy and all("SCHEDULE-AGENT=CLIENT" in line for line in copy.splitlines() if line.startswith(("ORGANIZER", "ATTENDEE")))


def test_with_the_box_off_nothing_goes_out_also_on_a_decline(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    """The server deletes from what it holds, so a copy kept from when the server mailed is told to stay silent first."""
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    settings(setup, invitation_answers="none")
    server.requests.clear()
    result = answer(setup, message, "DECLINED")
    assert not result.server_answers and drafts(setup) == [] and on_server(server) == []
    assert writes(server) == ["PUT", "DELETE"]


def test_a_copy_told_to_stay_silent_is_told_otherwise_when_the_server_mails_again(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    message = invited(setup)
    settings(setup, invitation_answers="none")
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    settings(setup, invitation_answers="server")
    answer(setup, message, "TENTATIVE")
    assert "SCHEDULE-AGENT" not in on_server(server)[0]
    settings(setup, invitation_answers="none")
    answer(setup, message, "ACCEPTED")
    settings(setup, invitation_answers="server")
    server.requests.clear()
    assert answer(setup, message, "DECLINED").server_answers  # the mark is off before the delete: else the organizer gets no answer
    assert writes(server) == ["PUT", "DELETE"] and drafts(setup) == []


def test_a_server_that_always_mails_leaves_no_choice(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    settings(setup, invitation_answers="none", dav_always_answers=True)
    message = invited(setup)
    assert answer(setup, message, "ACCEPTED", calendar_id(setup)).server_answers
    server.requests.clear()
    assert answer(setup, message, "DECLINED").server_answers
    assert writes(server) == ["DELETE"] and drafts(setup) == []


def test_another_address_on_a_server_that_always_mails_gets_one_correct_answer(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    """Measured on Kerio: for an address that is not its user it mails nothing on Accept, and on a delete a DECLINED in its user's name, which
    is no answer of the invited person. Without an organizer the delete mails nothing, so Chrono Mail's answer is the one mail."""
    settings(setup, dav_always_answers=True, dav_addresses=("someone-else@example.com",))
    message = invited(setup)
    accepted = answer(setup, message, "ACCEPTED", calendar_id(setup))
    assert not accepted.server_answers and len(drafts(setup)) == 1
    server.requests.clear()
    declined = answer(setup, message, "DECLINED")
    assert not declined.server_answers and declined.draft_message is not None
    assert writes(server) == ["PUT", "DELETE"] and on_server(server) == []
    bare = without_organizer(calendar_text())
    assert bare is not None and "ORGANIZER" not in bare and "ATTENDEE" not in bare and "SUMMARY:Choice of colours" in bare


def test_a_refused_delete_leaves_the_meeting_as_it_was(setup: Setup, server: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    settings(setup, dav_always_answers=True, dav_addresses=("someone-else@example.com",))
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    before = on_server(server)[0]
    assert "ORGANIZER" in before

    def refused(*_: object) -> None:
        raise ServerRefused("not allowed")

    monkeypatch.setattr(ItemGateway, "delete", refused)
    with pytest.raises(UsageError):
        answer(setup, message, "DECLINED")
    assert "ORGANIZER" in on_server(server)[0] and "ATTENDEE" in on_server(server)[0]


def test_a_decline_before_the_meeting_reached_the_server_is_mailed_by_chrono_mail(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    """P7: Accept offline, Decline before the server saw it: it never hears of the meeting, so it cannot answer."""
    message = invited(setup)
    server.writes_failing = True
    assert answer(setup, message, "ACCEPTED", calendar_id(setup)).server_answers  # sent later, then the server mails
    declined = answer(setup, message, "DECLINED")
    assert not declined.server_answers and declined.draft_message is not None and len(drafts(setup)) == 1
    server.writes_failing = False
    call(setup.engine, "dav.sync", account=setup.account_id)
    assert on_server(server) == []


def test_a_server_that_announces_nothing_mails_only_when_the_person_chose_it(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    """Not certain what it does: the person's choice counts; left alone, Chrono Mail sends, and the server is told nothing either way."""
    settings(setup, invitation_answers="", dav_auto_schedule=False)
    assert answer(setup, invited(setup), "ACCEPTED", calendar_id(setup)).draft_message is not None
    settings(setup, invitation_answers="server", dav_auto_schedule=False)
    assert answer(setup, invited(setup), "ACCEPTED", calendar_id(setup)).draft_message is None
    assert all("SCHEDULE-AGENT" not in each for each in on_server(server))


def test_a_delegation_with_the_box_off_passes_it_on_and_tells_the_organizer_nothing(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    settings(setup, invitation_answers="none")
    result = call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=invited(setup), to="sam@example.net")
    assert result.answer_message == "" and len(drafts(setup)) == 1


def test_the_synchronisation_keeps_what_the_server_said(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    server.product = "Kerio Connect 8.5.2"
    server.user_addresses = ("mailto:Me@Example.com", "/principals/me", "mailto:second@example.com")
    call(setup.engine, "dav.sync", account=setup.account_id)
    found = setup.engine.config().find(setup.account_id)
    assert found.dav_auto_schedule and found.dav_always_answers and found.dav_addresses == ("me@example.com", "second@example.com")
    assert found.answers_by == "server"
    server.product = "Another Server 1.0"
    call(setup.engine, "dav.sync", account=setup.account_id)
    assert not setup.engine.config().find(setup.account_id).dav_always_answers


def test_the_choice_survives_a_restart(setup: Setup, server: FakeDav, tmp_path: Path) -> None:  # noqa: F811
    settings(setup, invitation_answers="none", dav_always_answers=True, dav_addresses=("me@example.com",))
    path = tmp_path / "again.toml"
    save_config(path, setup.engine.config())
    found = load_config(path).find(setup.account_id)
    assert (found.invitation_answers, found.dav_always_answers, found.dav_addresses) == ("none", True, ("me@example.com",))


def test_a_delegates_answer_says_who_they_replace() -> None:
    """RFC 5546 section 3.2.2.3: the organizer sees whose place the answer takes."""
    text = calendar_text(you="sam@example.net").replace("ATTENDEE;CN=Me;", 'ATTENDEE;CN=Me;DELEGATED-FROM="mailto:me@example.com";')
    reply = reply_text(text, "sam@example.net", "ACCEPTED", datetime(2026, 11, 4, tzinfo=UTC))
    assert reply is not None and 'DELEGATED-FROM="mailto:me@example.com"' in reply.replace("\r\n ", "")
    assert "DELEGATED-FROM" not in (reply_text(calendar_text(), "me@example.com", "ACCEPTED", datetime(2026, 11, 4, tzinfo=UTC)) or "")
