from collections.abc import Callable
from datetime import datetime
from typing import override

from PySide6.QtCore import QSettings, QSize, Qt, Signal
from PySide6.QtGui import QCloseEvent, QColor, QGuiApplication, QHideEvent, QMouseEvent, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.ui import prefs
from chrono_mail.ui.icons import bundled_icon
from chrono_mail.ui.scale import wpx
from chrono_mail.ui.windowstate import restore_geometry, save_geometry

# --- Version ------------------------------------------------------------
__version__ = "operations.py 0.5.145"

# --- Version history ----------------------------------------------------
# v0.5.145: Initial version: the Operations window (Operations, Errors and Log) of send and receive

TABS: tuple[str, ...] = ("Operations", "Errors", "Log")
MAXIMUM_LOG_LINES: int = 5000  # the log is kept only while the program runs
HIDE_KEY: str = "operations_hide_when_done"  # the same setting as Settings > General > Hide window when send and receive completes
NO_ERRORS: str = "There are no errors."
WAITING: str = "Waiting"
DONE: str = "Done"
FAILED: str = "Failed"
Names = Callable[[str, str], str]  # account id, "receive" or "send" -> "address (POP3)"


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def account_picture(size: int, palette: QPalette) -> QPixmap:
    """The account icon with a small blue badge that holds the refresh icon in white, at the bottom right."""
    picture = QPixmap(size, size)
    picture.fill(Qt.GlobalColor.transparent)
    painter = QPainter(picture)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    bundled_icon("user-identity").paint(painter, 0, 0, size, size)
    badge = max(8, size * 9 // 20)
    left, top = size - badge, size - badge
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(palette.color(QPalette.ColorRole.Highlight))
    painter.drawEllipse(left, top, badge, badge)
    inner = badge * 2 // 3
    symbol = QPixmap(inner, inner)
    symbol.fill(Qt.GlobalColor.transparent)
    symbol_painter = QPainter(symbol)
    bundled_icon("view-refresh").paint(symbol_painter, 0, 0, inner, inner)
    symbol_painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    symbol_painter.fillRect(0, 0, inner, inner, QColor("#ffffff"))
    symbol_painter.end()
    painter.drawPixmap(left + (badge - inner) // 2, top + (badge - inner) // 2, symbol)
    painter.end()
    return picture


class ClickableBar(QProgressBar):
    """The busy bar of the status line: a click on it opens the Operations window."""

    clicked = Signal()

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class OperationRow(QFrame):
    """One account at work: its picture, its name in bold, the step it is at and a bar."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("operationRow")
        self.setStyleSheet("#operationRow { border-bottom: 1px solid palette(mid); }")
        self.finished: bool = False
        self.failed: bool = False
        self.step: str = WAITING
        layout = QHBoxLayout(self)
        layout.setContentsMargins(wpx(10), wpx(8), wpx(10), wpx(8))
        layout.setSpacing(wpx(12))
        self.picture = QLabel(self)
        size = wpx(40)
        self.picture.setPixmap(account_picture(size, self.palette()))
        self.picture.setFixedSize(QSize(size, size))
        layout.addWidget(self.picture, 0, Qt.AlignmentFlag.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(wpx(3))
        self.name_label = QLabel(name, self)
        self.name_label.setTextFormat(Qt.TextFormat.PlainText)
        font = self.name_label.font()
        font.setBold(True)
        self.name_label.setFont(font)
        self.step_label = QLabel(WAITING, self)
        self.step_label.setTextFormat(Qt.TextFormat.PlainText)
        self.bar = QProgressBar(self)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(wpx(5))
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        highlight = self.palette().color(QPalette.ColorRole.Highlight).name()
        self.bar.setStyleSheet(f"QProgressBar {{ border: none; background: palette(mid); }} QProgressBar::chunk {{ background: {highlight}; }}")
        texts.addWidget(self.name_label)
        texts.addWidget(self.step_label)
        texts.addWidget(self.bar)
        layout.addLayout(texts, 1)

    def show_step(self, step: str, done: int | None, total: int | None) -> None:
        self.step = step
        if total:
            self.step_label.setText(f"{step} {done or 0:,} of {total:,}")
            self.bar.setRange(0, total)
            self.bar.setValue(min(done or 0, total))
        else:
            self.step_label.setText(step)
            self.bar.setRange(0, 0)  # moving: the end is not known

    def finish(self, error: str | None) -> None:
        self.finished, self.failed = True, error is not None
        self.step = FAILED if error is not None else DONE
        self.step_label.setText(f"{FAILED}: {error}" if error is not None else DONE)
        self.bar.setRange(0, 1)
        self.bar.setValue(1)


class ErrorRow(QFrame):
    """One error: the account in bold, the error under it and the time."""

    def __init__(self, name: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("errorRow")
        self.setStyleSheet("#errorRow { border-bottom: 1px solid palette(mid); }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(wpx(10), wpx(8), wpx(10), wpx(8))
        layout.setSpacing(wpx(3))
        top = QHBoxLayout()
        self.name_label = QLabel(name, self)
        self.name_label.setTextFormat(Qt.TextFormat.PlainText)
        font = self.name_label.font()
        font.setBold(True)
        self.name_label.setFont(font)
        self.time_label = QLabel(_now(), self)
        top.addWidget(self.name_label, 1)
        top.addWidget(self.time_label)
        self.text_label = QLabel(text, self)
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)  # an error can hold text from a server: never markup
        self.text_label.setWordWrap(True)
        layout.addLayout(top)
        layout.addWidget(self.text_label)


def _list_page(parent: QWidget) -> tuple[QScrollArea, QVBoxLayout]:
    area = QScrollArea(parent)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    inside = QWidget(area)
    layout = QVBoxLayout(inside)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addStretch(1)
    area.setWidget(inside)
    return area, layout


class OperationsWindow(QDialog):
    """What send and receive is doing, account by account (Operations), what went wrong (Errors) and every step (Log).
    It only shows what the main window and the engine's step reports tell it; it starts nothing itself."""

    def __init__(self, names: Names, settings: QSettings | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Operations")
        self.setModal(False)
        self._names = names
        self._settings = settings
        self._rows: dict[tuple[str, str], OperationRow] = {}
        self._errors: list[ErrorRow] = []
        self.round_errors: int = 0  # errors since the last send and receive started
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.tabs = QTabBar(self)
        for label in TABS:
            self.tabs.addTab(label)
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.setStyleSheet(
            "QTabBar::tab { padding: 8px 16px; border: none; border-bottom: 3px solid transparent; }"
            " QTabBar::tab:selected { border-bottom: 3px solid palette(highlight); }"
        )
        layout.addWidget(self.tabs)
        self.pages = QStackedWidget(self)
        operations_area, self._operations_layout = _list_page(self)
        self.pages.addWidget(operations_area)
        errors = QStackedWidget(self)
        self.no_errors = QLabel(NO_ERRORS, errors)
        self.no_errors.setAlignment(Qt.AlignmentFlag.AlignCenter)
        errors_area, self._errors_layout = _list_page(errors)
        errors.addWidget(self.no_errors)
        errors.addWidget(errors_area)
        self._errors_stack = errors
        self.pages.addWidget(errors)
        self.log = QPlainTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(MAXIMUM_LOG_LINES)
        self.log.setFrameShape(QFrame.Shape.NoFrame)
        self.pages.addWidget(self.log)
        layout.addWidget(self.pages, 1)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(wpx(10), wpx(8), wpx(10), wpx(8))
        self.hide_when_done = QCheckBox("Hide when all operations are complete", self)
        self.hide_when_done.setChecked(prefs.get_bool(settings, HIDE_KEY) if settings is not None else bool(prefs.DEFAULTS[HIDE_KEY]))
        self.ignore_all = QPushButton("Ignore All", self)
        self.copy_log = QPushButton("Copy Log", self)
        self.clear_log = QPushButton("Clear Log", self)
        bottom.addWidget(self.hide_when_done)
        bottom.addStretch(1)
        for button in (self.ignore_all, self.copy_log, self.clear_log):
            button.setAutoDefault(False)
            bottom.addWidget(button)
        layout.addLayout(bottom)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.hide_when_done.toggled.connect(self._hide_toggled)
        self.ignore_all.clicked.connect(self.clear_errors)
        self.copy_log.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.log.toPlainText()))
        self.clear_log.clicked.connect(self.log.clear)
        self._tab_changed(0)
        self._show_errors_page()
        self.resize(wpx(500), wpx(600))
        if settings is not None:
            restore_geometry(self, settings, "operations_geometry")

    # --- what the main window and the engine tell it ----------------------------------------------------------------

    def begin_round(self) -> None:
        """A send and receive starts: the rows of the last one go (the errors stay until they are ignored)."""
        if self.running():
            return
        for row in self._rows.values():
            self._operations_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        self.round_errors = 0

    def add(self, account_id: str, direction: str) -> None:
        """An account starts: a row that waits."""
        key = (account_id, direction)
        row = self._rows.get(key)
        if row is not None and not row.finished:
            return
        if row is not None:
            self._operations_layout.removeWidget(row)
            row.deleteLater()
        row = OperationRow(self._names(account_id, direction), self)
        self._operations_layout.insertWidget(self._operations_layout.count() - 1, row)
        self._rows[key] = row
        self._log(row, WAITING)

    def step(self, account_id: str, direction: str, step: str, done: int | None, total: int | None) -> None:
        """A step reported by the engine. A send has no row before its first step (only accounts with mail to send get one)."""
        key = (account_id, direction)
        if key not in self._rows or (direction == "send" and self._rows[key].finished):
            if direction != "send":
                return  # a receive the window was not told about (an answer for a held-back folder)
            self.add(account_id, direction)
        row = self._rows[key]
        if row.finished:
            return
        if step != row.step:
            self._log(row, step)
        row.show_step(step, done, total)

    def finish(self, account_id: str, direction: str, error: str | None = None) -> None:
        """An account is done, or failed with this error."""
        row = self._rows.get((account_id, direction))
        if error is not None:
            self.add_error(self._names(account_id, direction) if account_id else "Outbox", error)
        if row is None or row.finished:
            return
        row.finish(error)
        self._log(row, f"{FAILED}: {error}" if error is not None else DONE)

    def finish_all(self, direction: str) -> None:
        """Every row of this kind that is still at work is done (a send ends for every account at once)."""
        for (account_id, kind), row in list(self._rows.items()):
            if kind == direction and not row.finished:
                self.finish(account_id, kind)

    def add_error(self, name: str, text: str) -> None:
        row = ErrorRow(name, text, self)
        self._errors_layout.insertWidget(self._errors_layout.count() - 1, row)
        self._errors.append(row)
        self.round_errors += 1
        self._show_errors_page()

    def clear_errors(self) -> None:
        """Ignore All."""
        for row in self._errors:
            self._errors_layout.removeWidget(row)
            row.deleteLater()
        self._errors.clear()
        self._show_errors_page()

    # --- state ------------------------------------------------------------------------------------------------------

    def running(self) -> bool:
        return any(not row.finished for row in self._rows.values())

    def rows(self) -> dict[tuple[str, str], OperationRow]:
        return dict(self._rows)

    def error_texts(self) -> list[str]:
        return [row.text_label.text() for row in self._errors]

    def _log(self, row: OperationRow, step: str) -> None:
        self.log.appendPlainText(f"{_now()}  {row.name_label.text()} {step}")

    def _show_errors_page(self) -> None:
        self._errors_stack.setCurrentIndex(1 if self._errors else 0)
        self.ignore_all.setEnabled(bool(self._errors))

    def _tab_changed(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.ignore_all.setVisible(index == 1)
        self.copy_log.setVisible(index == 2)
        self.clear_log.setVisible(index == 2)

    def _hide_toggled(self, on: bool) -> None:
        if self._settings is not None:
            self._settings.setValue(HIDE_KEY, on)

    def sync_hide_setting(self) -> None:
        """Settings changed the same setting: the tick box follows."""
        if self._settings is not None:
            self.hide_when_done.blockSignals(True)
            self.hide_when_done.setChecked(prefs.get_bool(self._settings, HIDE_KEY))
            self.hide_when_done.blockSignals(False)

    @override
    def hideEvent(self, event: QHideEvent) -> None:
        if self._settings is not None:
            save_geometry(self, self._settings, "operations_geometry")
        super().hideEvent(event)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self.hide()
        event.ignore()  # the window is kept: its rows, errors and log stay for the next time it is opened
