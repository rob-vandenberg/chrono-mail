import pytest
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


def open_add_tag_list_and_click(qtbot: QtBot, editor: EventEditor, name: str) -> tuple[list[str], bool]:
    """A click on Add tag…, then on a tag in its list, as a person does; what the list offered, and whether it closed."""
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtTest import QTest

    offered: list[str] = []

    def inside() -> None:
        menu = editor.tag_menu
        if not menu.isVisible():
            QTimer.singleShot(50, inside)  # the list is not open yet: look again in a moment
            return
        offered.extend(action.text() for action in menu.actions() if action.text())
        action = next(action for action in menu.actions() if action.text() == name)
        rect = menu.actionGeometry(action)
        QTest.mouseMove(menu, rect.center())
        QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=rect.center())

    QTimer.singleShot(50, inside)
    QTest.mouseClick(editor.add_tag, Qt.MouseButton.LeftButton)
    qtbot.wait(100)
    return offered, editor.tag_menu.isVisible()


def test_a_click_in_the_add_tag_list_adds_the_tag_and_closes_the_list(world: World, qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QLabel

    world.call("tag.create", name="Party", colour="#aa00aa")
    _, editor = editor_for(world, qtbot)
    editor.show()
    qtbot.waitExposed(editor)
    offered, still_open = open_add_tag_list_and_click(qtbot, editor, "Party")
    assert "Food" not in offered and "Party" in offered and offered[-1] == "New tag…"  # only the tags it does not have yet
    assert not still_open and editor.chosen_tags == ["Food", "Party"]
    assert any(label.text() == "Party" and label.isVisible() for label in editor.tag_box.findChildren(QLabel))  # seen at once in the box


def test_the_tags_box_has_the_dark_field_colour_of_the_attendee_list(world: World, qtbot: QtBot) -> None:
    _, editor = editor_for(world, qtbot)
    editor.show()
    qtbot.waitExposed(editor)
    picture = editor.grab().toImage()
    box = editor.tag_box.mapTo(editor, editor.tag_box.rect().bottomRight())
    attendees = editor.attendees.mapTo(editor, editor.attendees.rect().bottomRight())
    assert picture.pixelColor(box.x() - 20, box.y() - 6) == picture.pixelColor(attendees.x() - 20, attendees.y() - 6)


def test_the_window_opens_780_pixels_wide_on_the_owner_s_screen(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui import forms
    from chrono_mail.ui.scale import wpx

    _, editor = editor_for(world, qtbot)
    screen = editor.screen()
    room = screen.availableGeometry().width() if screen is not None else 10_000
    assert editor.width() == max(editor.minimumWidth(), min(wpx(forms.owner_width(780)), room))


def test_a_click_on_a_tag_selects_it_and_remove_tag_or_delete_removes_it(world: World, qtbot: QtBot) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel

    _, editor = editor_for(world, qtbot)
    editor.choose_tag("Party", True)
    editor.choose_tag("Sport", True)
    editor.show()
    qtbot.waitExposed(editor)
    assert not editor.remove_tag.isEnabled() and editor.remove_tag.toolTip() != ""  # greyed, with why
    label = next(label for label in editor.tag_box.findChildren(QLabel) if label.text() == "Party")
    QTest.mouseClick(label, Qt.MouseButton.LeftButton)
    assert editor.tag_box.selected() == "Party" and editor.remove_tag.isEnabled()
    shown = next(label for label in editor.tag_box.findChildren(QLabel) if label.text() == "Party")  # the box drew its lines again
    row = shown.parentWidget()
    assert row is not None and row.autoFillBackground()  # highlighted
    editor.remove_tag.click()
    assert editor.chosen_tags == ["Food", "Sport"] and not editor.remove_tag.isEnabled()
    sport = next(label for label in editor.tag_box.findChildren(QLabel) if label.text() == "Sport")
    QTest.mouseClick(sport, Qt.MouseButton.LeftButton)
    QTest.keyClick(editor.tag_box, Qt.Key.Key_Delete)
    assert editor.chosen_tags == ["Food"]
    from PySide6.QtWidgets import QStyle, QStyleOptionButton

    radio = next(button for button in editor.tag_box.findChildren(QRadioButton))
    option = QStyleOptionButton()
    option.initFrom(radio)
    circle = radio.style().subElementRect(QStyle.SubElement.SE_RadioButtonIndicator, option, radio)
    QTest.mouseClick(radio, Qt.MouseButton.LeftButton, pos=circle.center())  # on the round button itself
    assert editor.tag_box.selected() == ""  # a click on the radio button chooses the colour, it selects nothing


def test_a_read_only_window_greys_add_tag_and_remove_tag(world: World, qtbot: QtBot) -> None:
    _, editor = editor_for(world, qtbot, reading=True)
    editor.tag_box.select("Food")
    assert not editor.add_tag.isEnabled() and not editor.remove_tag.isEnabled()


def test_an_older_answer_about_the_tags_never_brings_back_old_colours(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from collections.abc import Callable

    from chrono_mail.engine.commands_pim import TagInfo

    page = open_calendar(world, qtbot, layout.WEEK)
    answers: list[Callable[[tuple[TagInfo, ...]], None]] = []

    def held(command: object, parameters: object, arrived: Callable[[tuple[TagInfo, ...]], None], failed: object, owner: object = None) -> None:
        answers.append(arrived)  # held back: delivered below in the wrong order, as threads may

    monkeypatch.setattr(page._backend, "ask", held)  # noqa: SLF001
    page.reload_tags()  # asked first: still the old colour
    page.reload_tags()  # asked second: the new colour
    old = (TagInfo("Food", "#111111", 0, 1, 0),)
    new = (TagInfo("Food", "#e01b84", 0, 1, 0),)
    answers[1](new)
    answers[0](old)  # the older answer comes last
    assert next(entry for entry in page.entries if entry.summary == "Lunch").colour == "#e01b84"
