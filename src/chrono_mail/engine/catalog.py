from chrono_mail.engine import commands_accounts, commands_actions, commands_addressbook, commands_calendar, commands_combined, commands_contacts, commands_dav, commands_drafts, commands_export, commands_folders, commands_imap, commands_local, commands_mail, commands_outbox, commands_pim, commands_rules, commands_schedule, commands_sidebar, commands_tasklist, commands_watch
from chrono_mail.engine.registry import CommandRegistry

# --- Version ------------------------------------------------------------
__version__ = "catalog.py 0.0.86"

# --- Version history ----------------------------------------------------
# v0.0.86: Send All (mail.send-outbox)
# v0.0.66: Commands for the calendar and contacts server
# v0.0.60: Commands for collections, tags and contacts
# v0.0.47: Pinned favorites, account groups and search in combined views
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.45: Local Folders: POP3 accounts can keep their mail in a shared mailbox
# v0.0.44: Folder choices, roles and check interval per account
# v0.0.35: Send later and snooze
# v0.0.45: Local Folders
# v0.0.44: IMAP folder choices
# v0.0.34: send later and snooze
# v0.0.33: rule commands
# v0.0.32: mail.wait
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
    commands_watch.register(registry)
    commands_rules.register(registry)
    commands_imap.register(registry)
    commands_combined.register(registry)
    commands_local.register(registry)
    commands_sidebar.register(registry)
    commands_dav.register(registry)
    commands_pim.register(registry)
    commands_addressbook.register(registry)
    commands_calendar.register(registry)
    commands_tasklist.register(registry)
    commands_schedule.register(registry)
    commands_outbox.register(registry)
    return registry
