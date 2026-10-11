"""The encrypted store and the rebuildable index for calendars, tasks and contacts (ARCHITECTURE.md section 9.4)."""

from chrono_mail.pimstore.extract import Extract, extract
from chrono_mail.pimstore.index import FLOATING_MARGIN, IndexedItem, ItemQuery, PimIndex
from chrono_mail.pimstore.store import PimStore, new_id

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 0.0.59"

# --- Version history ----------------------------------------------------
# v0.0.59: Collections, encrypted items, index
# v0.0.57: Initial empty package, so that the layer check knows it

__all__ = ["FLOATING_MARGIN", "Extract", "IndexedItem", "ItemQuery", "PimIndex", "PimStore", "extract", "new_id"]
