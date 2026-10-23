import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from chrono_mail.ui.sections import CALENDAR
from chrono_mail.ui.syncconflicts import ConflictDialog, SyncConflictsPanel
from chrono_mail.ui.syncmarks import CONFLICT, WAITING
from tests.fakedav import FakeDav
from tests.test_engine_conflicts import change_on_server, on_server
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_sync_conflicts.py 2.4.334"

# --- Version history ----------------------------------------------------
# v2.4.334: the counts in the texts are written one way (F1b)
# v1.1.233: the table fills the width (fixed columns, Name and Where shared); no triangle in the rows; the Why icons drawn in the text colour
# v1.1.232: the legend one under the other; Close lines up with the rest
# v1.1.231: "Why" and its icon centred in the column
# v1.1.230: the mark where the text would start, 3 px before the text
# v1.1.229: a window of its own; Fields first; Why as an icon with a legend; dragged columns; a click on a triangle opens its conflict; entries at the top
# v1.1.228: Retry sync below the list and in the popup: greyed for a clash; the account is synchronised at once
# v1.1.227: mail rows with Keep here only; the Sync conflicts entry under the mail folders; the waiting mark in the message list (D-195)
# v1.1.226: Initial: Settings > Sync conflicts, the popup per field, the marks on items, the sidebar entry and the waiting count (D-194)


def linked(world: World, qtbot: QtBot, server: FakeDav) -> str:
    """The window started, its account kept in step with the server; returns the account."""
    started(qtbot, world)
    account = world.call("account.list")[0].account_id
    world.call("account.set-dav", account=account, url=server.url, username="ann", password="secret")
    world.call("dav.sync", account=account)
    return str(account)


def shown_name(world: World) -> str:
    """How the window names the account of the server's calendar."""
    return str(next(c.account_name for c in world.call("collection.list") if c.kind == "events" and c.account != "0" * 31 + "1"))


def calendar_of(world: World, account: str) -> str:
    return next(c.collection for c in world.call("collection.list") if c.account == account and c.kind == "events")


def clash(world: World, server: FakeDav, account: str, summary: str = "Meeting") -> tuple[str, str]:
    """An event whose title was changed here and on the server: a conflict. Returns (item, uid)."""
    made = world.call("event.add", calendar=calendar_of(world, account), summary=summary, start="2026-10-14T16:00", end="2026-10-14T17:00")
    change_on_server(server, str(made.uid), f"SUMMARY:{summary}", "SUMMARY:Theirs")
    world.call("event.edit", item=made.item, summary="Ours")
    return str(made.item), str(made.uid)


def panel_of(world: World, qtbot: QtBot) -> SyncConflictsPanel:
    panel = SyncConflictsPanel(world.backend, world.window.sync_marks)
    qtbot.addWidget(panel)
    return panel


def tick(panel: SyncConflictsPanel, row: int) -> None:
    item = panel.table.topLevelItem(row)
    assert item is not None
    item.setCheckState(0, Qt.CheckState.Checked)


def test_the_page_lists_a_conflict_and_use_chrono_mail_settles_it(world: World, qtbot: QtBot) -> None:
    with FakeDav() as server:
        account = linked(world, qtbot, server)
        _, uid = clash(world, server, account)
        panel = panel_of(world, qtbot)
        wait(qtbot, lambda: panel.table.topLevelItemCount() == 1)
        row = panel.table.topLevelItem(0)
        assert row is not None and [row.text(column) for column in range(1, 5)] == ["Title", "Event", "Ours", f"{shown_name(world)} — Calendar"]
        assert row.text(5) == "" and row.data(5, Qt.ItemDataRole.UserRole + 2) == "both" and row.toolTip(5).startswith("Changed on both sides")  # Why: drawn
        assert row.icon(0).isNull()  # no triangle: every row is a conflict (the owner)
        assert not panel.use_local.isEnabled() and not panel.use_server.isEnabled()  # greyed: nothing ticked
        tick(panel, 0)
        assert panel.use_local.isEnabled() and panel.use_server.isEnabled() and panel.check_all.isChecked()
        panel.invert.click()
        assert panel.ticked() == [] and not panel.check_all.isChecked()
        panel.check_all.click()
        assert len(panel.ticked()) == 1
        panel.use_local.click()
        wait(qtbot, lambda: panel.table.topLevelItemCount() == 0)  # the row leaves at once
        assert "SUMMARY:Ours" in (on_server(server, "events", uid) or ("", ""))[1]


