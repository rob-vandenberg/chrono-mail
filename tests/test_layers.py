import subprocess
import sys
from pathlib import Path

# --- Version ------------------------------------------------------------
__version__ = "test_layers.py 0.0.2"

# --- Version history ----------------------------------------------------
# v0.0.2: Initial skeleton

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


def test_layer_contracts_hold() -> None:
    result = subprocess.run(
        [str(Path(sys.executable).parent / "lint-imports"), "--config", "pyproject.toml"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
