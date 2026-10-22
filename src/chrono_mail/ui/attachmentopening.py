from collections.abc import Callable

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_attachments import MESSAGE_ATTACHMENT_OPEN, AttachmentOpenParameters, AttachmentOpenResult
from chrono_mail.ui.backend import Backend

# --- Version ------------------------------------------------------------
__version__ = "attachmentopening.py 2.0.302"

# --- Version history ----------------------------------------------------
# v2.0.302: Initial Open attachment: a private copy opened in the program the computer uses for its kind of file; a program is asked
#           about first (D-236)


def open_attachment(
    parent: QWidget, backend: Backend, where: tuple[str, str, str], position: int, name: str,
    failed: Callable[[str, ChronoMailError | None], None], owner: QObject,
) -> None:
    """Open one attachment of the message at where (account, folder, message id) in the program the computer uses for its kind of
    file. A file that runs when opened is asked about first: mail is hostile input, one double click must never run a stranger's program."""

    def opened(result: AttachmentOpenResult) -> None:
        if result.program and ask_to_open_program(parent, name) != QMessageBox.StandardButton.Open:
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(result.path)):
            failed(f"No program on this computer opens {name}", None)

    account, folder, message_id = where
    backend.ask(
        MESSAGE_ATTACHMENT_OPEN, AttachmentOpenParameters(account, folder, message_id, position), opened,
        lambda error: failed("Could not open the attachment", error), owner=owner,
    )


def ask_to_open_program(parent: QWidget, name: str) -> QMessageBox.StandardButton:
    return QMessageBox.warning(
        parent, "Open a program?",
        f"{name} is a program. Opening it may run it, with your rights on this computer.\n\n"
        "Only open it when you know the sender and expected this file.",
        QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel,
    )
