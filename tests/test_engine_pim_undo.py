from pathlib import Path

import pytest

from chrono_mail.core import ChronoMailError
from chrono_mail.engine import Engine, UsageError
from chrono_mail.engine.pimundo import CHANGED_SINCE, PimUndoResult
from chrono_mail.sync.davitems import ItemGateway, ServerRefused
from tests.enginefactory import make_engine
from tests.fakedav import FakeDav
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_event_menus import ME, SAM, added, kerio, on_server, rows, series, server_calendar, single, writes  # noqa: F401 - kerio is a fixture
from tests.test_engine_invitation_answers import drafts

__all__ = ["kerio", "setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_pim_undo.py 4.7.656"

# --- Version history ----------------------------------------------------
# v4.7.656: Initial (B-18 step 3, D-368): Undo and Redo of appointments, tasks, contacts and contact groups; the server first, nobody mailed,
#           offline, refused, changed since; New is never undone; each section has its own list


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path)
    call(engine, "pim.enable")
    return engine


def collection(engine: Engine, kind: str) -> str:
    return next(c.collection for c in call(engine, "collection.list", kind=kind))


def second(engine: Engine, kind: str, name: str) -> str:
    return call(engine, "collection.create", kind=kind, name=name).collection


def undo(engine: Engine, section: str) -> PimUndoResult:
    return call(engine, "pim.undo", section=section)


def redo(engine: Engine, section: str) -> PimUndoResult:
    return call(engine, "pim.redo", section=section)


def labels(engine: Engine, section: str) -> tuple[str, str]:
    answer = call(engine, "pim.undo-labels", section=section)
    return answer.undo, answer.redo


def appointment(engine: Engine, title: str = "Dentist") -> str:
    return call(engine, "event.add", calendar=collection(engine, "events"), summary=title, start="2030-03-04T10:00").item


def titles(engine: Engine) -> list[str]:
    return [row.summary for row in call(engine, "event.list", from_date="2030-03-01", to_date="2030-03-10").events]


# --- appointments --------------------------------------------------------------------------------------------------------------------------------


def test_an_edit_is_undone_and_redone(engine: Engine) -> None:
    item = appointment(engine)
    call(engine, "event.edit", item=item, summary="Dentist at noon", undo=1)
    assert labels(engine, "events") == ("Edit (1 appointment)", "")
    result = undo(engine, "events")
    assert titles(engine) == ["Dentist"] and result.items == (item,) and result.action == "Edit (1 appointment)" and result.not_done == 0
    assert labels(engine, "events") == ("", "Edit (1 appointment)")
    redo(engine, "events")
    assert titles(engine) == ["Dentist at noon"] and labels(engine, "events") == ("Edit (1 appointment)", "")


def test_a_deleted_appointment_comes_back_with_its_uid_and_redo_deletes_it_again(engine: Engine) -> None:
    item = appointment(engine)
    uid = call(engine, "event.show", item=item).uid
    call(engine, "event.delete", item=item, undo=1)
    assert titles(engine) == []
    (back,) = undo(engine, "events").items
    assert titles(engine) == ["Dentist"] and call(engine, "event.show", item=back).uid == uid
    redo(engine, "events")
    assert titles(engine) == []


def test_a_date_deleted_from_a_series_comes_back(engine: Engine) -> None:
    (item,) = call(engine, "event.import", calendar=collection(engine, "events"), text=series()).items
    middle = sorted(call(engine, "event.list", from_date="2030-03-01", to_date="2030-03-10").events, key=lambda row: row.start)[1]
    call(engine, "event.delete", item=item, occurrence=middle.occurrence, undo=1)
    assert len(titles(engine)) == 2
    undo(engine, "events")
    assert len(titles(engine)) == 3


def test_a_move_to_another_calendar_is_undone(engine: Engine) -> None:
    first = collection(engine, "events")
    item = appointment(engine)
    other = second(engine, "events", "Work")
    call(engine, "event.move", item=item, calendar=other, undo=1)
    (back,) = undo(engine, "events").items
    assert call(engine, "event.show", item=back).collection == first
    (again,) = redo(engine, "events").items
    assert call(engine, "event.show", item=again).collection == other


def test_cancel_is_undone(engine: Engine) -> None:
    item = appointment(engine)
    call(engine, "event.cancel", item=item, undo=1)
    undo(engine, "events")
    assert call(engine, "event.show", item=item).status != "CANCELLED"


def test_an_appointment_changed_since_is_left_alone_and_told(engine: Engine) -> None:
    item = appointment(engine)
    call(engine, "event.edit", item=item, summary="First", undo=1)
    call(engine, "event.edit", item=item, summary="Changed in its window without Undo")
    result = undo(engine, "events")
    assert result.not_done == 1 and result.reason == CHANGED_SINCE and titles(engine) == ["Changed in its window without Undo"]


def test_new_is_never_undone_and_a_command_without_a_number_is_not_kept(engine: Engine) -> None:
    item = appointment(engine)
    call(engine, "event.edit", item=item, summary="From the command line")
    assert labels(engine, "events") == ("", "")
    with pytest.raises(UsageError, match="Nothing to undo"):
        undo(engine, "events")


