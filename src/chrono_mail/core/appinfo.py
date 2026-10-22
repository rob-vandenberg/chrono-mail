import tomllib
from importlib import metadata
from pathlib import Path

# --- Version ------------------------------------------------------------
__version__ = "appinfo.py 0.0.26"

# --- Version history ----------------------------------------------------
# v0.0.26: Initial program version lookup

PACKAGE_NAME: str = "chrono-mail"


def program_version() -> str:
    """The version of the program: from pyproject.toml when running from the source tree (the one place the version
    lives, and always current), otherwise from the installed package."""
    project_file = Path(__file__).resolve().parents[3] / "pyproject.toml"
    try:
        project = tomllib.loads(project_file.read_text("utf-8")).get("project", {})
        if project.get("name") == PACKAGE_NAME and isinstance(project.get("version"), str):
            return str(project["version"])
    except (OSError, ValueError):
        pass
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return "unknown"
