import dataclasses

import pytest
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from chrono_mail.engine.registry import UsageError
from chrono_mail.ui.messagelist import ROW_ROLE
from chrono_mail.ui.tagoverview import TagOverviewWindow
from tests.ui.test_ui_tags import tag_rows
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_tag_overview.py 4.4.572"

# --- Version history ----------------------------------------------------
# v4.4.572: Initial tests of tag.overview and the Tag overview window (D-328)

TAG = "Harbourline"


def first(world: World, kind: str) -> str:
    return next(c.collection for c in world.call("collection.list", kind=kind))


def seed(world: World) -> None:
    world.call("pim.enable")
    world.call("contact.add", collection=first(world, "contacts"), given="Lena", family="Brouwer", organization="Harbourline Ltd", tag=(TAG,))
    world.call("contact.add", collection=first(world, "contacts"), given="Sara", family="Bakker")
    calendar = first(world, "events")
    world.call("event.add", calendar=calendar, summary="Old meeting", start="2020-03-02T10:00", end="2020-03-02T11:00", time_zone="UTC", tag=(TAG,))
    world.call("event.add", calendar=calendar, summary="Launch", start="2099-06-01T10:00", end="2099-06-01T11:00", time_zone="UTC", tag=(TAG,))
    world.call("event.add", calendar=calendar, summary="Weekly call", start="2020-01-06T09:00", end="2020-01-06T09:30", time_zone="UTC", rrule="FREQ=WEEKLY", tag=(TAG,))
    world.call("event.add", calendar=calendar, summary="Dentist", start="2099-06-02T10:00", end="2099-06-02T11:00", time_zone="UTC")
    tasks = first(world, "tasks")
    world.call("task.add", list=tasks, summary="Send quote", due="2099-01-31", tag=(TAG,))
    done = world.call("task.add", list=tasks, summary="Old job", due="2020-02-01", tag=(TAG,))
    world.call("task.complete", item=done.item)


def test_tag_overview_gives_the_contacts_appointments_and_tasks_of_a_tag_in_their_order(world: World) -> None:
    seed(world)
    found = world.call("tag.overview", tag="harbourline")
    assert [item.title for item in found.contacts] == ["Lena Brouwer"]
    assert [(item.title, item.later) for item in found.events] == [("Weekly call", True), ("Launch", True), ("Old meeting", False)]  # the series by its next date
    weekly = found.events[0]
    assert weekly.repeats and weekly.start is not None and weekly.start > 1_700_000_000  # its next date, not its first of 2020
    assert [(item.title, item.later) for item in found.tasks] == [("Send quote", True), ("Old job", False)]
    assert [item.title for item in world.call("tag.overview", tag=TAG, text="launch").events] == ["Launch"]
    assert world.call("tag.overview", tag="Nothing") == type(found)((), (), (), ())


def test_tag_overview_needs_a_tag(world: World) -> None:
    with pytest.raises(UsageError):
        world.call("tag.overview", tag="  ")


def tag_first_mail(world: World, tag: str, add: bool = True) -> str:
    row = world.window.list_model.index(0, 0).data(ROW_ROLE)
    world.call("message.tag", account=world.account_id, folder=row.folder, messages=(row.message_id,), **({"add": (tag,)} if add else {"remove": (tag,)}))
    return str(row.message_id)


def open_overview(world: World, qtbot: QtBot) -> TagOverviewWindow:
    seed(world)
    started(qtbot, world)
    tag_first_mail(world, TAG)
    world.window.show_tag_overview(TAG)
    window = world.window.tag_overviews[TAG.casefold()]
    wait(qtbot, lambda: headings(window) == ["▾ Contacts (1)", "▾ Appointments (3)", "▾ Tasks (2)", "▾ Mail (1)"])
    return window


def texts(window: TagOverviewWindow) -> list[str]:
    return [str(window.model.index(row).data()) for row in range(window.model.rowCount())]


def headings(window: TagOverviewWindow) -> list[str]:
    return [text for text in texts(window) if text[:1] in "▾▸"]


def row_of(window: TagOverviewWindow, words: str) -> int:
    return next(row for row, text in enumerate(texts(window)) if words in text)


def test_the_overview_lists_every_group_with_the_earlier_and_finished_ones_folded(world: World, qtbot: QtBot) -> None:
    window = open_overview(world, qtbot)
    shown = texts(window)
    assert any("Lena Brouwer" in text and "Harbourline Ltd" in text for text in shown)
    assert any("Launch" in text for text in shown) and not any("Old meeting" in text for text in shown)  # earlier ones folded
    assert any("Earlier (1)" in text for text in shown) and any("Finished (1)" in text for text in shown)
    assert any("Send quote" in text for text in shown) and not any("Old job" in text for text in shown)
    wait(qtbot, lambda: not any(text.strip() == "…" for text in texts(window)))  # the mail's row came in


def test_a_click_on_a_heading_folds_it_and_a_click_on_earlier_unfolds_it(world: World, qtbot: QtBot) -> None:
    window = open_overview(world, qtbot)
    window._clicked(window.model.index(row_of(window, "Contacts (1)")))  # noqa: SLF001 - a click on the heading
    assert "▸ Contacts (1)" in headings(window) and not any("Lena Brouwer" in text for text in texts(window))
    window._clicked(window.model.index(row_of(window, "Earlier (1)")))  # noqa: SLF001
    assert any("Old meeting" in text for text in texts(window))