def test_one_action_of_several_commands_is_one_undo(engine: Engine) -> None:
    first, other = appointment(engine, "One"), appointment(engine, "Two")
    call(engine, "event.delete", item=first, undo=7)
    call(engine, "event.delete", item=other, undo=7)
    assert labels(engine, "events")[0] == "Delete (2 appointments)"
    undo(engine, "events")
    assert sorted(titles(engine)) == ["One", "Two"]


# --- on a server -------------------------------------------------------------------------------------------------------------------------------


def test_undo_of_a_delete_of_your_meeting_goes_to_the_server_first_and_mails_nobody(setup: Setup, kerio: FakeDav) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single(ME, SAM))
    call(setup.engine, "event.delete", item=item, tell=True, undo=1)
    mails = len(drafts(setup))
    kerio.requests.clear()
    undo(setup.engine, "events")
    assert "PUT" in writes(kerio) and any("UID:single@example.org" in text for text in on_server(kerio)) and len(drafts(setup)) == mails


def test_offline_undo_shows_here_at_once_and_goes_when_the_connection_is_back(setup: Setup, kerio: FakeDav) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single())
    call(setup.engine, "event.edit", item=item, summary="Moved", undo=1)
    kerio.writes_failing = True
    undo(setup.engine, "events")
    assert [row.summary for row in rows(setup)] == ["Single"] and "SUMMARY:Moved" in on_server(kerio)[0]
    kerio.writes_failing = False
    call(setup.engine, "dav.sync", account=setup.account_id)
    assert "SUMMARY:Single" in on_server(kerio)[0]


def test_a_refused_undo_changes_nothing_here_and_says_why(setup: Setup, kerio: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single())
    call(setup.engine, "event.edit", item=item, summary="Moved", undo=1)

    def refuse(*arguments: object, **options: object) -> object:
        raise ServerRefused("403 Forbidden")

    monkeypatch.setattr(ItemGateway, "save", refuse)
    result = undo(setup.engine, "events")
    assert result.not_done == 1 and "did not take" in result.reason and [row.summary for row in rows(setup)] == ["Moved"]


# --- tasks, contacts, groups ----------------------------------------------------------------------------------------------------------------------


def test_done_delete_and_move_of_a_task_are_undone(engine: Engine) -> None:
    first = collection(engine, "tasks")
    item = call(engine, "task.add", list=first, summary="Pay the rent").item
    call(engine, "task.complete", item=item, undo=1)
    assert labels(engine, "tasks")[0] == "Mark as done (1 task)"
    undo(engine, "tasks")
    assert call(engine, "task.show", item=item).completed == ""
    call(engine, "task.delete", item=item, undo=2)
    (back,) = undo(engine, "tasks").items
    assert call(engine, "task.show", item=back).summary == "Pay the rent"
    other = second(engine, "tasks", "Home")
    call(engine, "task.move", item=back, list=other, undo=3)
    (home,) = undo(engine, "tasks").items
    assert call(engine, "task.show", item=home).collection == first


def test_an_edited_and_a_deleted_contact_are_undone(engine: Engine) -> None:
    item = call(engine, "contact.add", collection=collection(engine, "contacts"), name="Ann Jansen", email=("ann@example.com",)).item
    call(engine, "contact.edit", item=item, email=("ann@example.org",), undo=1)
    undo(engine, "contacts")
    assert call(engine, "contact.show", item=item).emails[0].value == "ann@example.com"
    call(engine, "contact.delete", item=item, undo=2)
    (back,) = undo(engine, "contacts").items
    assert call(engine, "contact.show", item=back).name == "Ann Jansen"


def test_a_group_renamed_and_its_members_changed_are_undone(engine: Engine) -> None:
    book = collection(engine, "contacts")
    ann = call(engine, "contact.add", collection=book, name="Ann", email=("ann@example.com",)).item
    group = call(engine, "contact-group.create", collection=book, name="Family").item
    call(engine, "contact-group.rename", item=group, name="Relatives", undo=1)
    undo(engine, "contacts")
    assert [row.name for row in call(engine, "contact-group.list")] == ["Family"]
    call(engine, "contact-group.add-members", item=group, contacts=(ann,), undo=2)
    undo(engine, "contacts")
    assert call(engine, "contact-group.list")[0].members == 0


def test_each_section_has_its_own_undo(engine: Engine) -> None:
    item = appointment(engine)
    call(engine, "event.edit", item=item, summary="Changed", undo=1)
    task = call(engine, "task.add", list=collection(engine, "tasks"), summary="Call").item
    call(engine, "task.complete", item=task, undo=2)
    undo(engine, "events")
    assert titles(engine) == ["Dentist"] and call(engine, "task.show", item=task).completed != ""


def test_an_unknown_section_is_refused(engine: Engine) -> None:
    with pytest.raises(ChronoMailError, match="events, tasks or contacts"):
        undo(engine, "mail")
