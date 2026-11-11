import dataclasses

from PySide6.QtCore import QRect, QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QRadioButton, QToolButton
from pytestqt.qtbot import QtBot

from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.calendarwidgets import AgendaTags, event_tip, paint_tags
from chrono_mail.ui.eventeditor import EventEditor
from chrono_mail.ui.tagbox import TagBox
from tests.ui.test_ui_calendar import open_calendar
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_tag_colours.py 4.4.573"

# --- Version history ----------------------------------------------------
# v4.4.573: Initial tests: the Tags box of the appointment window and its radio buttons, the window's layout, the tag icons and the hover text (D-329)


def checked(box: TagBox) -> list[bool]:
    return [button.isChecked() for button in box.findChildren(QRadioButton)]


def test_the_first_tag_added_gives_the_colour_until_another_is_chosen_or_it_is_removed(qtbot: QtBot) -> None:
    box = TagBox()
    qtbot.addWidget(box)
    box.add("Work")
    box.add("Private")
    box.add("Birthday")
    assert box.colour_tag() == "Work" and box.tags() == ["Work", "Private", "Birthday"]  # adding more never moves it
    box.choose("Birthday")
    assert box.tags() == ["Birthday", "Work", "Private"] and box.names() == ["Work", "Private", "Birthday"]  # the list keeps its order
    box.remove("Birthday")
    assert box.colour_tag() == "Work"  # the first tag left
    box.remove("Work")
    box.remove("Private")
    assert box.colour_tag() == "" and box.tags() == []


def test_a_click_on_a_radio_button_chooses_that_tag(qtbot: QtBot) -> None:
    box = TagBox()
    qtbot.addWidget(box)
    box.set_tags(["Work", "Private"])
    assert checked(box) == [True, False]
    radios = [button for button in box.findChildren(QRadioButton) if not button.isChecked()]
    radios[0].click()
    assert box.colour_tag() == "Private"


def editor_for(world: World, qtbot: QtBot, reading: bool = False) -> tuple[CalendarPage, EventEditor]:
    page = open_calendar(world, qtbot)
    lunch = next(entry for entry in page.entries if entry.summary == "Lunch")
    event = world.call("event.show", item=lunch.row.item)
    editor = EventEditor(world.backend, list(page.panel.collections), list(world.call("tag.list")), event, event.collection, reading=reading)
    qtbot.addWidget(editor)
    return page, editor


def test_the_window_saves_the_colour_tag_first_and_has_no_colour_field(world: World, qtbot: QtBot) -> None:
    _, editor = editor_for(world, qtbot)
    assert editor.findChild(QToolButton, "colour") is None
    assert editor.chosen_tags == ["Food"] and editor.tag_box.colour_tag() == "Food"
    editor.choose_tag("Party", True)
    editor.tag_box.choose("Party")
    assert editor.parameters().tag == ("Party", "Food")


def test_the_tags_box_is_beside_show_as_and_its_button_on_the_line_of_the_attendees_button(world: World, qtbot: QtBot) -> None:
    _, editor = editor_for(world, qtbot)
    editor.show()
    qtbot.waitExposed(editor)
    box_left = editor.tag_box.mapTo(editor, editor.tag_box.rect().topLeft())
    busy_top_right = editor.busy.mapTo(editor, editor.busy.rect().topRight())
    assert box_left.x() > busy_top_right.x() and abs(box_left.y() - busy_top_right.y()) <= 2  # it starts at the height of Show as
    add = editor.add_tag.mapTo(editor, editor.add_tag.rect().topLeft())
    attendees = editor.attendees_button.mapTo(editor, editor.attendees_button.rect().topLeft())
    assert add.y() == attendees.y()  # the two buttons on one line


def test_a_read_only_window_greys_the_tags(world: World, qtbot: QtBot) -> None:
    _, editor = editor_for(world, qtbot, reading=True)
    assert not editor.add_tag.isEnabled() and not any(button.isEnabled() for button in editor.tag_box.findChildren(QRadioButton))
    editor.start_editing()
    assert editor.add_tag.isEnabled() and all(button.isEnabled() for button in editor.tag_box.findChildren(QRadioButton))


def test_every_tag_shows_on_the_appointment_and_in_its_hover_text(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.WEEK)
    world.call("tag.create", name="Food", colour="#225b77")
    page.reload_tags()
    wait(qtbot, lambda: any(entry.summary == "Lunch" and entry.tag_colours == (("Food", "#225b77"),) for entry in page.entries))
    lunch = next(entry for entry in page.entries if entry.summary == "Lunch")
    tip = event_tip(lunch)
    assert "<b>Lunch</b>" in tip and "background:#225b77" in tip and "Food" in tip and "<td>Calendar</td>" in tip
    picture = QImage(200, 30, QImage.Format.Format_ARGB32)
    picture.fill(QColor("#ffffff"))
    painter = QPainter(picture)
    rect = QRectF(0, 0, 200, 30)
    other = paint_tags(painter, rect, 5, 20, lunch, QColor("#ffffff"), QColor("#000000"))
    painter.end()
    assert other < rect.right() - 8  # the title gives way to the tag
    assert any(picture.pixelColor(x, 15).name() == "#225b77" for x in range(int(other), 200))  # a tag in its colour
    same = QImage(200, 30, QImage.Format.Format_ARGB32)
    same.fill(QColor("#225b77"))
    painter = QPainter(same)
    paint_tags(painter, rect, 5, 20, lunch, QColor("#225b77"), QColor("#ffffff"))
    painter.end()
    assert any(same.pixelColor(x, y).lightness() > 200 for x in range(150, 200) for y in range(5, 25))  # its own colour as an outline
    assert QFont().pointSize() > 0


