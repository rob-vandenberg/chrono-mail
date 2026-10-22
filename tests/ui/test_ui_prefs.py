import re
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSettings

from chrono_mail.ui import prefs
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.prefs import DEFAULTS, Preferences, Remembered, StartupView, read_preferences, save_preferences
from tests.ui.world import World, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_prefs.py 2.0.280"

# --- Version history ----------------------------------------------------
# v2.0.280: the columns are kept per folder by Remembered (folder_columns, type_columns); the one setup of earlier versions is handed over once (B-16)
# v1.2.253: Initial version: every setting read and written in one place, typed, checked the same way at start and after OK (R-12)

UI = Path(__file__).resolve().parents[2] / "src" / "chrono_mail" / "ui"
REMEMBERED = ("last_account", "last_folder", "last_kind", "last_section", "sort", "oldest_first", "show_groups", "column_layout", "columns", "folder_columns", "type_columns",
              "columns_contact_and_delete_added", "columns_snoozed_added", "tree_state", "search_history", "splitter2", "content_splitter")


def settings_in(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


def test_an_empty_settings_file_gives_every_default(tmp_path: Path) -> None:
    assert read_preferences(settings_in(tmp_path)) == Preferences()
    for key, value in DEFAULTS.items():  # the one table of defaults and the typed object agree
        field = "startup" if key == "startup_view" else key
        typed = getattr(Preferences(), field)
        written = {"startup": StartupView().text, "shortcuts": "{}", "remote_senders": ""}.get(field, typed)
        assert written == value, key


def test_every_setting_round_trips_and_only_a_change_is_written(tmp_path: Path) -> None:
    settings = settings_in(tmp_path)
    save_preferences(settings, Preferences())
    assert settings.allKeys() == []  # nothing changed: nothing written (the file stays as it is)
    chosen = Preferences(
        check_on_start=False, auto_check_minutes=17, color_scheme="dark", accent_light="#123456", interface_scale=1.25, list_layout="cards",
        reading_pane="bottom", date_style="iso", list_density="roomy", mark_read="window", mark_read_delay_ms=1500, remote_senders=("a@b.c", "d@e.f"),
        default_account="x" * 32, reply_quote="none", autosave_seconds=0, startup=StartupView("mail", "y" * 32, "folder", "Inbox/Work"),
        shortcuts={"refresh": "F6"},
    )
    save_preferences(settings, chosen)
    assert read_preferences(settings) == chosen
    settings.sync()
    assert read_preferences(settings_in(tmp_path)) == chosen  # through the file
    settings.clear()
    save_preferences(settings, replace(Preferences(), tray=False))
    assert settings.allKeys() == ["tray"]


def test_a_damaged_or_unknown_value_gives_its_default(tmp_path: Path) -> None:
    settings = settings_in(tmp_path)
    for key, value in (("list_layout", "tiles"), ("reading_pane", "left"), ("date_style", "roman"), ("auto_check_minutes", "soon"),
                       ("mark_read_delay_ms", "99999"), ("accent_dark", "red"), ("startup_view", "mail\tonly two"),
                       ("shortcuts", "{not json"), ("tray", "maybe")):
        settings.setValue(key, value)
    chosen = read_preferences(settings)
    assert (chosen.list_layout, chosen.reading_pane, chosen.date_style, chosen.auto_check_minutes) == ("columns", "right", "short", 5)
    assert (chosen.mark_read_delay_ms, chosen.accent_dark, chosen.startup, chosen.shortcuts, chosen.tray) == (10_000, "", StartupView(), {}, False)


def test_show_on_startup_is_read_once_into_one_value() -> None:
    for text in ("", "mail\t\tfavorite\tunread", "mail\tabc\tfolder\tInbox/Sub", "section\tcalendar"):
        assert StartupView.parse(text).text == text
    assert StartupView.parse("mail\tabc\tfolder\tInbox").path == "Inbox" and StartupView.parse("section\tcalendar").section == "calendar"
    assert StartupView.parse("rubbish") == StartupView() and StartupView.parse("section\t") == StartupView()


def test_what_the_window_remembers_has_one_default_each(tmp_path: Path) -> None:
    memory = Remembered(settings_in(tmp_path))
    assert (memory.last_kind, memory.folder_columns, memory.type_columns, memory.show_groups, memory.tree_state, memory.last_section) == ("folder", "", "", True, {}, "")
    memory.remember_view("a" * 32, "Inbox", "folder")
    memory.folder_columns, memory.type_columns = "{1}", "{2}"
    memory.tree_state = {"account|x|": False}
    memory.show_groups = False
    memory.remember_column_added("snoozed")
    again = Remembered(settings_in(tmp_path))
    assert (again.last_account, again.last_folder, again.folder_columns, again.type_columns, again.tree_state, again.show_groups) == ("a" * 32, "Inbox", "{1}", "{2}", {"account|x|": False}, False)
    assert again.column_added("snoozed") and not again.column_added("contact_and_delete")
    settings_in(tmp_path).setValue("tree_state", "{damaged")
    assert Remembered(settings_in(tmp_path)).tree_state == {}


def test_no_window_code_reads_a_setting_by_its_name_outside_the_settings_module() -> None:
    names = set(DEFAULTS) | set(REMEMBERED)
    found: list[str] = []
    for path in sorted(UI.glob("*.py")):
        if path.name == "prefs.py":
            continue
        for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            for name in re.findall(r'(?:value|setValue|contains|remove)\(\s*"(\w+)"', line):
                if name in names:
                    found.append(f"{path.name}:{number}: {name}")
    assert found == []  # every one through ui/prefs.py (R-12), so a default or a check can never differ between two places


def test_the_window_uses_the_same_checked_value_at_start_and_after_ok(world: World) -> None:
    settings = world.window.settings
    settings.setValue("list_layout", "tiles")  # damaged or from a later version
    settings.setValue("reading_pane", "left")
    again = MainWindow(world.backend, settings)
    try:
        assert (again.columns.layout, again.reading.place) == ("columns", "right")  # noqa: SLF001  before: taken unchecked at start
        again.apply_preferences()
        assert (again.columns.layout, again.reading.place) == ("columns", "right")  # noqa: SLF001  and the same after OK
    finally:
        again.deleteLater()
    assert prefs.get_text(settings, "list_layout", prefs.LIST_LAYOUTS) == "columns"


def test_the_one_setup_of_earlier_versions_is_handed_over_once(tmp_path: Path) -> None:
    settings = settings_in(tmp_path)
    assert Remembered(settings).earlier_setup() is None  # a new installation: nothing to hand over
    for key, value in (("columns", "subject,nothing,date"), ("column_layout", '{"icons": {}, "shares": {}}'), ("sort", "size"), ("oldest_first", True)):
        settings.setValue(key, value)
    memory = Remembered(settings)
    assert memory.earlier_setup() == (("subject", "date"), '{"icons": {}, "shares": {}}', ("size", True))
    assert memory.earlier_setup() is None and not any(settings.contains(key) for key in ("columns", "column_layout", "sort", "oldest_first"))
    settings.setValue("sort", "date")  # only a sort was ever saved (the columns were never changed)
    assert Remembered(settings).earlier_setup() == ((), "", ("date", False))
