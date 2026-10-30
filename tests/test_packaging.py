import configparser
from pathlib import Path

import pytest

from chrono_mail.ui import app
from chrono_mail.ui.app import DESKTOP_FILE_NAME, PROJECT_DESKTOP_FILE_NAME, desktop_file_name
from chrono_mail.ui.icons import RESOURCES

# --- Version ------------------------------------------------------------
__version__ = "test_packaging.py 4.2.434"

# --- Version history ----------------------------------------------------
# v4.2.434: the installed menu entry starts /usr/bin/chrono-mail by its exact place; the built program and a copy from the project folder
#           tell the desktop different entries
# v0.0.14: Initial launcher file test

DESKTOP_FILE: Path = Path(__file__).resolve().parent.parent / "packaging" / "chrono-mail.desktop"


def test_the_launcher_file_matches_what_the_program_announces() -> None:
    # (ConfigParser lower-cases the keys.) Qt registers the program with the desktop under DESKTOP_FILE_NAME; without a matching
    # launcher file the portal reports "App info not found" (seen in a terminal on KDE).
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(DESKTOP_FILE, encoding="utf-8")
    entry = parser["Desktop Entry"]
    assert DESKTOP_FILE.stem == DESKTOP_FILE_NAME
    assert entry["type"] == "Application" and entry["name"] == "Chrono Mail"
    assert entry["terminal"] == "false" and "Email" in entry["categories"] and entry["categories"].endswith(";")
    assert entry["startupwmclass"] == DESKTOP_FILE_NAME
    assert (RESOURCES / f"{entry['Icon']}.svg").is_file()  # the icon name is the file name of the shipped icon


def test_the_start_command_exists_in_the_project_description() -> None:
    text = (DESKTOP_FILE.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert 'chrono-mail = "chrono_mail.ui:main"' in text


def test_the_installed_menu_entry_starts_the_installed_program_by_its_exact_place() -> None:
    """A start command without a folder is looked up on the person's search path, where another chrono-mail (a copy from the project
    folder) may come first: the installed entry then started that copy."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(DESKTOP_FILE, encoding="utf-8")
    entry = parser["Desktop Entry"]
    assert entry["exec"] == "/usr/bin/chrono-mail" and entry["tryexec"] == "/usr/bin/chrono-mail"


def test_the_built_program_and_a_project_copy_belong_to_different_menu_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both said chrono-mail: a window of the project copy, started from its own pinned taskbar button, got a second button of the
    installed entry."""
    assert desktop_file_name() == PROJECT_DESKTOP_FILE_NAME  # the tests run from the project folder
    monkeypatch.setitem(vars(app), "__compiled__", object())  # the mark Nuitka gives the built program
    assert desktop_file_name() == DESKTOP_FILE_NAME
