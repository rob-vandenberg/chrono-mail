from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

# --- Version ------------------------------------------------------------
__version__ = "sourcedialog.py 0.0.22"

# --- Version history ----------------------------------------------------
# v0.0.22: Initial message source window


class SourceDialog(QDialog):
    """The raw source of a message (headers and body as stored), read only."""

    def __init__(self, subject: str, source: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Source: {subject or '(no subject)'}")
        self.resize(820, 620)
        layout = QVBoxLayout(self)
        size = int(getattr(source, "size"))
        note = f"{size:,} bytes".replace(",", " ")
        if getattr(source, "truncated"):
            note += " (only the beginning is shown)"
        layout.addWidget(QLabel(note, self))
        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.view.setPlainText(str(getattr(source, "text")))
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
