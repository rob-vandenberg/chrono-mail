from collections.abc import Callable
from typing import override

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QAction, QKeyEvent, QShortcutEvent
from PySide6.QtWidgets import QWidget

# --- Version ------------------------------------------------------------
__version__ = "heldkey.py 4.2.394"

# --- Version history ----------------------------------------------------
# v4.2.394: Initial: whether an action was set off by the automatic repeat of a held key; a click or a menu never is (D-266)


class HeldKey(QObject):
    """Whether an action of a window (the main window, a message window) was set off by the automatic repeat of a held key (D-266).
    The window hears every key press first, from whichever part has the focus (it passes up through the parents). A watched action is
    told by Qt when its own shortcut key sets it off; only then does the key press count, and the action takes the answer once. A click,
    a menu or a press done later never comes with a shortcut, so it is never taken for a repeat."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._key_repeat: bool = False  # the last key press the window heard was a held key's repeat
        self._held: bool = False  # a watched action was just set off by that repeat; taken by the action
        window.installEventFilter(self)

    def watch(self, action: QAction) -> None:
        """This action asks take() when it acts: it hears when its shortcut key sets it off."""
        action.installEventFilter(self)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress) and isinstance(event, QKeyEvent):
            self._key_repeat = event.isAutoRepeat()
            self._held = False
        elif kind == QEvent.Type.Shortcut and isinstance(watched, QAction) and isinstance(event, QShortcutEvent) and not event.isAmbiguous():
            self._held = self._key_repeat  # the action acts right after this, in the same key press (an ambiguous key sets nothing off)
        return False

    def take(self) -> bool:
        """Whether the action acting now was set off by a held key's repeat; asked once, at the start of the action."""
        held, self._held = self._held, False
        return held

    def once(self, handler: Callable[[], object]) -> Callable[..., None]:
        """An action that opens a window acts once per press: a held key's repeats open no more windows."""

        def act(*_: object) -> None:
            if not self.take():
                handler()

        return act
