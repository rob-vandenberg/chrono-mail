from PySide6.QtWidgets import QAbstractItemView

from chrono_mail.ui.colours import SEPARATOR, selection_grey
from chrono_mail.ui.scale import scale_css

# --- Version ------------------------------------------------------------
__version__ = "theme.py 0.5.144"

# --- Version history ----------------------------------------------------
# v0.5.144: The stylesheet at a given size factor: the main window at its own, the message windows and composers at that of the other windows
# v0.3.106: Every size in pixels follows the factor of the interface (lines of 1 pixel stay)
# v0.2.97: The rounding of the search field is set by the field itself (half its height)
# v0.2.94: The search field has round ends (rounding 15 px, half its height; 16 px was more than half and Qt drew square corners)
# v0.1.93: The search field: grey border while not in use, blue while clicked in, orange while a search is active; a thin line before This folder
# v0.1.90: The Filter button: blue and bold while a filter is on, its ▾ placed as on the toolbar buttons; Qt's own second menu arrow hidden on Sorted by
# v0.1.89: Column headers 4 px padding left and right, so that the symbols fit in a 24 px column
# v0.0.88: Selection grey while the folder list or message list has no focus; black 1 px lines between the panes and the column headers; Sorted by in bold
# v0.0.86: Split toolbar buttons (Refresh, Snooze): the arrow part beside the text
# v0.0.85: The arrow of a toolbar button with a menu sits beside its text, not in the corner
# v0.0.81: The Menu button is the first, blue button of every toolbar and the row of the menu bar is gone; New is a plain toolbar button
# v0.0.79: The column headers of the message list have a thin line between them again
# v0.0.78: Composer: new look (Send in blue on top, rounded fields, flat toolbar) and rich text is the default for new messages
# v0.0.77: Message list and reading pane: Received instead of Date, blue unread dot (empty circle when read), flat list header, reading pane header and banner
# v0.0.76: Blue accent, Settings Accounts Rules and Exit in the root of the Menu, plain section icons with the open one in blue, Contacts before Tasks
# v0.0.73: Initial look of the main window: the Menu button, the toolbar, the search field, the folder list and the section buttons

HOVER: str = "rgba(128, 128, 128, 0.22)"  # a veil that suits a light and a dark window alike
SEARCH_ACTIVE: str = "#f0a030"  # the border of the search field while a search is active


def show_focus(view: QAbstractItemView, focused: bool) -> None:
    """Tells the stylesheet whether the view has the focus: its selection is blue with it and grey without it."""
    view.setProperty("focused", focused)
    view.style().unpolish(view)
    view.style().polish(view)
    view.viewport().update()


