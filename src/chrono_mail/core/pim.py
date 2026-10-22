from dataclasses import dataclass, field
from enum import Enum

from chrono_mail.core.colours import COLLECTION_WITHOUT_COLOUR, SUGGESTED_EVENT_COLOURS

# --- Version ------------------------------------------------------------
__version__ = "pim.py 1.1.221"

# --- Version history ----------------------------------------------------
# v1.1.221: The palette belongs to the account (palette_of takes the account's colours, palette_problem); CollectionLook: a server collection's name and
#           colour as Chrono Mail holds them, kept with the account (D-191, D-192)
# v1.1.219: A calendar's own palette of named colours (palette, own_colour_name, palette_of, D-191)
# v0.0.59: Initial collection kinds and descriptions for calendars, tasks and contacts


class CollectionKind(Enum):
    """What a collection holds."""

    EVENTS = "events"  # a calendar
    TASKS = "tasks"  # a task list
    CONTACTS = "contacts"  # an address book


OWN_COLOUR_ID: str = "calendar"  # the first colour of every palette: the calendar's own colour; it cannot be deleted
OWN_COLOUR_NAME: str = "Calendar colour"
MAXIMUM_PALETTE_COLOURS: int = 100
MAXIMUM_COLOUR_NAME: int = 60


@dataclass(frozen=True, slots=True)
class PaletteColour:
    """One colour of an account's palette of appointment colours: an id that never changes (what an appointment remembers), a name and the colour (#rrggbb)."""

    colour_id: str
    name: str
    colour: str


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


def palette_of(own_colour: str, palette: tuple[PaletteColour, ...] | None, own_name: str = "") -> tuple[PaletteColour, ...]:
    """A calendar's palette as it is shown: its own colour first, then its account's colours (the suggestions while the person never changed them)."""
    own = PaletteColour(OWN_COLOUR_ID, own_name or OWN_COLOUR_NAME, own_colour or COLLECTION_WITHOUT_COLOUR)
    rest = palette if palette is not None else tuple(PaletteColour(*entry) for entry in SUGGESTED_EVENT_COLOURS)
    return (own, *rest)


def is_colour(text: str) -> bool:
    """Whether a text is a colour as Chrono Mail keeps one: #rrggbb."""
    return len(text) == 7 and text[0] == "#" and all(character in "0123456789abcdef" for character in text[1:].lower())


def palette_problem(palette: tuple[PaletteColour, ...], own_name: str = "") -> str:
    """What is wrong with a palette as it would be kept (at most so many colours, each with an id of its own, never the calendar's own, a name and
    a #rrggbb); empty when nothing is."""
    if len(palette) > MAXIMUM_PALETTE_COLOURS:
        return f"A palette has at most {MAXIMUM_PALETTE_COLOURS} colours"
    ids = [entry.colour_id for entry in palette]
    if len(set(ids)) != len(ids) or OWN_COLOUR_ID in ids or any(not entry_id or len(entry_id) > 32 or not entry_id.isalnum() for entry_id in ids):
        return "Every colour of a palette needs an id of its own"
    for entry in palette:
        if not entry.name.strip() or len(entry.name) > MAXIMUM_COLOUR_NAME:
            return f"A colour needs a name of up to {MAXIMUM_COLOUR_NAME} characters"
        if not is_colour(entry.colour):
            return "A colour is written #rrggbb"
    if len(own_name) > MAXIMUM_COLOUR_NAME:
        return f"A colour's name has at most {MAXIMUM_COLOUR_NAME} characters"
    return ""


# How Chrono Mail holds the name or the colour of a collection of a server, compared with the server (D-192):
LOOK_FOLLOWS: str = ""  # as the server has it (nothing kept)
LOOK_WAITING: str = "waiting"  # changed here, not on the server yet: sent at the next synchronisation
LOOK_KEPT: str = "kept"  # Chrono Mail's own, kept here: the server refused it or keeps none (a colour of an address book), or the two differed


@dataclass(frozen=True, slots=True)
class CollectionLook:
    """The name and colour of one collection of a server as Chrono Mail holds them, kept with the account under the collection's place on the
    server (`key`: what it holds and where), so that they survive the copy here being taken away and made again (D-192). A value is in the
    server's form (a name without " (public)"); `base_*` is what the server had when it was changed here, so that a change on the server
    meanwhile is seen."""

    key: str
    name: str = ""
    name_state: str = LOOK_FOLLOWS
    base_name: str = ""
    colour: str = ""
    colour_state: str = LOOK_FOLLOWS
    base_colour: str = ""

    @property
    def empty(self) -> bool:
        return self.name_state == LOOK_FOLLOWS and self.colour_state == LOOK_FOLLOWS
