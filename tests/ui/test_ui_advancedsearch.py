from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot
from PySide6.QtWidgets import QFormLayout, QLabel, QWidget

from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.ui.advancedsearch import AdvancedSearch, AdvancedSearchDialog, SearchScope
from chrono_mail.ui.savedsearches import SavedSearches
from chrono_mail.ui.searchform import DateChoice, SearchForm, SearchLine
from chrono_mail.ui.searchlines import DateRow
from chrono_mail.engine.commands_view import OrderParameters
from chrono_mail.ui.messagelist import MessageListModel, ViewSpec
from chrono_mail.ui.searchfield import SearchField
from tests.mailfactory import raw_message
from tests.ui.test_ui_window import select, subjects
from tests.ui.world import account_node, World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_advancedsearch.py 4.2.410"

# --- Version history ----------------------------------------------------
# v4.2.410: the search builder: the lines and their joining words, the first line without [−], the date choices, the saved
#           searches (Save, Save as, Rename, Delete, Reset), the form kept for the next time (D-289)
# v4.2.408: the lower fields line up with the upper ones; closing More gives the room back; the filters on are kept;
#           Where to search and the main fields first, the rest below More... (opens and closes wherever it is clicked); Sent, Tag,
#           Snoozed until and Send at, greyed where such mail cannot be; the new days and the tag reach the list (D-288)
# v4.2.407: All folders at start; the dialog opens on the search field's choice; Advanced search… in the field's menu (D-287)
# v4.2.406: Scope has the search field's three choices in its order; a search with This folder + subfolders runs from the dialog (D-287)
# v2.0.298: the folders under an account in the owner's new order (D-232)
# v2.0.292: the folders under an account in the owner's order (D-232)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.6.260: A view with dates or sizes asks the order for them (the list has one kind of view, R-15)
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.19: Initial advanced search tests


def set_line(dialog: AdvancedSearchDialog, number: int, part: str, kind: str, text: str) -> None:
    """Fill a search line as the person would: its two choices and its words (lines are added with [+] first)."""
    line = dialog.lines.line(number)
    line.part.setCurrentIndex(line.part.findData(part))
    line.kind.setCurrentIndex(line.kind.findData(kind))
    line.text.setText(text)


def choose_days(row: DateRow, choice: str, first: QDate | None = None, last: QDate | None = None) -> None:
    for day, edit in ((first, row.first_day), (last, row.last_day)):
        if day is not None:
            edit.setDate(day)
    row.choice.setCurrentIndex(row.choice.findData(choice))


def test_the_dialog_collects_its_fields(qtbot: QtBot) -> None:
    dialog = AdvancedSearchDialog(today=date(2026, 10, 28))
    qtbot.addWidget(dialog)
    assert dialog.search() == AdvancedSearch()
    set_line(dialog, 0, "anywhere", "all", "budget")
    dialog.lines.line(0).add.click()
    set_line(dialog, 1, "from", "all", "ann")
    dialog.lines.line(1).add.click()
    set_line(dialog, 2, "anywhere", "none", "draft")
    choose_days(dialog.received, "between", QDate(2026, 2, 1), QDate(2026, 3, 31))
    choose_days(dialog.sent, "last7")
    dialog.minimum_size.setValue(2)
    dialog.maximum_size.setValue(500)
    dialog.unread.setChecked(True)
    dialog.scope.setCurrentIndex(2)
    assert dialog.preview.text() == "Search words: budget from:ann -draft"
    found = dialog.search()
    assert found == AdvancedSearch(
        "budget from:ann -draft", SearchScope.ALL, True, False, False, "2026-02-01", "2026-03-31", 2048, 512000, sent_from="2026-10-22", sent_to="2026-10-28",
    )
    assert found.extra_filters and not AdvancedSearch("x").extra_filters

