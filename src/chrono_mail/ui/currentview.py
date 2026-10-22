from dataclasses import dataclass
from enum import StrEnum

from chrono_mail.ui.advancedsearch import AdvancedSearch

# --- Version ------------------------------------------------------------
__version__ = "currentview.py 1.5.256"

# --- Version history ----------------------------------------------------
# v1.5.256: What the message list shows, kept as one value (R-14): the entry of the folder list, the search that was run, where it
#           looks, Advanced search and the filters; the kinds of entry by name (EntryKind)


class EntryKind(StrEnum):
    """The kinds of entry in the folder list. The values are the words the folder list and Show on startup use."""

    FOLDER = "folder"
    FAVORITE = "favorite"  # an entry of Favorites: the same kind of folder in every mailbox
    PINLINK = "pinlink"  # a folder pinned to Favorites (it opens that folder)
    TAGVIEW = "tagview"  # an entry of Tags
    GROUPINBOX = "groupinbox"  # the All Inboxes of a group
    UNREAD = "unread"
    FLAGGED = "flagged"
    ACCOUNT = "account"
    GROUP = "group"
    SECTION = "section"  # a heading (Favorites, Tags)
    NONE = "none"  # nothing is shown (an account without an Inbox)


@dataclass(frozen=True, slots=True)
class CurrentView:
    """What the message list shows, in one value (R-14). The window changes it in one step and the buttons, the search field,
    the filter chip and the list only show it; nothing is read back from the widgets."""

    account: str | None = None  # None: nothing is shown; "" in Favorites and Tags (the rows say their account)
    kind: EntryKind = EntryKind.FOLDER  # what is open (a pinned folder opens as a folder)
    key_kind: EntryKind = EntryKind.FOLDER  # the kind of the entry selected in the folder list (a pin stays a pin)
    path: str = ""  # the folder, favorite or tag of the entry selected in the folder list
    folder: str | None = None  # the folder shown (a folder view only)
    folder_role: str = "other"
    combined: str | None = None  # the favorite shown (inboxes, unread, …, "tagged" for a Tags entry), or None
    combined_accounts: tuple[str, ...] = ()  # the accounts of the group whose All Inboxes is shown
    combined_tag: str = ""  # the tag of a Tags entry
    query: str = ""  # the search that was run (Enter, the history, Advanced search); text being typed is not part of it
    all_folders: bool = False  # the search looks in every folder of the account
    advanced: AdvancedSearch | None = None  # Advanced search with days or sizes
    unread: bool = False  # Filter > Unread
    flagged: bool = False  # Filter > Flagged
    attachments: bool = False  # Filter > With attachments
    tag: str | None = None  # Filter > Tagged with

    @property
    def filtered(self) -> bool:
        """At least one filter is on (the Filter button is blue)."""
        return self.unread or self.flagged or self.attachments or self.tag is not None
