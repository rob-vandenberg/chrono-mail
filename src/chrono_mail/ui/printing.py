import html

from PySide6.QtGui import QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import QWidget

# --- Version ------------------------------------------------------------
__version__ = "printing.py 0.0.22"

# --- Version history ----------------------------------------------------
# v0.0.22: Initial printing of a message

SAFE_STYLE = "font-family: sans-serif; font-size: 10pt"


def print_document(text: object) -> QTextDocument:
    """A message.text result as a document ready to print. Only plain text and our own markup go in: no message HTML,
    so nothing is fetched from the internet and no script can run."""
    lines = [f"<h2 style='margin:0'>{html.escape(str(getattr(text, 'subject')) or '(no subject)')}</h2>", "<table style='margin-top:6px'>"]
    for label, value in (
        ("From", ", ".join(getattr(text, "sender"))), ("To", ", ".join(getattr(text, "to"))),
        ("Cc", ", ".join(getattr(text, "cc"))), ("Date", str(getattr(text, "date"))),
        ("Attachments", ", ".join(getattr(text, "attachments"))),
    ):
        if value:
            lines.append(f"<tr><td style='padding-right:12px'><b>{label}</b></td><td>{html.escape(value)}</td></tr>")
    lines.append("</table><hr>")
    lines.append(f"<pre style='white-space: pre-wrap; {SAFE_STYLE}'>{html.escape(str(getattr(text, 'text')))}</pre>")
    document = QTextDocument()
    document.setUndoRedoEnabled(False)
    document.setHtml(f"<div style='{SAFE_STYLE}'>{''.join(lines)}</div>")
    return document


def print_message(parent: QWidget | None, text: object) -> bool:
    """Ask which printer to use and print. False when the person cancelled."""
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    dialog = QPrintDialog(printer, parent)
    dialog.setWindowTitle("Print message")
    if dialog.exec() != QPrintDialog.DialogCode.Accepted:
        return False
    print_document(text).print_(printer)
    return True