def test_use_server_data_and_a_refusal_greys_use_chrono_mail(world: World, qtbot: QtBot) -> None:
    with FakeDav() as server:
        account = linked(world, qtbot, server)
        item, _ = clash(world, server, account)
        panel = panel_of(world, qtbot)
        wait(qtbot, lambda: panel.table.topLevelItemCount() == 1)
        tick(panel, 0)
        panel.use_server.click()
        wait(qtbot, lambda: panel.table.topLevelItemCount() == 0)
        assert world.call("event.show", item=item).summary == "Theirs"
        server.writes_failing = True
        world.call("event.edit", item=item, summary="Offline")
        server.writes_failing, server.writes_refused = False, True
        world.call("dav.sync", account=account)
        wait(qtbot, lambda: panel.table.topLevelItemCount() == 1)
        tick(panel, 0)
        assert panel.use_server.isEnabled() and not panel.use_local.isEnabled()  # the server does not take Chrono Mail's version


def test_the_popup_settles_field_by_field(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeDav() as server:
        account = linked(world, qtbot, server)
        made = world.call("event.add", calendar=calendar_of(world, account), summary="Meeting", location="Room 1", start="2026-10-14T16:00", end="2026-10-14T17:00")
        change_on_server(server, str(made.uid), "SUMMARY:Meeting", "SUMMARY:Their title")
        change_on_server(server, str(made.uid), "LOCATION:Room 1", "LOCATION:Their room")
        world.call("event.edit", item=made.item, summary="My title", location="My room")
        panel = panel_of(world, qtbot)
        wait(qtbot, lambda: panel.table.topLevelItemCount() == 1)
        conflict = world.window.sync_marks.conflicts[0]
        dialog = ConflictDialog(conflict)
        qtbot.addWidget(dialog)
        assert not dialog.apply_button.isEnabled()  # nothing chosen yet
        labels = [field.label for field in conflict.fields]
        ours, _ = dialog.groups[conflict.fields[labels.index("Title")].key]
        _, theirs = dialog.groups[conflict.fields[labels.index("Location")].key]
        ours.setChecked(True)
        assert not dialog.apply_button.isEnabled()
        theirs.setChecked(True)
        assert dialog.apply_button.isEnabled()
        dialog.all_server.click()
        assert dialog.side == "server"

        def chosen(self: ConflictDialog) -> int:  # the person picks the title of Chrono Mail and the place of the server, then Apply
            title_ours, _ = self.groups[conflict.fields[labels.index("Title")].key]
            _, place_theirs = self.groups[conflict.fields[labels.index("Location")].key]
            title_ours.setChecked(True)
            place_theirs.setChecked(True)
            self._apply()  # noqa: SLF001
            return QDialog.DialogCode.Accepted.value

        monkeypatch.setattr(ConflictDialog, "exec", chosen)
        row = panel.table.topLevelItem(0)
        assert row is not None
        panel.table.itemDoubleClicked.emit(row, 2)
        wait(qtbot, lambda: panel.table.topLevelItemCount() == 0)
        text = (on_server(server, "events", str(made.uid)) or ("", ""))[1]
        assert "SUMMARY:My title" in text and "LOCATION:Their room" in text


def test_the_marks_the_sidebar_entry_and_the_waiting_count(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeDav() as server:
        account = linked(world, qtbot, server)
        window = world.window
        window.switch_section(CALENDAR)
        page = window.pages[CALENDAR]
        entry = page.panel.conflicts_entry
        assert entry.isHidden()  # no conflicts: no entry
        item, _ = clash(world, server, account)
        wait(qtbot, lambda: not entry.isHidden() and entry.text() == "Sync conflicts (1)")
        assert page.month.mark_of(item) == CONFLICT and page.grid.canvas.mark_of(item) == CONFLICT
        opened: list[str | None] = []
        monkeypatch.setattr(type(window), "open_sync_conflicts", lambda self: opened.append("window"))
        entry.click()
        assert opened == ["window"]
        world.call("conflict.settle", conflicts=(window.sync_marks.conflicts[0].conflict,), side="server")
        wait(qtbot, lambda: entry.isHidden() and page.month.mark_of(item) == "")
        server.writes_failing = True
        world.call("event.edit", item=item, summary="Offline")
        wait(qtbot, lambda: window.waiting_label.isVisible() and window.waiting_label.text() == "Waiting for the server: 1 change")
        assert page.month.mark_of(item) == WAITING
        server.writes_failing = False
        world.call("dav.sync", account=account)
        wait(qtbot, lambda: not window.waiting_label.isVisible() and page.month.mark_of(item) == "")


def mail_conflict(reason: str) -> object:
    from chrono_mail.engine.commands_conflicts import ConflictField, ConflictSummary

    keep = reason in ("message_refused", "folder_refused")
    return ConflictSummary(
        f"c-{reason}", "acc", "Account", "Inbox", "Inbox", "m1", "mail", reason, "The server did not take it", "Message 1", "read only", False, False, 1.0,
        (ConflictField("marks", "Marks", "read", "unread"),) if reason == "marks_refused" else (), server_allowed=not keep, keep_allowed=keep,
    )


def test_mail_rows_keep_here_only_and_the_entry_under_the_mail_folders(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    marks = window.sync_marks
    panel = panel_of(world, qtbot)
    marks.conflicts = (mail_conflict("marks_refused"), mail_conflict("folder_refused"))  # type: ignore[assignment]
    marks.changed.emit()
    assert panel.table.topLevelItemCount() == 2
    first = panel.table.topLevelItem(0)
    assert first is not None and (first.text(1), first.text(2)) == ("Marks", "Message")
    tick(panel, 0)
    assert panel.use_server.isEnabled() and not panel.use_local.isEnabled() and not panel.keep_here.isEnabled()
    panel.invert.click()  # now only the folder that exists only here: the server's version would delete it
    assert panel.keep_here.isEnabled() and not panel.use_server.isEnabled() and not panel.use_local.isEnabled()
    tick(panel, 0)  # both: no button fits both
    assert not panel.use_server.isEnabled() and not panel.keep_here.isEnabled()
    dialog = ConflictDialog(marks.conflicts[1])  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    assert dialog.keep.isEnabled() and not dialog.all_server.isEnabled() and not dialog.all_local.isEnabled()
    dialog.keep.click()
    assert dialog.apply_button.isEnabled() and dialog.side == "keep"
    assert window.mail_conflicts_entry.isVisible() and window.mail_conflicts_entry.text() == "Sync conflicts (2)"
    window.switch_section(CALENDAR)
    assert not window.mail_conflicts_entry.isVisible()  # the sections have their own entry


def test_the_message_list_draws_the_waiting_mark_before_the_subject(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    first = window.list_model.index(0, 0)
    row = first.data(Qt.ItemDataRole.UserRole + 1) if first.isValid() else None
    waiting_for: list[str] = []

    def mark_of(message_id: str) -> str:
        waiting_for.append(message_id)
        return WAITING

    window.column_delegate.mark_of = mark_of
    window.delegate.mark_of = mark_of
    window.list_view.grab()
    assert waiting_for  # asked for the rows on screen, by their message id
    assert row is None or isinstance(waiting_for[0], str)


def test_retry_sync_is_for_refusals_and_synchronises_the_account_at_once(world: World, qtbot: QtBot) -> None:
    with FakeDav() as server:
        account = linked(world, qtbot, server)
        item, _ = clash(world, server, account)
        panel = panel_of(world, qtbot)
        wait(qtbot, lambda: panel.table.topLevelItemCount() == 1)
        tick(panel, 0)
        assert not panel.retry.isEnabled()  # a clash: trying again would only clash again
        dialog = ConflictDialog(world.window.sync_marks.conflicts[0])
        qtbot.addWidget(dialog)
        assert not dialog.retry.isEnabled()
        panel.use_server.click()
        wait(qtbot, lambda: panel.table.topLevelItemCount() == 0)
        server.writes_failing = True
        world.call("event.edit", item=item, summary="Offline")
        server.writes_failing, server.writes_refused = False, True
        world.call("dav.sync", account=account)
        wait(qtbot, lambda: panel.table.topLevelItemCount() == 1)
        tick(panel, 0)
        assert panel.retry.isEnabled() and not panel.use_local.isEnabled()
        refused = ConflictDialog(world.window.sync_marks.conflicts[0])
        qtbot.addWidget(refused)
        assert refused.retry.isEnabled()
        server.writes_refused = False
        with qtbot.waitSignal(panel.sync_requested, timeout=8000) as signal:
            panel.retry.click()
        assert signal.args == [[account]]
        wait(qtbot, lambda: panel.table.topLevelItemCount() == 0)


def test_the_window_of_its_own_with_its_legend_and_columns_that_can_be_dragged(world: World, qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QHeaderView, QLabel

    from chrono_mail.ui.syncconflicts import SyncConflictsWindow

    started(qtbot, world)
    window = world.window
    window.open_sync_conflicts()
    conflicts = window._conflicts_window  # noqa: SLF001
    assert isinstance(conflicts, SyncConflictsWindow) and conflicts.isVisible() and not conflicts.isModal() and conflicts.width() >= 1000
    window.open_sync_conflicts()
    assert window._conflicts_window is conflicts  # noqa: SLF001 - one window, brought to the front
    header = conflicts.panel.table.header()
    modes = [header.sectionResizeMode(column) for column in range(header.count())]
    assert modes == [QHeaderView.ResizeMode.Fixed] * 3 + [QHeaderView.ResizeMode.Interactive] + [QHeaderView.ResizeMode.Fixed] * 2  # only Name's line moves
    assert [conflicts.panel.table.headerItem().text(column) for column in range(6)] == ["", "Fields", "What", "Name", "Where", "Why"]
    texts = {label.text() for label in conflicts.panel.findChildren(QLabel)}
    assert {"Changed on both sides", "Refused by the server", "Deleted on the server, changed here", "Deleted here, changed on the server"} <= texts
    assert "Sync conflicts…" in [action.text() for action in window.findChildren(type(window.act_sync_conflicts))]
    from chrono_mail.ui.settingsdialog import SettingsContext, SettingsDialog

    settings = SettingsDialog(world.backend, window.settings, SettingsContext(tray_available=False, shortcut_actions={}), window)
    qtbot.addWidget(settings)
    titles = [item.text() for item in (settings.sidebar.item(row) for row in range(settings.sidebar.count())) if item is not None]
    assert "Sync conflicts" not in titles  # it is not a setting: no page in Settings


def test_a_click_on_the_triangle_opens_its_conflict_and_the_entry_is_at_the_top(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QTest

    from chrono_mail.ui import calendarlayout as layout

    with FakeDav() as server:
        account = linked(world, qtbot, server)
        window = world.window
        window.switch_section(CALENDAR)
        page = window.pages[CALENDAR]
        page.set_view(layout.MONTH)
        page.month.resize(900, 600)
        page.month.show()
        item, _ = clash(world, server, account)
        wait(qtbot, lambda: page.month.mark_of(item) == CONFLICT)
        wait(qtbot, lambda: bool((page.month.grab(), page.month.mark_hits)[1]))
        entry = page.panel.conflicts_entry
        assert page.panel.layout().indexOf(entry) < page.panel.layout().indexOf(page.panel.tree)  # at the top, under the heading
        assert window.centralWidget() is not None
        opened: list[str] = []
        monkeypatch.setattr(ConflictDialog, "exec", lambda self: (opened.append(self.conflict.name), QDialog.DialogCode.Rejected.value)[1])
        area, marked = page.month.mark_hits[0]
        assert page.month.mark_at(area.center()) is marked
        line = next(rect for rect, entry in page.month.hits if entry is marked)
        assert abs(area.left() - line.left() - 13) < 1  # (whole pixels) where the text would start (16 from the day's edge): 3.5 px after the dot, as the text (the owner)
        from chrono_mail.ui.syncmarks import MARK_GAP, MARK_SIZE, paint_mark
        from PySide6.QtGui import QImage, QPainter

        canvas = QImage(40, 20, QImage.Format.Format_ARGB32)
        painter = QPainter(canvas)
        assert paint_mark(painter, 0, 10, CONFLICT) == MARK_SIZE + MARK_GAP == 14  # the text starts 3 px after the mark
        painter.end()
        QTest.mouseClick(page.month, Qt.MouseButton.LeftButton, pos=area.center().toPoint())
        assert opened == ["Ours"]  # what a person would guess: its conflict opens


def test_why_and_its_icon_are_centred_in_their_column(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.syncconflicts import WHY_COLUMN

    with FakeDav() as server:
        account = linked(world, qtbot, server)
        clash(world, server, account)
        panel = panel_of(world, qtbot)
        panel.resize(1100, 400)
        panel.show()
        wait(qtbot, lambda: panel.table.topLevelItemCount() == 1)
        header = panel.table.header()
        header.resizeSection(WHY_COLUMN, 120)  # wide, so a left-placed icon could not pass for a centred one
        assert panel.table.headerItem().textAlignment(WHY_COLUMN) & Qt.AlignmentFlag.AlignHCenter
        row = panel.table.topLevelItem(0)
        assert row is not None
        rect = panel.table.visualItemRect(row)
        left = header.sectionViewportPosition(WHY_COLUMN)
        image = panel.table.viewport().grab().toImage()
        background = image.pixelColor(left + 3, rect.center().y())
        drawn = [x for x in range(left, left + 120) for y in range(rect.top(), rect.bottom()) if image.pixelColor(x, y) != background]
        assert drawn, "no icon drawn"
        middle = (min(drawn) + max(drawn)) / 2
        assert abs(middle - (left + 60)) <= 3  # in the middle of the cell, not at its left edge


def test_the_legend_is_one_under_the_other_and_close_lines_up(world: World, qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QDialogButtonBox, QLabel

    started(qtbot, world)
    window = world.window
    window.open_sync_conflicts()
    conflicts = window._conflicts_window  # noqa: SLF001
    assert conflicts is not None
    words = ["Changed on both sides", "Refused by the server", "Deleted on the server, changed here", "Deleted here, changed on the server"]
    labels = {label.text(): label for label in conflicts.panel.findChildren(QLabel) if label.text() in words}
    places = [labels[word].mapTo(conflicts, labels[word].rect().topLeft()) for word in words]
    assert len({place.x() for place in places}) == 1 and [place.y() for place in places] == sorted({place.y() for place in places})  # one column
    close = conflicts.findChild(QDialogButtonBox)
    assert close is not None
    right_gap = conflicts.width() - close.mapTo(conflicts, close.rect().topRight()).x()
    table_gap = conflicts.panel.table.mapTo(conflicts, conflicts.panel.table.rect().topLeft()).x()
    assert right_gap > 4 and abs(right_gap - table_gap) <= 2  # the same margin as the table on the left, not in the corner


def test_the_table_fills_the_width_name_and_where_share_it_and_the_icons_are_in_the_text_colour(world: World, qtbot: QtBot) -> None:
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication

    from chrono_mail.ui.syncconflicts import FIXED_COLUMNS, NAME_COLUMN, WHERE_COLUMN, WHY_COLUMN

    with FakeDav() as server:
        account = linked(world, qtbot, server)
        clash(world, server, account)
        panel = panel_of(world, qtbot)
        panel.show()
        wait(qtbot, lambda: panel.table.topLevelItemCount() == 1)
        header = panel.table.header()
        fixed = [header.sectionSize(column) for column in FIXED_COLUMNS]
        for width in (900, 1400):
            panel.resize(width, 400)
            QApplication.processEvents()
            total = sum(header.sectionSize(column) for column in range(header.count()))
            assert total == panel.table.viewport().width()  # always exactly the width: no scrollbar, nothing missing
            assert [header.sectionSize(column) for column in FIXED_COLUMNS] == fixed
            free = header.sectionSize(NAME_COLUMN) + header.sectionSize(WHERE_COLUMN)
            assert abs(header.sectionSize(NAME_COLUMN) / free - 0.6) < 0.02  # 60/40 at first
        header.resizeSection(NAME_COLUMN, int(free * 0.3))  # the person drags the line between Name and Where
        panel.resize(1000, 400)
        QApplication.processEvents()
        free = header.sectionSize(NAME_COLUMN) + header.sectionSize(WHERE_COLUMN)
        assert abs(header.sectionSize(NAME_COLUMN) / free - 0.3) < 0.02 and sum(header.sectionSize(c) for c in range(header.count())) == panel.table.viewport().width()
        row = panel.table.topLevelItem(0)
        assert row is not None
        image = panel.table.viewport().grab().toImage()
        rect = panel.table.visualItemRect(row)
        left = header.sectionViewportPosition(WHY_COLUMN)
        text = QApplication.palette().color(QPalette.ColorRole.ButtonText)
        def distance(x: int, y: int) -> int:
            colour = image.pixelColor(x, y)
            return abs(colour.red() - text.red()) + abs(colour.green() - text.green()) + abs(colour.blue() - text.blue())
        nearest = min(distance(x, y) for x in range(left, left + header.sectionSize(WHY_COLUMN)) for y in range(rect.top(), rect.bottom()))
        assert nearest < 60  # the lines in the theme's text colour (softened a little at their edges)
