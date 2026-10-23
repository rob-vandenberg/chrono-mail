import base64
import json
from typing import cast

from PySide6.QtCore import QByteArray, QSettings

# --- Version ------------------------------------------------------------
__version__ = "windowsettings.py 2.4.324"

# --- Version history ----------------------------------------------------
# v2.4.324: apply_window_settings: a backup's window settings put back (kept here unless Replace, D-244)
# v2.4.322: Initial: the window's own settings (sizes, columns per folder, theme, keys…) as text for a backup (D-244)


def window_settings_text(settings: QSettings) -> str:
    """Every setting the window keeps itself (in a file on Linux, in the registry on Windows) as JSON, each value with its kind, so a
    restore can put back exactly what was there."""
    settings.sync()  # what was changed a moment ago too
    values: dict[str, dict[str, object]] = {}
    for key in sorted(settings.allKeys()):
        values[key] = _kept(settings.value(key))
    return json.dumps(values, indent=1, sort_keys=True)


def _kept(value: object) -> dict[str, object]:
    if isinstance(value, QByteArray):
        return {"kind": "bytes", "value": base64.b64encode(value.data()).decode("ascii")}
    if isinstance(value, bool):
        return {"kind": "flag", "value": value}
    if isinstance(value, (int, float)):
        return {"kind": "number", "value": value}
    if isinstance(value, (list, tuple)):
        return {"kind": "list", "value": [str(item) for item in value]}  # the items of a stored list are texts
    if value is None:
        return {"kind": "none", "value": None}
    return {"kind": "text", "value": str(value)}


def apply_window_settings(settings: QSettings, text: str, replace_existing: bool) -> int:
    """The window's own settings from a backup put in place: a setting that is here already is kept unless replace_existing. How many
    were written. A value of a kind not known is left out."""
    try:
        values: object = json.loads(text)
    except ValueError:
        return 0
    if not isinstance(values, dict):
        return 0
    written = 0
    present = set(settings.allKeys())
    for key, kept in cast(dict[str, object], values).items():
        if (key in present and not replace_existing) or not isinstance(kept, dict):
            continue
        value = _value(cast(dict[str, object], kept))
        if value is _UNKNOWN:
            continue
        settings.setValue(key, value)
        written += 1
    settings.sync()
    return written


_UNKNOWN: object = object()


def _value(kept: dict[str, object]) -> object:
    kind, value = kept.get("kind"), kept.get("value")
    if kind == "bytes" and isinstance(value, str):
        try:
            return QByteArray(base64.b64decode(value, validate=True))
        except ValueError:
            return _UNKNOWN
    if kind == "flag" and isinstance(value, bool):
        return value
    if kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if kind == "list" and isinstance(value, list):
        return [str(item) for item in cast(list[object], value)]
    if kind == "none":
        return None
    if kind == "text" and isinstance(value, str):
        return value
    return _UNKNOWN
