import subprocess
import sys
from pathlib import Path

# --- Version ------------------------------------------------------------
__version__ = "test_type_strictness.py 2.0.287"

# --- Version history ----------------------------------------------------
# v2.0.287: Pyrefly runs with this run's Python, so Windows finds its packages (the Windows computer's report)
# v0.0.2: Initial skeleton

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
BAD_FILE: Path = PROJECT_ROOT / "tests" / "data" / "bad_types.py"


def run_pyrefly(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyrefly", "check", "--python-interpreter-path", sys.executable, *arguments],  # the packages of this run (.venv-win on Windows)
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def test_project_has_zero_type_errors() -> None:
    result = run_pyrefly()
    assert result.returncode == 0, result.stdout + result.stderr


def test_strict_mode_reports_errors_on_bad_file() -> None:
    result = run_pyrefly("--config", "pyproject.toml", str(BAD_FILE))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "bad-assignment" in result.stdout + result.stderr
    assert "unannotated" in result.stdout + result.stderr
