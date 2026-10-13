import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Literal

import pytest

from chrono_mail.core import ConfigurationError
from chrono_mail.engine import CommandRegistry, UsageError, build_registry, to_data
from chrono_mail.engine.registry import option
from tests.enginefactory import make_engine

# --- Version ------------------------------------------------------------
__version__ = "test_engine_registry.py 0.4.119"

# --- Version history ----------------------------------------------------
# v0.4.119: mail.settle is marked destructive
# v0.0.67: Messages that are sent again and again
# v0.0.60: Commands for collections, tags, contacts, events and tasks
# v0.0.33: rule.remove is destructive
# v0.0.17: folder.empty is destructive
# v0.0.12: Initial command registry tests


@dataclass(frozen=True, slots=True)
class Sample:
    name: str = field(metadata=option("A name"))
    count: int = field(default=3, metadata=option("A number"))
    mode: Literal["fast", "slow"] = field(default="fast", metadata=option("A mode"))
    enabled: bool = field(default=True, metadata=option("A switch"))
    tags: tuple[str, ...] = field(default=(), metadata=option("Tags"))
    note: str | None = field(default=None, metadata=option("A note"))
    secret: str | None = field(default=None, metadata=option("A secret", secret=True))


def run(tmp_path: Path, values: dict[str, object]) -> Sample:
    registry = CommandRegistry()
    registry.register("sample.run", "Run the sample", Sample, lambda engine, parameters: parameters)
    result = registry.run(make_engine(tmp_path), "sample.run", values)
    assert isinstance(result, Sample)
    return result


def test_defaults_are_filled_in(tmp_path: Path) -> None:
    assert run(tmp_path, {"name": "x"}) == Sample("x")


def test_values_are_checked_and_converted(tmp_path: Path) -> None:
    result = run(tmp_path, {"name": "x", "count": "12", "mode": "slow", "enabled": "no", "tags": ["a", "b"], "note": "n"})
    assert result == Sample("x", 12, "slow", False, ("a", "b"), "n")
    assert run(tmp_path, {"name": "x", "tags": "single"}).tags == ("single",)
    assert run(tmp_path, {"name": "x", "count": -4}).count == -4
    assert run(tmp_path, {"name": "x", "enabled": "TRUE"}).enabled is True
    assert run(tmp_path, {"name": "x", "note": None}).note is None


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"name": None},
        {"name": 5},
        {"name": "x", "count": "many"},
        {"name": "x", "count": True},
        {"name": "x", "count": 1.5},
        {"name": "x", "mode": "medium"},
        {"name": "x", "mode": 3},
        {"name": "x", "enabled": "maybe"},
        {"name": "x", "enabled": 1},
        {"name": "x", "tags": [1, 2]},
        {"name": "x", "tags": 5},
        {"name": "x", "unknown": 1},
        {"name": "x", "note": 5},
    ],
)
def test_wrong_values_are_a_usage_error(tmp_path: Path, values: dict[str, object]) -> None:
    with pytest.raises(UsageError):
        run(tmp_path, values)


def test_unknown_command(tmp_path: Path) -> None:
    with pytest.raises(UsageError):
        CommandRegistry().run(make_engine(tmp_path), "no.such.command", {})


def test_a_command_cannot_be_registered_twice() -> None:
    registry = CommandRegistry()
    registry.register("a.b", "x", Sample, lambda engine, parameters: None)
    with pytest.raises(ValueError):
        registry.register("a.b", "x", Sample, lambda engine, parameters: None)


def test_unsupported_parameter_types_are_refused_at_registration() -> None:
    @dataclass(frozen=True, slots=True)
    class Bad:
        values: dict[str, int] = field(default_factory=dict)

    with pytest.raises(TypeError):
        CommandRegistry().register("bad.type", "x", Bad, lambda engine, parameters: None)


def test_specifications_describe_the_parameters() -> None:
    registry = CommandRegistry()
    registry.register("sample.run", "Run the sample", Sample, lambda engine, parameters: None, destructive=True)
    command = registry.get("sample.run")
    assert command.destructive and command.description == "Run the sample"
    by_name = {spec.name: spec for spec in command.parameters}
    assert by_name["name"].required and by_name["name"].kind == "text" and by_name["name"].help == "A name"
    assert not by_name["count"].required and by_name["count"].kind == "integer" and by_name["count"].default == 3
    assert by_name["mode"].choices == ("fast", "slow") and by_name["enabled"].kind == "flag" and by_name["enabled"].default is True
    assert by_name["tags"].kind == "list" and by_name["note"].optional and by_name["secret"].secret


def test_every_real_command_is_described_and_unique() -> None:
    registry = build_registry()
    assert len(registry.ids()) == len(set(registry.ids())) >= 20
    for command in registry.commands():
        assert command.description and "." in command.id and command.id == command.id.lower()
        names = [spec.name for spec in command.parameters]
        assert len(names) == len(set(names))
        assert all(spec.help for spec in command.parameters), command.id


def test_secrets_are_marked_on_the_real_commands() -> None:
    registry = build_registry()
    secrets = {(command.id, spec.name) for command in registry.commands() for spec in command.parameters if spec.secret}
    assert ("account.add", "password") in secrets and ("key.export-file", "passphrase") in secrets and ("key.restore-code", "code") in secrets


def test_destructive_commands_are_marked() -> None:
    registry = build_registry()
    assert {command.id for command in registry.commands() if command.destructive} == {"account.remove", "collection.delete", "contact.delete", "event.delete", "folder.delete", "folder.empty", "mail.repeat-cancel", "mail.settle", "message.delete", "rule.remove", "task.delete"}


class Colour(Enum):
    RED = "red"


@dataclass(frozen=True, slots=True)
class Inner:
    moment: datetime
    colour: Colour


@dataclass(frozen=True, slots=True)
class Outer:
    inner: Inner
    path: Path
    items: tuple[Inner, ...]
    mapping: dict[str, int]
    tags: frozenset[str]
    nothing: None = None


def test_results_become_plain_data() -> None:
    inner = Inner(datetime(2026, 1, 2, 3, 4, tzinfo=UTC), Colour.RED)
    data = to_data(Outer(inner, Path("/tmp/x"), (inner,), {"a": 1}, frozenset({"b", "a"})))
    assert data == {
        "inner": {"moment": "2026-01-02T03:04:00+00:00", "colour": "red"},
        "path": "/tmp/x",
        "items": [{"moment": "2026-01-02T03:04:00+00:00", "colour": "red"}],
        "mapping": {"a": 1},
        "tags": ["a", "b"],
        "nothing": None,
    }


def test_raw_bytes_and_unknown_objects_are_not_results() -> None:
    with pytest.raises(ConfigurationError):
        to_data(b"raw")
    with pytest.raises(TypeError):
        to_data(object())
    assert dataclasses.is_dataclass(Outer)
