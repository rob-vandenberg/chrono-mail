import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import override

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QLayout, QVBoxLayout, QWidget

from chrono_mail.ui import theme
from chrono_mail.ui.scale import wpx

# --- Version ------------------------------------------------------------
__version__ = "forms.py 4.2.445"

# --- Version history ----------------------------------------------------
# v4.2.445: open_at: a smallest width and the width a window opens at; owner_width: a width as the owner measures it on his screen
# v4.0.376: smallest sets only the width (the height is the layout's)
# v4.0.375: fill: a window takes its title from its form (the form's windowTitle), so the title is designed with the rest; the spacers are
#           scaled in the form's text before Qt reads it (asking the layouts for their items left Python stand-ins that outlived the
#           window and were taken for a part made later at the same address); a spacer of size 0 stays 0 (it took 1 pixel); the layouts
#           and roles inside the program's own parts are left to them (a form inside a form lost its margins); smallest: a window's own
#           smallest width at the size factor, never below what its layout needs, never wider than its screen
# v3.6.373: Initial: dialogs are built from Qt Designer forms (.ui files in ui/forms): the layout, the parts and their texts; the program adds
#           the look (theme.py), the sizes at the size factor, its own special parts (made by the loader at their placeholders) and
#           everything that changes (D-276)

FORMS_DIRECTORY: Path = Path(__file__).resolve().parent / "forms"
ROLE: str = "role"  # a part's dynamic property in Designer that names its kind for the look rules (theme.form_role_look)


class FormError(RuntimeError):
    """A form could not be read, or lacks a part the program needs: a fault of the program's own files, said with the form and the part."""


def form_file(name: str) -> Path:
    return FORMS_DIRECTORY / f"{name}.ui"


class _Loader(QUiLoader):
    """Qt's form loader, which makes the program's own parts itself where the form has their placeholders (by name): a part made at its
    place in the form gets its slot in the layout, its place in the Tab order (also one set in Designer) and the label that names it,
    exactly as the form's own parts do."""

    def __init__(self, own_parts: Mapping[str, Callable[[QWidget], QWidget]]) -> None:
        super().__init__()
        self._own_parts: Mapping[str, Callable[[QWidget], QWidget]] = own_parts
        self.made: dict[str, QWidget] = {}

    @override
    def createWidget(self, className: str, parent: QWidget | None = None, name: str = "") -> QWidget:  # noqa: N802, N803 - Qt's names
        make = self._own_parts.get(name)
        if make is None or parent is None:
            return super().createWidget(className, parent, name)
        widget = make(parent)
        widget.setObjectName(name)
        self.made[name] = widget
        return widget


def load(name: str, parent: QWidget, own_parts: Mapping[str, Callable[[QWidget], QWidget]] | None = None) -> QWidget:
    """The form as a new child of parent, the program's own parts made at their placeholders (own_parts: placeholder name -> how the
    part is made, given its parent), its spacers at the size factor of the windows and its parts with a role given their look."""
    path = form_file(name)
    text, in_form, own_margins = _read(path)
    buffer = QBuffer()
    buffer.setData(QByteArray(text))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    wanted = dict(own_parts or {})
    loader = _Loader(wanted)
    form = loader.load(buffer, parent)
    buffer.close()
    if form is None:
        raise FormError(f"The form {path.name} could not be built: {loader.errorString()}")
    missing = sorted(set(wanted) - set(loader.made))
    if missing:
        raise FormError(f"The form {path.name} has no placeholder named {', '.join(missing)}")
    form.setProperty("form", name)  # for the error texts of part()
    made = list(loader.made.values())

    def own(widget: QWidget | None) -> bool:
        """Inside a part of the program (which may be built from a form of its own): never touched here."""
        return widget is not None and any(part is widget or part.isAncestorOf(widget) for part in made)

    for layout in form.findChildren(QLayout):
        # never the layouts inside Qt's own parts (the row of OK and Cancel) or inside the program's
        if layout.objectName() in in_form and layout.objectName() not in own_margins and not own(layout.parentWidget()):
            layout.unsetContentsMargins()  # the loader sets 0; a layout made in code has the style's margins at the size factor
    for part_with_role in [form, *form.findChildren(QWidget)]:
        role = part_with_role.property(ROLE)
        if isinstance(role, str) and role and not own(part_with_role):
            part_with_role.setStyleSheet(theme.form_role_look(role))
    return form


