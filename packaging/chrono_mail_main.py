import os
import sys

# --- Version ------------------------------------------------------------
__version__ = "chrono_mail_main.py 0.5.127"

# --- Version history ----------------------------------------------------
# v0.5.127: Initial: the start of the packaged program (Nuitka builds it into the chrono-mail executable); tells Qt where
#           the helper program and the files of the HTML viewer are


def _point_qt_to_the_html_viewer() -> None:
    """Nuitka puts the HTML viewer's helper program (QtWebEngineProcess) and its files next to the program, while PySide6
    tells Qt to look in PySide6/Qt. Without this the viewer cannot start, or borrows the helper of a Qt installed on the
    system (another version). Set before PySide6 is loaded; a value the person set already is kept."""
    here = os.path.dirname(os.path.realpath(__file__))
    helper = os.path.join(here, "QtWebEngineProcess.exe" if sys.platform == "win32" else "QtWebEngineProcess")
    if os.path.exists(helper):
        os.environ.setdefault("QTWEBENGINEPROCESS_PATH", helper)
        os.environ.setdefault("QTWEBENGINE_RESOURCES_PATH", here)
        os.environ.setdefault("QTWEBENGINE_LOCALES_PATH", os.path.join(here, "qtwebengine_locales"))


if __name__ == "__main__":
    _point_qt_to_the_html_viewer()
    from chrono_mail.ui import main

    sys.exit(main())
