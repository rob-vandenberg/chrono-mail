import pytest
from PySide6.QtCore import QModelIndex, Qt
from pytestqt.qtbot import QtBot

from chrono_mail.engine.changes import MessageRow
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_list_cells.py 2.5.337"

# --- Version history ----------------------------------------------------
# v2.5.337: Initial: a cell never looks for its row for what the list does not answer; flags as before, ready made (W13 follow-up)


def test_what_the_list_never_answers_is_answered_without_looking_for_the_row(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Qt asks every cell it draws about eight things; a large delete redraws the list a hundred times (W13 follow-up)."""
    model = started(qtbot, world).window.list_model
    looked: list[int] = []
    real_row_at = model.row_at

    def counting(row: int, fetch: bool = False) -> MessageRow | None:
        looked.append(row)
        return real_row_at(row, fetch)

    monkeypatch.setattr(model, "row_at", counting)
    never = (Qt.ItemDataRole.DecorationRole, Qt.ItemDataRole.CheckStateRole, Qt.ItemDataRole.BackgroundRole, Qt.ItemDataRole.SizeHintRole, Qt.ItemDataRole.EditRole)
    cells = [model.index(row, column) for row in range(model.rowCount()) for column in range(model.columnCount())]
    assert all(cell.data(role) is None for cell in cells for role in never) and looked == []
    assert any(cell.data(Qt.ItemDataRole.DisplayRole) for cell in cells) and looked  # what it answers still comes from the row
    message = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemNeverHasChildren | Qt.ItemFlag.ItemIsDragEnabled
    assert all(model.flags(cell) == message for cell in cells) and model.flags(QModelIndex()) == Qt.ItemFlag.ItemNeverHasChildren
    model.show_groups = True
    model.reload()
    wait(qtbot, lambda: any(model.is_header(row) for row in range(model.rowCount())))
    heading = next(row for row in range(model.rowCount()) if model.is_header(row))
    assert model.flags(model.index(heading, 0)) == Qt.ItemFlag.ItemNeverHasChildren  # a heading: not enabled, never selected

