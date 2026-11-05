from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
from PySide6.QtCore import QDate, QEvent, QItemSelectionModel, QModelIndex, QPersistentModelIndex, QPoint, QTime, Qt
from PySide6.QtGui import QHelpEvent, QImage, QPainter, QStandardItemModel
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QStyleOptionViewItem, QToolTip
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_tasklist import TaskInfo, TaskPlace
from chrono_mail.ui.collectionpanel import collection_items
from chrono_mail.ui.sections import TASKS
from chrono_mail.ui.tasksview import PAGE, ROW_ROLE, TaskListModel, TasksPage, draw_mark, due_text, group_of
from chrono_mail.ui.eventreminders import AT_A_SET_TIME, NEEDS_A_DUE_DATE, ReminderRow
from chrono_mail.ui.taskeditor import TaskEditor
from tests.test_engine_pim_lists import MANY, todos
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_tasks.py 4.4.547"

# --- Version history ----------------------------------------------------
# v4.4.547: a task's reminders are the rows of an appointment: every reminder shown and kept, a number and a unit or a set time, at most 5, and the rows counted from the due date greyed without one (D-316)
# v4.4.546: a task without a due date has only a set-time reminder: the list, the date and time, the greyed choices and a due date taken away and given back (D-316)
# v3.2.342: a task from another program keeps its repeat, reminder and due time through a Save, shown on this computer's clock (RV-19, RV-23)
# v1.9.265: laying the list out fetches no row: only the rows that come into view (R-44)
# v1.1.244: a change of one task moves, adds or removes only its row and heading, never a reset; the selection stays by itself (R-06)
# v1.1.205: the list holds places in the engine's order (grouped, not sorted again); every task of a large list has a place and its rows come in as they come into view; a hidden list takes no room (R-02)
# v0.0.63: Initial tests of the tasks section


def choose(row: ReminderRow, unit: int | str) -> None:
    row.unit.setCurrentIndex(row.unit.findData(unit))


def said(row: ReminderRow) -> tuple[int, str]:
    return row.number.value(), row.unit.currentText()


def minutes(task: TaskInfo) -> list[int | None]:
    return [reminder.minutes_before for reminder in task.reminders]


class Row:
    def __init__(self, summary: str, due: str = "", done: bool = False, priority: int = 0) -> None:
        self.summary, self.due, self.done, self.priority = summary, due, done, priority
        self.item, self.tags, self.status, self.percent, self.overdue, self.repeats = summary, (), "NEEDS-ACTION", 0, False, False


def lists(world: World) -> str:
    return next(c.collection for c in world.call("collection.list", kind="tasks"))


def seed(world: World) -> None:
    world.call("pim.enable")
    book = lists(world)
    world.call("task.add", list=book, summary="Pay rent", due="2020-01-31", priority=1, tag=("Home",))
    world.call("task.add", list=book, summary="Plan holiday", due="2099-06-01")
    world.call("task.add", list=book, summary="Tidy the desk")
    done = world.call("task.add", list=book, summary="Old job", due="2020-02-01")
    world.call("task.complete", item=done.item)


def open_tasks(world: World, qtbot: QtBot, tasks: int) -> TasksPage:
    world.call("pim.enable")
    started(qtbot, world)
    world.window.switch_section(TASKS)
    page = world.window.pages[TASKS]
    assert isinstance(page, TasksPage)
    wait(qtbot, lambda: len(page.model.rows()) == tasks and page.panel.visible_ids() != [] and (tasks == 0 or page.count_label.text() != ""))
    return page


def headers(page: TasksPage) -> list[str]:
    return [str(payload) for kind, payload in page.model.entries if kind == "header"]


def names(page: TasksPage) -> list[str]:
    return [str(getattr(row, "summary")) for row in page.model.rows()]


def select(page: TasksPage, *summaries: str) -> None:
    page.list.selectionModel().clearSelection()
    for summary in summaries:
        position = page.model.position_of(next(str(getattr(r, "item")) for r in page.model.rows() if getattr(r, "summary") == summary))
        page.list.selectionModel().select(page.model.index(position), QItemSelectionModel.SelectionFlag.Select)
    page._selection_changed()  # noqa: SLF001


def place(item: str, due_day: str = "", done: bool = False) -> TaskPlace:
    return TaskPlace(item, "account", "list", done, due_day, ())


