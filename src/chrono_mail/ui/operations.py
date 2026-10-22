from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import override

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor, QGuiApplication, QHideEvent, QMouseEvent, QPainter, QPaintEvent, QPalette, QPixmap, QShowEvent
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
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.ui.prefs import Preferences, read_preferences, save_preferences
from chrono_mail.ui.colours import theme
from chrono_mail.ui.icons import bundled_icon
from chrono_mail.ui.scale import wpx
from chrono_mail.ui.windowstate import restore_geometry, save_geometry

# --- Version ------------------------------------------------------------
__version__ = "operations.py 1.2.253"

# --- Version history ----------------------------------------------------
# v1.2.253: Its tick "Hide when all operations are complete" reads and saves the one settings object (R-12)
# v1.1.172: The symbol on the blue badge takes the theme's colour for marks on the blue (D-158)
# v0.5.148: The bar of a row is drawn by the program (OperationBar): blue on every platform; Windows' own style drew it grey
# v0.5.145: Initial version: the Operations window (Operations, Errors and Log) of send and receive

TABS: tuple[str, ...] = ("Operations", "Errors", "Log")
MAXIMUM_LOG_LINES: int = 5000  # the log is kept only while the program runs
NO_ERRORS: str = "There are no errors."
WAITING: str = "Waiting"
DONE: str = "Done"
FAILED: str = "Failed"
BUSY_PIECE: float = 0.3  # the share of the bar the moving piece takes while the end is not known
BUSY_STEP_MS: int = 30
BUSY_STEPS: int = 50  # steps from one end to the other
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
    symbol_painter.fillRect(0, 0, inner, inner, QColor(theme(palette).on_blue))
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


class OperationBar(QWidget):
    """The bar of a row, drawn by the program itself so that every platform shows it alike (a style of the platform may
    draw a progress bar in its own colours): a grey track with the blue part of what is done, or a blue piece moving
    from end to end while the end is not known."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.done: int = 0
        self.total: int = 1
        self.busy: bool = False
        self.position: int = 0  # the step of the moving piece, 0 to 2 * BUSY_STEPS (there and back)
        self.setFixedHeight(wpx(5))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._timer = QTimer(self)
        self._timer.setInterval(BUSY_STEP_MS)
        self._timer.timeout.connect(self._advance)

    def show_count(self, done: int, total: int) -> None:
        self.busy, self.total, self.done = False, max(1, total), max(0, min(done, total))
        self._timer.stop()
        self.update()

    def show_empty(self) -> None:
        self.show_count(0, 1)

    def show_full(self) -> None:
        self.show_count(1, 1)

    def show_busy(self) -> None:
        if not self.busy:
            self.busy, self.position = True, 0
            if self.isVisible():
                self._timer.start()
        self.update()

    def filled(self) -> tuple[int, int]:
        """Where the blue part starts and how wide it is, in pixels."""
        width = self.width()
        if not self.busy:
            return 0, round(width * self.done / self.total)
        piece = max(1, round(width * BUSY_PIECE))
        step = self.position if self.position <= BUSY_STEPS else 2 * BUSY_STEPS - self.position
        return round((width - piece) * step / BUSY_STEPS), piece

    def _advance(self) -> None:
        self.position = (self.position + 1) % (2 * BUSY_STEPS)
        self.update()

    @override
    def showEvent(self, event: QShowEvent) -> None:
        if self.busy:
            self._timer.start()
        super().showEvent(event)

    @override
    def hideEvent(self, event: QHideEvent) -> None:
        self._timer.stop()
        super().hideEvent(event)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.Mid))
        start, width = self.filled()
        if width > 0:
            painter.fillRect(start, 0, width, self.height(), self.palette().color(QPalette.ColorRole.Highlight))
        painter.end()


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
        self.bar = OperationBar(self)
        texts.addWidget(self.name_label)
        texts.addWidget(self.step_label)
        texts.addWidget(self.bar)
        layout.addLayout(texts, 1)

    def show_step(self, step: str, done: int | None, total: int | None) -> None:
        self.step = step
        if total:
            self.step_label.setText(f"{step} {done or 0:,} of {total:,}")
            self.bar.show_count(done or 0, total)
        else:
            self.step_label.setText(step)
            self.bar.show_busy()  # the end is not known

    def finish(self, error: str | None) -> None:
        self.finished, self.failed = True, error is not None
        self.step = FAILED if error is not None else DONE
        self.step_label.setText(f"{FAILED}: {error}" if error is not None else DONE)
        self.bar.show_full()


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
        self.hide_when_done.setChecked((read_preferences(settings) if settings is not None else Preferences()).operations_hide_when_done)
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
            save_preferences(self._settings, replace(read_preferences(self._settings), operations_hide_when_done=on))

    def sync_hide_setting(self) -> None:
        """Settings changed the same setting: the tick box follows."""
        if self._settings is not None:
            self.hide_when_done.blockSignals(True)
            self.hide_when_done.setChecked(read_preferences(self._settings).operations_hide_when_done)
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
