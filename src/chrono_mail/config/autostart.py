import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import platformdirs

from chrono_mail.core import ConfigurationError

# --- Version ------------------------------------------------------------
__version__ = "autostart.py 4.2.402"

# --- Version history ----------------------------------------------------
# v4.2.402: Initial: start Chrono Mail when the person logs in, with the window open or minimized, kept in the system's own
#           list of programs that start at login (Linux: the autostart folder; Windows: the Run list of the user), D-284


MINIMIZED_OPTION: str = "--minimized"  # the start option of a start at login with the window minimized
ENTRY_FILE_NAME: str = "chrono-mail.desktop"  # Linux: the entry in the person's autostart folder
RUN_VALUE_NAME: str = "Chrono Mail"  # Windows: the name of the entry in the user's Run list
RUN_KEY: str = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY: str = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"  # what Startup apps switched off
_EXEC_SPECIAL: str = " \t\n\"'\\><~|&;$*?#()`"  # characters that make an argument of an Exec line need quotes


@dataclass(frozen=True, slots=True)
class Autostart:
    """Whether Chrono Mail starts when the person logs in, and whether its window then starts minimized."""

    on: bool = False
    minimized: bool = False


class RunList(Protocol):
    """Windows: the entry of Chrono Mail in the user's Run list, and whether Startup apps switched it off."""

    def read(self) -> str | None:
        """The command line of the entry; None when there is none."""
        ...

    def switched_off(self) -> bool:
        """Whether the person switched the entry off in the system's Startup apps."""
        ...

    def write(self, command: str) -> None:
        """Write the entry, switched on."""
        ...

    def remove(self) -> None:
        """Remove the entry (nothing when there is none)."""
        ...


def supported() -> bool:
    """Whether this system has a start at login Chrono Mail can set (Linux and Windows)."""
    return sys.platform == "win32" or sys.platform.startswith("linux")


def program_command() -> list[str]:
    """How this copy of the program is started: the packaged program itself, the launcher next to the Python that runs it
    (a run from source), or that Python with the program's module. The same from the window and from the command line tool."""
    if "__compiled__" in globals():  # the packaged program (Nuitka): sys.executable is the program itself
        return [sys.executable]
    launcher = Path(sys.executable).parent / ("chrono-mail.exe" if sys.platform == "win32" else "chrono-mail")
    if launcher.is_file():
        return [str(launcher)]
    return [sys.executable, "-m", "chrono_mail.ui"]


def entry_folder() -> Path:
    """Linux: the person's autostart folder (XDG: $XDG_CONFIG_HOME/autostart, else ~/.config/autostart)."""
    return platformdirs.user_config_path() / "autostart"


def read_autostart() -> Autostart:
    """The start at login as the system will do it."""
    if sys.platform == "win32":
        return read_run_list(WindowsRunList())
    if sys.platform.startswith("linux"):
        return read_entry(entry_folder())
    return Autostart()


def write_autostart(wanted: Autostart) -> None:
    """Make the system start Chrono Mail at login as wanted, or not at all."""
    if sys.platform == "win32":
        write_run_list(WindowsRunList(), wanted, program_command())
    elif sys.platform.startswith("linux"):
        write_entry(entry_folder(), wanted, program_command())
    else:
        raise ConfigurationError("Starting at login is not available on this system")


# --- Linux ------------------------------------------------------------------------------------------------------------


def _exec_argument(argument: str) -> str:
    """One argument of an Exec line (Desktop Entry Specification): quoted when it needs it, % doubled."""
    if argument and not any(character in _EXEC_SPECIAL for character in argument):
        return argument.replace("%", "%%")
    escaped = argument.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return '"' + escaped.replace("%", "%%") + '"'


def _string_value(text: str) -> str:
    """A value of a desktop file: a backslash written twice (the file's own escape, read before the Exec quoting)."""
    return text.replace("\\", "\\\\")


