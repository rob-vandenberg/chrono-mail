from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from chrono_mail.engine.commands_export import SourceResult

# --- Version ------------------------------------------------------------
__version__ = "sourcedialog.py 1.9.275"

# --- Version history ----------------------------------------------------
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v0.0.22: Initial message source window


class SourceDialog(QDialog):
    """The raw source of a message (headers and body as stored), read only."""

    def __init__(self, subject: str, source: SourceResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Source: {subject or '(no subject)'}")
        self.resize(820, 620)
        layout = QVBoxLayout(self)
        size = source.size
        note = f"{size:,} bytes".replace(",", " ")
        if source.truncated:
            note += " (only the beginning is shown)"
        layout.addWidget(QLabel(note, self))
        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.view.setPlainText(source.text)
        layout.addWidget(self.view, 1)
        buttons = QHBoxLayout()
        self.copy_button = QPushButton("Copy", self)
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        box.rejected.connect(self.reject)
        buttons.addWidget(box)
        layout.addLayout(buttons)
        self.copy_button.clicked.connect(self._copy)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.view.toPlainText())
