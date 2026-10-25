import json

from PySide6.QtCore import QMimeData

# --- Version ------------------------------------------------------------
__version__ = "dragdata.py 3.6.349"

# --- Version history ----------------------------------------------------
# v3.6.349: a summary of the drag (account, source folders), small whatever the drag holds, for the checks at every pointer move (RV-205)
# v1.10.277: Drag data for contacts (onto a contact group, R-39)
# v0.0.17: Initial drag data for messages

MESSAGES_MIME_TYPE: str = "application/x-chrono-mail-messages"
MESSAGES_SUMMARY_TYPE: str = "application/x-chrono-mail-messages-summary"  # the account and the folders they come from: read at every move
CONTACTS_MIME_TYPE: str = "application/x-chrono-mail-contacts"


def messages_mime_data(account_id: str, messages: list[tuple[str, str]]) -> QMimeData:
    """The drag data for messages: the account and the (folder, message id) pairs. Message contents are never in it."""
    data = QMimeData()
    data.setData(MESSAGES_MIME_TYPE, json.dumps({"account": account_id, "messages": messages}).encode("utf-8"))
    summary = {"account": account_id, "folders": sorted({folder for folder, _ in messages})}
    data.setData(MESSAGES_SUMMARY_TYPE, json.dumps(summary).encode("utf-8"))  # small whatever the drag holds: the drop's list is read once (RV-205)
    return data


def read_messages_summary(data: QMimeData) -> tuple[str, frozenset[str]] | None:
    """The account and the folders the dragged messages come from, without reading the messages themselves (a drag of 500,000 is checked
    at every move of the pointer, RV-205); None when it is not (valid) message drag data."""
    if not data.hasFormat(MESSAGES_SUMMARY_TYPE):
        return None
    try:
        decoded = json.loads(bytes(data.data(MESSAGES_SUMMARY_TYPE).data()).decode("utf-8"))
        account, folders = decoded["account"], frozenset(str(folder) for folder in decoded["folders"])
    except (ValueError, KeyError, TypeError):
        return None
    return (account, folders) if isinstance(account, str) and folders else None


def read_messages_mime_data(data: QMimeData) -> tuple[str, list[tuple[str, str]]] | None:
    """The account and the messages in drag data, or None when it is not (valid) message drag data."""
    if not data.hasFormat(MESSAGES_MIME_TYPE):
        return None
    try:
        decoded = json.loads(bytes(data.data(MESSAGES_MIME_TYPE).data()).decode("utf-8"))
        account = decoded["account"]
        messages = [(str(folder), str(message)) for folder, message in decoded["messages"]]
    except (ValueError, KeyError, TypeError):
        return None
    return (account, messages) if isinstance(account, str) and messages else None


def contacts_mime_data(collection: str, items: list[str]) -> QMimeData:
    """The drag data for contacts: their address book and their item ids. Nothing of the contacts themselves is in it."""
    data = QMimeData()
    data.setData(CONTACTS_MIME_TYPE, json.dumps({"collection": collection, "items": items}).encode("utf-8"))
    return data


def read_contacts_mime_data(data: QMimeData) -> tuple[str, list[str]] | None:
    """The address book and the contacts in drag data, or None when it is not (valid) contact drag data."""
    if not data.hasFormat(CONTACTS_MIME_TYPE):
        return None
    try:
        decoded = json.loads(bytes(data.data(CONTACTS_MIME_TYPE).data()).decode("utf-8"))
        collection = decoded["collection"]
        items = [str(item) for item in decoded["items"]]
    except (ValueError, KeyError, TypeError):
        return None
    return (collection, items) if isinstance(collection, str) and collection and items else None
