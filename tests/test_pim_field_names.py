from datetime import date, datetime

from chrono_mail.pim.conflictwords import Words, rows
from chrono_mail.pim.merge import merge

# --- Version ------------------------------------------------------------
__version__ = "test_pim_field_names.py 4.8.662"

# --- Version history ----------------------------------------------------
# v4.8.662: Initial: the list of sync conflicts (its Fields column) and the window of one conflict name every field alike


def card(name: str, parts: str, note: str) -> str:
    return f"BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c1\r\nFN:{name}\r\nN:{parts}\r\nNOTE:{note}\r\nEND:VCARD\r\n"


def task(summary: str, done: str) -> str:
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTODO\r\nUID:t1\r\nSUMMARY:{summary}\r\nPERCENT-COMPLETE:{done}\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"


def same_names(base: str, local: str, server: str) -> tuple[list[str], list[str]]:
    words = Words(lambda moment: str(moment) if isinstance(moment, date | datetime) else "", lambda colour: colour)
    listed = sorted(clash.label for clash in merge(base, local, server).clashes)
    shown = sorted(row.label for row in rows(base, local, server, words) if row.choice)
    return listed, shown


def test_a_contact_s_fields_have_one_name_in_the_list_and_the_window() -> None:
    listed, shown = same_names(card("Ann", "Smith;Ann;;;", "a"), card("Ann S", "Smith;Anne;;;", "b"), card("Ann Smith", "Smyth;Ann;;;", "c"))
    assert listed == shown == ["Display name", "Name", "Note"]


def test_a_task_s_fields_have_one_name_in_the_list_and_the_window() -> None:
    listed, shown = same_names(task("Plan", "0"), task("Plan the trip", "50"), task("Plan holidays", "70"))
    assert listed == shown == ["Done", "Summary"]
