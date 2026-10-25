"""Moving mail between server and store."""

from chrono_mail.sync.davsync import DavReport, DavState, sync_dav
from chrono_mail.sync.imap_state import ImapEntry, ImapState
from chrono_mail.sync.imap_sync import FolderPlan, FolderReport, ImapReport, ImapSyncOptions, plan_folders, planned_anyway, sync_imap
from chrono_mail.sync.pop3_fetch import FailedMessage, FetchReport, fetch_pop3
from chrono_mail.sync.pop3_state import DownloadRecord, Pop3State
from chrono_mail.sync.rebuild import RebuildReport, ReconcileReport, rebuild_index, reconcile_index
from chrono_mail.sync.send import SendOutcome, file_sent_again, send_and_file, send_stored

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 3.1.341"

# --- Version history ----------------------------------------------------
# v3.1.341: Export file_sent_again (RV-50)
# v1.9.276: fill_send_times is gone (every older index is rebuilt, D-226)
# v1.1.241: planned_anyway: the server folders synchronised whether or not they are on the server's list of folders to show (R-34)
# v1.1.196: Export fill_send_times
# v0.0.86: Export send_stored
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
    "planned_anyway",
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
    "file_sent_again",
    "send_and_file",
    "send_stored",
]
