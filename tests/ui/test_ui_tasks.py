from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
from PySide6.QtCore import QItemSelectionModel, QModelIndex, QPersistentModelIndex, QPoint, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QInputDialog, QMessageBox, QStyleOptionViewItem
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_tasklist import TaskPlace
from chrono_mail.ui.collectionpanel import collection_items
from chrono_mail.ui.sections import TASKS
from chrono_mail.ui.tasksview import PAGE, ROW_ROLE, TaskListModel, TasksPage, draw_mark, due_text, group_of
from chrono_mail.ui.taskeditor import TaskEditor
from tests.test_engine_pim_lists import MANY, todos
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_tasks.py 3.2.342"

# --- Version history ----------------------------------------------------
# v3.2.342: a task from another program keeps its repeat, reminder and due time through a Save, shown on this computer's clock (RV-19, RV-23)
# v1.9.265: laying the list out fetches no row: only the rows that come into view (R-44)
# v1.1.244: a change of one task moves, adds or removes only its row and heading, never a reset; the selection stays by itself (R-06)
# v1.1.205: the list holds places in the engine's order (grouped, not sorted again); every task of a large list has a place and its rows come in as they come into view; a hidden list takes no room (R-02)
# v0.0.63: Initial tests of the tasks section


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
        editor.reminder.setCurrentIndex(editor.reminder.findData("60"))
        editor.note.setPlainText("Use the template")
        editor.tags.setText("Work, Urgent")

    monkeypatch.setattr(TaskEditor, "exec", fake_exec(fill))
    page.act_new.trigger()
    wait(qtbot, lambda: names(page) == ["Write the report"])
    item = str(getattr(page.model.rows()[0], "item"))
    made = world.call("task.show", item=item)
    assert (made.due, made.priority, made.rrule, made.reminders, made.description, made.tags) == ("2099-03-01", 1, "FREQ=WEEKLY", (60,), "Use the template", ("Work", "Urgent"))

    def change(editor: TaskEditor) -> None:
        assert editor.summary.text() == "Write the report" and editor.due.enabled.isChecked() and editor.due.all_day.isChecked() and editor.tags.text() == "Work, Urgent"
        assert editor.repeat.currentData() == "FREQ=WEEKLY" and editor.reminder.currentData() == "60" and editor.priority.currentData() == 1
        editor.due.enabled.setChecked(False)
        editor.repeat.setCurrentIndex(0)
        editor.reminder.setCurrentIndex(0)
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
        shown.append((editor.repeat.currentText(), editor.reminder.currentText(), editor.due.stamp.dateTime().toString("HH:mm")))
        editor.note.setPlainText("Also the ones on the balcony")

    monkeypatch.setattr(TaskEditor, "exec", fake_exec(note))
    select(page, "Water the plants")
    page.act_edit.trigger()
    wait(qtbot, lambda: world.call("task.show", item=item).description == "Also the ones on the balcony")
    local = datetime(2099, 11, 4, 15, 0, tzinfo=UTC).astimezone()
    assert shown == [("Custom: FREQ=WEEKLY;BYDAY=MO,WE", "30 minutes before", local.strftime("%H:%M"))]
    after = world.call("task.show", item=item)
    assert (after.rrule, after.reminders) == ("FREQ=WEEKLY;BYDAY=MO,WE", (30,))
    assert datetime.fromisoformat(after.due) == datetime(2099, 11, 4, 15, 0, tzinfo=UTC)
