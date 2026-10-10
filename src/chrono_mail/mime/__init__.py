"""Parsing and building messages, robust against malformed input."""

from chrono_mail.mime.builder import build_message, envelope_recipients
from chrono_mail.mime.model import Address, Attachment, OutgoingMessage, ParsedMessage
from chrono_mail.mime.parser import parse_message

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Export the parser, the builder and the message model
# v0.0.2: Initial skeleton

__all__ = [
    "Address",
    "Attachment",
    "OutgoingMessage",
    "ParsedMessage",
    "build_message",
    "envelope_recipients",
    "parse_message",
]