def test_a_view_with_dates_or_sizes_asks_for_them(world: World) -> None:
    model = MessageListModel(world.backend)
    for spec in (ViewSpec("a", "Inbox", date_from="2026-01-01"), ViewSpec("a", "Inbox", date_to="2026-01-01"), ViewSpec("a", "Inbox", minimum_size=1), ViewSpec("a", "Inbox", maximum_size=1)):
        asked = model._order_parameters(spec, True, [])  # noqa: SLF001
        assert isinstance(asked, OrderParameters) and (asked.date_from, asked.date_to, asked.minimum_size, asked.maximum_size) == (spec.date_from, spec.date_to, spec.minimum_size, spec.maximum_size)


def put(world: World, subject: str, body: str, moment: datetime) -> None:
    with open_mailbox(world.engine, world.account_id) as mailbox:
        mailbox.deliver(mailbox.folder("Inbox"), raw_message(subject=subject, body=body + "\n", message_id=f"<{subject}@x>", date=moment), frozenset())


def test_the_window_runs_an_advanced_search_and_clears_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    put(world, "Old cats", "cats cats", datetime(2025, 1, 5, 9, 0, tzinfo=UTC))
    put(world, "New cats", "cats and dogs", datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
    put(world, "New dogs", "dogs only", datetime(2026, 8, 6, 9, 0, tzinfo=UTC))
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 6)

    def fake_exec(self: AdvancedSearchDialog) -> QDialog.DialogCode:
        set_line(self, 0, "anywhere", "any", "cats dogs")
        self.lines.line(0).add.click()
        set_line(self, 1, "anywhere", "none", "only")
        choose_days(self.received, "after", QDate(2025, 12, 31))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AdvancedSearchDialog, "exec", fake_exec)
    world.window.act_advanced.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    assert subjects(world.window) == ["New cats"]
    assert world.window.search_box.text() == "cats OR dogs -only" and not world.window.filters.chip.isHidden()
    world.window.filters.chip.click()  # the dates go, the words stay
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2 and world.window.filters.chip.isHidden())
    assert sorted(subjects(world.window)) == ["New cats", "Old cats"]
    world.window.search_box.clear()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 6)
    select(world.window, 0)


def test_changing_folder_drops_the_advanced_dates(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)

    def fake_exec(self: AdvancedSearchDialog) -> QDialog.DialogCode:
        choose_days(self.received, "before", last=QDate(2001, 1, 2))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AdvancedSearchDialog, "exec", fake_exec)
    world.window.act_advanced.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 0 and not world.window.filters.chip.isHidden())
    account = account_node(world.window)
    world.window.tree.setCurrentIndex(world.window.tree_model.index(5, 0, account))  # Flagged (Inbox, Unread, Outbox, Sent, Drafts, Flagged: D-232)
    world.window.tree.setCurrentIndex(world.window.tree_model.index(0, 0, account))  # Inbox again
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3 and world.window.filters.chip.isHidden())


def test_the_scope_offers_the_same_three_choices_as_the_search_field(qtbot: QtBot) -> None:
    """D-287: This folder only, This folder + subfolders, All folders, in this order, in the dialog and in the search field's menu."""
    dialog = AdvancedSearchDialog()
    qtbot.addWidget(dialog)
    names = ["This folder only", "This folder + subfolders", "All folders"]
    assert [dialog.scope.itemText(number) for number in range(dialog.scope.count())] == names
    assert [SearchScope(dialog.scope.itemData(number)) for number in range(dialog.scope.count())] == list(SearchScope)
    field = SearchField()
    qtbot.addWidget(field)
    menu = field.scope.menu()
    assert menu is not None and [action.text() for action in menu.actions()] == names
    dialog.scope.setCurrentIndex(1)
    assert dialog.search().scope is SearchScope.SUBFOLDERS


