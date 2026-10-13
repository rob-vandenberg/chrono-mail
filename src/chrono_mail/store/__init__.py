"""The encrypted Maildir message store."""

from chrono_mail.store.layout import SEPARATOR
from chrono_mail.store.store import Checkpoint, MessageNotFoundError, RecoveryReport, Store, StoredMessage

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 0.3.110"

# --- Version history ----------------------------------------------------
# v0.3.110: Export MessageNotFoundError
# v0.0.7: Export the store
# v0.0.2: Initial skeleton

__all__ = ["SEPARATOR", "Checkpoint", "MessageNotFoundError", "RecoveryReport", "Store", "StoredMessage"]
