from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chrono_mail.config import AppConfig, load_config, save_config
from chrono_mail.core import MessageFlag
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.engine.registry import UsageError
from chrono_mail.pim.itip import reply_text, without_organizer
from chrono_mail.protocols.dav import DavClient
from chrono_mail.sync.davitems import ItemGateway, ServerRefused
from tests.fakedav import FakeDav
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import answer, drafts
from tests.test_engine_invitations import calendar_text, deliver, invitation_mail

# --- Version ------------------------------------------------------------
__version__ = "test_engine_invitation_mailing.py 4.4.554"

# --- Version history ----------------------------------------------------
# v4.4.554: A Decline is one write that stays in the calendar; the waiting delete is tested through Change my answer (D-310)
# v4.4.526: an answer says it waits for the connection (D-310)
# v4.4.520: an answer the server mails sets the Replied mark (D-310)
# v4.4.516: Change my answer tells nobody (D-310)
# v4.4.515: a Decline while offline mails as chosen, a Decline refused twice keeps the meeting with a sync conflict (D-310)
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


def calendar_items(setup: Setup) -> list[str]:  # noqa: F811
    with open_pim(setup.engine, setup.account_id) as store:
        return [store.get(calendar_id(setup), item) for item in store.index.item_ids(calendar_id(setup))]


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
    """A copy kept from when the server mailed is told to stay silent, in the one write of the answer: the event stays in its calendar."""
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    settings(setup, invitation_answers="none")
    server.requests.clear()
    result = answer(setup, message, "DECLINED")
    assert not result.server_answers and drafts(setup) == []
    assert writes(server) == ["PUT"] and "DECLINED" in on_server(server)[0] and "SCHEDULE-AGENT=CLIENT" in on_server(server)[0]


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
    assert answer(setup, message, "DECLINED").server_answers  # the mark goes with the answer: else the organizer gets no answer
    assert writes(server) == ["PUT"] and "SCHEDULE-AGENT" not in on_server(server)[0] and drafts(setup) == []