def test_tasks_are_grouped_by_their_due_date() -> None:
    today = date(2026, 10, 12)
    for due, group in (("2026-10-11", "Overdue"), ("2026-10-12", "Today"), ("2026-10-13", "Tomorrow"), ("2026-10-18", "This week"), ("2026-11-05", "This month"), ("2027-01-01", "Later"), ("", "No due date")):
        assert group_of(place("x", due), today)[1] == group
    assert group_of(place("x", "2026-10-12", done=True), today)[1] == "Completed"
    model = TaskListModel()
    model.load([place("d", "2026-10-11"), place("a", "2026-10-11"), place("b", "2027-01-01"), place("c"), place("e", "2026-10-12", done=True)], today)  # in the engine's order
    assert [(kind, payload if isinstance(payload, str) else payload.item) for kind, payload in model.entries] == [
        ("header", "Overdue"), ("task", "d"), ("task", "a"), ("header", "Later"), ("task", "b"), ("header", "No due date"), ("task", "c"), ("header", "Completed"), ("task", "e"),
    ]
    assert not model.flags(model.index(0)) & Qt.ItemFlag.ItemIsSelectable and model.flags(model.index(1)) & Qt.ItemFlag.ItemIsSelectable
    assert model.position_of("a") == 2 and model.position_of("zzz") == -1
    assert "12" in due_text("2026-10-12") and "2026" in due_text("2026-10-12") and "17:00" in due_text("2026-10-12T17:00:00") and due_text("") == ""  # the month is written in the language of the system