def stylesheet(factor: float | None = None) -> str:
    """The look of the main window, for the light and the dark scheme alike: it takes all its colours from the palette (the blue is the highlight colour, the same as in the menus).
    The rules name their widgets (object names), so nothing else in the program changes. Its sizes are at the factor
    given (the main window's when none is)."""
    grey = selection_grey()
    return scale_css(f"""
QMenuBar {{ background: palette(window); border: 0; padding: 2px 6px; }}
QMenuBar::item {{ padding: 5px 12px; background: transparent; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {HOVER}; }}
QMenu {{ background: palette(window); border: 1px solid palette(mid); padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 22px; border-radius: 3px; }}
QMenu::item:selected {{ background: palette(highlight); color: palette(highlighted-text); }}
QMenu::separator {{ height: 1px; background: palette(mid); margin: 4px 8px; }}
QToolBar#mainToolbar {{ background: palette(window); border: 0; spacing: 2px; padding: 4px 10px; }}
QToolBar#mainToolbar QToolButton {{ background: transparent; border: 0; border-radius: 4px; padding: 6px 9px; }}
QToolBar#mainToolbar QToolButton:hover, QToolBar#mainToolbar QToolButton:pressed {{ background: {HOVER}; }}
QToolBar#mainToolbar QToolButton[popupMode="2"] {{ padding-right: 22px; }}
QToolBar#mainToolbar QToolButton[popupMode="1"] {{ padding-right: 24px; }}
QToolBar#mainToolbar QToolButton::menu-button {{ border: 0; width: 18px; }}
QToolBar#mainToolbar QToolButton::menu-indicator {{ subcontrol-origin: padding; subcontrol-position: right center; right: 7px; }}
QToolButton#menuButton, QToolBar#mainToolbar QToolButton#menuButton {{ background: palette(highlight); color: palette(highlighted-text); font-weight: 600; border: 0; border-radius: 4px; padding: 6px 14px; margin-right: 6px; }}
QToolButton#menuButton:hover, QToolBar#mainToolbar QToolButton#menuButton:hover {{ background: palette(link); }}
QToolButton#menuButton::menu-indicator {{ image: none; width: 0; }}
QFrame#searchPill {{ border: 1px solid palette(mid); background: palette(base); }}
QFrame#searchPill QLineEdit {{ border: 0; background: transparent; padding: 5px 2px; }}
QFrame#searchPill QComboBox {{ border: 0; background: transparent; padding: 2px 6px; }}
QFrame#searchPill QLabel {{ border: 0; background: transparent; }}
QFrame#searchPill[look="focused"] {{ border: 1px solid palette(highlight); }}
QFrame#searchPill[look="active"] {{ border: 1px solid {SEARCH_ACTIVE}; }}
QFrame#searchSeparator {{ background: palette(mid); border: 0; margin: 4px 0; }}
QToolButton#searchEnd {{ border: 0; padding: 0 2px; }}
QLabel#sideHeading {{ font-size: 20px; font-weight: 600; padding: 12px 14px 6px 14px; }}
QTreeView#folderTree {{ border: 0; background: palette(window); outline: 0; padding: 2px 6px; }}
QTreeView#folderTree::item {{ padding: 4px 4px; border-radius: 4px; min-height: 22px; }}
QTreeView#folderTree::item:hover:!selected {{ background: {HOVER}; }}
QTreeView#folderTree::item:selected {{ background: palette(highlight); color: palette(highlighted-text); }}
QTreeView#folderTree[focused="false"]::item:selected {{ background: {grey}; color: palette(text); }}
QTreeView#messageList {{ border: 0; outline: 0; }}
QTreeView#messageList[focused="false"]::item:selected {{ background: {grey}; color: palette(text); }}
QTreeView#messageList QHeaderView::section {{ background: palette(window); border: 0; border-bottom: 1px solid {SEPARATOR}; border-right: 1px solid {SEPARATOR}; padding: 5px 4px; }}
QSplitter#mainSplitter::handle, QSplitter#contentSplitter::handle {{ background: {SEPARATOR}; }}
QSplitter#mainSplitter::handle:horizontal, QSplitter#contentSplitter::handle:horizontal {{ width: 1px; }}
QSplitter#mainSplitter::handle:vertical, QSplitter#contentSplitter::handle:vertical {{ height: 1px; }}
QToolButton#sortButton {{ font-weight: bold; color: palette(text); }}
QToolButton#sortButton::menu-indicator {{ image: none; width: 0; }}
QToolButton#filterButton {{ padding-right: 22px; }}
QToolButton#filterButton::menu-indicator {{ subcontrol-origin: padding; subcontrol-position: right center; right: 7px; }}
QToolButton#filterButton:checked {{ color: palette(highlight); font-weight: bold; background: transparent; border: 0; }}
QFrame#readerHeader {{ background: palette(window); border: 0; border-bottom: 1px solid palette(mid); }}
QFrame#remoteBanner {{ background: palette(window); border: 0; border-bottom: 1px solid palette(mid); }}
QFrame#remoteBanner QPushButton {{ background: {HOVER}; border: 0; border-radius: 4px; padding: 5px 12px; }}
QFrame#remoteBanner QPushButton:hover {{ background: rgba(128, 128, 128, 0.4); }}
QMainWindow#composeWindow QLineEdit, QMainWindow#composeWindow QComboBox {{ border: 1px solid palette(mid); border-radius: 4px; padding: 5px 8px; background: palette(base); }}
QMainWindow#composeWindow QLineEdit:focus {{ border: 1px solid palette(highlight); }}
QMainWindow#composeWindow QFormLayout QLabel {{ color: palette(placeholder-text); }}
QMainWindow#composeWindow QTextEdit, QMainWindow#composeWindow QPlainTextEdit {{ border: 0; }}
QPushButton#sendButton {{ background: palette(highlight); color: palette(highlighted-text); border: 0; border-radius: 4px; padding: 7px 20px; font-weight: 600; }}
QPushButton#sendButton:hover {{ background: palette(link); }}
QPushButton#sendButton:disabled {{ background: {HOVER}; color: palette(placeholder-text); }}
QPushButton#flatButton {{ background: transparent; border: 0; border-radius: 4px; padding: 7px 12px; }}
QPushButton#flatButton:hover, QPushButton#flatButton:checked {{ background: {HOVER}; }}
QToolBar#richToolbar {{ background: palette(window); border: 0; border-top: 1px solid palette(mid); border-bottom: 1px solid palette(mid); padding: 2px 8px; spacing: 2px; }}
QToolBar#richToolbar QToolButton {{ background: transparent; border: 0; border-radius: 4px; padding: 5px 10px; }}
QToolBar#richToolbar QToolButton:hover, QToolBar#richToolbar QToolButton:checked {{ background: {HOVER}; }}
QWidget#sectionBar {{ background: palette(window); border-top: 1px solid palette(mid); }}
QToolButton#sectionButton {{ border: 0; border-bottom: 3px solid transparent; border-radius: 0; background: transparent; }}
QToolButton#sectionButton:hover {{ background: {HOVER}; }}
QToolButton#sectionButton:checked {{ border-bottom: 3px solid palette(highlight); background: transparent; }}
""", factor)