def test_both_start_on_all_folders_and_the_dialog_opens_on_the_choice_given(qtbot: QtBot) -> None:
    """D-287: All folders at every start, in the search field and in Advanced search; the dialog opens where the field looks;
    the field's menu ends with Advanced search… after a line, which is no choice."""
    assert AdvancedSearch().scope is SearchScope.ALL
    field = SearchField()
    qtbot.addWidget(field)
    assert field.scope.current_data() is SearchScope.ALL and field.scope.text() == "All folders" and field.scope.toolTip() == "All folders"
    for scope in SearchScope:
        dialog = AdvancedSearchDialog(scope=scope)
        qtbot.addWidget(dialog)
        assert dialog.search().scope is scope
    command = QAction("Advanced search…", field)
    field.scope.add_command(command)
    menu = field.scope.menu()
    assert menu is not None
    actions = menu.actions()
    assert [action.text() for action in actions[:3]] == ["This folder only", "This folder + subfolders", "All folders"]
    assert actions[3].isSeparator() and actions[4] is command and not command.isCheckable()


def test_the_search_fields_menu_opens_advanced_search(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-287: Advanced search… in the search field's ▾ menu opens the dialog; the button keeps showing where the search looks."""
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    opened: list[bool] = []

    def fake_exec(self: AdvancedSearchDialog) -> QDialog.DialogCode:
        opened.append(True)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(AdvancedSearchDialog, "exec", fake_exec)
    menu = world.window.scope.menu()
    assert menu is not None
    (item,) = [action for action in menu.actions() if action.text().startswith("Advanced search")]
    item.trigger()
    assert opened == [True] and world.window.scope.current_data() is SearchScope.ALL


def test_the_window_searches_a_folder_and_its_subfolders(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-287: This folder + subfolders, from the search field and from Advanced search, shows the folder's matches and those of
    every folder inside it, and a message moved into a subfolder joins the list at once."""
    world.call("folder.create", account=world.account_id, folder="Inbox/Old")
    world.call("folder.create", account=world.account_id, folder="Projects")
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    first, second = (str(getattr(world.window.list_model.row_at(row), "message_id")) for row in (0, 1))
    world.call("message.move", account=world.account_id, folder="Inbox", message=first, target="Inbox/Old")
    world.call("message.move", account=world.account_id, folder="Inbox", message=second, target="Projects")
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    assert world.window.scope.current_data() is SearchScope.ALL  # at every start
    world.window.scope.set_current_index(0)  # This folder only
    world.window.search_box.setText("Message")
    world.window.search_box.returnPressed.emit()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    world.window.scope.set_current_index(1)  # This folder + subfolders
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2)
    assert sorted(str(getattr(world.window.list_model.row_at(row), "folder")) for row in range(2)) == ["Inbox", "Inbox/Old"]
    world.call("message.move", account=world.account_id, folder="Projects", message=second, target="Inbox/Old")
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)  # it joins at once
    world.window.scope.set_current_index(0)  # This folder only
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)

    opened_on: list[SearchScope] = []

    def fake_exec(self: AdvancedSearchDialog) -> QDialog.DialogCode:
        opened_on.append(SearchScope(self.scope.currentData()))
        set_line(self, 0, "anywhere", "all", "Message")
        self.scope.setCurrentIndex(1)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AdvancedSearchDialog, "exec", fake_exec)
    world.window.act_advanced.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    assert world.window.scope.text() in ("This folder + subfolders", "")  # the field shows where the search looks (or only its ▾)
    assert world.window.scope.current_data() is SearchScope.SUBFOLDERS and opened_on == [SearchScope.FOLDER]  # it opened on the field's choice
    world.window.act_advanced.trigger()  # again: now on This folder + subfolders, which it keeps
    assert opened_on == [SearchScope.FOLDER, SearchScope.SUBFOLDERS]
    world.call("folder.delete", account=world.account_id, folder="Inbox/Old", with_messages=True)  # its mail leaves the list
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)


def _labels(dialog: AdvancedSearchDialog, name: str) -> list[str]:
    layout = dialog.findChild(QFormLayout, name)
    assert layout is not None
    found: list[str] = []
    for row in range(layout.rowCount()):
        item = layout.itemAt(row, QFormLayout.ItemRole.LabelRole)
        widget = item.widget() if item is not None else None
        assert isinstance(widget, QLabel)
        found.append(widget.text())
    return found


def test_snoozed_until_and_send_at_are_greyed_where_such_mail_cannot_be(qtbot: QtBot) -> None:
    """D-288: usable in all folders, in Snoozed (Snoozed until) and in Scheduled or the Outbox (Send at), and in their Favorites
    entries; greyed elsewhere, and a greyed row asks for nothing."""
    cases: list[tuple[str, str | None, SearchScope, bool, bool]] = [
        ("inbox", None, SearchScope.FOLDER, False, False),
        ("inbox", None, SearchScope.SUBFOLDERS, False, False),
        ("inbox", None, SearchScope.ALL, True, True),
        ("snoozed", None, SearchScope.FOLDER, True, False),
        ("scheduled", None, SearchScope.FOLDER, False, True),
        ("outbox", None, SearchScope.FOLDER, False, True),
        ("other", "inboxes", SearchScope.ALL, False, False),
        ("other", "snoozed", SearchScope.ALL, True, False),
        ("other", "scheduled", SearchScope.ALL, False, True),
    ]
    for role, combined, scope, snoozed, send_at in cases:
        dialog = AdvancedSearchDialog(scope=scope, folder_role=role, combined=combined)
        qtbot.addWidget(dialog)
        assert (dialog.snoozed.isEnabled(), dialog.send_at.isEnabled()) == (snoozed, send_at), (role, combined, scope)
    dialog = AdvancedSearchDialog(scope=SearchScope.ALL, folder_role="inbox")
    qtbot.addWidget(dialog)
    choose_days(dialog.snoozed, "next7")
    assert dialog.search().snoozed_to is not None
    dialog.scope.setCurrentIndex(0)  # This folder only: greyed, the choice kept, nothing asked
    assert not dialog.snoozed.isEnabled() and dialog.snoozed.value().choice == "next7" and dialog.search().snoozed_to is None


def test_the_window_passes_the_sent_days_and_the_tag_to_the_list(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-288: the Sent days narrow the list (the chip shows them) and the Tag is Filter > Tagged with."""
    put(world, "Old cats", "cats", datetime(2025, 1, 5, 9, 0, tzinfo=UTC))
    put(world, "New cats", "cats", datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 5)

    def fake_exec(self: AdvancedSearchDialog) -> QDialog.DialogCode:
        set_line(self, 0, "anywhere", "all", "cats")
        choose_days(self.sent, "after", QDate(2025, 12, 31))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AdvancedSearchDialog, "exec", fake_exec)
    world.window.act_advanced.trigger()
    wait(qtbot, lambda: subjects(world.window) == ["New cats"] and not world.window.filters.chip.isHidden())
    spec = world.window.messages.view_spec()
    assert spec is not None and spec.sent_from == "2026-01-01" and spec.date_from is None
    assert {"from", "to", "folder"} <= set(world.window.columns.columns)  # a search of all folders: sent and received mail, and where each is

    def tagged_exec(self: AdvancedSearchDialog) -> QDialog.DialogCode:
        self._choose_tag("Work")  # a tag of the window's own would come from the tags kept
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AdvancedSearchDialog, "exec", tagged_exec)
    world.window.act_advanced.trigger()
    wait(qtbot, lambda: world.window.mail_state.view.tag == "Work" and world.window.filters.button.isChecked())




