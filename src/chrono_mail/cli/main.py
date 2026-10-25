import argparse
import getpass
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TextIO

from chrono_mail.config import AppDirectories, KeyringPasswordStore, default_directories
from chrono_mail.config.running import program_is_running
from chrono_mail.core import (
    AuthenticationError,
    ChronoMailError,
    ConfigurationError,
    CryptoError,
    MessageFormatError,
    ProtocolError,
    StorageError,
)
from chrono_mail.crypto import KeyringKeyStore
from chrono_mail.engine import Command, CommandRegistry, Engine, ParameterSpec, UsageError, build_registry, to_data
from chrono_mail.engine.changes import RebuildStep
from chrono_mail.cli.render import json_safe, printable, render_text

# --- Version ------------------------------------------------------------
__version__ = "main.py 3.6.351"

# --- Version history ----------------------------------------------------
# v3.6.351: the --json output never carries a raw C1 control character from mail (RV-129)
# v3.6.349: the progress and error lines print text from servers safely, as the results do (RV-203)
# v2.0.281: a rebuild of an index shows how far it is (how many of how many)
# v0.5.144: A command is refused while the Chrono Mail program runs (program_running; the tests give their own)
# v0.0.12: Initial command line interface

# Exit codes (stable; automation may rely on them).
EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_AUTHENTICATION = 0, 1, 2, 3
EXIT_NETWORK, EXIT_STORAGE, EXIT_CONFIGURATION, EXIT_MESSAGE = 4, 5, 6, 7
EXIT_INTERRUPTED = 130


def exit_code(error: ChronoMailError) -> int:
    """The exit code for an error, most specific kind first."""
    for kind, code in (
        (UsageError, EXIT_USAGE),
        (AuthenticationError, EXIT_AUTHENTICATION),
        (ProtocolError, EXIT_NETWORK),
        (StorageError, EXIT_STORAGE),
        (CryptoError, EXIT_STORAGE),
        (ConfigurationError, EXIT_CONFIGURATION),
        (MessageFormatError, EXIT_MESSAGE),
    ):
        if isinstance(error, kind):
            return code
    return EXIT_ERROR


def _option(name: str) -> str:
    return "--" + name.replace("_", "-")


def _add_parameter(parser: argparse.ArgumentParser, spec: ParameterSpec) -> None:
    flag, text = _option(spec.name), spec.help
    if spec.secret:
        parser.add_argument(f"{flag}-stdin", dest=f"{spec.name}__stdin", action="store_true", help=f"{text} (read from standard input)")
    elif spec.kind == "flag":
        if spec.default is False and spec.required is False and not spec.optional:
            parser.add_argument(flag, dest=spec.name, action="store_true", default=None, help=text)
        else:
            parser.add_argument(flag, dest=spec.name, action=argparse.BooleanOptionalAction, default=None, help=text)
    elif spec.kind == "list":
        parser.add_argument(flag, dest=spec.name, action="append", default=None, help=text)
    elif spec.kind == "integer":
        parser.add_argument(flag, dest=spec.name, type=int, default=None, help=text)
    else:
        parser.add_argument(flag, dest=spec.name, default=None, choices=list(spec.choices) or None, help=text)


def build_parser(registry: CommandRegistry) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chrono-mail-cli", description="Chrono Mail on the command line. Every command is also available to the graphical program.")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--yes", action="store_true", help="confirm a command that deletes something")
    parser.add_argument("--config-dir", type=Path, help="use this settings directory")
    parser.add_argument("--data-dir", type=Path, help="use this data directory")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    for command in registry.commands():
        sub = commands.add_parser(command.id, help=command.description, description=command.description)
        for spec in command.parameters:
            _add_parameter(sub, spec)
    return parser


def _collect(command: Command, arguments: argparse.Namespace, stdin: TextIO, interactive: bool) -> dict[str, object]:
    values: dict[str, object] = {}
    for spec in command.parameters:
        if spec.secret:
            if getattr(arguments, f"{spec.name}__stdin"):
                values[spec.name] = stdin.readline().rstrip("\r\n")
            elif spec.required and interactive:
                values[spec.name] = getpass.getpass(f"{spec.name.replace('_', ' ').capitalize()}: ")
            elif spec.required:
                raise UsageError(f"{_option(spec.name)}-stdin is needed: secrets are never taken from the command line")
            continue
        value = getattr(arguments, spec.name)
        if value is not None:
            values[spec.name] = value
    return values


def main(
    argv: Sequence[str] | None = None,
    engine_factory: Callable[[AppDirectories], Engine] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    program_running: Callable[[], bool] = program_is_running,
) -> int:
    """Run one command. Returns the exit code. Nothing runs while the Chrono Mail program runs (it would change the same mail at once)."""
    out, err, source = stdout or sys.stdout, stderr or sys.stderr, stdin or sys.stdin
    registry = build_registry()
    parser = build_parser(registry)
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as stop:
        return EXIT_OK if stop.code in (0, None) else EXIT_USAGE
    wants_json = bool(arguments.json)
    try:
        if program_running():
            raise UsageError("Chrono Mail is running. Close it first, then use the command line tool.")
        command = registry.get(arguments.command)
        if command.destructive and not arguments.yes:
            raise UsageError(f"{command.id} deletes something: repeat it with --yes to confirm")
        directories = default_directories()
        directories = replace(
            directories,
            config_directory=arguments.config_dir or directories.config_directory,
            data_directory=arguments.data_dir or directories.data_directory,
        )
        engine = engine_factory(directories) if engine_factory is not None else Engine(directories, KeyringPasswordStore(), KeyringKeyStore())
        if err.isatty():
            def show_progress(what: str, done: int, total: int | None) -> None:
                err.write(f"\r{printable(what)}: {done}{'/' + str(total) if total else ''}   ")  # a folder name of a server (RV-203)

            def show_rebuilding(step: RebuildStep) -> None:
                if not step.ended:
                    show_progress("Rebuilding the index", step.done, step.total)

            engine.progress = show_progress
            engine.rebuilding = show_rebuilding
        values = _collect(command, arguments, source, source.isatty())
        result = command.run(engine, values)
    except KeyboardInterrupt:
        err.write("\nInterrupted\n")
        return EXIT_INTERRUPTED
    except OSError as error:  # for example a file that cannot be written: reported like any storage problem
        message = printable(f"{error.strerror or type(error).__name__}" + (f": {error.filename}" if error.filename else ""))
        if wants_json:
            out.write(json.dumps({"error": message, "exit_code": EXIT_STORAGE}) + "\n")
        else:
            err.write(f"Error: {message}\n")
        return EXIT_STORAGE
    except ChronoMailError as error:
        code = exit_code(error)
        if wants_json:
            out.write(json_safe(json.dumps({"error": str(error), "exit_code": code})) + "\n")
        else:
            err.write(f"Error: {printable(str(error))}\n")  # may hold a server's reply (RV-203)
        return code
    if wants_json:
        out.write(json_safe(json.dumps(to_data(result), indent=2, ensure_ascii=False)) + "\n")  # never a raw C1 control character (RV-129)
    else:
        out.write(render_text(result) + "\n")
    return EXIT_OK
