from chrono_mail.engine import commands_accounts, commands_actions, commands_attachments, commands_addressbook, commands_calendar, commands_combined, commands_conflicts, commands_contacts, commands_dav, commands_drafts, commands_emclient, commands_export, commands_folders, commands_groups, commands_imap, commands_local, commands_mail, commands_many, commands_outbox, commands_pim, commands_rules, commands_schedule, commands_sidebar, commands_tags, commands_tasklist, commands_view, commands_watch, junkfilter
from chrono_mail.engine.registry import CommandRegistry

# --- Version ------------------------------------------------------------
__version__ = "catalog.py 2.1.308"

# --- Version history ----------------------------------------------------
# v2.1.308: The junk filter's first learning of the mail kept (junk.learn-existing, D-238)
# v2.0.302: Opening attachments in another program (message.attachment.open, message.attachment.clean-opened, D-236)
# v1.10.277: Contact groups (contact-group.*, R-39)
# v1.6.260: The order of a view and rows asked by the message (message.order, message.combined-order, message.rows, R-15)
# v1.1.247: Actions on many messages of a folder at once (message.*-many, R-08)
# v1.1.225: Sync conflicts and what waits for the calendar servers (conflict.list, conflict.settle, sync.waiting, D-194)
# v0.5.134: Tags for mail (message.tag, tag.create, tag.delete)
# v0.5.132: The importer of the reference client's exports
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
    commands_attachments.register(registry)
    commands_drafts.register(registry)
    commands_actions.register(registry)
    commands_export.register(registry)
    commands_contacts.register(registry)
    commands_watch.register(registry)
    commands_rules.register(registry)
    commands_imap.register(registry)
    commands_many.register(registry)
    junkfilter.register(registry)
    commands_combined.register(registry)
    commands_view.register(registry)
    commands_local.register(registry)
    commands_sidebar.register(registry)
    commands_dav.register(registry)
    commands_pim.register(registry)
    commands_addressbook.register(registry)
    commands_groups.register(registry)
    commands_calendar.register(registry)
    commands_tasklist.register(registry)
    commands_schedule.register(registry)
    commands_outbox.register(registry)
    commands_emclient.register(registry)
    commands_tags.register(registry)
    commands_conflicts.register(registry)
    return registry