def test_a_server_that_always_mails_leaves_no_choice(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    settings(setup, invitation_answers="none", dav_always_answers=True)
    message = invited(setup)
    assert answer(setup, message, "ACCEPTED", calendar_id(setup)).server_answers
    server.requests.clear()
    assert answer(setup, message, "DECLINED").server_answers
    assert writes(server) == ["PUT"] and "DECLINED" in on_server(server)[0] and drafts(setup) == []


def test_another_address_on_a_server_that_always_mails_gets_one_correct_answer(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    """Measured on Kerio: for an address that is not its user it mails nothing on Accept nor on a saved Decline, so Chrono Mail's answer is the one mail.
    On a delete it mails a DECLINED in its user's name, which is no answer of the invited person: without an organizer the delete mails nothing."""
    settings(setup, dav_always_answers=True, dav_addresses=("someone-else@example.com",))
    message = invited(setup)
    accepted = answer(setup, message, "ACCEPTED", calendar_id(setup))
    assert not accepted.server_answers and len(drafts(setup)) == 1
    server.requests.clear()
    declined = answer(setup, message, "DECLINED")
    assert not declined.server_answers and declined.draft_message is not None
    assert writes(server) == ["PUT"] and "DECLINED" in on_server(server)[0] and "ORGANIZER" in on_server(server)[0]
    bare = without_organizer(calendar_text())
    assert bare is not None and "ORGANIZER" not in bare and "ATTENDEE" not in bare and "SUMMARY:Choice of colours" in bare


def reopen(setup: Setup, message: str):  # noqa: ANN201, F811 - the command's own answer
    return call(setup.engine, "invitation.reopen", account=setup.account_id, folder="Inbox", message=message)


def test_change_my_answer_removes_the_meeting_without_a_word_to_the_organizer(setup: Setup, server: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    sent = put_texts(server, monkeypatch)
    server.requests.clear()
    assert reopen(setup, message).removed
    assert writes(server) == ["PUT", "DELETE"] and "SCHEDULE-AGENT=CLIENT" in sent[0]  # told to stay silent first, though it mails your answers
    assert on_server(server) == [] and calendar_items(setup) == [] and drafts(setup) == []
    assert not reopen(setup, message).removed
    answer(setup, message, "TENTATIVE", calendar_id(setup))
    assert "TENTATIVE" in calendar_items(setup)[0]


def test_change_my_answer_takes_the_organizer_off_a_server_that_mails_whatever_it_is_told(setup: Setup, server: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    settings(setup, dav_always_answers=True)
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    sent = put_texts(server, monkeypatch)
    reopen(setup, message)
    assert "ORGANIZER" not in sent[0] and on_server(server) == []


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
        reopen(setup, message)
    assert "ORGANIZER" in on_server(server)[0] and "ATTENDEE" in on_server(server)[0]


def put_texts(fake: FakeDav, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every text the server is given from now on, in order."""
    seen: list[str] = []
    original = DavClient.put

    def watched(client: DavClient, href: str, text: str, *rest: object, **options: object) -> str:
        seen.append(text)
        return original(client, href, text, *rest, **options)  # type: ignore[arg-type]

    monkeypatch.setattr(DavClient, "put", watched)
    return seen


def declined_offline(setup: Setup, fake: FakeDav, message: str) -> object:  # noqa: F811
    fake.writes_failing = True
    result = answer(setup, message, "DECLINED")
    assert "DECLINED" in calendar_items(setup)[0] and "DECLINED" not in on_server(fake)[0]  # hidden here at once, on the server when the connection is back
    fake.writes_failing = False
    fake.requests.clear()
    return result


def reopened_offline(setup: Setup, fake: FakeDav, message: str) -> None:  # noqa: F811
    """Change my answer on an accepted meeting: its copy goes here at once and waits to go on the server."""
    fake.writes_failing = True
    reopen(setup, message)
    assert calendar_items(setup) == [] and on_server(fake) != []
    fake.writes_failing = False
    fake.requests.clear()


def test_offline_with_server_chosen_the_server_mails_the_decline_when_the_connection_is_back(setup: Setup, server: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    message = invited(setup)
    settings(setup, invitation_answers="none")
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    assert "SCHEDULE-AGENT=CLIENT" in on_server(server)[0]
    settings(setup, invitation_answers="server")
    result = declined_offline(setup, server, message)
    assert result.server_answers and result.waiting and drafts(setup) == []  # type: ignore[attr-defined]
    sent = put_texts(server, monkeypatch)
    call(setup.engine, "dav.sync", account=setup.account_id)
    assert writes(server) == ["PUT"] and "DECLINED" in on_server(server)[0] and drafts(setup) == []
    assert "SCHEDULE-AGENT" not in sent[0]  # the mark is off: else the server stays silent and nobody tells the organizer


def test_offline_with_chrono_mail_chosen_the_server_stays_silent_and_one_mail_goes(setup: Setup, server: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    assert "SCHEDULE-AGENT" not in on_server(server)[0]
    settings(setup, invitation_answers="chrono")
    declined_offline(setup, server, message)
    assert len(drafts(setup)) == 1
    sent = put_texts(server, monkeypatch)
    call(setup.engine, "dav.sync", account=setup.account_id)
    assert writes(server) == ["PUT"] and "DECLINED" in on_server(server)[0] and "SCHEDULE-AGENT=CLIENT" in sent[0]


def test_offline_the_organizer_is_taken_off_first_on_a_server_that_mails_whatever_it_is_told(setup: Setup, server: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    settings(setup, dav_always_answers=True, dav_addresses=("someone-else@example.com",))
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    reopened_offline(setup, server, message)
    sent = put_texts(server, monkeypatch)
    call(setup.engine, "dav.sync", account=setup.account_id)
    assert writes(server) == ["PUT", "DELETE"] and on_server(server) == [] and "ORGANIZER" not in sent[0]


def test_a_decline_that_waits_counts_as_one_change(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    settings(setup, invitation_answers="chrono")
    told: list[str] = []
    setup.engine.notice = told.append  # type: ignore[method-assign]
    server.writes_failing = True
    answer(setup, message, "DECLINED")
    assert len(told) == 1 and "1 change waiting" in told[0]


def test_a_delete_still_failing_after_the_copy_went_sends_only_the_delete_next_time(setup: Setup, server: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    settings(setup, invitation_answers="chrono")
    reopened_offline(setup, server, message)
    original = DavClient.delete

    def busy(client: DavClient, href: str, etag: str) -> None:
        server.writes_failing = True  # the copy went, the delete does not
        try:
            original(client, href, etag)
        finally:
            server.writes_failing = False

    monkeypatch.setattr(DavClient, "delete", busy)
    call(setup.engine, "dav.sync", account=setup.account_id)
    monkeypatch.setattr(DavClient, "delete", original)
    assert len(on_server(server)) == 1 and "SCHEDULE-AGENT=CLIENT" in on_server(server)[0]
    server.requests.clear()
    call(setup.engine, "dav.sync", account=setup.account_id)
    assert writes(server) == ["DELETE"] and on_server(server) == []


def test_a_meeting_changed_on_the_server_while_change_my_answer_waited_is_a_conflict(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    settings(setup, invitation_answers="chrono")
    reopened_offline(setup, server, message)
    ((href, (_, text)),) = server.collections["events"].items.items()
    server.collections["events"].items[href] = ("changed-there", text.replace("SUMMARY:", "SUMMARY:Moved "))
    call(setup.engine, "dav.sync", account=setup.account_id)
    assert "DELETE" not in writes(server) and len(on_server(server)) == 1
    assert [conflict.reason for conflict in call(setup.engine, "conflict.list").conflicts] == ["changed_on_server"]


def test_a_meeting_changed_on_the_server_while_the_decline_waited_is_a_conflict(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    settings(setup, invitation_answers="chrono")
    declined_offline(setup, server, message)
    ((href, (_, text)),) = server.collections["events"].items.items()
    server.collections["events"].items[href] = ("changed-there", text.replace("SUMMARY:", "SUMMARY:Moved "))
    call(setup.engine, "dav.sync", account=setup.account_id)
    assert "SUMMARY:Moved" in on_server(server)[0] and "DECLINED" not in on_server(server)[0]  # the server's stays until the person settles it
    assert [conflict.reason for conflict in call(setup.engine, "conflict.list").conflicts] == ["clash"]


def test_a_decline_the_server_refuses_leaves_the_meeting_as_it_was_and_mails_nothing(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    """Server first: the event is hidden only when the server confirms."""
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    before = calendar_items(setup)
    settings(setup, invitation_answers="chrono")
    server.writes_refused = True
    with pytest.raises(UsageError):
        answer(setup, message, "DECLINED")
    assert calendar_items(setup) == before and "DECLINED" not in on_server(server)[0] and drafts(setup) == []


def test_change_my_answer_refused_twice_keeps_the_meeting_and_a_conflict_until_it_is_settled(setup: Setup, server: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    original = calendar_items(setup)
    settings(setup, invitation_answers="chrono")
    original_delete = DavClient.delete

    def refused(client: DavClient, href: str, etag: str) -> None:
        server.writes_refused = True  # the copy went, from here on nothing goes
        original_delete(client, href, etag)

    monkeypatch.setattr(DavClient, "delete", refused)
    with pytest.raises(UsageError, match="kept here as it was.*Settings > Sync conflicts"):
        reopen(setup, message)
    assert calendar_items(setup) == original and "SCHEDULE-AGENT=CLIENT" in on_server(server)[0]
    assert drafts(setup) == []  # nobody is told
    (conflict,) = call(setup.engine, "conflict.list").conflicts
    assert conflict.reason == "refused"
    call(setup.engine, "dav.sync", account=setup.account_id)
    assert calendar_items(setup) == original  # waits for the person
    result = call(setup.engine, "conflict.settle", conflicts=(conflict.conflict,), side="local")
    assert result.open == (conflict.conflict,) and calendar_items(setup) == original
    assert len(call(setup.engine, "conflict.list").conflicts) == 1  # a refused settling leaves the conflict
    server.writes_refused = False
    assert call(setup.engine, "conflict.settle", conflicts=(conflict.conflict,), side="server").settled == (conflict.conflict,)
    assert call(setup.engine, "conflict.list").conflicts == () and len(calendar_items(setup)) == 1


def test_a_decline_before_the_meeting_reached_the_server_goes_there_as_one_write(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    """Accept offline, Decline before the server saw it: the one change that waits says Declined, and the server mails that, as an Accept that waits."""
    message = invited(setup)
    server.writes_failing = True
    assert answer(setup, message, "ACCEPTED", calendar_id(setup)).server_answers  # sent later, then the server mails
    declined = answer(setup, message, "DECLINED")
    assert declined.server_answers and declined.draft_message is None and drafts(setup) == []
    server.writes_failing = False
    server.requests.clear()
    call(setup.engine, "dav.sync", account=setup.account_id)
    assert writes(server) == ["PUT"] and "DECLINED" in on_server(server)[0]


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


def test_an_answer_the_server_mails_sets_the_replied_mark_at_once(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        assert MessageFlag.REPLIED in mailbox.current_flags(mailbox.reference(mailbox.folder("Inbox"), message))

