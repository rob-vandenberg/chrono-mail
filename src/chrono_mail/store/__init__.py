"""The encrypted Maildir message store."""

from chrono_mail.store.layout import SEPARATOR
from chrono_mail.store.store import Checkpoint, RecoveryReport, Store, StoredMessage

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 0.0.7"

# --- Version history ----------------------------------------------------
# v0.0.7: Export the store
# v0.0.2: Initial skeleton

__all__ = ["SEPARATOR", "Checkpoint", "RecoveryReport", "Store", "StoredMessage"]