def test_the_marks_are_drawn_for_every_state(qtbot: QtBot) -> None:
    image = QImage(200, 40, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    for number, row in enumerate((Row("todo"), Row("done", done=True), Row("late"), Row("working"), Row("off"))):
        row.overdue = row.summary == "late"
        row.status = {"working": "IN-PROCESS", "off": "CANCELLED"}.get(row.summary, row.status)
        draw_mark(painter, QPoint(20 + 36 * number, 20), row)
    painter.end()
    colours = {image.pixelColor(x, 20).name() for x in range(0, 200)}
    assert len(colours) > 3  # amber, green, red and grey were all painted


def test_the_tasks_are_listed_in_groups_and_the_tabs_and_lists_filter_them(world: World, qtbot: QtBot) -> None:
    seed(world)
    page = open_tasks(world, qtbot, 4)
    assert headers(page) == ["Overdue", "Later", "No due date", "Completed"] and names(page) == ["Pay rent", "Plan holiday", "Tidy the desk", "Old job"]
    assert page.count_label.text() == "3 active, 1 completed"
    page.tab_buttons["upcoming"].click()
    wait(qtbot, lambda: names(page) == ["Pay rent", "Plan holiday", "Tidy the desk"])
    page.tab_buttons["completed"].click()
    wait(qtbot, lambda: names(page) == ["Old job"] and headers(page) == ["Completed"])
    page.tab_buttons["all"].click()
    wait(qtbot, lambda: len(page.model.rows()) == 4)
    page.search.setText("holiday")
    wait(qtbot, lambda: names(page) == ["Plan holiday"])
    page.search.setText("")
    wait(qtbot, lambda: len(page.model.rows()) == 4)
    item = collection_items(page.panel.tree)[0]
    item.setCheckState(0, Qt.CheckState.Unchecked)  # a list that is not ticked is not shown
    wait(qtbot, lambda: page.model.rows() == [])
    item.setCheckState(0, Qt.CheckState.Checked)
    wait(qtbot, lambda: len(page.model.rows()) == 4)


def test_the_detail_pane_shows_the_task_and_the_row_is_painted(world: World, qtbot: QtBot) -> None:
    seed(world)
    page = open_tasks(world, qtbot, 4)
    select(page, "Pay rent")
    wait(qtbot, lambda: "Pay rent" in page.detail.toPlainText())
    text = page.detail.toPlainText()
    assert "Not started" in text and "31" in text and "2020" in text and "High" in text and "Home" in text
    select(page, "Old job")
    wait(qtbot, lambda: "Completed" in page.detail.toPlainText() and "Old job" in page.detail.toPlainText())
    page.list.selectionModel().clearSelection()
    page._selection_changed()  # noqa: SLF001
    assert "Select a task" in page.detail.toPlainText()
    image = QImage(400, 60, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    option = QStyleOptionViewItem()
    setattr(option, "rect", page.list.visualRect(page.model.index(1)))  # the stubs leave these attributes untyped
    setattr(option, "font", page.list.font())
    setattr(option, "palette", page.list.palette())
    for index in range(page.model.rowCount()):
        page.delegate.paint(painter, option, page.model.index(index))
        assert page.delegate.sizeHint(option, page.model.index(index)).height() in (30, 52)
    painter.end()


def test_a_task_is_added_quickly_completed_reopened_and_deleted(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.call("pim.enable")
    page = open_tasks(world, qtbot, 0)
    page.quick.setText("Call the dentist")
    page.quick.returnPressed.emit()
    wait(qtbot, lambda: names(page) == ["Call the dentist"] and page.quick.text() == "")
    select(page, "Call the dentist")
    assert page.act_complete.text() == "Mark as completed"
    page.act_complete.trigger()
    wait(qtbot, lambda: headers(page) == ["Completed"] and page.act_complete.text() == "Mark as open again")
    page.act_complete.trigger()
    wait(qtbot, lambda: headers(page) == ["No due date"] and names(page) == ["Call the dentist"])  # the heading moves at once, the row's text comes after
    monkeypatch.setattr(QMessageBox, "question", answer_no)
    select(page, "Call the dentist")
    page.act_delete.trigger()
    qtbot.wait(200)
    assert names(page) == ["Call the dentist"]
    monkeypatch.setattr(QMessageBox, "question", answer_yes)
    page.act_delete.trigger()
    wait(qtbot, lambda: names(page) == [])
    assert "no tasks" in page.detail.toPlainText()


def answer_yes(*arguments: object, **keywords: object) -> QMessageBox.StandardButton:
    return QMessageBox.StandardButton.Yes


def answer_no(*arguments: object, **keywords: object) -> QMessageBox.StandardButton:
    return QMessageBox.StandardButton.No


def answering(text: str) -> Callable[..., tuple[str, bool]]:
    def get_text(*arguments: object, **keywords: object) -> tuple[str, bool]:
        return text, True

    return get_text


def fake_exec(fill: Callable[[TaskEditor], None]) -> Callable[[TaskEditor], int]:
    def run(editor: TaskEditor) -> int:
        fill(editor)
        editor._save()  # noqa: SLF001
        return 0

    return run


def test_a_task_is_made_and_changed_in_the_editor(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.call("pim.enable")
    page = open_tasks(world, qtbot, 0)

    def fill(editor: TaskEditor) -> None:
        editor.summary.setText("Write the report")
        editor.due.enabled.setChecked(True)
        editor.due.all_day.setChecked(True)
        editor.due.stamp.setDate(editor.due.stamp.date().fromString("2099-03-01", "yyyy-MM-dd"))
        editor.priority.setCurrentIndex(editor.priority.findData(1))
        editor.repeat.setCurrentIndex(editor.repeat.findData("FREQ=WEEKLY"))
        editor.reminders.add()
        editor.reminders.rows[0].number.setValue(1)
        choose(editor.reminders.rows[0], 60)
        editor.note.setPlainText("Use the template")
        editor.tags.setText("Work, Urgent")

    monkeypatch.setattr(TaskEditor, "exec", fake_exec(fill))
    page.act_new.trigger()
    wait(qtbot, lambda: names(page) == ["Write the report"])
    item = str(getattr(page.model.rows()[0], "item"))
    made = world.call("task.show", item=item)
    assert (made.due, made.priority, made.rrule, minutes(made), made.description, made.tags) == ("2099-03-01", 1, "FREQ=WEEKLY", [60], "Use the template", ("Work", "Urgent"))

    def change(editor: TaskEditor) -> None:
        assert editor.summary.text() == "Write the report" and editor.due.enabled.isChecked() and editor.due.all_day.isChecked() and editor.tags.text() == "Work, Urgent"
        assert editor.repeat.currentData() == "FREQ=WEEKLY" and said(editor.reminders.rows[0]) == (1, "hours") and editor.priority.currentData() == 1
        editor.due.enabled.setChecked(False)
        editor.repeat.setCurrentIndex(0)
        editor.reminders.remove(editor.reminders.rows[0])
        editor.tags.setText("")
        editor.status.setCurrentIndex(editor.status.findData("in-process"))
        editor.percent.setValue(40)

    monkeypatch.setattr(TaskEditor, "exec", fake_exec(change))
    select(page, "Write the report")
    wait(qtbot, lambda: "Write the report" in page.detail.toPlainText())
    page.act_edit.trigger()
    wait(qtbot, lambda: world.call("task.show", item=item).percent == 40)
    after = world.call("task.show", item=item)
    assert (after.due, after.rrule, after.reminders, after.tags, after.status) == ("", "", (), (), "IN-PROCESS")

    def empty(editor: TaskEditor) -> None:
        editor.summary.setText("")

    monkeypatch.setattr(TaskEditor, "exec", fake_exec(empty))
    page.act_edit.trigger()
    qtbot.wait(300)
    assert world.call("task.show", item=item).summary == "Write the report"  # a task needs a summary: nothing was saved
    monkeypatch.setattr(QInputDialog, "getText", answering("x"))  # not used here: keeps the dialog away should something ask


def test_tags_and_moves_work_on_several_tasks_at_once(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seed(world)
    other = world.call("collection.create", kind="tasks", name="Work").collection
    page = open_tasks(world, qtbot, 4)
    wait(qtbot, lambda: "Home" in page._tags_known)  # noqa: SLF001
    select(page, "Plan holiday", "Tidy the desk")
    page._fill_tag_menu()  # noqa: SLF001
    labels = [a.text() for a in page.tag_menu.actions()]
    assert "Home" in labels and "New tag…" in labels and "Remove all tags" in labels
    monkeypatch.setattr(QInputDialog, "getText", answering("Chores"))
    page._new_tag()  # noqa: SLF001
    wait(qtbot, lambda: [getattr(r, "tags") for r in page.model.rows() if getattr(r, "summary") in ("Plan holiday", "Tidy the desk")] == [("Chores",), ("Chores",)])
    assert sorted(names(page)) == sorted(["Pay rent", "Plan holiday", "Tidy the desk", "Old job"]) and len(page._selected_items()) == 2  # noqa: SLF001
    page._fill_move_menu()  # noqa: SLF001
    assert [a.text() for a in page.move_menu.actions()] == ["Work"]
    page.move_menu.actions()[0].trigger()
    wait(qtbot, lambda: {str(getattr(r, "list_name")) for r in page.model.rows() if getattr(r, "summary") in ("Plan holiday", "Tidy the desk")} == {"Work"})
    assert [t.summary for t in world.call("task.list", list=other).tasks] == ["Plan holiday", "Tidy the desk"]


def test_the_menu_of_the_list_is_built(world: World, qtbot: QtBot) -> None:
    seed(world)
    page = open_tasks(world, qtbot, 4)
    select(page, "Pay rent")
    entries = [(kind, payload) for kind, payload in page.model.entries]
    assert entries[0] == ("header", "Overdue") and page.list.model().index(0, 0).data(ROW_ROLE) == entries[0] and isinstance(page.list.model().index(0, 0), QModelIndex)


def test_every_task_of_a_large_list_has_a_place_and_its_rows_come_in_as_they_come_into_view(world: World, qtbot: QtBot) -> None:
    seed(world)
    big = world.call("collection.create", kind="tasks", name="Big").collection
    assert world.call("task.import", list=big, text=todos(MANY, "big")).imported == MANY
    world.call("pim.enable")
    started(qtbot, world)
    world.window.switch_section(TASKS)
    page = world.window.pages[TASKS]
    assert isinstance(page, TasksPage)
    wait(qtbot, lambda: big in page.panel.visible_ids() and len(page.model.places()) == MANY + 4)
    wait(qtbot, lambda: page.count_label.text() == f"{MANY + 3} active, 1 completed")  # every task counted, none cut
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001 - every row asked for has come in
    assert 0 < len(page.model.loaded) <= 2 * PAGE  # only the rows that came into view: laying the list out asks for no row (R-44)
    last = page.model.places()[-1].item
    page.list.scrollToBottom()
    page.model.data(page.model.index(page.model.rowCount() - 1), ROW_ROLE)  # what a paint of the last row asks
    wait(qtbot, lambda: last in page.model.loaded)
    item = next(entry for entry in collection_items(page.panel.tree) if str(entry.text(0)) == "Big")
    item.setCheckState(0, Qt.CheckState.Unchecked)  # a list that is not ticked takes no room
    wait(qtbot, lambda: len(page.model.places()) == 4 and len(page.model.rows()) == 4 and page.count_label.text() == "3 active, 1 completed")


def entries_of(model: TaskListModel) -> list[tuple[str, str]]:
    return [(kind, payload if isinstance(payload, str) else payload.item) for kind, payload in model.entries]


def test_one_task_changed_moves_only_its_row_and_heading() -> None:
    today = date(2026, 10, 12)
    model = TaskListModel()
    a, b, c = place("a", "2026-10-11"), place("b", "2026-10-12"), place("c")
    model.load([a, b, c], today)
    resets: list[bool] = []
    model.modelReset.connect(lambda: resets.append(True))
    kept = QPersistentModelIndex(model.index(model.position_of("b")))  # what the view's selection holds
    moved = QPersistentModelIndex(model.index(model.position_of("a")))
    model.follow([b, c, place("a", "2026-10-11", done=True)], "a", today)  # a is ticked done
    assert entries_of(model) == [("header", "Today"), ("task", "b"), ("header", "No due date"), ("task", "c"), ("header", "Completed"), ("task", "a")]
    assert kept.row() == model.position_of("b") and moved.row() == model.position_of("a")  # both stay with their tasks
    model.follow([place("n", "2026-10-13"), b, c, model.places()[-1]], "n", today)  # a new task: its heading comes
    assert entries_of(model) == [("header", "Today"), ("task", "b"), ("header", "Tomorrow"), ("task", "n"), ("header", "No due date"), ("task", "c"), ("header", "Completed"), ("task", "a")]
    model.drop("b")  # the last one of Today: the heading goes
    assert ("header", "Today") not in entries_of(model) and kept.row() == -1
    model.follow([place("c", "2026-10-12"), place("n", "2026-10-13"), model.places()[-1]], "c", today)  # its due day changed
    assert entries_of(model) == [("header", "Today"), ("task", "c"), ("header", "Tomorrow"), ("task", "n"), ("header", "Completed"), ("task", "a")]
    assert resets == []  # never laid out again


def test_completing_a_task_keeps_the_selection_and_never_resets_the_list(world: World, qtbot: QtBot) -> None:
    seed(world)
    page = open_tasks(world, qtbot, 4)
    resets: list[bool] = []
    page.model.modelReset.connect(lambda: resets.append(True))
    select(page, "Plan holiday")
    tidy = next(row for row in page.model.rows() if row.summary == "Tidy the desk")
    world.call("task.complete", item=tidy.item)  # another task changes (also as from another program)
    wait(qtbot, lambda: page.model.places()[-1].item == tidy.item and len(page.model.rows()) == 4)
    assert [getattr(page.model.loaded.get(item), "summary", "") for item in page._selected_items()] == ["Plan holiday"]  # noqa: SLF001
    select(page, "Pay rent")
    page.act_complete.trigger()  # the selected task is ticked done: it moves under Completed and stays selected
    wait(qtbot, lambda: "Overdue" not in headers(page) and len(page.model.rows()) == 4)
    assert [getattr(page.model.loaded.get(item), "summary", "") for item in page._selected_items()] == ["Pay rent"]  # noqa: SLF001
    assert resets == []


FROM_ELSEWHERE: str = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//EN\r\nBEGIN:VTODO\r\nUID:weekly@example.com\r\nDTSTAMP:20261020T100000Z\r\n"
    "SUMMARY:Water the plants\r\nDUE:20991104T150000Z\r\nRRULE:FREQ=WEEKLY;BYDAY=MO,WE\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:x\r\n"
    "TRIGGER;RELATED=END:-PT30M\r\nEND:VALARM\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
)


def test_a_task_from_another_program_keeps_its_repeat_reminder_and_due_time(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-19 and RV-23: a repeat or reminder the window does not offer showed as none and Save removed it; the due time showed on the
    task's own clock (UTC here), so Save moved it by the difference with this computer's zone."""
    world.call("pim.enable")
    item = world.call("task.import", list=lists(world), text=FROM_ELSEWHERE).items[0]
    page = open_tasks(world, qtbot, 1)
    shown: list[tuple[str, str, str]] = []

    def note(editor: TaskEditor) -> None:
        shown.append((editor.repeat.currentText(), f"{said(editor.reminders.rows[0])}", editor.due.stamp.dateTime().toString("HH:mm")))
        editor.note.setPlainText("Also the ones on the balcony")

    monkeypatch.setattr(TaskEditor, "exec", fake_exec(note))
    select(page, "Water the plants")
    page.act_edit.trigger()
    wait(qtbot, lambda: world.call("task.show", item=item).description == "Also the ones on the balcony")
    local = datetime(2099, 11, 4, 15, 0, tzinfo=UTC).astimezone()
    assert shown == [("Custom: FREQ=WEEKLY;BYDAY=MO,WE", "(30, 'minutes')", local.strftime("%H:%M"))]
    after = world.call("task.show", item=item)
    assert (after.rrule, minutes(after)) == ("FREQ=WEEKLY;BYDAY=MO,WE", [30])
    assert datetime.fromisoformat(after.due) == datetime(2099, 11, 4, 15, 0, tzinfo=UTC)


def new_editor(world: World, qtbot: QtBot) -> TaskEditor:
    page = open_tasks(world, qtbot, 0)
    editor = TaskEditor(page._backend, page._lists(), [], collection=lists(world))  # noqa: SLF001
    qtbot.addWidget(editor)
    return editor


def unit_usable(row: ReminderRow, unit: int | str) -> bool:
    model = row.unit.model()
    assert isinstance(model, QStandardItemModel)
    return model.item(row.unit.findData(unit)).isEnabled()


def unit_hover_text(row: ReminderRow, unit: int | str) -> str:
    return str(row.unit.itemData(row.unit.findData(unit), Qt.ItemDataRole.ToolTipRole) or "")


COUNTED_UNITS: tuple[int, ...] = (1, 60, 1440, 10080)


def test_a_new_task_has_no_row_and_add_notification_gives_one_of_15_minutes(world: World, qtbot: QtBot) -> None:
    """D-316: the Reminder part of an appointment: no reminder is no row."""
    editor = new_editor(world, qtbot)
    editor.due.enabled.setChecked(True)
    assert editor.reminders.rows == [] and editor.reminders.add_button.text() == "Add notification"
    editor.reminders.add()
    row = editor.reminders.rows[0]
    assert said(row) == (15, "minutes") and [row.kind.itemText(index) for index in range(row.kind.count())] == ["Notification", "Email"]
    assert [row.unit.itemText(index) for index in range(row.unit.count())] == ["minutes", "hours", "days", "weeks", "At a set time…"]


def test_12_hours_before_can_be_set_by_email_and_comes_back_as_12_hours(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-316: six fixed choices could not say 12 hours."""
    page = open_tasks(world, qtbot, 0)

    def fill(editor: TaskEditor) -> None:
        editor.summary.setText("Call the bank")
        editor.due.enabled.setChecked(True)
        editor.reminders.add()
        row = editor.reminders.rows[0]
        row.kind.setCurrentIndex(1)
        row.number.setValue(12)
        choose(row, 60)

    monkeypatch.setattr(TaskEditor, "exec", fake_exec(fill))
    page.new_item()
    wait(qtbot, lambda: len(page.model.rows()) == 1)
    made = world.call("task.show", item=str(getattr(page.model.rows()[0], "item")))
    assert [(reminder.action, reminder.minutes_before, reminder.relative_to_end) for reminder in made.reminders] == [("EMAIL", 720, True)]
    seen: list[tuple[str, tuple[int, str]]] = []

    def look(editor: TaskEditor) -> None:
        seen.append((editor.reminders.rows[0].kind.currentText(), said(editor.reminders.rows[0])))
        assert editor.parameters().reminder == ()

    monkeypatch.setattr(TaskEditor, "exec", fake_exec(look))
    select(page, "Call the bank")
    page.act_edit.trigger()
    wait(qtbot, lambda: seen != [])
    assert seen == [("Email", (12, "hours"))]


def test_at_most_5_rows_can_be_added(world: World, qtbot: QtBot) -> None:
    """D-316: the owner's number for appointments; the button greys and comes back when a row goes."""
    editor = new_editor(world, qtbot)
    for _ in range(5):
        assert editor.reminders.add_button.isEnabled()
        editor.reminders.add()
    assert not editor.reminders.add_button.isEnabled()
    editor.reminders.remove(editor.reminders.rows[0])
    assert editor.reminders.add_button.isEnabled()


TWO_REMINDERS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//EN\r\nBEGIN:VTODO\r\nUID:two@example.com\r\nDTSTAMP:20261020T100000Z\r\nSUMMARY:Call the bank\r\n"
    "DUE:20990101T170000Z\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Bring the papers\r\nTRIGGER;RELATED=END:-PT30M\r\nEND:VALARM\r\n"
    "BEGIN:VALARM\r\nACTION:AUDIO\r\nTRIGGER;RELATED=END:-PT2H\r\nEND:VALARM\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:x\r\nTRIGGER;RELATED=END:PT10M\r\nEND:VALARM\r\n"
    "BEGIN:VALARM\r\nACTION:PROCEDURE\r\nDESCRIPTION:x\r\nTRIGGER;VALUE=DATE-TIME:20990101T080000Z\r\nEND:VALARM\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
)


def test_every_reminder_from_another_program_is_shown_and_a_change_of_one_row_keeps_the_others(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-316: only the first reminder showed, and a change of it removed the others unseen; removing a row removes that one only."""
    world.call("pim.enable")
    item = world.call("task.import", list=lists(world), text=TWO_REMINDERS).items[0]
    page = open_tasks(world, qtbot, 1)
    seen: list[list[str]] = []

    def change(editor: TaskEditor) -> None:
        rows = editor.reminders.rows
        seen.append([row.fixed.text() if row.fixed.text() else f"{said(row)}" for row in rows])
        assert editor.parameters().reminder == () and editor.parameters().clear == ()
        rows[1].number.setValue(3)

    monkeypatch.setattr(TaskEditor, "exec", fake_exec(change))
    select(page, "Call the bank")
    page.act_edit.trigger()
    wait(qtbot, lambda: 180 in minutes(world.call("task.show", item=item)))
    assert seen == [["(30, 'minutes')", "(2, 'hours')", "10 minutes after the due time", "At 2099-01-01 08:00"]]
    after = world.call("task.show", item=item)
    assert [(reminder.action, reminder.minutes_before) for reminder in after.reminders] == [("DISPLAY", 30), ("DISPLAY", 180), ("DISPLAY", -10), ("PROCEDURE", None)]

    def remove(editor: TaskEditor) -> None:
        editor.reminders.remove(editor.reminders.rows[1])

    wait(qtbot, lambda: names(page) == ["Call the bank"])  # the row comes back with the fact, after the task is stored
    monkeypatch.setattr(TaskEditor, "exec", fake_exec(remove))
    select(page, "Call the bank")
    page.act_edit.trigger()
    wait(qtbot, lambda: 180 not in minutes(world.call("task.show", item=item)))
    assert minutes(world.call("task.show", item=item)) == [30, -10, None]


def test_without_a_due_date_a_new_row_is_at_a_set_time_and_only_a_set_time_can_be_chosen(world: World, qtbot: QtBot) -> None:
    """D-316: a reminder counted from the due date can never fire without one: its units are greyed with a hover text, a due date makes them usable at once."""
    editor = new_editor(world, qtbot)
    editor.reminders.add()
    row = editor.reminders.rows[0]
    assert row.unit.currentData() == AT_A_SET_TIME and row.kind.isEnabled()
    assert unit_usable(row, AT_A_SET_TIME) and not any(unit_usable(row, unit) for unit in COUNTED_UNITS)
    assert all(unit_hover_text(row, unit) == NEEDS_A_DUE_DATE for unit in COUNTED_UNITS) and unit_hover_text(row, AT_A_SET_TIME) == ""
    editor.due.enabled.setChecked(True)
    assert all(unit_usable(row, unit) and unit_hover_text(row, unit) == "" for unit in COUNTED_UNITS)
    editor.due.enabled.setChecked(False)
    assert not any(unit_usable(row, unit) for unit in COUNTED_UNITS)


def test_the_hover_text_of_a_greyed_unit_shows_in_the_open_list(world: World, qtbot: QtBot) -> None:
    """OR-15: the hover text must reach the person, also on a choice that is greyed."""
    editor = new_editor(world, qtbot)
    editor.show()
    editor.reminders.add()
    unit = editor.reminders.rows[0].unit
    unit.showPopup()
    view = unit.view()
    place = view.visualRect(view.model().index(unit.findData(60), 0)).center()
    QApplication.sendEvent(view.viewport(), QHelpEvent(QEvent.Type.ToolTip, place, view.viewport().mapToGlobal(place)))
    assert QToolTip.text() == NEEDS_A_DUE_DATE
    QToolTip.hideText()
    unit.hidePopup()


def test_a_set_time_shows_a_date_and_a_time_and_is_stored_in_the_task(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-316: a row at a set time shows its date and time in place of the number, is saved in the task, and comes back on this computer's clock; left alone it is not sent."""
    world.call("pim.enable")
    page = open_tasks(world, qtbot, 0)
    shown: list[tuple[bool, bool, bool, str, str]] = []

    def state(row: ReminderRow) -> tuple[bool, bool, bool, str, str]:
        return not row.date.isHidden(), not row.time.isHidden(), not row.number.isHidden(), row.date.date().toString("yyyy-MM-dd"), row.time.time().toString("HH:mm")

    def fill(editor: TaskEditor) -> None:
        editor.show()
        editor.summary.setText("Renew the passport")
        editor.due.enabled.setChecked(True)
        editor.reminders.add()
        row = editor.reminders.rows[0]
        shown.append(state(row)[:3] + ("", ""))
        choose(row, AT_A_SET_TIME)
        shown.append(state(row)[:3] + ("", ""))
        row.date.setDate(QDate(2099, 3, 1))
        row.time.setTime(QTime(9, 30))
        editor.due.enabled.setChecked(False)

    monkeypatch.setattr(TaskEditor, "exec", fake_exec(fill))
    page.new_item()
    wait(qtbot, lambda: len(page.model.rows()) == 1)
    made = world.call("task.show", item=str(getattr(page.model.rows()[0], "item")))
    assert shown == [(False, False, True, "", ""), (True, True, False, "", "")]
    assert made.due == "" and minutes(made) == [None]
    assert datetime.fromisoformat(made.reminders[0].at) == datetime(2099, 3, 1, 9, 30).astimezone()

    def look(editor: TaskEditor) -> None:
        editor.show()
        row = editor.reminders.rows[0]
        shown.append(state(row))
        assert row.kind.isEnabled() and editor.parameters().reminder == () and editor.parameters().clear == ()
        row.time.setTime(QTime(10, 0))
        assert editor.parameters().reminder == ("at:2099-03-01T10:00",)
        row.kind.setCurrentIndex(1)
        assert editor.parameters().reminder == ("email:at:2099-03-01T10:00",)
        row.kind.setCurrentIndex(0)
        editor.note.setPlainText("Moved")

    monkeypatch.setattr(TaskEditor, "exec", fake_exec(look))
    select(page, "Renew the passport")
    page.act_edit.trigger()
    wait(qtbot, lambda: world.call("task.show", item=made.item).description == "Moved")
    assert shown[-1] == (True, True, False, "2099-03-01", "09:30")
    assert datetime.fromisoformat(world.call("task.show", item=made.item).reminders[0].at) == datetime(2099, 3, 1, 10, 0).astimezone()


def test_a_first_set_time_starts_in_the_future(world: World, qtbot: QtBot) -> None:
    editor = new_editor(world, qtbot)
    editor.reminders.add()
    chosen = datetime.fromisoformat(editor.parameters().reminder[0].removeprefix("at:"))
    assert chosen > datetime.now() and chosen.minute == 0


def test_a_row_at_a_set_time_fits_the_width_the_window_opens_at(world: World, qtbot: QtBot) -> None:
    """A width set on a window overrules its layout: the row is not there yet when the width is set, and must not be cut off when it comes."""
    editor = new_editor(world, qtbot)
    editor.show()
    width = editor.width()
    for _ in range(5):
        editor.reminders.add()
    layout = editor.layout()
    assert layout is not None
    assert layout.totalMinimumSize().width() <= width == editor.width()
    wait(qtbot, lambda: editor.height() >= layout.totalMinimumSize().height())


def label_colour(row: ReminderRow) -> set[int]:
    """The colours of the text in the closed list."""
    image = row.unit.grab().toImage()
    return {image.pixel(x, y) for x in range(image.width() // 2) for y in range(image.height())}


def test_a_task_whose_due_date_goes_keeps_its_rows_greyed_and_they_count_again_with_a_due_date(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-316: the row stays, shown greyed with the hover text, and what is stored is untouched; a set time beside it stays usable; a due date makes the row count again."""
    world.call("pim.enable")
    collection = lists(world)
    item = world.call("task.add", list=collection, summary="Call the bank", due="2099-01-01T17:00", reminder=("15", "at:2099-01-01T09:00")).item
    page = open_tasks(world, qtbot, 1)
    seen: list[tuple[tuple[int, str], bool, bool, str, str, bool]] = []

    def state(row: ReminderRow, greyed: bool) -> tuple[tuple[int, str], bool, bool, str, str, bool]:
        return said(row), row.kind.isEnabled(), row.number.isEnabled(), row.number.toolTip(), row.unit.toolTip(), greyed

    def take_away(editor: TaskEditor) -> None:
        editor.show()
        row, set_time = editor.reminders.rows
        usual = label_colour(row)
        editor.due.enabled.setChecked(False)
        seen.append(state(row, label_colour(row) != usual))
        assert row.unit.isEnabled() and row.remove.isEnabled() and not unit_usable(row, 60)
        assert set_time.kind.isEnabled() and set_time.date.isEnabled() and set_time.time.isEnabled() and set_time.unit.toolTip() == ""
        assert editor.parameters().reminder == () and editor.parameters().clear == ()

    monkeypatch.setattr(TaskEditor, "exec", fake_exec(take_away))
    select(page, "Call the bank")
    page.act_edit.trigger()
    wait(qtbot, lambda: world.call("task.show", item=item).due == "")
    assert seen == [((15, "minutes"), False, False, NEEDS_A_DUE_DATE, NEEDS_A_DUE_DATE, True)]
    assert minutes(world.call("task.show", item=item)) == [15, None]

    def give_back(editor: TaskEditor) -> None:
        editor.show()
        row = editor.reminders.rows[0]
        seen.append(state(row, False))
        editor.due.enabled.setChecked(True)
        assert unit_usable(row, 60) and row.unit.toolTip() == "" and row.kind.isEnabled() and row.number.isEnabled() and row.number.toolTip() == ""
        editor.due.stamp.setDate(QDate(2099, 2, 1))

    wait(qtbot, lambda: names(page) == ["Call the bank"])  # the row comes back with the fact, after the task is stored
    monkeypatch.setattr(TaskEditor, "exec", fake_exec(give_back))
    select(page, "Call the bank")
    page.act_edit.trigger()
    wait(qtbot, lambda: world.call("task.show", item=item).due != "")
    assert seen[-1] == ((15, "minutes"), False, False, NEEDS_A_DUE_DATE, NEEDS_A_DUE_DATE, False)
    assert minutes(world.call("task.show", item=item)) == [15, None]


def test_a_greyed_row_can_become_a_set_time(world: World, qtbot: QtBot) -> None:
    """D-316: the unit stays usable: it is the way out of a row that does nothing, and the row then counts."""
    editor = new_editor(world, qtbot)
    editor.due.enabled.setChecked(True)
    editor.reminders.add()
    editor.due.enabled.setChecked(False)
    row = editor.reminders.rows[0]
    assert not row.kind.isEnabled()
    choose(row, AT_A_SET_TIME)
    assert row.kind.isEnabled() and row.kind.toolTip() == "" and row.unit.toolTip() == ""


def test_a_set_time_changed_to_hours_is_greyed_like_any_row_counted_from_the_due_date(world: World, qtbot: QtBot) -> None:
    """The review: what greys a row is its unit now, not the reminder it was made from."""
    world.call("pim.enable")
    made = world.call("task.add", list=lists(world), summary="Call the bank", due="2099-01-01T17:00", reminder=("at:2099-01-01T09:00",))
    page = open_tasks(world, qtbot, 1)
    editor = TaskEditor(page._backend, page._lists(), [], made)  # noqa: SLF001
    qtbot.addWidget(editor)
    row = editor.reminders.rows[0]
    choose(row, 60)
    editor.due.enabled.setChecked(False)
    assert not row.kind.isEnabled() and not row.number.isEnabled() and row.number.toolTip() == NEEDS_A_DUE_DATE


def test_the_row_of_an_appointment_has_no_set_time(qtbot: QtBot) -> None:
    """The appointment window does not change: its units are the four of before, and the date and time stay out of sight."""
    row = ReminderRow(lambda _row: None)
    qtbot.addWidget(row)
    row.show()
    assert [row.unit.itemText(index) for index in range(row.unit.count())] == ["minutes", "hours", "days", "weeks"]
    assert row.number.isVisible() and not row.date.isVisible() and not row.time.isVisible() and row.value() == "15"