def test_the_builder_s_rows_its_lines_and_more(qtbot: QtBot) -> None:
    """D-289: Saved searches and Where to search, all or any of the lines, Received, then More...; a line starts with Where, the
    others with and or or; [+] adds a line right below its own; the first line has no [−] but keeps its place, so every [+] is in
    one column; Tab follows the screen; More... opens and closes wherever it is clicked; the labels are one column."""
    dialog = AdvancedSearchDialog(marks=(True, False, True, "Work"), tags=["work"])
    qtbot.addWidget(dialog)
    dialog.show()
    assert _labels(dialog, "main") == ["Saved searches", "Where to search"] and _labels(dialog, "dates") == ["Received"]
    assert _labels(dialog, "extra") == ["Sent", "Size", "Only", "Tag", "Snoozed until", "Send at"]
    first = dialog.lines.line(0)
    assert first.joining.text() == "Where" and first.remove.isHidden() and first.part.currentText() == "Anywhere"
    assert first.kind.currentText() == "All of these words" and first.text.placeholderText() == "e.g. invoice march"
    alone = first.add.mapTo(dialog, QPoint(0, 0)).x()
    first.add.click()
    first.add.click()  # the second new line comes right below the first, above the other new one
    set_line(dialog, 1, "from", "any", "ann")
    qtbot.wait(20)
    assert [dialog.lines.line(number).joining.text() for number in range(3)] == ["Where", "and", "and"]
    assert dialog.lines.line(1).text.placeholderText() == "e.g. ann or ann@example.com" and dialog.lines.line(2).text.text() == ""
    assert {dialog.lines.line(number).add.mapTo(dialog, QPoint(0, 0)).x() for number in range(3)} == {alone}  # one column, [−] or not
    dialog.match.setCurrentIndex(dialog.match.findData("any"))
    assert [dialog.lines.line(number).joining.text() for number in range(3)] == ["Where", "or", "or"]
    order: list[QWidget] = [dialog.lines.line(1).part]
    walker: QWidget | None = order[0]
    while len(order) < 6 and walker is not None:  # where Tab stops: the parts that take the keyboard by Tab and are shown
        walker = walker.nextInFocusChain()
        if walker is not None and walker.focusPolicy() & Qt.FocusPolicy.TabFocus and walker.isVisible():
            order.append(walker)
    second, third = dialog.lines.line(1), dialog.lines.line(2)
    assert order == [second.part, second.kind, second.text, second.add, second.remove, third.part]
    dialog.lines.line(2).remove.click()
    assert dialog.lines.line_count() == 2 and dialog.search().query == "from:ann"
    set_line(dialog, 0, "anywhere", "any", "x " * 300)  # more combinations than a search takes
    assert not dialog.search_button.isEnabled() and "Fewer words" in dialog.preview.text()
    set_line(dialog, 0, "anywhere", "all", "")
    assert dialog.search_button.isEnabled() and dialog.preview.text() == "Search words: from:ann"
    closed = dialog.height()
    for place in (QPoint(dialog.more.width() - 3, dialog.more.height() // 2), QPoint(3, 3)):
        before = dialog.more_fields.isVisible()
        qtbot.mouseClick(dialog.more, Qt.MouseButton.LeftButton, pos=place)
        assert dialog.more_fields.isVisible() != before and dialog.more.arrow.text() == ("▴" if not before else "▾")
    assert dialog.height() == closed
    for upper, lower in (("scopeLabel", "sentLabel"), ("receivedLabel", "tagLabel")):
        above, below = dialog.findChild(QLabel, upper), dialog.findChild(QLabel, lower)
        assert above is not None and below is not None and above.mapTo(dialog, QPoint(0, 0)).x() == below.mapTo(dialog, QPoint(0, 0)).x()
    found = dialog.search()
    assert (found.unread, found.flagged, found.attachments, found.tag) == (True, False, True, "work")  # the filters on, a tag in any letters


def test_saving_renaming_deleting_and_resetting_saved_searches(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-289: Save as asks a name and asks before replacing; Save puts the form over the chosen search; Rename and Delete act on it;
    Save, Rename and Delete are greyed while none is chosen; Reset empties the form and keeps the saved searches; picking one fills
    the whole form."""
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    names: list[str] = []
    questions: list[str] = []
    answers: list[QMessageBox.StandardButton] = []
    def name_given(*args: object, **kwargs: object) -> tuple[str, bool]:
        return names.pop(0), True

    def answered(parent: object, title: str, text: str, *args: object) -> QMessageBox.StandardButton:
        questions.append(text)
        return answers.pop(0)

    monkeypatch.setattr(QInputDialog, "getText", name_given)
    monkeypatch.setattr(QMessageBox, "question", answered)
    saved = SavedSearches(tmp_path / "saved-searches.json")
    dialog = AdvancedSearchDialog(saved=saved)
    qtbot.addWidget(dialog)
    assert not any(button.isEnabled() for button in (dialog.save_button, dialog.rename_button, dialog.delete_button))
    assert dialog.save_as_button.isEnabled() and dialog.reset_button.isEnabled() and dialog.saved_choice.placeholderText() == "No saved search chosen"
    set_line(dialog, 0, "subject", "exact", "monthly invoice")
    choose_days(dialog.received, "last30")
    names.append("Invoices")
    dialog.save_as_button.click()
    assert dialog.chosen_name() == "Invoices" and dialog.save_button.isEnabled() and questions == []
    set_line(dialog, 0, "from", "all", "ann")
    dialog.save_button.click()  # over Invoices
    stored = saved.form("Invoices")
    assert stored is not None and stored.lines == (SearchLine("from", "all", "ann"),) and stored.received == DateChoice("last30")
    names.append("invoices")
    answers.append(QMessageBox.StandardButton.No)
    dialog.save_as_button.click()  # the same name in other letters: asked, and kept
    assert len(questions) == 1 and saved.names() == ["Invoices"]
    names.append("Bills")
    dialog.rename_button.click()
    assert saved.names() == ["Bills"] and dialog.chosen_name() == "Bills"
    dialog.reset_button.click()
    assert dialog.lines.lines() == (SearchLine(),) and dialog.received.value() == DateChoice() and dialog.chosen_name() is None
    assert saved.names() == ["Bills"] and not dialog.delete_button.isEnabled()
    dialog.saved_choice.setCurrentIndex(0)
    dialog.saved_choice.activated.emit(0)
    assert dialog.lines.lines() == (SearchLine("from", "all", "ann"),) and dialog.received.value() == DateChoice("last30")
    answers.append(QMessageBox.StandardButton.Yes)
    dialog.delete_button.click()
    assert saved.names() == [] and dialog.chosen_name() is None and questions[-1] == "Delete the saved search “Bills”?"


def test_the_window_opens_as_it_was_last_left_also_after_a_restart(qtbot: QtBot, tmp_path: Path) -> None:
    """D-289: Search and Close keep the form; the next time (another start too) it opens with it, on the search field's choice of
    where to search and with the filters that are on now."""
    path = tmp_path / "saved-searches.json"
    dialog = AdvancedSearchDialog(saved=SavedSearches(path))
    qtbot.addWidget(dialog)
    set_line(dialog, 0, "body", "any", "a b")
    dialog.match.setCurrentIndex(dialog.match.findData("any"))
    choose_days(dialog.sent, "this_year")
    dialog.unread.setChecked(True)
    dialog.reject()  # Close
    again = AdvancedSearchDialog(scope=SearchScope.FOLDER, saved=SavedSearches(path))  # a new start reads the file again
    qtbot.addWidget(again)
    form = again.current_form()
    assert form.lines == (SearchLine("body", "any", "a b"),) and form.match == "any" and form.sent == DateChoice("this_year")
    assert form.scope is SearchScope.FOLDER and not form.unread  # the field's choice and the filters on now
    assert SavedSearches(path).last == dialog.current_form()
    assert isinstance(SearchForm(), SearchForm)