def test_a_colour_not_yet_made_a_tag_still_shows_on_an_appointment_with_tags(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.WEEK)  # the start made every colour a tag already; this one comes after it
    cal = next(c.collection for c in world.call("collection.list", kind="events"))
    world.call("collection.set-palette", collection=cal, colours=("g7\tPeacock\t#039be5",))
    lunch = next(entry for entry in page.entries if entry.summary == "Lunch")
    world.call("event.edit", item=lunch.row.item, colour="g7")
    wait(qtbot, lambda: next(entry for entry in page.entries if entry.summary == "Lunch").colour == "#039be5")


def has_colour(picture: QImage, colour: str, left: int, top: int, right: int, bottom: int) -> bool:
    """A pixel of this colour in that part of the picture."""
    return any(picture.pixelColor(x, y).name() == colour for x in range(max(0, left), min(picture.width(), right)) for y in range(max(0, top), min(picture.height(), bottom)))


def test_the_tags_show_in_month_as_dot_lines_and_in_the_agenda(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.MONTH)
    world.call("tag.create", name="Food", colour="#e01b84")
    page.reload_tags()  # what the main window does after Settings saved a tag
    wait(qtbot, lambda: any(entry.summary == "Lunch" and entry.tag_colours == (("Food", "#e01b84"),) for entry in page.entries))
    page.month.resize(900, 600)
    picture = page.month.grab().toImage()
    rect = next(rect for rect, entry in page.month.hits if entry.summary == "Lunch")
    assert has_colour(picture, "#e01b84", int(rect.right()) - 24, int(rect.top()), int(rect.right()), int(rect.bottom()))  # the tag at the end of a dot line
    page.set_view(layout.AGENDA)
    page.agenda.resize(600, 400)
    wait(qtbot, lambda: any("Lunch" in page.agenda.item(row).text() for row in range(page.agenda.count())))
    line = next(page.agenda.item(row) for row in range(page.agenda.count()) if "Lunch" in page.agenda.item(row).text())
    area = page.agenda.visualItemRect(line)
    picture = page.agenda.viewport().grab().toImage()
    assert has_colour(picture, "#e01b84", area.right() - 30, area.top(), area.right(), area.bottom())  # and at the end of an Agenda line


def test_a_long_agenda_line_stops_before_its_tags(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.AGENDA)
    lunch = next(entry for entry in page.entries if entry.summary == "Lunch")
    entry = dataclasses.replace(lunch, tag_colours=(("First", "#ff0000"), ("Second", "#00ff00")))
    line = QRect(0, 0, 300, 20)
    room = AgendaTags.text_room(line, entry)
    assert room.left() == 0 and room.right() < 300 - 2 * 14  # the text is cut off before the two tags
    assert AgendaTags.text_room(line, dataclasses.replace(lunch, tag_colours=())).width() == 300 - 6  # without tags almost all of it
    assert isinstance(page.agenda.itemDelegate(), AgendaTags)


def test_the_agenda_keeps_its_selected_line_when_a_tag_changes_colour(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.AGENDA)
    wait(qtbot, lambda: any("Lunch" in page.agenda.item(row).text() for row in range(page.agenda.count())))
    line = next(page.agenda.item(row) for row in range(page.agenda.count()) if "Lunch" in page.agenda.item(row).text())
    page.agenda.setCurrentItem(line)
    world.call("tag.create", name="Food", colour="#e01b84")
    page.reload_tags()
    wait(qtbot, lambda: any(entry.summary == "Lunch" and entry.colour == "#e01b84" for entry in page.entries))
    current = page.agenda.currentItem()
    assert current is not None and "Lunch" in current.text()


def test_when_not_every_tag_fits_the_first_ones_stay(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.WEEK)
    lunch = next(entry for entry in page.entries if entry.summary == "Lunch")
    entry = dataclasses.replace(lunch, tag_colours=(("First", "#ff0000"), ("Second", "#00ff00"), ("Third", "#0000ff")))
    picture = QImage(90, 30, QImage.Format.Format_ARGB32)
    picture.fill(QColor("#ffffff"))
    painter = QPainter(picture)
    paint_tags(painter, QRectF(0, 0, 90, 30), 5, 20, entry, QColor("#ffffff"), QColor("#000000"))
    painter.end()
    drawn = {picture.pixelColor(x, y).name() for x in range(90) for y in range(30)}
    assert "#ff0000" in drawn and "#0000ff" not in drawn  # the colour tag stays, the last one gives way


def test_a_selected_agenda_line_is_highlighted_behind_its_tags_too(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.AGENDA)
    world.call("tag.create", name="Food", colour="#e01b84")
    page.reload_tags()
    wait(qtbot, lambda: any(entry.summary == "Lunch" and entry.colour == "#e01b84" for entry in page.entries))
    page.agenda.resize(600, 400)
    line = next(page.agenda.item(row) for row in range(page.agenda.count()) if "Lunch" in page.agenda.item(row).text())
    page.agenda.setCurrentItem(line)
    area = page.agenda.visualItemRect(line)
    picture = page.agenda.viewport().grab().toImage()
    highlight = page.agenda.palette().color(page.agenda.palette().ColorRole.Highlight).name()
    assert picture.pixelColor(area.right() - 2, area.top() + 2).name() == highlight  # the end of the line, behind the tags, is selected too


def test_a_long_tag_name_ends_in_dots_and_shows_whole_on_hover(qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QLabel

    box = TagBox()
    qtbot.addWidget(box)
    name = "A very long tag name for a birthday party"
    box.set_tags([name])
    label = next(label for label in box.findChildren(QLabel) if label.toolTip() == name)
    assert label.text().endswith("…") and label.text() != name
