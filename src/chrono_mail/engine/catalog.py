from chrono_mail.engine import commands_accounts, commands_actions, commands_contacts, commands_drafts, commands_export, commands_folders, commands_mail
from chrono_mail.engine.registry import CommandRegistry

# --- Version ------------------------------------------------------------
__version__ = "catalog.py 0.0.23"

# --- Version history ----------------------------------------------------
# v0.0.23: address suggestions
# v0.0.22: export commands
# v0.0.17: bulk action commands
# v0.0.16: draft commands
# v0.0.12: Initial command catalog


def build_registry() -> CommandRegistry:
    """Every command of the application. The UI, the CLI and automation all use this registry."""
    registry = CommandRegistry()
    commands_accounts.register(registry)
    commands_folders.register(registry)
    commands_mail.register(registry)
    commands_drafts.register(registry)
    commands_actions.register(registry)
    commands_export.register(registry)
    commands_contacts.register(registry)
    return registry
