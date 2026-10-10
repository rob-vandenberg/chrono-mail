from pathlib import Path

# --- Version ------------------------------------------------------------
__version__ = "test_legal_names.py 0.0.25"

# --- Version history ----------------------------------------------------
# v0.0.25: Initial check that no other product's name is used

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = ("em" + " client", "em" + "client", "em" + "-client")  # written in two pieces so that this file does not contain the name


def test_no_other_mail_programs_name_is_in_the_project() -> None:
    """For legal reasons the repository and the program never refer to a competing product by name (D-046)."""
    found: list[str] = []
    paths = [*ROOT.glob("*.md"), ROOT / "pyproject.toml", *ROOT.joinpath("src").rglob("*.py"), *ROOT.joinpath("tests").rglob("*.py"), *ROOT.joinpath("packaging").rglob("*")]
    for path in paths:
        if path.is_file() and path.suffix in (".md", ".toml", ".py", ".desktop", ".svg", ".txt"):
            text = path.read_text("utf-8", errors="replace").lower()
            found.extend(f"{path.relative_to(ROOT)}: {name}" for name in FORBIDDEN if name in text)
    assert found == []
