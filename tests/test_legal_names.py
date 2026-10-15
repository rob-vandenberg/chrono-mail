from pathlib import Path

# --- Version ------------------------------------------------------------
__version__ = "test_legal_names.py 0.5.132"

# --- Version history ----------------------------------------------------
# v0.5.132: The importer may name the program it imports from (the owner, 2026-10-15); elsewhere only its module names
# v0.0.25: Initial check that no other product's name is used

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = ("em" + " client", "em" + "client", "em" + "-client")  # written in two pieces so that this file does not contain the name
# The one exception (the owner, 2026-10-15): the importer is called "Import from …" with the name of the program it imports from.
IMPORTER_FILES: frozenset[str] = frozenset({
    "src/chrono_mail/engine/" + "em" + "client_source.py",
    "src/chrono_mail/engine/commands_" + "em" + "client.py",
    "src/chrono_mail/ui/importdialog.py",
    "tests/test_" + "em" + "client_source.py",
    "tests/test_import_" + "em" + "client.py",
    "tests/ui/test_ui_import.py",
})
IMPORTER_MODULES: tuple[str, ...] = ("commands_" + "em" + "client", "em" + "client_source")  # other files may import them by these module names


def test_no_other_mail_programs_name_is_in_the_project() -> None:
    """For legal reasons the repository and the program never refer to a competing product by name (D-046)."""
    found: list[str] = []
    paths = [*ROOT.glob("*.md"), ROOT / "pyproject.toml", *ROOT.joinpath("src").rglob("*.py"), *ROOT.joinpath("tests").rglob("*.py"), *ROOT.joinpath("packaging").rglob("*")]
    for path in paths:
        if path.is_file() and path.suffix in (".md", ".toml", ".py", ".desktop", ".svg", ".txt") and path.relative_to(ROOT).as_posix() not in IMPORTER_FILES:
            text = path.read_text("utf-8", errors="replace").lower()
            for module in IMPORTER_MODULES:
                text = text.replace(module, "")
            found.extend(f"{path.relative_to(ROOT)}: {name}" for name in FORBIDDEN if name in text)
    assert found == []
