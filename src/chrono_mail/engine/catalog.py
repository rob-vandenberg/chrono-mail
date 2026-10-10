from chrono_mail.engine import commands_accounts, commands_folders, commands_mail
from chrono_mail.engine.registry import CommandRegistry

# --- Version ------------------------------------------------------------
__version__ = "catalog.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial command catalog


def build_registry() -> CommandRegistry:
    """Every command of the application. The UI, the CLI and automation all use this registry."""
    registry = CommandRegistry()
    commands_accounts.register(registry)
    commands_folders.register(registry)
    commands_mail.register(registry)
    return registry
