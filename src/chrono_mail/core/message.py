from dataclasses import dataclass

# --- Version ------------------------------------------------------------
__version__ = "message.py 0.0.4"

# --- Version history ----------------------------------------------------
# v0.0.4: Initial message reference


@dataclass(frozen=True, slots=True)
class MessageRef:
    """The stable identity of a message in the store.

    store_id is the random, meaningless identity from the store (D-004); changing a flag
    does not change it.
    """

    account_id: str
    folder_path: tuple[str, ...]
    store_id: str