def entry_text(wanted: Autostart, command: list[str]) -> str:
    """The autostart entry that starts this command, with the window minimized when wanted."""
    arguments = command + ([MINIMIZED_OPTION] if wanted.minimized else [])
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Chrono Mail\n"
        "Comment=Read and send e-mail\n"
        f"Exec={_string_value(' '.join(_exec_argument(argument) for argument in arguments))}\n"
        f"TryExec={_string_value(command[0])}\n"  # a program removed since: the desktop skips the entry without a word
        "Icon=chrono-mail\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def read_entry(folder: Path) -> Autostart:
    """Linux: the entry in the autostart folder. Missing, hidden or switched off by the desktop's settings: off."""
    try:
        text = (folder / ENTRY_FILE_NAME).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return Autostart()
    except OSError as error:
        raise ConfigurationError(f"The start at login could not be read: {error.strerror}") from None
    values: dict[str, str] = {}
    in_entry = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_entry = line == "[Desktop Entry]"
        elif in_entry and "=" in line:
            key, _, value = line.partition("=")
            values.setdefault(key.strip(), value.strip())
    command = values.get("Exec", "")
    switched_off = values.get("Hidden", "").lower() == "true" or values.get("X-GNOME-Autostart-enabled", "").lower() == "false"
    return Autostart(on=bool(command) and not switched_off, minimized=MINIMIZED_OPTION in command.split())


def write_entry(folder: Path, wanted: Autostart, command: list[str]) -> None:
    """Linux: write the entry in one atomic step, or remove it."""
    path = folder / ENTRY_FILE_NAME
    if not wanted.on:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise ConfigurationError(f"The start at login could not be switched off: {error.strerror}") from None
        return
    temporary = folder / f".{ENTRY_FILE_NAME}.{os.urandom(6).hex()}.tmp"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            os.write(descriptor, entry_text(wanted, command).encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass  # nothing was written there
        raise ConfigurationError(f"The start at login could not be switched on: {error.strerror}") from None


# --- Windows ----------------------------------------------------------------------------------------------------------


def run_command(wanted: Autostart, command: list[str]) -> str:
    """Windows: the command line of the Run entry."""
    return subprocess.list2cmdline(command + ([MINIMIZED_OPTION] if wanted.minimized else []))


def read_run_list(run_list: RunList) -> Autostart:
    """Windows: the entry in the Run list; missing or switched off in Startup apps: off."""
    try:
        command = run_list.read()
        switched_off = run_list.switched_off()
    except OSError as error:
        raise ConfigurationError(f"The start at login could not be read: {error.strerror}") from None
    if command is None:
        return Autostart()
    return Autostart(on=not switched_off, minimized=MINIMIZED_OPTION in command.split())


def write_run_list(run_list: RunList, wanted: Autostart, command: list[str]) -> None:
    """Windows: write the entry switched on, or remove it."""
    try:
        if wanted.on:
            run_list.write(run_command(wanted, command))
        else:
            run_list.remove()
    except OSError as error:
        raise ConfigurationError(f"The start at login could not be changed: {error.strerror}") from None


class WindowsRunList:
    """The user's own Run list in the registry (no administrator needed), and the mark Startup apps sets beside it."""

    def read(self) -> str | None:
        if sys.platform == "win32":  # inside this block the type check for Linux knows to skip it
            import winreg

            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                    value, _ = winreg.QueryValueEx(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                return None
            return value if isinstance(value, str) else None
        return None

    def switched_off(self) -> bool:
        if sys.platform == "win32":
            import winreg

            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY) as key:
                    value, _ = winreg.QueryValueEx(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                return False  # never switched off
            return isinstance(value, bytes) and len(value) > 0 and value[0] % 2 == 1  # 2 or 6: on, 3: off
        return False

    def write(self, command: str) -> None:
        if sys.platform == "win32":
            import winreg

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, command)
            self._forget_switched_off()

    def remove(self) -> None:
        if sys.platform == "win32":
            import winreg

            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass  # there was none
            self._forget_switched_off()

    def _forget_switched_off(self) -> None:
        """Startup apps' mark goes with a change made here, so the tick is what the system does."""
        if sys.platform == "win32":
            import winreg

            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass  # never switched off
