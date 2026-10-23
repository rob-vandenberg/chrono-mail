from pathlib import Path

from chrono_mail.config import default_directories
from chrono_mail.ui.app import WINDOW_SETTINGS_FILE, directories_and_settings, take_profile

# --- Version ------------------------------------------------------------
__version__ = "test_ui_profile.py 2.3.320"

# --- Version history ----------------------------------------------------
# v2.3.320: Initial: --profile keeps everything in its own folder, the person's own never touched (D-243)


def test_the_profile_option_is_taken_out_of_the_arguments(tmp_path: Path) -> None:
    arguments = ["chrono-mail", "--profile", str(tmp_path), "-style", "fusion"]
    assert take_profile(arguments) == tmp_path
    assert arguments == ["chrono-mail", "-style", "fusion"]  # Qt never sees it
    joined = ["chrono-mail", f"--profile={tmp_path}"]
    assert take_profile(joined) == tmp_path
    assert joined == ["chrono-mail"]


def test_without_the_option_nothing_changes() -> None:
    arguments = ["chrono-mail", "-style", "fusion"]
    assert take_profile(arguments) is None
    assert arguments == ["chrono-mail", "-style", "fusion"]
    assert take_profile(["chrono-mail", "--profile"]) is None  # no folder after it: not a profile


def test_a_profile_keeps_mail_settings_and_window_settings_in_its_folder(tmp_path: Path) -> None:
    directories, settings = directories_and_settings(tmp_path)
    assert directories.config_directory.is_relative_to(tmp_path)
    assert directories.data_directory.is_relative_to(tmp_path)
    assert Path(settings.fileName()) == directories.config_directory / WINDOW_SETTINGS_FILE
    own = default_directories()
    assert directories.config_directory != own.config_directory and directories.data_directory != own.data_directory


def test_without_a_profile_the_persons_own_folders_are_used() -> None:
    directories, settings = directories_and_settings(None)
    assert directories == default_directories()
    assert settings.applicationName() == "chrono-mail" and settings.organizationName() == "chrono-mail"
