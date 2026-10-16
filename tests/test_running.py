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
from chrono_mail.config.running import running_programs, windows_processes
from chrono_mail.engine import Engine

# --- Version ------------------------------------------------------------
__version__ = "test_running.py 0.5.144"

# --- Version history ----------------------------------------------------
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
