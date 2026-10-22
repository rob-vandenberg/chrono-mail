from pathlib import Path

from chrono_mail.config import AppDirectories, InMemoryPasswordStore
from chrono_mail.crypto import InMemoryKeyStore
from chrono_mail.engine import Engine

# --- Version ------------------------------------------------------------
__version__ = "enginefactory.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial engine factory for tests


def make_engine(root: Path, clock: float = 1_800_000_000.0) -> Engine:
    """An engine with in-memory key and password stores, writing below root, with a fixed clock."""
    now = [clock]
    engine = Engine(
        AppDirectories(root / "config", root / "data"),
        InMemoryPasswordStore(),
        InMemoryKeyStore(),
        clock=lambda: now[0],
        durable=False,
    )
    return engine
