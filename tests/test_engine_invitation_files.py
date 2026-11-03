import base64
from pathlib import Path

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind
from chrono_mail.engine.commands_invitations import SourceParameters, invitation_card
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.engine.registry import UsageError
from chrono_mail.pim.ical import MAXIMUM_TEXT_CHARACTERS
from chrono_mail.pim.itip import item_count
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import drafts, kept
from tests.test_engine_invitation_tasks import kept_tasks, stored_task, task_list, task_text
from tests.test_engine_invitations import calendar_text, deliver, stored

# --- Version ------------------------------------------------------------
__version__ = "test_engine_invitation_files.py 4.4.507"

# --- Version history ----------------------------------------------------
# v4.4.507: Initial tests: an .ics attachment or file as a card, every command on it, several items added each on its own (D-310 step 4)


def two_events(method: str = "") -> str:
    def event(number: int) -> list[str]:
        return ["BEGIN:VEVENT", f"UID:item-{number}@example.com", "DTSTAMP:20261028T090000Z", f"DTSTART:2026110{number}T100000Z", f"DTEND:2026110{number}T110000Z", f"SUMMARY:Item {number}", "END:VEVENT"]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Example//Example//EN", *([f"METHOD:{method}"] if method else []), *event(1), *event(2), "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def mail_with_file(calendar: str, name: str = "agenda.ics") -> bytes:
    encoded = base64.b64encode(calendar.encode()).decode()
    return (
        "From: Olivia Organizer <olivia@example.org>\r\nTo: me@example.com\r\nSubject: Invitation: Choice of colours\r\nMIME-Version: 1.0\r\n"
        "Content-Type: multipart/mixed; boundary=MIX\r\n\r\n--MIX\r\nContent-Type: text/plain\r\n\r\nSee the file.\r\n"
        f'--MIX\r\nContent-Type: application/octet-stream; name="{name}"\r\nContent-Disposition: attachment; filename="{name}"\r\n'
        f"Content-Transfer-Encoding: base64\r\n\r\n{encoded}\r\n--MIX--\r\n"
    ).encode()


def calendar_id(setup: Setup) -> str:
    ensure_local_pim(setup.engine)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        (calendar,) = store.collections(CollectionKind.EVENTS)
        return calendar.collection_id


def file_with(tmp_path: Path, text: str | bytes, name: str = "agenda.ics") -> str:
    path = tmp_path / name
    path.write_bytes(text.encode() if isinstance(text, str) else text)
    return str(path)


def on_file(setup: Setup, command: str, path: str, **more: object):  # noqa: ANN201 - the command's own answer
    return call(setup.engine, command, account="", folder="", message="", file=path, **more)


def test_an_attachment_without_a_method_is_a_card_offered_to_keep(setup: Setup) -> None:
    message = deliver(setup, mail_with_file(calendar_text(method="")))
    position = next(index for index, each in enumerate(call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=message).attachments) if each.filename == "agenda.ics")
    card = call(setup.engine, "invitation.card", account=setup.account_id, folder="Inbox", message=message, part=position)
    assert card.method == "PUBLISH" and card.plain and card.items == 1 and card.position == position and card.title == "Choice of colours"


def test_a_file_with_a_method_works_as_the_mail_would_and_the_answer_is_a_mail_of_its_own(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, calendar_text())
    calendar = calendar_id(setup)
    card = on_file(setup, "invitation.card", path)
    assert card.method == "REQUEST" and not card.plain and card.position == -1
    result = on_file(setup, "invitation.answer", path, answer="ACCEPTED", calendar=calendar, mail=True)
    assert result.stored_in and result.draft_message and "PARTSTAT=ACCEPTED" in kept(setup)[0]
    assert b"In-Reply-To" not in drafts(setup)[0]


def test_a_file_that_asks_nothing_is_added_as_it_is(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, calendar_text(method=""))
    on_file(setup, "invitation.answer", path, answer="ACCEPTED", calendar=calendar_id(setup), mail=True)
    assert "UID:meeting-1@example.com" in kept(setup)[0] and drafts(setup) == []
    card = on_file(setup, "invitation.card", path)
    assert card.stored_in  # the card says it is kept now


def test_several_items_each_become_an_item_of_their_own(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, two_events())
    calendar = calendar_id(setup)
    card = on_file(setup, "invitation.card", path)
    assert card.items == 2 and card.title == "Item 1" and card.stored_in == ""
    result = on_file(setup, "invitation.answer", path, answer="ACCEPTED", calendar=calendar, mail=False)
    assert result.stored_in and sorted("Item 1" in text for text in kept(setup)) == [False, True] and len(kept(setup)) == 2


