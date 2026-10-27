from dataclasses import dataclass, field

from chrono_mail.core.errors import ChronoMailError
from chrono_mail.core.flags import MessageFlag
from chrono_mail.core.folder import FolderRole
from chrono_mail.core.pim import CollectionKind

# --- Version ------------------------------------------------------------
__version__ = "model.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial version: what a .pst holds, as plain data the import can use without the reading library


class PstError(ChronoMailError):
    """The file is not a data file that can be read (not a .pst, damaged, or protected); the message says which, in plain words."""


@dataclass(frozen=True, slots=True)
class PstFolder:
    """A folder of the data file, below its top of the folders the person sees."""

    path: tuple[str, ...]  # the names from the top, as the program showed them
    kind: CollectionKind | None  # None: mail (or a kind Chrono Mail has no place for, see unsupported)
    role: FolderRole | None  # a standard mail folder, known by the data file's own pointers to it (never by its name, OR-26)
    main: bool = False  # the data file's own Calendar, Contacts or Tasks folder (not one the person made)
    unsupported: str = ""  # a folder of a kind Chrono Mail has no place for (notes, journal): what it holds, for the report
    items: int = 0


@dataclass(frozen=True, slots=True)
class PstStore:
    """The data file as a whole: its name as the program shows it and its folders, in the program's order."""

    display_name: str
    folders: tuple[PstFolder, ...]


@dataclass(frozen=True, slots=True)
class PstMail:
    folder: tuple[str, ...]
    raw: bytes  # the message as RFC 5322 bytes, rebuilt from what the data file keeps
    flags: frozenset[MessageFlag]
    categories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PstEvent:
    folder: tuple[str, ...]
    ical: str  # one VCALENDAR with the event (and its changed occurrences)
    categories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PstTask:
    folder: tuple[str, ...]
    ical: str  # one VCALENDAR with the VTODO
    categories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PstContact:
    folder: tuple[str, ...]
    vcard: str  # one vCard 4.0
    categories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PstGroup:
    """A distribution list: a contact group with its members."""

    folder: tuple[str, ...]
    name: str
    members: tuple[tuple[str, str], ...] = field(default=())  # (name, address)


@dataclass(frozen=True, slots=True)
class PstFailure:
    """An item that could not be read; what says which kind and why, never its contents (AGENTS.md 4.6.5)."""

    folder: tuple[str, ...]
    what: str


type PstItem = PstMail | PstEvent | PstTask | PstContact | PstGroup | PstFailure
