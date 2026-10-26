from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QPushButton, QWidget

from chrono_mail.engine.commands_export import SourceResult
from chrono_mail.ui import forms
from chrono_mail.ui.forms import part
from chrono_mail.ui.scale import wpx

# --- Version ------------------------------------------------------------
__version__ = "sourcedialog.py 4.0.377"

# --- Version history ----------------------------------------------------
# v4.0.377: built from its Qt Designer form (ui/forms/view_source.ui, D-276); its size follows the size factor
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v0.0.22: Initial message source window


class SourceDialog(QDialog):
    """The raw source of a message (headers and body as stored), read only."""

    def __init__(self, subject: str, source: SourceResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Source: {subject or '(no subject)'}")
        form = forms.fill(self, "view_source")  # D-276
        self.resize(wpx(820), wpx(620))
        size = source.size
        note = f"{size:,} bytes".replace(",", " ")
        if source.truncated:
            note += " (only the beginning is shown)"
        part(form, QLabel, "note").setText(note)
        self.view: QPlainTextEdit = part(form, QPlainTextEdit, "view")
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.view.setPlainText(source.text)
        self.copy_button: QPushButton = part(form, QPushButton, "copy")
        part(form, QDialogButtonBox, "box").rejected.connect(self.reject)
        self.copy_button.clicked.connect(self._copy)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.view.toPlainText())
