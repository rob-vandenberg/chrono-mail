from dataclasses import replace

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.engine.registry import UsageError
from chrono_mail.mime import parse_message
from chrono_mail.pim.ical import parse_tasks, tasks_to_text
from chrono_mail.pim.itip import proposal_usable, read_calendar_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import drafts
from tests.test_engine_invitations import deliver, invitation_mail, show

# --- Version ------------------------------------------------------------
__version__ = "test_engine_invitation_tasks.py 4.4.555"

# --- Version history ----------------------------------------------------
# v4.4.555: A cancelled task stays, Cancelled (D-310)
# v4.4.507: Initial tests: every flow of an invitation also works on a task, in task lists, in the person's words (D-310 step 4)


def task_text(method: str = "REQUEST", sequence: int = 0, answer: str = "NEEDS-ACTION", due: str = "20261105T170000Z", organizer: str = "olivia@example.org",
              attendee: str = "me@example.com", extra: tuple[str, ...] = ()) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Example//Example//EN", *([f"METHOD:{method}"] if method else []), "BEGIN:VTODO",
             "UID:task-1@example.com", f"SEQUENCE:{sequence}", "DTSTAMP:20261028T090000Z", f"DUE:{due}", "SUMMARY:Water the plants",
             f"ORGANIZER;CN=The organizer:mailto:{organizer}", f"ATTENDEE;CN=Attendee;PARTSTAT={answer};RSVP=TRUE:mailto:{attendee}", *extra, "END:VTODO", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def task_list(setup: Setup) -> str:
    ensure_local_pim(setup.engine)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        (tasks,) = store.collections(CollectionKind.TASKS)
        return tasks.collection_id


def stored_task(setup: Setup, text: str) -> None:
    identifier = task_list(setup)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        store.put(identifier, text)


def kept_tasks(setup: Setup) -> list[str]:
    identifier = task_list(setup)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        return [store.get(identifier, item) for item in store.index.item_ids(identifier)]


def from_sam(calendar: str) -> bytes:
    return invitation_mail(calendar).replace(b"From: Olivia Organizer <olivia@example.org>", b"From: Sam <sam@example.net>", 1)


def run(setup: Setup, command: str, message: str, **more: object):  # noqa: ANN201 - the command's own answer
    return call(setup.engine, command, account=setup.account_id, folder="Inbox", message=message, **more)


def your_task(method: str = "", answer: str = "NEEDS-ACTION", due: str = "20261105T170000Z", sequence: int = 0) -> str:
    """A task you organize, with Sam on it."""
    return task_text(method, sequence, answer, due, organizer="me@example.com", attendee="sam@example.net")


def test_a_task_invitation_is_shown_as_a_task_with_its_due_moment(setup: Setup) -> None:
    card = show(setup, deliver(setup, invitation_mail(task_text()))).invitation
    assert card is not None and card.kind == "task" and card.title == "Water the plants" and card.start is not None and card.start.day == 5


def test_the_choice_of_where_it_goes_lists_task_lists(setup: Setup) -> None:
    identifier = task_list(setup)
    choices = run(setup, "invitation.choices", deliver(setup, invitation_mail(task_text())))
    assert [each.collection for each in choices.calendars] == [identifier]


def test_accept_keeps_the_task_in_the_task_list_and_makes_the_answer(setup: Setup) -> None:
    identifier = task_list(setup)
    result = run(setup, "invitation.answer", deliver(setup, invitation_mail(task_text())), answer="ACCEPTED", calendar=identifier, mail=True)
    (text,) = kept_tasks(setup)
    assert result.stored_in and "PARTSTAT=ACCEPTED" in text and "METHOD" not in text and "ORGANIZER" in text
    (draft,) = drafts(setup)
    part = next(each for each in parse_message(draft).attachments if each.content_type == "text/calendar")
    reply = read_calendar_message(part.data)
    assert reply is not None and reply.method == "REPLY" and reply.kind == "task" and reply.attendees[0].status == "ACCEPTED"
    assert b"BEGIN:VTODO" in part.data and b"DUE" in part.data


def test_decline_removes_the_task_you_had(setup: Setup) -> None:
    stored_task(setup, task_text(method=""))
    run(setup, "invitation.answer", deliver(setup, invitation_mail(task_text())), answer="DECLINED", mail=False)
    assert kept_tasks(setup) == []


def test_a_newer_version_from_the_organizer_asks_again_when_its_due_moment_moved(setup: Setup) -> None:
    stored_task(setup, task_text(method="", answer="ACCEPTED"))
    result = run(setup, "invitation.apply", deliver(setup, invitation_mail(task_text(sequence=1, due="20261106T170000Z"))))
    (text,) = kept_tasks(setup)
    assert result.done == "updated" and "DUE:20261106T170000Z" in text and "PARTSTAT=NEEDS-ACTION" in text


def test_a_cancelled_task_stays_in_the_task_list(setup: Setup) -> None:
    stored_task(setup, task_text(method=""))
    assert run(setup, "invitation.apply", deliver(setup, invitation_mail(task_text("CANCEL", sequence=1)))).done == "cancelled"
    assert "STATUS:CANCELLED" in kept_tasks(setup)[0]


