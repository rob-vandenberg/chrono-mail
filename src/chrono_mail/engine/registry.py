import dataclasses
import types
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from chrono_mail.core import ChronoMailError, ConfigurationError
from chrono_mail.core.words import digits

if TYPE_CHECKING:
    from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "registry.py 3.6.351"

# --- Version history ----------------------------------------------------
# v3.6.351: numbers are read only from the digits 0 to 9 (core.words.digits): "²" passed isdigit() and stopped the work with a ValueError
# v2.1.311: every command says what it is doing (doing), in plain words: the busy bar names what runs
# v1.1.205: TypedCommand: a command with the types of its parameters and its answer, so every caller is checked by the type checker (R-02, R-19)
# v0.0.12: Initial command registry

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


def option(text: str, secret: bool = False) -> dict[str, object]:
    """Field metadata for a command parameter: its help text, and whether it is a secret (never taken from the command line)."""
    return {"help": text, "secret": secret}


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    name: str
    kind: Literal["text", "integer", "flag", "list"]
    required: bool
    default: JsonValue
    choices: tuple[str, ...]
    secret: bool
    help: str
    optional: bool  # the value may be left out and then is None


@dataclass(frozen=True, slots=True)
class Command:
    """A user-visible action. The UI, the CLI and any automation call commands by their id."""

    id: str
    description: str
    parameters: tuple[ParameterSpec, ...]
    destructive: bool
    doing: str  # what the program is doing while the command runs, in plain words (the busy bar's hover text)
    _parameter_type: type
    _handler: Callable[["Engine", object], object]

    def run(self, engine: "Engine", values: Mapping[str, object]) -> object:
        return self._handler(engine, build_parameters(self._parameter_type, self.parameters, values))


class UsageError(ChronoMailError):
    """A command was called with missing or invalid parameters."""


@dataclass(frozen=True, slots=True)
class TypedCommand[P, R]:
    """A command with the types of its parameters (a dataclass) and of its answer, for callers inside the program: they give
    a parameter object and get the answer as its own type, so the type checker checks every use (R-19). The command line
    keeps calling by id with text values."""

    id: str
    parameter_type: type[P]
    answer_type: type[R]

    def values(self, parameters: P) -> dict[str, object]:
        """The parameter object as the values a command is run with."""
        return {parameter_field.name: getattr(parameters, parameter_field.name) for parameter_field in dataclasses.fields(cast(type, type(parameters)))}

    def answer(self, result: object) -> R:
        """The answer as its own type; anything else is a fault of the program, shown as an error."""
        if not isinstance(result, self.answer_type):
            raise ChronoMailError(f"{self.id} answered {type(result).__name__}, not {self.answer_type.__name__}")
        return result


def _strip_optional(annotation: object) -> tuple[object, bool]:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        arguments = [argument for argument in typing.get_args(annotation) if argument is not type(None)]
        if len(arguments) == 1 and len(typing.get_args(annotation)) == 2:
            return arguments[0], True
    return annotation, False


def _spec(parameter_field: dataclasses.Field[object], annotation: object) -> ParameterSpec:
    inner, optional = _strip_optional(annotation)
    choices: tuple[str, ...] = ()
    kind: Literal["text", "integer", "flag", "list"]
    if typing.get_origin(inner) is Literal:
        choices = tuple(str(value) for value in typing.get_args(inner))
        kind = "text"
    elif inner is str:
        kind = "text"
    elif inner is bool:
        kind = "flag"
    elif inner is int:
        kind = "integer"
    elif typing.get_origin(inner) is tuple and typing.get_args(inner) == (str, Ellipsis):
        kind = "list"
    else:
        raise TypeError(f"Unsupported command parameter type for {parameter_field.name}: {annotation!r}")
    has_default = parameter_field.default is not dataclasses.MISSING or parameter_field.default_factory is not dataclasses.MISSING
    default: JsonValue = None
    raw_default = parameter_field.default
    if isinstance(raw_default, tuple):
        default = [str(item) for item in raw_default]
    elif raw_default is None or isinstance(raw_default, (bool, int, float, str)):
        default = raw_default
    metadata = parameter_field.metadata
    return ParameterSpec(
        parameter_field.name,
        kind,
        not has_default,
        default,
        choices,
        bool(metadata.get("secret", False)),
        str(metadata.get("help", "")),
        optional,
    )


