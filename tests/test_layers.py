import subprocess
import sys
from pathlib import Path

# --- Version ------------------------------------------------------------
__version__ = "test_layers.py 3.6.355"

# --- Version history ----------------------------------------------------
# v3.6.355: The owner rule each check of the project enforces is named (OR-42)
# v0.0.2: Initial skeleton

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


def test_layer_contracts_hold() -> None:
    # owner rule OR-5: the layers, only ui imports PySide6
    result = subprocess.run(
        [str(Path(sys.executable).parent / "lint-imports"), "--config", "pyproject.toml"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
