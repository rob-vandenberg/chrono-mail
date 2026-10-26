import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import override

from PySide6.QtCore import QFile, QIODevice, QObject, Qt
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QLayout, QSpacerItem, QVBoxLayout, QWidget

from chrono_mail.ui import theme
from chrono_mail.ui.scale import wpx

# --- Version ------------------------------------------------------------
__version__ = "forms.py 3.6.373"

# --- Version history ----------------------------------------------------
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
        self.made: set[str] = set()

    @override
    def createWidget(self, className: str, parent: QWidget | None = None, name: str = "") -> QWidget:  # noqa: N802, N803 - Qt's names
        make = self._own_parts.get(name)
        if make is None or parent is None:
            return super().createWidget(className, parent, name)
        widget = make(parent)
        widget.setObjectName(name)
        self.made.add(name)
        return widget


def load(name: str, parent: QWidget, own_parts: Mapping[str, Callable[[QWidget], QWidget]] | None = None) -> QWidget:
    """The form as a new child of parent, the program's own parts made at their placeholders (own_parts: placeholder name -> how the
    part is made, given its parent), its spacers at the size factor of the windows and its parts with a role given their look."""
    path = form_file(name)
    file = QFile(str(path))
    if not file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise FormError(f"The form {path.name} could not be read: {file.errorString()}")
    wanted = dict(own_parts or {})
    try:
        loader = _Loader(wanted)
        form = loader.load(file, parent)
    finally:
        file.close()
    if form is None:
        raise FormError(f"The form {path.name} could not be built: {loader.errorString()}")
    missing = sorted(set(wanted) - loader.made)
    if missing:
        raise FormError(f"The form {path.name} has no placeholder named {', '.join(missing)}")
    form.setProperty("form", name)  # for the error texts of part()
    in_form, own_margins = _layouts(path)
    for layout in form.findChildren(QLayout):
        if layout.objectName() in in_form:  # never the layouts inside Qt's own parts (the row of OK and Cancel)
            if layout.objectName() not in own_margins:
                layout.unsetContentsMargins()  # the loader sets 0; a layout made in code has the style's margins at the size factor
            _scale_spacers(layout)
    for part_with_role in [form, *form.findChildren(QWidget)]:
        role = part_with_role.property(ROLE)
        if isinstance(role, str) and role:
            part_with_role.setStyleSheet(theme.form_role_look(role))
    return form


def fill(host: QWidget, name: str, own_parts: Mapping[str, Callable[[QWidget], QWidget]] | None = None) -> QWidget:
    """The form as the whole content of host (a page or a dialog): the form's own layout keeps its margins, except in a window, which has
    the style's margins of a window (wider than those of a part inside it) unless the form sets its own."""
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    form = load(name, host, own_parts)
    layout.addWidget(form)
    root = form.layout()
    if host.isWindow() and root is not None and root.objectName() not in _layouts(form_file(name))[1]:
        layout.unsetContentsMargins()
        root.setContentsMargins(0, 0, 0, 0)
    return form


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


def _layouts(path: Path) -> tuple[set[str], set[str]]:
    """The names of the form's layouts, and of those whose margins the form sets itself (for example 0, edge to edge)."""
    margins = {"leftMargin", "topMargin", "rightMargin", "bottomMargin", "margin"}
    layouts = list(ElementTree.parse(path).iter("layout"))
    return (
        {layout.get("name", "") for layout in layouts} - {""},  # a layout without a name could stand for Qt's own unnamed ones
        {layout.get("name", "") for layout in layouts if any(item.get("name") in margins for item in layout.findall("property"))} - {""},
    )


def _scale_spacers(layout: QLayout) -> None:
    """A spacer's size in a form is at the standard size: here it follows the size factor of the windows, as every other size does."""
    for index in range(layout.count()):
        item = layout.itemAt(index)
        spacer = item.spacerItem() if item is not None else None
        if isinstance(spacer, QSpacerItem):
            hint, policy = spacer.sizeHint(), spacer.sizePolicy()
            spacer.changeSize(wpx(hint.width()), wpx(hint.height()), policy.horizontalPolicy(), policy.verticalPolicy())
    layout.invalidate()
