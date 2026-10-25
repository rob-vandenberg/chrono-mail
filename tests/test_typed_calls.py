import ast
from pathlib import Path

# --- Version ------------------------------------------------------------
__version__ = "test_typed_calls.py 3.6.355"

# --- Version history ----------------------------------------------------
# v3.6.355: The owner rule each check of the project enforces is named; "more than 50 modules" is "some modules" (OR-42)
# v1.9.275: The window reads the engine's answers by their fields: a getattr only with a "# getattr:" reason on its line (R-19 round 5)
# v1.9.273: Initial guard (R-19): the window calls the engine only by typed commands, so Pyrefly checks every parameter and every answer

UI = Path(__file__).resolve().parents[1] / "src" / "chrono_mail" / "ui"
UNTYPED = ("call", "run_now")  # Backend's doors by a command's text name; only backend.py itself uses them (ask is built on call)


def _untyped_calls(path: Path) -> list[str]:
    """Every place in a module that calls the engine by a command's text name (x.call(...), x.run_now(...))."""
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in UNTYPED:
            found.append(f"{path.name}:{node.lineno} .{node.func.attr}(")
    return found


def test_the_window_calls_the_engine_only_by_typed_commands() -> None:
    # owner rule OR-40 (D-175): typed calls
    """A command called by its text name is checked by nobody: a renamed or misspelled field shows only when someone clicks
    (R-19). Every call goes through Backend.ask with the command's TypedCommand, its parameter dataclass and its answer type."""
    modules = sorted(path for path in UI.rglob("*.py") if path.name != "backend.py")
    assert modules  # the window's modules were found (no count of them: a number of its own, OR-42)
    found = [place for path in modules for place in _untyped_calls(path)]
    assert found == [], "untyped engine calls (use Backend.ask with a TypedCommand): " + ", ".join(found)


def test_the_guard_finds_an_untyped_call(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text('backend.call("message.show", {}, print)\nbackend.ask(MESSAGE_SHOW, parameters, print)\nself.backend.run_now("key.status", {})\n', "utf-8")
    assert _untyped_calls(sample) == ["sample.py:1 .call(", "sample.py:3 .run_now("]


def _guessed_reads(path: Path) -> list[str]:
    """Every line of a module that reads a field by its name with getattr and does not say why on that line."""
    return [f"{path.name}:{number}" for number, line in enumerate(path.read_text("utf-8").splitlines(), 1) if "getattr(" in line and "# getattr:" not in line]


def test_the_window_reads_the_engines_answers_by_their_fields() -> None:
    # owner rule OR-40 (D-175): typed calls
    """A field read with getattr(x, "name") is checked by nobody (R-19): the window keeps every answer as its own type and reads
    x.name, which Pyrefly checks. Only a read that cannot be typed (PySide's stubs give a style option's fields no type, the
    window's own actions by name, a dataclass copied field by field) stays, with "# getattr: <why>" on its line."""
    modules = sorted(UI.rglob("*.py"))
    found = [place for path in modules for place in _guessed_reads(path)]
    assert found == [], "guessed reads (keep the answer as its type and read its field, or say why): " + ", ".join(found)


def test_the_guard_finds_a_guessed_read(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text('name = getattr(row, "subject")\nstate = getattr(option, "state")  # getattr: PySide\'s stubs\n', "utf-8")
    assert _guessed_reads(sample) == ["sample.py:1"]
