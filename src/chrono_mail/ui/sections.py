from collections.abc import Callable

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QToolBar, QToolButton, QWidget

from chrono_mail.ui.icons import theme_icon

# --- Version ------------------------------------------------------------
__version__ = "sections.py 0.0.61"

# --- Version history ----------------------------------------------------
# v0.0.61: Initial section buttons (Mail, Calendar, Tasks, Contacts) and the base of a section

MAIL: str = "mail"
CALENDAR: str = "calendar"
TASKS: str = "tasks"
CONTACTS: str = "contacts"
SECTIONS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (  # id, name, icon names, shortcut
    (MAIL, "Mail", ("mail-message",), "Ctrl+1"),
    (CALENDAR, "Calendar", ("x-office-calendar", "view-calendar"), "Ctrl+2"),
    (TASKS, "Tasks", ("view-calendar-tasks", "view-task"), "Ctrl+3"),
    (CONTACTS, "Contacts", ("x-office-address-book",), "Ctrl+4"),
)
BUTTON_SIZE: int = 40


class SectionBar(QWidget):
    """The square buttons at the bottom of the left side: one for each section of the program. One of them is always pressed."""

    chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        self.buttons: dict[str, QToolButton] = {}
        for section, name, icons, shortcut in SECTIONS:
            button = QToolButton(self)
            button.setIcon(theme_icon(*icons))
            button.setIconSize(QSize(22, 22))
            button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setToolTip(f"{name} ({shortcut})")
            button.setAccessibleName(name)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda checked=False, chosen=section: self.chosen.emit(chosen))
            layout.addWidget(button)
            self.buttons[section] = button
        layout.addStretch(1)
        self.show_section(MAIL)

    def show_section(self, section: str) -> None:
        for name, button in self.buttons.items():
            button.setChecked(name == section)  # clicking the pressed one leaves it pressed


class SectionPage(QObject):
    """What a section other than mail is made of: the sidebar, the main area and the toolbar. The window puts them in its stacks and shows the right ones."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.side: QWidget = QWidget()
        self.content: QWidget = QWidget()
        self.toolbar: QToolBar = QToolBar()
        self.status: Callable[[str], None] = lambda text: None  # the window sets this: where to say how many items there are

    def on_show(self) -> None:
        """The section was chosen: show what is current."""

    def new_item(self) -> None:
        """File > New in this section (a new event, task or contact)."""

    def refresh(self) -> None:
        """Refresh in this section."""

    def delete_selected(self) -> None:
        """Delete in this section."""

    def focus_search(self) -> None:
        """The search key in this section."""