def build_parameters(parameter_type: type, specs: tuple[ParameterSpec, ...], values: Mapping[str, object]) -> object:
    """Check the raw values against the specification and build the parameter object."""
    known = {spec.name for spec in specs}
    unknown = sorted(set(values) - known)
    if unknown:
        raise UsageError(f"Unknown parameter: {', '.join(unknown)}")
    arguments: dict[str, object] = {}
    for spec in specs:
        if spec.name not in values or values[spec.name] is None:
            if spec.required and not spec.optional:
                raise UsageError(f"Parameter '{spec.name}' is required")
            continue
        arguments[spec.name] = _coerce(spec, values[spec.name])
    return parameter_type(**arguments)


def _coerce(spec: ParameterSpec, value: object) -> object:
    name = spec.name
    if spec.kind == "flag":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false", "yes", "no", "1", "0"):
            return value.lower() in ("true", "yes", "1")
        raise UsageError(f"Parameter '{name}' must be true or false")
    if spec.kind == "integer":
        if isinstance(value, bool):
            raise UsageError(f"Parameter '{name}' must be a whole number")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and digits(value.strip().removeprefix("-")) and len(value.strip()) <= 18:  # one minus at most ("--5" is no
            return int(value)  # number); never more digits than a number of this program needs (int() refuses over 4,300)
        raise UsageError(f"Parameter '{name}' must be a whole number")
    if spec.kind == "list":
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
            return tuple(str(item) for item in value)
        raise UsageError(f"Parameter '{name}' must be a list of text values")
    if not isinstance(value, str):
        raise UsageError(f"Parameter '{name}' must be text")
    if spec.choices and value not in spec.choices:
        raise UsageError(f"Parameter '{name}' must be one of: {', '.join(spec.choices)}")
    return value


class CommandRegistry:
    """All commands, by id. The one place that knows what the application can do."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register[P, R](
        self,
        command_id: str,
        description: str,
        parameter_type: type[P],
        handler: Callable[["Engine", P], R],
        destructive: bool = False,
        *,
        doing: str,
    ) -> None:
        if command_id in self._commands:
            raise ValueError(f"Command {command_id} is registered twice")
        if not doing.strip():
            raise ValueError(f"Command {command_id} does not say what it is doing")
        hints = typing.get_type_hints(parameter_type)
        specs = tuple(_spec(parameter_field, hints[parameter_field.name]) for parameter_field in dataclasses.fields(cast(type, parameter_type)))
        self._commands[command_id] = Command(
            command_id,
            description,
            specs,
            destructive,
            doing,
            parameter_type,
            lambda engine, parameters: handler(engine, cast(P, parameters)),
        )

    def get(self, command_id: str) -> Command:
        try:
            return self._commands[command_id]
        except KeyError:
            raise UsageError(f"Unknown command: {command_id}") from None

    def ids(self) -> list[str]:
        return sorted(self._commands)

    def commands(self) -> list[Command]:
        return [self._commands[command_id] for command_id in self.ids()]

    def run(self, engine: "Engine", command_id: str, values: Mapping[str, object] | None = None) -> object:
        return self.get(command_id).run(engine, values or {})

    def ask[P, R](self, engine: "Engine", command: TypedCommand[P, R], parameters: P) -> R:
        """Run a typed command in the calling thread."""
        return command.answer(self.run(engine, command.id, command.values(parameters)))


def to_data(value: object) -> JsonValue:
    """A command result as plain data (for JSON output): dataclasses become dictionaries."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        raise ConfigurationError("Raw bytes cannot be part of a command result")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {parameter_field.name: to_data(getattr(value, parameter_field.name)) for parameter_field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): to_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value, key=str) if isinstance(value, (set, frozenset)) else value
        return [to_data(item) for item in items]
    raise TypeError(f"Cannot turn {type(value).__name__} into data")
