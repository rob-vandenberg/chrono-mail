"""The encrypted Maildir message store."""

from chrono_mail.store.layout import SEPARATOR
from chrono_mail.store.store import Checkpoint, MessageNotFoundError, Placed, RecoveryReport, Store, StoredMessage
from chrono_mail.store.properties import MessageProperties, Properties, clean_tags

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 2.5.336"

# --- Version history ----------------------------------------------------
# v2.5.336: Export Placed
# v1.1.206: Export MessageProperties and Properties instead of MessageTags
# v0.5.134: Export MessageTags and clean_tags
# v0.3.110: Export MessageNotFoundError
# v0.0.7: Export the store
# v0.0.2: Initial skeleton

__all__ = ["SEPARATOR", "Checkpoint", "MessageNotFoundError", "MessageProperties", "Placed", "Properties", "RecoveryReport", "Store", "StoredMessage", "clean_tags"]
