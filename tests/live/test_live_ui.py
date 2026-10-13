import time
from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.core import ConnectionSecurity, ServerSettings
from chrono_mail.engine import build_registry
from chrono_mail.protocols.pop3 import Pop3Client
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.composer import ComposeWindow
from chrono_mail.ui.mainwindow import MainWindow
from tests.enginefactory import make_engine
from tests.live.account import LiveAccount
from tests.ui.world import Dialogs, dialogs, row_of, wait

# --- Version ------------------------------------------------------------
__version__ = "test_live_ui.py 0.4.122"

# --- Version history ----------------------------------------------------
# v0.4.122: The Trash list has group header rows: they are skipped when the message is looked for
# v0.0.13: Initial live user interface test

pytestmark = pytest.mark.live
REGISTRY = build_registry()
__all__ = ["dialogs"]  # the fixture comes from tests.ui.world


def server_has(account: LiveAccount, token: str) -> bool:
    settings = ServerSettings(account.host, account.pop3_ssl_port, ConnectionSecurity.TLS, account.user)
    with Pop3Client(settings, account.password) as client:
        return any(token.encode() in client.top(number, 0) for number, _ in client.uidl())


@pytest.fixture
def live_window(live_account: LiveAccount, tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> Generator[tuple[MainWindow, Backend, str]]:
    engine = make_engine(tmp_path)
    added = REGISTRY.run(engine, "account.add", dict(
        display_name="Live test", email_address=live_account.user, username=live_account.user, password=live_account.password,
        incoming_host=live_account.host, incoming_port=live_account.pop3_ssl_port, incoming_security="tls",
        outgoing_host=live_account.host, outgoing_port=live_account.smtp_starttls_port, outgoing_security="starttls",
        delete_policy="delete_after_download",
    ))
    backend = Backend(engine)
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    settings.setValue("check_on_start", False)
    window = MainWindow(backend, settings)
    qtbot.addWidget(window)
    window.show()
    yield window, backend, str(getattr(added, "account_id"))
    backend.wait_for_done(10000)
    window.close()


def test_send_receive_read_and_delete_through_the_window(live_window: tuple[MainWindow, Backend, str], live_account: LiveAccount, qtbot: QtBot, dialogs: Dialogs) -> None:
    window, backend, account_id = live_window
    token = f"live-ui-{int(time.time())}-{id(window) % 10000}"
    try:
        window.start()
        wait(qtbot, lambda: window.tree_model.rowCount() >= 1 and window._account_id == account_id)  # noqa: SLF001
        listed = backend.run_now("account.list", {})
        accounts: list[object] = list(listed) if isinstance(listed, tuple) else []
        composer = ComposeWindow(backend, accounts, account_id)
        qtbot.addWidget(composer)
        composer.show()
        composer.to.setText(live_account.user)
        composer.subject.setText(f"Chrono Mail live UI test {token}")
        composer.body.setPlainText(f"Hello from the window.\nToken: {token}\n")
        composer.send_button.click()
        wait(qtbot, lambda: not composer.isVisible(), timeout=30000)
        assert not dialogs.shown or all(kind != "warning" for kind, _, _ in dialogs.shown), dialogs.shown

        def arrived() -> bool:
            window.check_mail()
            qtbot.wait(3000)
            return any(token in str(getattr(row_of(window, row), "subject", "")) for row in range(window.list_model.rowCount()) if row_of(window, row) is not None)

        deadline = time.monotonic() + 90
        while not arrived() and time.monotonic() < deadline:
            qtbot.wait(2000)
        position = next((row for row in range(window.list_model.rowCount()) if row_of(window, row) is not None and token in row_of(window, row).subject), -1)
        assert position >= 0, "the message did not arrive in time"
        window.list_view.setCurrentIndex(window.list_model.index(position))
        wait(qtbot, lambda: token in window.reader.text_view.toPlainText(), timeout=15000)
        assert token in window.reader._subject.text()  # noqa: SLF001
        assert not server_has(live_account, token)  # the account deletes from the server after download
        window.act_delete.trigger()
        wait(qtbot, lambda: window.status_label.text().startswith("Moved 1 message"))
        trash = window._find_tree_item((account_id, "folder", "Trash"))  # noqa: SLF001
        assert trash is not None
        window.tree.setCurrentIndex(trash)
        wait(qtbot, lambda: window._folder == "Trash" and window.list_model.rowCount() >= 1)  # noqa: SLF001
        row = next(r for r in range(window.list_model.rowCount()) if row_of(window, r) is not None and token in row_of(window, r).subject)  # header bars are not messages
        window.list_view.setCurrentIndex(window.list_model.index(row))
        dialogs.answer = QMessageBox.StandardButton.Yes
        window.act_delete.trigger()
        wait(qtbot, lambda: window.status_label.text().startswith("Deleted 1 message"))
    finally:
        backend.wait_for_done(10000)
    assert not server_has(live_account, token)
