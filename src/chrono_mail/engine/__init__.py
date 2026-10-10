"""Session object and command registry. Every user action is a command here."""

from chrono_mail.engine.catalog import build_registry
from chrono_mail.engine.registry import Command, CommandRegistry, JsonValue, ParameterSpec, UsageError, to_data
from chrono_mail.engine.session import OUTBOX, STANDARD_FOLDERS, Engine

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Export the engine, the registry and the command catalog
# v0.0.2: Initial skeleton

__all__ = [
    "OUTBOX",
    "STANDARD_FOLDERS",
    "Command",
    "CommandRegistry",
    "Engine",
    "JsonValue",
    "ParameterSpec",
    "UsageError",
    "build_registry",
    "to_data",
]
