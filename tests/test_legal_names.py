import re
import subprocess
from pathlib import Path

# --- Version ------------------------------------------------------------
__version__ = "test_legal_names.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: The import from a data file may name the program it imports from, in its own files only (OR-10); everywhere else the check
#           refuses that name as it refuses the first one; also the forms (.ui), the installer and script files (.iss, .sh, .ps1), .yaml and
#           .xml are checked
# v3.6.355: The owner rule each check of the project enforces is named (OR-42)
# v1.9.266: R-45: only the files that go to GitHub (private files never leave this computer), the name as a word of its own
#           (SlowItemClient is no hit), and the name may be anywhere in the importer: its files, modules, commands and tests
# v0.5.132: The importer may name the program it imports from (the owner, 2026-10-15); elsewhere only its module names
# v0.0.25: Initial check that no other product's name is used

ROOT = Path(__file__).resolve().parent.parent
NAME = "em" + "client"  # written in two pieces so that this file does not contain the name
# The name, also written "<em> <client>" or "<em>-<client>", starting a word: inside another word ("SlowItemClient") it is not the name.
FORBIDDEN = re.compile(r"(?<![a-z0-9])em[ -]?client")
# The one exception (the owner, 2026-10-15 and 2026-10-21): the importer may name the program it imports from, anywhere in it.
IMPORTER_FILES: frozenset[str] = frozenset({
    "src/chrono_mail/engine/" + NAME + "_source.py",
    "src/chrono_mail/engine/commands_" + NAME + ".py",
    "src/chrono_mail/ui/importdialog.py",
    "tests/test_" + NAME + "_source.py",
    "tests/test_import_" + NAME + ".py",
    "tests/ui/test_ui_import.py",
})
# The importer's own names, which other files and the documents may mention: its modules, its test files and its commands
# (import.<name>, import.<name>-scan, import.<name>-passwords).
IMPORTER_NAMES = re.compile(r"commands_" + NAME + r"|" + NAME + r"_source|test_import_" + NAME + r"|import\." + NAME + r"[a-z-]*")
# The second program an import reads from (written in two pieces for the same reason), allowed only in its import's own files, and its
# import's own names (its module, its test files, its commands) anywhere.
SECOND = "out" + "look"
SECOND_FORBIDDEN = re.compile(r"(?<![a-z0-9])" + SECOND)
SECOND_FILES: frozenset[str] = frozenset({
    "src/chrono_mail/engine/commands_" + SECOND + ".py",
    "src/chrono_mail/ui/importdialog.py",
    "tests/test_import_" + SECOND + ".py",
    "tests/ui/test_ui_import_" + SECOND + ".py",
})
SECOND_PARTS: tuple[str, ...] = ("src/chrono_mail/pst/", "tests/test_pst_")  # the reader of its data files is part of that import
SECOND_NAMES = re.compile(r"commands_" + SECOND + r"|test_(?:ui_)?import_" + SECOND + r"|import\." + SECOND + r"[a-z-]*")
CHECKED_SUFFIXES: frozenset[str] = frozenset({".md", ".toml", ".py", ".desktop", ".svg", ".txt", ".ui", ".iss", ".sh", ".ps1", ".yaml", ".xml"})


def published_files() -> list[Path]:
    """Every file that goes to GitHub: the files git keeps, and new ones it is not told to ignore (private files never leave)."""
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / line for line in listed.stdout.splitlines() if line]


def hits(text: str) -> list[str]:
    """The places where the name stands, apart from the importer's own names."""
    return FORBIDDEN.findall(IMPORTER_NAMES.sub("", text.lower()))


def second_hits(text: str) -> list[str]:
    """The places where the second program's name stands, apart from its import's own names."""
    return SECOND_FORBIDDEN.findall(SECOND_NAMES.sub("", text.lower()))


def test_the_check_finds_the_name_and_only_the_name() -> None:
    assert hits("Import from " + "eM " + "Client") != [] and hits(NAME + ".com") != [] and hits("em-" + "client") != []
    assert hits("class SlowItemClient") == [] and hits("import." + NAME + "-passwords") == [] and hits("tests/test_import_" + NAME + ".py") == []
    assert second_hits("Import from " + SECOND.capitalize()) != [] and second_hits("import." + SECOND + "-scan, commands_" + SECOND) == []
    assert second_hits("tests/ui/test_ui_import_" + SECOND + ".py") == [] and second_hits("a " + SECOND + "s") != []


def test_no_other_mail_programs_name_is_in_the_project() -> None:
    # owner rule OR-10: no other mail program named, except in the importer
    """For legal reasons the repository and the program never refer to a competing product by name (D-046)."""
    found: list[str] = []
    for path in published_files():
        relative = path.relative_to(ROOT).as_posix()
        if not path.is_file() or path.suffix not in CHECKED_SUFFIXES:
            continue
        text = path.read_text("utf-8", errors="replace")
        if relative not in IMPORTER_FILES:
            found.extend(f"{relative}: {name}" for name in hits(text))
        if relative not in SECOND_FILES and not relative.startswith(SECOND_PARTS):
            found.extend(f"{relative}: {name}" for name in second_hits(text))
    assert found == []
