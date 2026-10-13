import re
from typing import override

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QProxyStyle, QStyle, QStyleOption, QWidget

# --- Version ------------------------------------------------------------
__version__ = "scale.py 0.3.108"

# --- Version history ----------------------------------------------------
# v0.3.108: The size factor of the message list (lpx, saved_list); saved_reading converts the older zoom of messages (percent); it is the size of reading and writing
# v0.3.106: Initial size factor of the interface (a magnifying glass): text, icons, spacing and fixed sizes grow and shrink together

MINIMUM: float = 0.75
MAXIMUM: float = 2.00
STEP: float = 0.05
STANDARD: float = 1.00
ICON_METRICS: frozenset[QStyle.PixelMetric] = frozenset({
    QStyle.PixelMetric.PM_SmallIconSize, QStyle.PixelMetric.PM_LargeIconSize, QStyle.PixelMetric.PM_ToolBarIconSize,
    QStyle.PixelMetric.PM_ButtonIconSize, QStyle.PixelMetric.PM_ListViewIconSize, QStyle.PixelMetric.PM_IconViewIconSize,
    QStyle.PixelMetric.PM_TabBarIconSize, QStyle.PixelMetric.PM_MessageBoxIconSize,
    QStyle.PixelMetric.PM_IndicatorWidth, QStyle.PixelMetric.PM_IndicatorHeight,
    QStyle.PixelMetric.PM_ExclusiveIndicatorWidth, QStyle.PixelMetric.PM_ExclusiveIndicatorHeight,
})
_PIXELS = re.compile(r"(?<![\w.#-])(\d+(?:\.\d+)?)px")


class _State:
    factor: float = STANDARD
    list_factor: float = STANDARD


_state = _State()


def interface() -> float:
    """The size factor of the interface now (1.00 is the system's normal size)."""
    return _state.factor


def set_interface(factor: float) -> None:
    _state.factor = clamp(factor)


def message_list() -> float:
    """The size factor of the message list now."""
    return _state.list_factor


def set_message_list(factor: float) -> None:
    _state.list_factor = clamp(factor)


def lpx(pixels: float) -> int:
    """A size in pixels at the factor of the message list; never below 1."""
    return max(1, round(pixels * _state.list_factor))


def clamp(factor: float) -> float:
    """Within Small and Large, on a step of the slider."""
    steps = round((min(MAXIMUM, max(MINIMUM, factor)) - MINIMUM) / STEP)
    return round(MINIMUM + steps * STEP, 2)


def px(pixels: float) -> int:
    """A size in pixels at the factor of the interface; never below 1."""
    return max(1, round(pixels * _state.factor))


def scale_css(css: str) -> str:
    """A stylesheet with every size in pixels at the factor of the interface. Lines of 1 pixel stay 1 pixel."""

    def scaled(match: re.Match[str]) -> str:
        value = float(match.group(1))
        return match.group(0) if value <= 1 else f"{px(value)}px"

    return _PIXELS.sub(scaled, css)


def slider_position(factor: float) -> int:
    return round((clamp(factor) - MINIMUM) / STEP)


def factor_at(position: int) -> float:
    return clamp(MINIMUM + position * STEP)


SLIDER_STEPS: int = slider_position(MAXIMUM)


def saved_factor(settings: QSettings, system_points: float) -> float:
    """The factor in the settings; a text size in points saved by an older version becomes the factor that gives it."""
    if settings.contains("interface_scale"):
        try:
            return clamp(float(str(settings.value("interface_scale"))))
        except ValueError:
            return STANDARD
    if settings.contains("ui_font_size") and system_points > 0:
        try:
            return clamp(float(str(settings.value("ui_font_size"))) / system_points)
        except ValueError:
            return STANDARD
    return STANDARD


def _stored(settings: QSettings, key: str) -> float | None:
    if not settings.contains(key):
        return None
    try:
        return clamp(float(str(settings.value(key))))
    except ValueError:
        return STANDARD


def saved_reading(settings: QSettings) -> float:
    """The size of the text of messages; a zoom in percent of an older version (reader_zoom) becomes the factor."""
    stored = _stored(settings, "reading_scale")
    if stored is not None:
        return stored
    if settings.contains("reader_zoom"):
        try:
            return clamp(float(str(settings.value("reader_zoom"))) / 100)
        except ValueError:
            return STANDARD
    return STANDARD


def saved_list(settings: QSettings, system_points: float) -> float:
    """The size of the message list; a text size in points of an older version (list_font_size, 0 the system's) becomes the factor."""
    stored = _stored(settings, "list_scale")
    if stored is not None:
        return stored
    if settings.contains("list_font_size") and system_points > 0:
        try:
            points = float(str(settings.value("list_font_size")))
        except ValueError:
            return STANDARD
        return clamp(points / system_points) if points > 0 else STANDARD
    return STANDARD


class ScaledStyle(QProxyStyle):
    """The style of the platform (or the one chosen) with its icons and tick boxes at the factor of the interface."""

    def __init__(self, name: str, factor: float) -> None:
        super().__init__(name)
        self.factor: float = factor
        self.base_name: str = name
        base = self.baseStyle()
        self.setObjectName(base.objectName() if base is not None else name)

    @override
    def pixelMetric(self, metric: QStyle.PixelMetric, option: QStyleOption | None = None, widget: QWidget | None = None) -> int:
        value = super().pixelMetric(metric, option, widget)
        return max(1, round(value * self.factor)) if metric in ICON_METRICS else value
