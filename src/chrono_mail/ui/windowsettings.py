import base64
import json

from PySide6.QtCore import QByteArray, QSettings

# --- Version ------------------------------------------------------------
__version__ = "windowsettings.py 2.4.322"

# --- Version history ----------------------------------------------------
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
