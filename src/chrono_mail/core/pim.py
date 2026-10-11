from dataclasses import dataclass, field
from enum import Enum

# --- Version ------------------------------------------------------------
__version__ = "pim.py 0.0.59"

# --- Version history ----------------------------------------------------
# v0.0.59: Initial collection kinds and descriptions for calendars, tasks and contacts


class CollectionKind(Enum):
    """What a collection holds."""

    EVENTS = "events"  # a calendar
    TASKS = "tasks"  # a task list
    CONTACTS = "contacts"  # an address book


@dataclass(frozen=True, slots=True)
class CollectionInfo:
    """A calendar, a task list or an address book of an account."""

    collection_id: str  # 32 lower-case hex characters, made here; never a name or a server address
    kind: CollectionKind
    name: str
    colour: str = ""  # #rrggbb, or empty
    read_only: bool = False
    server: tuple[tuple[str, str], ...] = field(default=())  # what a server needs to find it again (an address, a sync token ...), as pairs of text

    def server_value(self, key: str, default: str = "") -> str:
        for name, value in self.server:
            if name == key:
                return value
        return default
