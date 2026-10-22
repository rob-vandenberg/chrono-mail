import html

from PySide6.QtGui import QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import QWidget

from chrono_mail.engine.commands_export import TextResult

# --- Version ------------------------------------------------------------
__version__ = "printing.py 1.9.275"

# --- Version history ----------------------------------------------------
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v0.0.22: Initial printing of a message

SAFE_STYLE = "font-family: sans-serif; font-size: 10pt"


def print_document(text: TextResult) -> QTextDocument:
    """A message.text result as a document ready to print. Only plain text and our own markup go in: no message HTML,
    so nothing is fetched from the internet and no script can run."""
    lines = [f"<h2 style='margin:0'>{html.escape(text.subject or '(no subject)')}</h2>", "<table style='margin-top:6px'>"]
    for label, value in (
        ("From", ", ".join(text.sender)), ("To", ", ".join(text.to)),
        ("Cc", ", ".join(text.cc)), ("Date", text.date),
        ("Attachments", ", ".join(text.attachments)),
    ):
        if value:
            lines.append(f"<tr><td style='padding-right:12px'><b>{label}</b></td><td>{html.escape(value)}</td></tr>")
    lines.append("</table><hr>")
    lines.append(f"<pre style='white-space: pre-wrap; {SAFE_STYLE}'>{html.escape(text.text)}</pre>")
    document = QTextDocument()
    document.setUndoRedoEnabled(False)
    document.setHtml(f"<div style='{SAFE_STYLE}'>{''.join(lines)}</div>")
    return document


def print_message(parent: QWidget | None, text: TextResult) -> bool:
    """Ask which printer to use and print. False when the person cancelled."""
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    dialog = QPrintDialog(printer, parent)
    dialog.setWindowTitle("Print message")
    if dialog.exec() != QPrintDialog.DialogCode.Accepted:
        return False
    print_document(text).print_(printer)
    return True
