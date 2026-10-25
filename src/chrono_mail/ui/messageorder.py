import bisect
from collections.abc import Iterable, Iterator, Sequence

# --- Version ------------------------------------------------------------
__version__ = "messageorder.py 1.6.260"

# --- Version history ----------------------------------------------------
# v1.6.260: Initial: every message of a view in its order, with its folder: found, put in and taken out quickly also with half a million (R-15)

BLOCK: int = 512  # messages per block: a block is split at twice this, so finding a message inside one stays cheap


class _Block:
    __slots__ = ("folders", "keys")

    def __init__(self, keys: list[str], folders: list[str]) -> None:
        self.keys: list[str] = keys
        self.folders: list[str] = folders


class MessageOrder:
    """Every message of a view in the view's order, each with its folder (R-15). A message is known by its key (its id; in a
    Favorites view its mailbox and its id). Held in blocks with a map from each key to its block, so the place of a message,
    the message at a place, putting one in and taking one out cost about a block, not the whole view."""

    def __init__(self, keys: Sequence[str] = (), folders: Sequence[str] = ()) -> None:
        if len(keys) != len(folders):
            raise ValueError("Every message needs its folder")
        self._blocks: list[_Block] = [_Block(list(keys[start:start + BLOCK]), list(folders[start:start + BLOCK])) for start in range(0, len(keys), BLOCK)]
        self._owner: dict[str, _Block] = {}
        for block in self._blocks:
            self._owner.update(dict.fromkeys(block.keys, block))
        self._starts: list[int] = []  # where each block starts, worked out again only after a change (lazily)
        self._numbers: dict[int, int] = {}  # id of a block -> its number
        self._length: int = len(keys)
        self._stale: bool = True

    def __len__(self) -> int:
        return self._length

    def __contains__(self, key: object) -> bool:
        return key in self._owner

    def _index(self) -> None:
        if not self._stale:
            return
        starts: list[int] = []
        start = 0
        for block in self._blocks:
            starts.append(start)
            start += len(block.keys)
        self._starts = starts
        self._numbers = {id(block): number for number, block in enumerate(self._blocks)}
        self._stale = False

    def position(self, key: str) -> int:
        """The place of a message in the order (0 is the first), or -1 when the view does not hold it."""
        block = self._owner.get(key)
        if block is None:
            return -1
        self._index()
        return self._starts[self._numbers[id(block)]] + block.keys.index(key)

    def _locate(self, position: int) -> tuple[_Block, int]:
        self._index()
        number = bisect.bisect_right(self._starts, position) - 1
        return self._blocks[number], position - self._starts[number]

    def key_at(self, position: int) -> str | None:
        if not 0 <= position < self._length:
            return None
        block, offset = self._locate(position)
        return block.keys[offset]

    def folder_at(self, position: int) -> str:
        if not 0 <= position < self._length:
            return ""
        block, offset = self._locate(position)
        return block.folders[offset]

    def folder_of(self, key: str) -> str:
        block = self._owner.get(key)
        return "" if block is None else block.folders[block.keys.index(key)]

    def keys(self, start: int, stop: int) -> list[str]:
        """The keys of the places start up to (not including) stop."""
        start, stop = max(0, start), min(self._length, stop)
        if start >= stop:
            return []
        block, offset = self._locate(start)
        number = self._numbers[id(block)]
        found: list[str] = []
        while len(found) < stop - start and number < len(self._blocks):
            found.extend(self._blocks[number].keys[offset:offset + stop - start - len(found)])
            number, offset = number + 1, 0
        return found

    def __iter__(self) -> Iterator[str]:
        for block in self._blocks:
            yield from block.keys

    def insert(self, position: int, key: str, folder: str) -> None:
        """Put a message at this place (at the end when the place is past it). A key the view holds already is refused."""
        if key in self._owner:
            raise ValueError("The view holds this message already")
        position = min(max(0, position), self._length)
        if not self._blocks:
            self._blocks.append(_Block([], []))
            self._stale = True
        if position == self._length:
            block, offset = self._blocks[-1], len(self._blocks[-1].keys)
        else:
            block, offset = self._locate(position)
        block.keys.insert(offset, key)
        block.folders.insert(offset, folder)
        self._owner[key] = block
        self._length += 1
        self._stale = True
        if len(block.keys) >= 2 * BLOCK:
            self._split(block)

    def _split(self, block: _Block) -> None:
        number = self._blocks.index(block)
        half = len(block.keys) // 2
        second = _Block(block.keys[half:], block.folders[half:])
        del block.keys[half:]
        del block.folders[half:]
        self._blocks.insert(number + 1, second)
        for key in second.keys:
            self._owner[key] = second

    def remove(self, key: str) -> int:
        """Take a message out; its place before, or -1 when the view did not hold it."""
        position = self.position(key)
        if position < 0:
            return -1
        block = self._owner.pop(key)
        offset = block.keys.index(key)
        del block.keys[offset]
        del block.folders[offset]
        if not block.keys:
            self._blocks.remove(block)
        self._length -= 1
        self._stale = True
        return position

    def keys_in(self, folder: str, mailbox: str = "") -> list[str]:
        """The keys of the messages in this folder (of this mailbox, in a Favorites view), in order."""
        prefix = f"{mailbox}{KEY_SEPARATOR}" if mailbox else ""
        found: list[str] = []
        for block in self._blocks:
            found.extend(key for key, held in zip(block.keys, block.folders, strict=True) if held == folder and key.startswith(prefix))
        return found

    def rename_folder(self, old: str, new: str, mailbox: str = "") -> None:
        """A folder got another name: the messages in it (and in the folders below it) say the new one."""
        prefix = f"{mailbox}{KEY_SEPARATOR}" if mailbox else ""
        names: dict[str, str] = {}
        for block in self._blocks:
            for offset, (key, held) in enumerate(zip(block.keys, block.folders, strict=True)):
                if (held == old or held.startswith(old + "/")) and key.startswith(prefix):
                    block.folders[offset] = names.setdefault(held, new + held[len(old):])

    def set_folder(self, key: str, folder: str) -> None:
        block = self._owner.get(key)
        if block is not None:
            block.folders[block.keys.index(key)] = folder

    def without(self, keys: Iterable[str]) -> "MessageOrder":
        """The same order without these messages (one step for many, instead of taking them out one by one)."""
        gone = set(keys)
        kept_keys: list[str] = []
        kept_folders: list[str] = []
        for block in self._blocks:
            for key, folder in zip(block.keys, block.folders, strict=True):
                if key not in gone:
                    kept_keys.append(key)
                    kept_folders.append(folder)
        return MessageOrder(kept_keys, kept_folders)


KEY_SEPARATOR: str = "\t"  # between the mailbox and the id in the key of a message of a Favorites view


def message_key(mailbox: str, message_id: str) -> str:
    """The key of a message in a view: its id; in a Favorites view (mailbox given) its mailbox and its id."""
    return f"{mailbox}{KEY_SEPARATOR}{message_id}" if mailbox else message_id


def split_key(key: str) -> tuple[str, str]:
    """The mailbox ("" for a view of one mailbox) and the id of a key."""
    mailbox, separator, message_id = key.rpartition(KEY_SEPARATOR)
    return (mailbox, message_id) if separator else ("", key)
