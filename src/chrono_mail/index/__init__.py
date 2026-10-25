"""The rebuildable search and list index."""

from chrono_mail.index.database import MINIMUM_SQLITE_VERSION, IndexDatabase, facts_lost, facts_told, told_at_once
from chrono_mail.index.entry import entry_from_raw, unreadable_entry
from chrono_mail.index.index import Index, IndexBuilder, ParsedQuery, match_expression, parse_query
from chrono_mail.index.model import (
    UNREADABLE_SUBJECT,
    IndexedMessage,
    IndexEntry,
    KnownAddress,
    SearchFilter,
    SortField,
)
from chrono_mail.index.text import MAXIMUM_BODY_CHARACTERS, html_to_text, searchable_body

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: Export told_at_once, facts_told and facts_lost (index writes told at once, RV-206)
# v1.8.263: Export IndexDatabase and MINIMUM_SQLITE_VERSION (R-16)
# v0.0.23: Export KnownAddress
# v0.0.19: Export the query parser
# v0.0.8: Export the index
# v0.0.2: Initial skeleton

__all__ = [
    "MAXIMUM_BODY_CHARACTERS",
    "MINIMUM_SQLITE_VERSION",
    "UNREADABLE_SUBJECT",
    "Index",
    "IndexDatabase",
    "IndexBuilder",
    "IndexEntry",
    "IndexedMessage",
    "KnownAddress",
    "ParsedQuery",
    "SearchFilter",
    "SortField",
    "entry_from_raw",
    "facts_lost",
    "facts_told",
    "html_to_text",
    "match_expression",
    "parse_query",
    "searchable_body",
    "told_at_once",
    "unreadable_entry",
]
