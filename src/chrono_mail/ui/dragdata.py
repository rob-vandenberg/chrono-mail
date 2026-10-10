import json

from PySide6.QtCore import QMimeData

# --- Version ------------------------------------------------------------
__version__ = "dragdata.py 0.0.17"

# --- Version history ----------------------------------------------------
# v0.0.17: Initial drag data for messages

MESSAGES_MIME_TYPE: str = "application/x-chrono-mail-messages"


def messages_mime_data(account_id: str, messages: list[tuple[str, str]]) -> QMimeData:
    """The drag data for messages: the account and the (folder, message id) pairs. Message contents are never in it."""
    data = QMimeData()
    data.setData(MESSAGES_MIME_TYPE, json.dumps({"account": account_id, "messages": messages}).encode("utf-8"))
    return data


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
