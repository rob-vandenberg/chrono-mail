"""Moving mail between server and store."""

from chrono_mail.sync.davsync import DavReport, DavState, sync_dav
from chrono_mail.sync.imap_state import ImapEntry, ImapState
from chrono_mail.sync.imap_sync import FolderPlan, FolderReport, ImapReport, ImapSyncOptions, plan_folders, sync_imap
from chrono_mail.sync.pop3_fetch import FailedMessage, FetchReport, fetch_pop3
from chrono_mail.sync.pop3_state import DownloadRecord, Pop3State
from chrono_mail.sync.rebuild import RebuildReport, ReconcileReport, rebuild_index, reconcile_index
from chrono_mail.sync.send import SendOutcome, send_and_file

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 0.0.66"

# --- Version history ----------------------------------------------------
# v0.0.66: Export the calendar and contacts synchronisation
# v0.0.29: Export the IMAP sync and its state
# v0.0.10: Export the POP3 fetch and its state
# v0.0.8: Export the index rebuild and reconcile
# v0.0.2: Initial skeleton

__all__ = [
    "DavReport",
    "DavState",
    "sync_dav",
    "FolderPlan",
    "FolderReport",
    "ImapEntry",
    "ImapReport",
    "ImapState",
    "ImapSyncOptions",
    "plan_folders",
    "sync_imap",
    "DownloadRecord",
    "FailedMessage",
    "FetchReport",
    "Pop3State",
    "RebuildReport",
    "ReconcileReport",
    "SendOutcome",
    "fetch_pop3",
    "rebuild_index",
    "reconcile_index",
    "send_and_file",
]
