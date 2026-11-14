from chrono_mail.engine import Engine
from chrono_mail.engine.commands_tags import CreateParameters, EditParameters, tag_create, tag_edit

# --- Version ------------------------------------------------------------
__version__ = "tagging.py 4.4.614"

# --- Version history ----------------------------------------------------
# v4.4.614: Initial: the demo's one list of tags (D-332), each with its colour, its Show in choices and a shortcut for the three used most

EVERY_SECTION: tuple[str, ...] = ("mail", "events", "contacts", "tasks")
DEMO_TAGS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (  # name, colour, Show in (Settings > Tags), shortcut
    ("Important", "#e74c3c", EVERY_SECTION, "Ctrl+Alt+1"),
    ("Clients", "#3498db", EVERY_SECTION, "Ctrl+Alt+2"),
    ("Family", "#2ecc71", EVERY_SECTION, "Ctrl+Alt+3"),
    ("Travel", "#1abc9c", ("mail", "events", "tasks"), ""),
    ("Receipts", "#f39c12", ("mail",), ""),
    ("To read", "#9b59b6", ("mail", "tasks"), ""),
    ("Health", "#e67e22", ("events", "contacts", "tasks"), ""),
    ("Errands", "#95a5a6", ("events", "tasks"), ""),
    ("Book club", "#8e44ad", ("mail", "events", "contacts"), ""),
    ("Garden", "#6ab04c", ("events", "contacts", "tasks"), ""),
)


def make_tags(engine: Engine) -> None:
    """The tags the person made over the years, in her order, through the commands of Settings > Tags: one list, each tag shown in the
    sections it is for (D-332). The demo is no first start, so it has these instead of the eleven starting tags (D-333)."""
    for name, colour, kinds, shortcut in DEMO_TAGS:
        tag_create(engine, CreateParameters(name, colour))
        tag_edit(engine, EditParameters(name, shortcut=shortcut, use_for=kinds))