def test_a_shared_message_with_several_items_is_never_put_in_one_object(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, two_events("PUBLISH"))
    on_file(setup, "invitation.answer", path, answer="ACCEPTED", calendar=calendar_id(setup), mail=False)
    assert len(kept(setup)) == 2 and all(text.count("BEGIN:VEVENT") == 1 for text in kept(setup))


def test_several_items_can_only_be_added(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, two_events())
    with pytest.raises(UsageError, match="only be added"):
        on_file(setup, "invitation.answer", path, answer="DECLINED", calendar=calendar_id(setup), mail=False)
    with pytest.raises(UsageError, match="only be added"):
        on_file(setup, "invitation.apply", path)


def test_several_items_ask_for_a_calendar_even_when_the_first_is_kept(setup: Setup, tmp_path: Path) -> None:
    calendar = calendar_id(setup)
    path = file_with(tmp_path, two_events())
    on_file(setup, "invitation.answer", path, answer="ACCEPTED", calendar=calendar, mail=False)
    assert on_file(setup, "invitation.choices", path).stored == ""


def test_a_file_of_tasks_goes_to_a_task_list(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, task_text(method=""))
    on_file(setup, "invitation.answer", path, answer="ACCEPTED", calendar=task_list(setup), mail=False)
    assert len(kept_tasks(setup)) == 1


def test_a_newer_file_updates_the_kept_copy(setup: Setup, tmp_path: Path) -> None:
    stored_task(setup, task_text(method="", sequence=1))
    assert on_file(setup, "invitation.apply", file_with(tmp_path, task_text(method="", sequence=2, due="20261108T170000Z"))).done == "updated"
    assert "DUE:20261108T170000Z" in kept_tasks(setup)[0]


def test_an_item_without_a_uid_gets_one_from_the_import(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, calendar_text(method="").replace("UID:meeting-1@example.com\r\n", ""))
    on_file(setup, "invitation.answer", path, answer="ACCEPTED", calendar=calendar_id(setup), mail=False)
    assert "UID:" in kept(setup)[0]


def test_a_file_with_a_byte_order_mark_is_read(setup: Setup, tmp_path: Path) -> None:
    assert on_file(setup, "invitation.card", file_with(tmp_path, b"\xef\xbb\xbf" + calendar_text().encode())).title == "Choice of colours"


@pytest.mark.parametrize("text", ["not a calendar", "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n",
                                  "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VJOURNAL\r\nUID:a\r\nEND:VJOURNAL\r\nEND:VCALENDAR\r\n"], ids=["text", "empty", "journal"])
def test_a_text_with_nothing_to_show_is_refused(setup: Setup, tmp_path: Path, text: str) -> None:
    with pytest.raises(UsageError, match="no calendar item"):
        on_file(setup, "invitation.card", file_with(tmp_path, text))


def test_a_file_that_is_gone_or_too_large_is_refused(setup: Setup, tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="could not be read"):
        on_file(setup, "invitation.card", str(tmp_path / "gone.ics"))
    big = tmp_path / "big.ics"
    with big.open("wb") as file:
        file.truncate(MAXIMUM_TEXT_CHARACTERS + 1)
    with pytest.raises(UsageError, match="too large"):
        invitation_card(setup.engine, SourceParameters("", "", "", file=str(big)))


def test_an_attachment_position_that_does_not_exist_is_refused(setup: Setup) -> None:
    message = deliver(setup, mail_with_file(calendar_text(method="")))
    with pytest.raises(UsageError, match="no such attachment"):
        call(setup.engine, "invitation.card", account=setup.account_id, folder="Inbox", message=message, part=9)


def test_the_kept_copy_of_a_single_file_is_shown_on_the_card(setup: Setup, tmp_path: Path) -> None:
    stored(setup, calendar_text(method="", answer="ACCEPTED"))
    card = on_file(setup, "invitation.card", file_with(tmp_path, calendar_text(method="")))
    assert card.stored_in and card.your_answer == "ACCEPTED"


def test_the_items_of_a_text_are_counted_by_their_uid() -> None:
    series = calendar_text(method="").replace("END:VEVENT", "END:VEVENT\r\nBEGIN:VEVENT\r\nUID:meeting-1@example.com\r\nRECURRENCE-ID:20261105T100000Z\r\nDTSTART:20261105T110000Z\r\nEND:VEVENT")
    assert item_count(series) == 1 and item_count(two_events()) == 2 and item_count("nothing") == 0
