from pathlib import Path

from chrono_mail.config.autostart import (
    ENTRY_FILE_NAME,
    MINIMIZED_OPTION,
    Autostart,
    entry_text,
    read_entry,
    read_run_list,
    run_command,
    write_entry,
    write_run_list,
)

# --- Version ------------------------------------------------------------
__version__ = "test_config_autostart.py 4.2.402"

# --- Version history ----------------------------------------------------
# v4.2.402: Initial: the start at login in the autostart folder (Linux) and the Run list (Windows), D-284


class FakeRunList:
    """The user's Run list of Windows, and the mark Startup apps sets, in memory."""

    def __init__(self, command: str | None = None, switched_off: bool = False) -> None:
        self.command: str | None = command
        self.off: bool = switched_off

    def read(self) -> str | None:
        return self.command

    def switched_off(self) -> bool:
        return self.off

    def write(self, command: str) -> None:
        self.command = command
        self.off = False

    def remove(self) -> None:
        self.command = None
        self.off = False


def test_no_entry_is_off(tmp_path: Path) -> None:
    assert read_entry(tmp_path / "autostart") == Autostart(False, False)


def test_the_entry_starts_the_program_with_the_window_open_or_minimized(tmp_path: Path) -> None:
    folder = tmp_path / "autostart"
    write_entry(folder, Autostart(True, False), ["/usr/bin/chrono-mail"])
    assert read_entry(folder) == Autostart(True, False)
    text = (folder / ENTRY_FILE_NAME).read_text(encoding="utf-8")
    assert "Exec=/usr/bin/chrono-mail\n" in text and "TryExec=/usr/bin/chrono-mail\n" in text
    write_entry(folder, Autostart(True, True), ["/usr/bin/chrono-mail"])
    assert read_entry(folder) == Autostart(True, True)
    assert f"Exec=/usr/bin/chrono-mail {MINIMIZED_OPTION}\n" in (folder / ENTRY_FILE_NAME).read_text(encoding="utf-8")
    assert [path.name for path in folder.iterdir()] == [ENTRY_FILE_NAME]  # no temporary file left behind


def test_off_removes_the_entry(tmp_path: Path) -> None:
    folder = tmp_path / "autostart"
    write_entry(folder, Autostart(True, True), ["chrono-mail"])
    write_entry(folder, Autostart(False, False), ["chrono-mail"])
    assert not (folder / ENTRY_FILE_NAME).exists() and read_entry(folder) == Autostart(False, False)
    write_entry(folder, Autostart(False, False), ["chrono-mail"])  # off twice: nothing to remove, no error


def test_an_entry_the_desktop_switched_off_reads_as_off(tmp_path: Path) -> None:
    folder = tmp_path / "autostart"
    for switched_off in ("Hidden=true", "X-GNOME-Autostart-enabled=false"):
        write_entry(folder, Autostart(True, False), ["chrono-mail"])
        path = folder / ENTRY_FILE_NAME
        path.write_text(path.read_text(encoding="utf-8").replace("X-GNOME-Autostart-enabled=true", switched_off), encoding="utf-8")
        assert read_entry(folder).on is False


def test_a_program_path_with_spaces_and_special_characters_is_quoted() -> None:
    text = entry_text(Autostart(True, True), ["/home/a b/.venv/bin/chrono-mail", "100%"])
    assert f'Exec="/home/a b/.venv/bin/chrono-mail" 100%% {MINIMIZED_OPTION}\n' in text
    assert '"$x"' not in entry_text(Autostart(True, False), ["/a/$x"]) and 'Exec="/a/\\\\$x"' in entry_text(Autostart(True, False), ["/a/$x"])


def test_the_windows_run_list_entry() -> None:
    run_list = FakeRunList()
    assert read_run_list(run_list) == Autostart(False, False)
    write_run_list(run_list, Autostart(True, True), [r"C:\Program Files\Chrono Mail\chrono-mail.exe"])
    assert run_list.command == rf'"C:\Program Files\Chrono Mail\chrono-mail.exe" {MINIMIZED_OPTION}'
    assert read_run_list(run_list) == Autostart(True, True)
    write_run_list(run_list, Autostart(True, False), [r"C:\Program Files\Chrono Mail\chrono-mail.exe"])
    assert read_run_list(run_list) == Autostart(True, False)
    write_run_list(run_list, Autostart(False, False), [r"C:\x.exe"])
    assert run_list.command is None and read_run_list(run_list) == Autostart(False, False)


def test_an_entry_switched_off_in_startup_apps_reads_as_off_and_on_again_when_ticked() -> None:
    run_list = FakeRunList(run_command(Autostart(True, False), [r"C:\x.exe"]), switched_off=True)
    assert read_run_list(run_list).on is False
    write_run_list(run_list, Autostart(True, False), [r"C:\x.exe"])
    assert read_run_list(run_list) == Autostart(True, False)
