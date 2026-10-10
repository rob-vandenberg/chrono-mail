"""The rebuildable search and list index."""

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
__version__ = "__init__.py 0.0.23"

# --- Version history ----------------------------------------------------
# v0.0.23: Export KnownAddress
# v0.0.19: Export the query parser
# v0.0.8: Export the index
# v0.0.2: Initial skeleton

__all__ = [
    "MAXIMUM_BODY_CHARACTERS",
    "UNREADABLE_SUBJECT",
    "Index",
    "IndexBuilder",
    "IndexEntry",
    "IndexedMessage",
    "KnownAddress",
    "ParsedQuery",
    "SearchFilter",
    "SortField",
    "entry_from_raw",
    "html_to_text",
    "match_expression",
    "parse_query",
    "searchable_body",
    "unreadable_entry",
]
