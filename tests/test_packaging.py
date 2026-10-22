import configparser
from pathlib import Path

from chrono_mail.ui.app import DESKTOP_FILE_NAME
from chrono_mail.ui.icons import RESOURCES

# --- Version ------------------------------------------------------------
__version__ = "test_packaging.py 0.0.14"

# --- Version history ----------------------------------------------------
# v0.0.14: Initial launcher file test

DESKTOP_FILE: Path = Path(__file__).resolve().parent.parent / "packaging" / "chrono-mail.desktop"


def test_the_launcher_file_matches_what_the_program_announces() -> None:
    # (ConfigParser lower-cases the keys.) Qt registers the program with the desktop under DESKTOP_FILE_NAME; without a matching
    # launcher file the portal reports "App info not found" (seen in a terminal on KDE).
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(DESKTOP_FILE, encoding="utf-8")
    entry = parser["Desktop Entry"]
    assert DESKTOP_FILE.stem == DESKTOP_FILE_NAME
    assert entry["type"] == "Application" and entry["name"] == "Chrono Mail" and entry["exec"] == "chrono-mail"
    assert entry["terminal"] == "false" and "Email" in entry["categories"] and entry["categories"].endswith(";")
    assert entry["startupwmclass"] == DESKTOP_FILE_NAME
    assert (RESOURCES / f"{entry['Icon']}.svg").is_file()  # the icon name is the file name of the shipped icon


def test_the_start_command_exists_in_the_project_description() -> None:
    text = (DESKTOP_FILE.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert 'chrono-mail = "chrono_mail.ui:main"' in text
