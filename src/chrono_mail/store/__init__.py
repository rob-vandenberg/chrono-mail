"""The encrypted Maildir message store."""

from chrono_mail.store.layout import SEPARATOR
from chrono_mail.store.store import RENAME_NOTE, SENT_LETTER, RenameRefused, Checkpoint, MessageNotFoundError, Placed, RecoveryReport, Store, StoredMessage
from chrono_mail.store.properties import MessageProperties, Properties, clean_tags
from chrono_mail.store.receivedby import ReceivedBy

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 4.2.418"

# --- Version history ----------------------------------------------------
# v4.2.418: Export ReceivedBy (D-292)
# v4.2.403: Export RENAME_NOTE and RenameRefused (D-285)
# v3.6.350: Export SENT_LETTER
# v2.5.336: Export Placed
# v1.1.206: Export MessageProperties and Properties instead of MessageTags
# v0.5.134: Export MessageTags and clean_tags
# v0.3.110: Export MessageNotFoundError
# v0.0.7: Export the store
# v0.0.2: Initial skeleton

__all__ = ["RENAME_NOTE", "SENT_LETTER", "RenameRefused", "SEPARATOR", "Checkpoint", "MessageNotFoundError", "MessageProperties", "Placed", "Properties", "ReceivedBy", "RecoveryReport", "Store", "StoredMessage", "clean_tags"]
