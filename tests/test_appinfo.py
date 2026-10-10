import tomllib
from importlib import metadata
from pathlib import Path

import pytest

from chrono_mail.core import program_version

# --- Version ------------------------------------------------------------
__version__ = "test_appinfo.py 0.0.26"

# --- Version history ----------------------------------------------------
# v0.0.26: Initial program version tests

ROOT = Path(__file__).resolve().parent.parent


def test_the_version_is_the_one_in_pyproject() -> None:
    assert program_version() == tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]


def test_without_the_source_tree_the_installed_package_is_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.core import appinfo

    def unreadable(self: Path, *args: object, **kwargs: object) -> str:
        raise OSError("gone")

    def installed(name: str) -> str:
        return "9.9.9"

    monkeypatch.setattr(Path, "read_text", unreadable)
    monkeypatch.setattr(appinfo.metadata, "version", installed)
    assert program_version() == "9.9.9"


def test_unknown_when_nothing_tells(monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.core import appinfo

    def missing(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    def unreadable(self: Path, *args: object, **kwargs: object) -> str:
        raise OSError("gone")

    monkeypatch.setattr(Path, "read_text", unreadable)
    monkeypatch.setattr(appinfo.metadata, "version", missing)
    assert program_version() == "unknown"
