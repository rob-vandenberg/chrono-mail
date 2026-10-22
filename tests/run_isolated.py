"""Run every test in a process of its own, so no test can see what another test did (R-45).

A test whose result depends on the tests that ran before it is a gamble, not a test. Here each test starts from a copy of
one prepared process that has loaded the program but run nothing (no application object, no window, no thread), so every
test starts exactly as if it were the only test of the run. A test that takes longer than the limit is killed and counted
as failed: no run can hang.

    .venv/bin/python -m tests.run_isolated                   # every test except live, oskeyring and perf, in the usual order
    .venv/bin/python -m tests.run_isolated --order reverse
    .venv/bin/python -m tests.run_isolated --order shuffle --seed 7
    .venv/bin/python -m tests.run_isolated tests/ui/test_ui_push.py tests/test_cli.py

Linux only (it copies the prepared process with fork). The result of each failed test is in the folder it names.
"""

import argparse
import os
import random
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# --- Version ------------------------------------------------------------
__version__ = "run_isolated.py 1.9.266"

# --- Version history ----------------------------------------------------
# v1.9.266: Initial runner: every test in a process of its own, a time limit per test, any order (R-45)

ROOT = Path(__file__).resolve().parent.parent
LIMIT_SECONDS: float = 20.0  # no test may take longer (the owner, 2026-10-21)
WORKERS: int = os.cpu_count() or 4


@dataclass
class Running:
    node: str
    started: float
    output: Path


def collect(paths: list[str]) -> list[str]:
    """The ids of every test the normal run would run (the markers of pyproject.toml apply)."""
    found = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *paths], cwd=ROOT, capture_output=True, text=True,
    )
    nodes = [line for line in found.stdout.splitlines() if "::" in line and not line.startswith(" ")]
    if found.returncode not in (0, 5) or not nodes:
        sys.exit(f"Collecting the tests failed:\n{found.stdout[-3000:]}\n{found.stderr[-3000:]}")
    return nodes


def prepare() -> None:
    """Load what every test loads, once, before any copy is made. Nothing may run yet: no application object, no thread."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import pytest  # noqa: F401, PLC0415
    import PySide6.QtWidgets  # noqa: F401, PLC0415
    import chrono_mail.engine  # noqa: F401, PLC0415
    import chrono_mail.ui.mainwindow  # noqa: F401, PLC0415


def start(node: str, output: Path) -> int:
    """A copy of the prepared process runs this one test; its output goes to its own file."""
    pid = os.fork()
    if pid:
        return pid
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.dup2(descriptor, 1)
        os.dup2(descriptor, 2)
        os.chdir(ROOT)
        os.setsid()  # its own process group, so a kill also takes what it started
        import pytest  # noqa: PLC0415

        code = pytest.main(["-q", "-p", "no:cacheprovider", "-p", "no:randomly", "-rfE", node])
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(int(code))
    except BaseException:  # noqa: BLE001 - whatever happens in the copy, it must end and say so
        os._exit(99)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", default=["tests"])
    parser.add_argument("--order", choices=("normal", "reverse", "shuffle"), default="normal")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--workers", type=int, default=WORKERS)
    arguments = parser.parse_args()
    nodes = collect(arguments.paths)
    if arguments.order == "reverse":
        nodes.reverse()
    elif arguments.order == "shuffle":
        random.Random(arguments.seed).shuffle(nodes)
    prepare()
    outputs = Path(tempfile.mkdtemp(prefix="chrono-mail-isolated-"))
    waiting = list(reversed(nodes))
    running: dict[int, Running] = {}
    failed: list[tuple[str, str, Path]] = []
    began = time.monotonic()
    done = 0
    while waiting or running:
        while waiting and len(running) < arguments.workers:
            node = waiting.pop()
            output = outputs / f"{len(nodes) - len(waiting):05d}.txt"
            running[start(node, output)] = Running(node, time.monotonic(), output)
        pid, status = os.waitpid(-1, os.WNOHANG)
        if pid == 0:
            now = time.monotonic()
            for late, test in list(running.items()):
                if now - test.started > LIMIT_SECONDS:
                    os.killpg(late, signal.SIGKILL)
                    os.waitpid(late, 0)
                    del running[late]
                    failed.append((test.node, f"took longer than {LIMIT_SECONDS:.0f} s and was stopped", test.output))
                    done += 1
            time.sleep(0.01)
            continue
        test = running.pop(pid, None)
        if test is None:
            continue
        done += 1
        code = os.waitstatus_to_exitcode(status)
        if code != 0:
            failed.append((test.node, "failed" if code > 0 else f"crashed (signal {-code})", test.output))
        if done % 100 == 0:
            print(f"{done} of {len(nodes)} done, {len(failed)} failed", flush=True)
    seconds = time.monotonic() - began
    print(f"\n{len(nodes)} tests, each in its own process, order {arguments.order}" + (f" (seed {arguments.seed})" if arguments.order == "shuffle" else ""))
    print(f"{len(nodes) - len(failed)} passed, {len(failed)} failed, in {seconds:.0f} s; outputs in {outputs}")
    for node, why, output in failed:
        print(f"FAILED {node}: {why} ({output.name})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
