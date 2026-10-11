# --- Version ------------------------------------------------------------
__version__ = "theme.py 0.0.73"

# --- Version history ----------------------------------------------------
# v0.0.73: Initial look of the main window: the Menu button, the toolbar, the search field, the folder list and the section buttons

ACCENT: str = "#ff8c00"  # the colour of the main button
ACCENT_HOVER: str = "#ffa733"
HOVER: str = "rgba(128, 128, 128, 0.22)"  # a veil that suits a light and a dark window alike


def stylesheet() -> str:
    """The look of the main window, for the light and the dark scheme alike: it takes its colours from the palette, except the accent.
    The rules name their widgets (object names), so nothing else in the program changes."""
    return f"""
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
QToolBar#mainToolbar QToolButton#newButton {{ background: {ACCENT}; color: white; font-weight: 600; padding: 6px 16px; margin-right: 6px; }}
QToolBar#mainToolbar QToolButton#newButton:hover {{ background: {ACCENT_HOVER}; }}
QFrame#searchPill {{ border: 1px solid palette(mid); border-radius: 16px; background: palette(base); }}
QFrame#searchPill QLineEdit {{ border: 0; background: transparent; padding: 5px 2px; }}
QFrame#searchPill QComboBox {{ border: 0; background: transparent; padding: 2px 6px; }}
QFrame#searchPill QLabel {{ border: 0; background: transparent; }}
QLabel#sideHeading {{ font-size: 20px; font-weight: 600; padding: 12px 14px 6px 14px; }}
QTreeView#folderTree {{ border: 0; background: palette(window); outline: 0; padding: 2px 6px; }}
QTreeView#folderTree::item {{ padding: 4px 4px; border-radius: 4px; min-height: 22px; }}
QTreeView#folderTree::item:hover:!selected {{ background: {HOVER}; }}
QTreeView#folderTree::item:selected {{ background: palette(highlight); color: palette(highlighted-text); }}
QWidget#sectionBar {{ background: palette(window); border-top: 1px solid palette(mid); }}
QToolButton#sectionButton {{ border: 0; border-bottom: 3px solid transparent; border-radius: 0; background: transparent; }}
QToolButton#sectionButton:hover {{ background: {HOVER}; }}
QToolButton#sectionButton:checked {{ border-bottom: 3px solid palette(highlight); background: transparent; }}
"""