def test_delegate_passes_the_task_on_in_the_persons_words(setup: Setup) -> None:
    run(setup, "invitation.delegate", deliver(setup, invitation_mail(task_text())), to="pat@example.net")
    bodies = [parse_message(draft).text_body or "" for draft in drafts(setup)]
    assert any("do this task in their place" in body for body in bodies) and not any("meeting" in body for body in bodies)


def test_an_attendees_progress_goes_into_your_task(setup: Setup) -> None:
    stored_task(setup, your_task())
    card = show(setup, deliver(setup, from_sam(your_task("REPLY", answer="IN-PROCESS")))).invitation
    assert card is not None and card.attendees[0].answer == "IN-PROCESS"
    message = deliver(setup, from_sam(your_task("REPLY", answer="COMPLETED")))
    result = run(setup, "invitation.organize", message, action="take")
    (text,) = kept_tasks(setup)
    assert result.done == "taken" and "PARTSTAT=COMPLETED" in text


def test_an_accepted_proposal_moves_the_due_moment_and_asks_everyone_again(setup: Setup) -> None:
    stored_task(setup, your_task(answer="ACCEPTED"))
    result = run(setup, "invitation.organize", deliver(setup, from_sam(your_task("COUNTER", due="20261107T170000Z"))), action="accept_proposal", mail=True)
    (text,) = kept_tasks(setup)
    assert result.done == "moved" and "DUE:20261107T170000Z" in text and "SEQUENCE:1" in text and "PARTSTAT=NEEDS-ACTION" in text
    (draft,) = drafts(setup)
    parsed = parse_message(draft)
    assert "The task has changed." in (parsed.text_body or "")
    request = next(each for each in parsed.attachments if each.content_type == "text/calendar")
    assert b"METHOD:REQUEST" in request.data and b"BEGIN:VTODO" in request.data


def test_a_proposal_that_puts_the_due_moment_before_the_start_cannot_be_accepted() -> None:
    start = ("DTSTART:20261104T090000Z",)
    stored = task_text("", organizer="me@example.com", attendee="sam@example.net", extra=start)
    assert proposal_usable(stored, task_text("COUNTER", due="20261104T170000Z", extra=start))
    assert not proposal_usable(stored, task_text("COUNTER", due="20261103T170000Z", extra=start))


def test_a_proposal_moves_a_repeating_task_without_damage(setup: Setup) -> None:
    repeat = ("DTSTART:20261104T090000Z", "RRULE:FREQ=WEEKLY;COUNT=4")
    stored_task(setup, task_text("", organizer="me@example.com", attendee="sam@example.net", extra=repeat))
    moved = ("DTSTART:20261104T100000Z", "RRULE:FREQ=WEEKLY;COUNT=4")
    proposal = task_text("COUNTER", due="20261105T180000Z", organizer="me@example.com", attendee="sam@example.net", extra=moved)
    assert run(setup, "invitation.organize", deliver(setup, from_sam(proposal)), action="accept_proposal", mail=False).done == "moved"
    (text,) = kept_tasks(setup)
    assert "DTSTART:20261104T100000Z" in text and "DUE:20261105T180000Z" in text and "RRULE:FREQ=WEEKLY;COUNT=4" in text


def test_a_declined_proposal_and_the_latest_version_are_about_a_task(setup: Setup) -> None:
    stored_task(setup, your_task(sequence=2))
    run(setup, "invitation.organize", deliver(setup, from_sam(your_task("COUNTER", due="20261107T170000Z", sequence=2))), action="decline_proposal")
    run(setup, "invitation.organize", deliver(setup, from_sam(your_task("REFRESH"))), action="send_latest")
    declined, latest = sorted((parse_message(draft) for draft in drafts(setup)), key=lambda each: each.subject or "", reverse=True)
    assert "the task stays as it is" in (declined.text_body or "") and "latest version of this task" in (latest.text_body or "")
    assert b"DECLINECOUNTER" in next(each.data for each in declined.attachments if each.content_type == "text/calendar")


def test_what_is_refused_says_task_and_task_list(setup: Setup) -> None:
    with pytest.raises(UsageError, match="This task is not in your task list"):
        run(setup, "invitation.apply", deliver(setup, invitation_mail(task_text("CANCEL"))))
    stored_task(setup, task_text(method="", sequence=3))
    with pytest.raises(UsageError, match="Your task list has a newer version of this task"):
        run(setup, "invitation.answer", deliver(setup, invitation_mail(task_text(sequence=1))), answer="ACCEPTED", mail=False)


def test_editing_a_kept_task_keeps_its_organizer_and_attendees() -> None:
    text = task_text(method="", answer="ACCEPTED")
    (task,) = parse_tasks(text)
    edited = tasks_to_text(replace(task, summary="Water the garden"), text)
    assert "SUMMARY:Water the garden" in edited and "ORGANIZER" in edited and "PARTSTAT=ACCEPTED" in edited
