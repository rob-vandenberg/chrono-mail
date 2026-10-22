import importlib

import pytest

# --- Version ------------------------------------------------------------
__version__ = "test_smoke.py 0.0.2"

# --- Version history ----------------------------------------------------
# v0.0.2: Initial skeleton

MODULES: list[str] = [
    "chrono_mail",
    "chrono_mail.core",
    "chrono_mail.crypto",
    "chrono_mail.mime",
    "chrono_mail.store",
    "chrono_mail.index",
    "chrono_mail.config",
    "chrono_mail.protocols",
    "chrono_mail.sync",
    "chrono_mail.engine",
    "chrono_mail.cli",
    "chrono_mail.ui",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name: str) -> None:
    importlib.import_module(module_name)
