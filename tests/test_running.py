import io
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from chrono_mail.cli import main
from chrono_mail.cli.main import EXIT_USAGE
from chrono_mail.config import AppDirectories
from chrono_mail.config.running import MAXIMUM_ANCESTORS, ancestors, running_programs, windows_matches, windows_parents, windows_processes
from chrono_mail.engine import Engine

# --- Version ------------------------------------------------------------
__version__ = "test_running.py 0.5.147"

# --- Version history ----------------------------------------------------
# v0.5.147: Windows: the processes above this one (launcher, redirector) are never another copy; made-up process lists
# v0.5.144: Initial tests: the running program is found by its process; the command line tool refuses while it runs

ON_LINUX = sys.platform.startswith("linux")
NAMED_HELPER = "from chrono_mail.config.running import set_process_name; set_process_name(); print('ready', flush=True); import time; time.sleep(60)"
PLAIN_HELPER = "print('ready', flush=True); import time; time.sleep(60)"


def _start(code: str) -> subprocess.Popen[str]:
    helper = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert helper.stdout is not None and helper.stdout.readline().strip() == "ready"
    return helper


def _stop(helper: subprocess.Popen[str]) -> None:
    helper.kill()
    helper.wait()


@pytest.mark.skipif(not ON_LINUX, reason="the Linux way of finding the program")
def test_a_process_named_chrono_mail_is_found_and_this_one_is_not() -> None:
    helper = _start(NAMED_HELPER)
    try:
        found = running_programs()
        assert helper.pid in found
        assert os.getpid() not in found
    finally:
        _stop(helper)


@pytest.mark.skipif(not ON_LINUX, reason="the Linux way of finding the program")
def test_a_program_that_ended_or_crashed_is_not_found() -> None:
    helper = _start(NAMED_HELPER)
    _stop(helper)  # killed: as a crash, nothing is left behind
    assert helper.pid not in running_programs()


@pytest.mark.skipif(not ON_LINUX, reason="the Linux way of finding the program")
def test_a_process_with_another_name_is_not_the_program() -> None:
    helper = _start(PLAIN_HELPER)
    try:
        time.sleep(0.1)
        assert helper.pid not in running_programs()
    finally:
        _stop(helper)


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows way of finding the program")
def test_the_windows_process_list_holds_this_process_with_its_user() -> None:
    processes = {number: (session, name, sid) for number, session, name, sid in windows_processes()}
    session, name, sid = processes[os.getpid()]
    assert name.lower().endswith(".exe")
    assert sid  # the user is known
    assert os.getpid() not in running_programs()


USER = b"user-sid"
SOMEONE_ELSE = b"other-sid"


def test_on_windows_the_launcher_above_the_redirector_is_not_another_copy() -> None:
    # a run from source: chrono-mail.exe (pip's launcher) -> pythonw.exe (the venv's redirector) -> pythonw.exe (the program)
    processes = [(500, 1, "explorer.exe", USER), (110, 1, "chrono-mail.exe", USER), (70, 1, "pythonw.exe", USER), (112, 1, "pythonw.exe", USER)]
    parents = {110: 500, 70: 110, 112: 70}
    assert ancestors(112, parents) == {70, 110, 500}
    assert windows_matches(processes, 112, {112} | ancestors(112, parents)) == []


def test_on_windows_a_real_other_copy_is_still_found() -> None:
    processes = [
        (500, 1, "explorer.exe", USER), (110, 1, "chrono-mail.exe", USER), (70, 1, "pythonw.exe", USER), (112, 1, "pythonw.exe", USER),
        (300, 1, "chrono-mail.exe", USER),  # started on its own from the desktop: not in this program's chain
        (301, 1, "chrono-mail.exe", SOMEONE_ELSE),  # another user's copy does not count
    ]
    parents = {110: 500, 70: 110, 112: 70, 300: 500, 301: 4}
    assert windows_matches(processes, 112, {112} | ancestors(112, parents)) == [300]


def test_a_loop_or_an_unknown_parent_ends_the_walk() -> None:
    assert ancestors(1, {1: 2, 2: 3, 3: 1}) == {2, 3}  # a loop back to the start
    assert ancestors(1, {1: 2, 2: 3, 3: 2}) == {2, 3}  # a loop higher up
    assert ancestors(1, {1: 2}) == {2}  # 2 is not in the list
    assert ancestors(1, {}) == set()
    assert ancestors(1, {1: 0}) == set()  # 0 is no process
    long_chain = {number: number + 1 for number in range(1, 1000)}
    assert len(ancestors(1, long_chain)) == MAXIMUM_ANCESTORS


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows process snapshot")
def test_the_windows_parent_list_holds_this_process_with_its_parent() -> None:
    parents = windows_parents()
    assert parents[os.getpid()] == os.getppid()
    assert os.getppid() in ancestors(os.getpid(), parents)


def test_the_command_line_tool_does_nothing_while_the_program_runs(tmp_path: Path) -> None:
    made: list[AppDirectories] = []

    def factory(directories: AppDirectories) -> Engine:
        made.append(directories)
        raise AssertionError("no engine may be made")

    out, err = io.StringIO(), io.StringIO()
    code = main(["--config-dir", str(tmp_path / "c"), "--data-dir", str(tmp_path / "d"), "account.list"], factory, io.StringIO(""), out, err, lambda: True)
    assert code == EXIT_USAGE
    assert "Chrono Mail is running. Close it first, then use the command line tool." in err.getvalue()
    assert made == []
    assert not (tmp_path / "c").exists() and not (tmp_path / "d").exists()


def test_the_command_line_tool_help_works_while_the_program_runs() -> None:
    assert main(["--help"], None, io.StringIO(""), io.StringIO(), io.StringIO(), lambda: True) == 0
