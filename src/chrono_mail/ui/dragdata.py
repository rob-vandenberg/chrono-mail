import json

from PySide6.QtCore import QMimeData
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "dragdata.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash; a drag says when the rest of the
#           selection follows (messages whose place the list did not know yet, RV-206)
# v3.6.349: a summary of the drag (account, source folders), small whatever the drag holds, for the checks at every pointer move (RV-205)
# v1.10.277: Drag data for contacts (onto a contact group, R-39)
# v0.0.17: Initial drag data for messages

MESSAGES_MIME_TYPE: str = "application/x-chrono-mail-messages"
MESSAGES_SUMMARY_TYPE: str = "application/x-chrono-mail-messages-summary"  # the account and the folders they come from: read at every move
CONTACTS_MIME_TYPE: str = "application/x-chrono-mail-contacts"


def messages_mime_data(account_id: str, messages: list[tuple[str, str]], rest: int | None = None, rest_folder: str | None = None) -> QMimeData:
    """The drag data for messages: the account and the (folder, message id) pairs. Message contents are never in it. rest: more selected
    messages go along whose place the list does not know yet; the drop takes them once it does, unless the list gave such places up since
    (rest is its places_epoch when the drag began, RV-206). rest_folder: the folder those are in, when the view is one folder (the folder
    list then refuses that folder, as for any drag)."""
    data = QMimeData()
    data.setData(MESSAGES_MIME_TYPE, json.dumps({"account": account_id, "messages": messages, "rest": rest}).encode("utf-8"))
    folders = {folder for folder, _ in messages} | ({rest_folder} if rest is not None and rest_folder is not None else set[str]())
    summary = {"account": account_id, "folders": sorted(folders), "rest": rest}
    data.setData(MESSAGES_SUMMARY_TYPE, json.dumps(summary).encode("utf-8"))  # small whatever the drag holds: the drop's list is read once (RV-205)
    return data


def read_messages_summary(data: QMimeData) -> tuple[str, frozenset[str]] | None:
    """The account and the folders the dragged messages come from, without reading the messages themselves (a drag of 500,000 is checked
    at every move of the pointer, RV-205); None when it is not (valid) message drag data."""
    if not data.hasFormat(MESSAGES_SUMMARY_TYPE):
        return None
    try:
        decoded = load_json(bytes(data.data(MESSAGES_SUMMARY_TYPE).data()).decode("utf-8"))
        account, folders = decoded["account"], frozenset(str(folder) for folder in decoded["folders"])
        rest = isinstance(decoded.get("rest"), int)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return (account, folders) if isinstance(account, str) and (folders or rest) else None


def read_messages_mime_data(data: QMimeData) -> tuple[str, list[tuple[str, str]]] | None:
    """The account and the messages in drag data, or None when it is not (valid) message drag data."""
    if not data.hasFormat(MESSAGES_MIME_TYPE):
        return None
    try:
        decoded = load_json(bytes(data.data(MESSAGES_MIME_TYPE).data()).decode("utf-8"))
        account = decoded["account"]
        messages = [(str(folder), str(message)) for folder, message in decoded["messages"]]
        rest = isinstance(decoded.get("rest"), int)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return (account, messages) if isinstance(account, str) and (messages or rest) else None


def read_messages_rest(data: QMimeData) -> int | None:
    """When more selected messages go along than the drag data names (their place in the list was not known yet), the list's places_epoch
    when the drag began; None otherwise (RV-206)."""
    if not data.hasFormat(MESSAGES_SUMMARY_TYPE):
        return None
    try:
        decoded = load_json(bytes(data.data(MESSAGES_SUMMARY_TYPE).data()).decode("utf-8"))
    except ValueError:
        return None
    rest = decoded.get("rest") if isinstance(decoded, dict) else None
    return rest if isinstance(rest, int) and not isinstance(rest, bool) else None


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
        decoded = load_json(bytes(data.data(CONTACTS_MIME_TYPE).data()).decode("utf-8"))
        collection = decoded["collection"]
        items = [str(item) for item in decoded["items"]]
    except (ValueError, KeyError, TypeError):
        return None
    return (collection, items) if isinstance(collection, str) and collection and items else None
