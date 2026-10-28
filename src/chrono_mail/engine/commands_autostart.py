from dataclasses import dataclass, field

from chrono_mail.config.autostart import Autostart, read_autostart, supported, write_autostart
from chrono_mail.core import ConfigurationError
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "commands_autostart.py 4.2.402"

# --- Version history ----------------------------------------------------
# v4.2.402: Initial: autostart.show and autostart.set, start Chrono Mail when the person logs in (D-284)


@dataclass(frozen=True, slots=True)
class AutostartInfo:
    on: bool  # Chrono Mail starts when the person logs in
    minimized: bool  # with the window minimized
    available: bool  # this copy can set it: Linux or Windows, and the person's own program (never a profile, D-243)


@dataclass(frozen=True, slots=True)
class AutostartParameters:
    on: bool = field(metadata=option("Start Chrono Mail when you log in (false: do not)"))
    minimized: bool = field(default=False, metadata=option("Start with the window minimized"))


def _available(engine: Engine) -> bool:
    return supported() and not engine.directories.profile_name  # a profile's start would replace the person's own entry


def autostart_show(engine: Engine, parameters: NoParameters) -> AutostartInfo:
    """Whether Chrono Mail starts at login, as the system will do it."""
    if not _available(engine):
        return AutostartInfo(False, False, False)
    try:
        now = read_autostart()
    except ConfigurationError as error:
        raise UsageError(str(error)) from None
    return AutostartInfo(now.on, now.minimized, True)


def autostart_set(engine: Engine, parameters: AutostartParameters) -> AutostartInfo:
    """Start Chrono Mail at login with the window open or minimized, or not at all."""
    if not _available(engine):
        raise UsageError("Starting at login is not available here")
    try:
        write_autostart(Autostart(parameters.on, parameters.minimized and parameters.on))
        now = read_autostart()
    except ConfigurationError as error:
        raise UsageError(str(error)) from None
    return AutostartInfo(now.on, now.minimized, True)


AUTOSTART_SHOW: TypedCommand[NoParameters, AutostartInfo] = TypedCommand("autostart.show", NoParameters, AutostartInfo)
AUTOSTART_SET: TypedCommand[AutostartParameters, AutostartInfo] = TypedCommand("autostart.set", AutostartParameters, AutostartInfo)


def register(registry: CommandRegistry) -> None:
    registry.register(AUTOSTART_SHOW.id, "Show whether Chrono Mail starts when you log in", NoParameters, autostart_show, doing="Reading the start at login")
    registry.register(AUTOSTART_SET.id, "Start Chrono Mail when you log in, with the window open or minimized, or not", AutostartParameters, autostart_set, doing="Changing the start at login")