def test_an_item_that_gets_or_loses_the_tag_comes_and_goes_in_place(world: World, qtbot: QtBot) -> None:
    window = open_overview(world, qtbot)
    window.list.setCurrentIndex(window.model.index(row_of(window, "Launch")))
    world.call("task.add", list=first(world, "tasks"), summary="Call back", tag=(TAG,))
    wait(qtbot, lambda: "▾ Tasks (3)" in headings(window) and any("Call back" in text for text in texts(window)))
    tag_first_mail(world, TAG, add=False)
    wait(qtbot, lambda: "▾ Mail (0)" in headings(window))
    assert "Launch" in str(window.list.currentIndex().data())  # the selection stayed


def test_the_reading_pane_shows_the_selected_contact_and_the_search_narrows_every_group(world: World, qtbot: QtBot) -> None:
    window = open_overview(world, qtbot)
    window.list.setCurrentIndex(window.model.index(row_of(window, "Lena Brouwer")))
    wait(qtbot, lambda: window.reading.currentWidget() is window.contact)
    window.search.setText("launch")
    wait(qtbot, lambda: headings(window) == ["▾ Contacts (0)", "▾ Appointments (1)", "▾ Tasks (0)", "▾ Mail (0)"])
    assert window.reading.currentWidget() is window.nothing  # the contact shown left the list


def test_a_double_click_opens_the_item_as_its_section_does(world: World, qtbot: QtBot) -> None:
    window = open_overview(world, qtbot)
    opened: list[tuple[str, ...]] = []
    window._openers = dataclasses.replace(  # noqa: SLF001 - the editors wait for a click: what would open is noted instead
        window._openers,  # noqa: SLF001
        contact=lambda item: opened.append(("contact", item)), event=lambda item: opened.append(("event", item)), task=lambda item: opened.append(("task", item)),
        mail=lambda account, folder, message: opened.append(("mail", account, folder)),
    )
    for words in ("Lena Brouwer", "Launch", "Send quote"):
        window._double_clicked(window.model.index(row_of(window, words)))  # noqa: SLF001
    mail_row = next(row for row, key in enumerate(window.model.keys) if key.startswith("m\x1f"))
    window._double_clicked(window.model.index(mail_row))  # noqa: SLF001
    assert [entry[0] for entry in opened] == ["contact", "event", "task", "mail"]
    assert opened[3][1:] == (world.account_id, "Inbox")


def test_the_same_tag_twice_brings_its_window_to_the_front(world: World, qtbot: QtBot) -> None:
    window = open_overview(world, qtbot)
    world.window.show_tag_overview(TAG.upper())
    assert list(world.window.tag_overviews.values()) == [window]
    window.close()
    wait(qtbot, lambda: not world.window.tag_overviews)


def test_a_tag_in_the_folder_list_offers_show_everything_tagged(world: World, qtbot: QtBot) -> None:
    seed(world)
    started(qtbot, world)
    tag_rows(world, qtbot, 0, name=TAG)  # a click in the Tag menu: the Tags section shows the tag
    wait(qtbot, lambda: world.window.folders.find_item(("", "tagview", TAG)) is not None)
    entry = world.window.folders.find_item(("", "tagview", TAG))
    assert entry is not None
    menu = QMenu()
    world.window.folders.tree_extras(menu, entry)
    action = next(action for action in menu.actions() if action.text() == "Show everything tagged…")
    action.trigger()
    wait(qtbot, lambda: TAG.casefold() in world.window.tag_overviews)


def test_a_mail_marked_read_elsewhere_loses_its_unread_dot(world: World, qtbot: QtBot) -> None:
    window = open_overview(world, qtbot)
    row = world.window.list_model.index(0, 0).data(ROW_ROLE)
    world.call("message.flag", account=world.account_id, folder=row.folder, message=row.message_id, seen=True)
    wait(qtbot, lambda: not any("● " in text for text in texts(window)))
    world.call("message.flag", account=world.account_id, folder=row.folder, message=row.message_id, seen=False)
    wait(qtbot, lambda: any("● " in text for text in texts(window)))


def test_the_reading_pane_follows_a_change_of_the_item_it_shows(world: World, qtbot: QtBot) -> None:
    window = open_overview(world, qtbot)
    window.list.setCurrentIndex(window.model.index(row_of(window, "Send quote")))
    wait(qtbot, lambda: window.reading.currentWidget() is window.text and "Send quote" in window.text.toPlainText())
    task = next(item for item in window.model.pim.values() if item.title == "Send quote")
    world.call("task.edit", item=task.item, summary="Send the quote")
    wait(qtbot, lambda: "Send the quote" in window.text.toPlainText() and any("Send the quote" in text for text in texts(window)))


def test_the_item_in_the_reading_pane_moved_to_another_list_stays_selected_and_shown(world: World, qtbot: QtBot) -> None:
    window = open_overview(world, qtbot)
    window.list.setCurrentIndex(window.model.index(row_of(window, "Send quote")))
    wait(qtbot, lambda: "Send quote" in window.text.toPlainText())
    other = world.call("collection.create", kind="tasks", name="Customers")
    task = next(item for item in window.model.pim.values() if item.title == "Send quote")
    world.call("task.move", item=task.item, list=other.collection)
    wait(qtbot, lambda: "Customers" in str(window.list.currentIndex().data()) and "Send quote" in window.text.toPlainText())
