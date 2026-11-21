from PySide6.QtWidgets import QRadioButton
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_conflicts import ConflictSummary
from chrono_mail.pim.merge import ends_before_start
from chrono_mail.ui.syncconflicts import ConflictDialog
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_conflict_order.py 4.8.662"

# --- Version history ----------------------------------------------------
# v4.8.662: Initial (Q36): Apply is greyed while the fields chosen would end the appointment before it starts, with a red line that says why


def event(start: str, end: str) -> str:
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nBEGIN:VEVENT\r\nUID:meeting-1\r\nSUMMARY:Planning\r\n"
        f"DTSTART:{start}\r\nDTEND:{end}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )


def conflict() -> ConflictSummary:
    """Moved here to the morning, on the server to the afternoon: Starts and Ends clash."""
    return ConflictSummary(
        conflict="c1", account="a", account_name="Work", collection="cal", collection_name="Calendar", item="meeting-1", kind="events",
        reason="both", reason_text="Changed on both sides", name="Planning", detail="", whole=False, local_allowed=True, when=0.0, fields=(),
        base_text=event("20261110T080000Z", "20261110T083000Z"), local_text=event("20261110T090000Z", "20261110T100000Z"),
        server_text=event("20261110T130000Z", "20261110T140000Z"),
    )


def circles(dialog: ConflictDialog, key: str) -> tuple[QRadioButton, QRadioButton]:
    """Chrono Mail's circle and the server's of a field (its key: its iCalendar name)."""
    return dialog.groups[key]


def test_apply_is_greyed_while_the_chosen_end_is_before_the_chosen_start(world: World, qtbot: QtBot) -> None:
    dialog = ConflictDialog(conflict())
    qtbot.addWidget(dialog)
    starts, ends = circles(dialog, "DTSTART"), circles(dialog, "DTEND")
    starts[1].setChecked(True)  # the server's start, 13:00
    ends[0].setChecked(True)  # Chrono Mail's end, 10:00: before it starts
    assert not dialog.apply_button.isEnabled()
    assert dialog.order_problem.isVisibleTo(dialog) and "end is before the chosen start" in dialog.order_problem.text()
    ends[1].setChecked(True)  # the server's end too
    assert dialog.apply_button.isEnabled() and not dialog.order_problem.isVisibleTo(dialog)
    starts[0].setChecked(True)  # Chrono Mail's start, the server's end: a mix that can be kept
    assert dialog.apply_button.isEnabled() and not dialog.order_problem.isVisibleTo(dialog)


def test_ends_before_start_reads_dates_moments_and_due_dates() -> None:
    assert ends_before_start(event("20261110T130000Z", "20261110T100000Z"))
    assert not ends_before_start(event("20261110T090000Z", "20261110T100000Z"))
    assert ends_before_start(event("20261112", "20261110").replace("DTSTART:", "DTSTART;VALUE=DATE:").replace("DTEND:", "DTEND;VALUE=DATE:"))
    zoned = event("20261110T150000", "20261110T150000Z").replace("DTSTART:", "DTSTART;TZID=Europe/Amsterdam:")  # 14:00 UTC
    assert not ends_before_start(zoned)
    task = "BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:t\r\nDTSTART:20261110T130000Z\r\nDUE:20261110T100000Z\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
    assert ends_before_start(task)
    assert not ends_before_start("not a calendar")