def fill(host: QWidget, name: str, own_parts: Mapping[str, Callable[[QWidget], QWidget]] | None = None) -> QWidget:
    """The form as the whole content of host (a page or a dialog): the form's own layout keeps its margins, except in a window, which has
    the style's margins of a window (wider than those of a part inside it) unless the form sets its own. A window takes the form's title."""
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    form = load(name, host, own_parts)
    layout.addWidget(form)
    root = form.layout()
    if host.isWindow() and root is not None and root.objectName() not in _read(form_file(name))[2]:
        layout.unsetContentsMargins()
        root.setContentsMargins(0, 0, 0, 0)
    if host.isWindow() and form.windowTitle():
        host.setWindowTitle(form.windowTitle())
    return form


def smallest(window: QWidget, width: int) -> None:
    """A window's smallest width: its own, at the standard size, here at the size factor of the windows, but never below what its layout
    needs (a width set on a window overrules its layout, so a part was cut off: a certificate's fingerprint, a long choice, a row of
    buttons; a wrapping text counts only with its longest word), and never wider than the screen it opens on (a very long text from a
    server is cut off rather than pushing the window off the screen). Called once the window holds its parts."""
    screen = window.screen()
    room = screen.availableGeometry().width() if screen is not None else wpx(width)
    window.setMinimumWidth(min(max(wpx(width), window.minimumSizeHint().width()), max(wpx(width), room)))


def owner_width(pixels: int) -> int:
    """A width the owner gave as he sees it on his screen (125 % screen scale, windows' size slider 0.95), as a width at the standard size:
    in Qt's units it then follows every screen's scale and the size slider, as the text does, and is exactly that width on his screen."""
    return round(pixels / (OWNER_SCREEN_SCALE * OWNER_WINDOWS_FACTOR))


OWNER_SCREEN_SCALE: float = 1.25  # the owner's screen, on which he measures the widths he gives
OWNER_WINDOWS_FACTOR: float = 0.95  # his windows' size slider


def open_at(window: QWidget, smallest_width: int, width: int) -> None:
    """A window's smallest width (as smallest does) and the width it opens at, both at the standard size; never below what its layout
    needs, never wider than the screen it opens on. Called once the window holds its parts."""
    smallest(window, smallest_width)
    screen = window.screen()
    room = screen.availableGeometry().width() if screen is not None else wpx(width)
    window.resize(max(window.minimumWidth(), min(wpx(width), room)), window.sizeHint().height())


def part[T: QObject](form: QWidget, kind: type[T], name: str) -> T:
    """The part of the form with this name, of this kind (a widget or a layout)."""
    found = form.findChild(kind, name)
    if found is None:
        raise FormError(f"The form {form.property('form')}.ui has no {kind.__name__} named {name}")
    return found


def tab_stops(form: QWidget) -> list[QWidget]:
    """Where Tab stops in the form, in order (for checks): every part that takes the focus and is not inside another such part."""
    found: list[QWidget] = []
    current = form.nextInFocusChain()
    while current is not None and current is not form:
        if form.isAncestorOf(current) and current.focusPolicy() & Qt.FocusPolicy.TabFocus and not any(stop.isAncestorOf(current) for stop in found):
            found.append(current)
        current = current.nextInFocusChain()
    return found


def _read(path: Path) -> tuple[bytes, set[str], set[str]]:
    """The form's text with its spacers at the size factor of the windows (a size in a form is at the standard size; changed here,
    before Qt reads it, so the program never takes hold of the layouts' items: Python's stand-ins for them would outlive the window
    and be taken for a part made later at the same place in memory), the names of its layouts, and of those whose margins the form
    sets itself (for example 0, edge to edge)."""
    try:
        tree = ElementTree.parse(path)
    except (OSError, ElementTree.ParseError) as error:
        raise FormError(f"The form {path.name} could not be read: {error}") from error
    for spacer in tree.iter("spacer"):
        for size in spacer.iterfind("property[@name='sizeHint']/size"):
            for length in (size.find("width"), size.find("height")):
                if length is not None and length.text and length.text.strip().isascii() and length.text.strip().isdigit() and int(length.text) > 0:
                    length.text = str(wpx(int(length.text)))
    margins = {"leftMargin", "topMargin", "rightMargin", "bottomMargin", "margin"}
    layouts = list(tree.iter("layout"))
    return (
        ElementTree.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True),
        {layout.get("name", "") for layout in layouts} - {""},  # a layout without a name could stand for Qt's own unnamed ones
        {layout.get("name", "") for layout in layouts if any(item.get("name") in margins for item in layout.findall("property"))} - {""},
    )
