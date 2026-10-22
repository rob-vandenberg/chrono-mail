"""Headless command-line front end (chrono-mail-cli)."""

from chrono_mail.cli.main import (
    EXIT_AUTHENTICATION,
    EXIT_CONFIGURATION,
    EXIT_ERROR,
    EXIT_INTERRUPTED,
    EXIT_MESSAGE,
    EXIT_NETWORK,
    EXIT_OK,
    EXIT_STORAGE,
    EXIT_USAGE,
    build_parser,
    exit_code,
    main,
)
from chrono_mail.cli.render import render_text

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Export the command line interface
# v0.0.2: Initial skeleton

__all__ = [
    "EXIT_AUTHENTICATION",
    "EXIT_CONFIGURATION",
    "EXIT_ERROR",
    "EXIT_INTERRUPTED",
    "EXIT_MESSAGE",
    "EXIT_NETWORK",
    "EXIT_OK",
    "EXIT_STORAGE",
    "EXIT_USAGE",
    "build_parser",
    "exit_code",
    "main",
    "render_text",
]
