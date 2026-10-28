import dataclasses
from pathlib import Path

import pytest

from chrono_mail.engine import commands_autostart
from chrono_mail.engine.catalog import build_registry
from chrono_mail.engine.commands_autostart import AutostartInfo
from chrono_mail.engine.registry import UsageError
from tests.enginefactory import make_engine

# --- Version ------------------------------------------------------------
__version__ = "test_engine_autostart.py 4.2.402"

# --- Version history ----------------------------------------------------
# v4.2.402: Initial: autostart.show and autostart.set, never in a profile (D-284)

REGISTRY = build_registry()


def test_the_start_at_login_is_set_and_shown(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    assert REGISTRY.run(engine, "autostart.show", {}) == AutostartInfo(False, False, True)
    assert REGISTRY.run(engine, "autostart.set", {"on": True, "minimized": True}) == AutostartInfo(True, True, True)
    assert commands_autostart.read_autostart().minimized is True  # the system's own entry, not a setting of the program
    assert REGISTRY.run(engine, "autostart.set", {"on": True, "minimized": False}) == AutostartInfo(True, False, True)
    assert REGISTRY.run(engine, "autostart.set", {"on": False, "minimized": True}) == AutostartInfo(False, False, True)
    assert REGISTRY.run(engine, "autostart.show", {}) == AutostartInfo(False, False, True)


def test_a_profile_cannot_set_the_start_at_login(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.directories = dataclasses.replace(engine.directories, profile_name="demo")
    assert REGISTRY.run(engine, "autostart.show", {}) == AutostartInfo(False, False, False)
    with pytest.raises(UsageError):
        REGISTRY.run(engine, "autostart.set", {"on": True})
    assert commands_autostart.read_autostart().on is False
