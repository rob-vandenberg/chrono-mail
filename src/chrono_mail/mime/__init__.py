"""Parsing and building messages, robust against malformed input."""

from chrono_mail.mime.builder import as_it_goes_out, build_message, envelope_recipients, with_extra_headers, without_bcc
from chrono_mail.mime.headers import delivery_addresses, header_values
from chrono_mail.mime.model import REPEAT_HEADER, SEND_AT_HEADER, Address, Attachment, OutgoingMessage, ParsedMessage, send_time
from chrono_mail.mime.parser import parse_message

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 4.2.428"

# --- Version history ----------------------------------------------------
# v4.2.428: Export as_it_goes_out
# v4.2.417: Export header_values and delivery_addresses
# v1.1.206: Export REPEAT_HEADER and with_extra_headers
# v1.1.196: Export SEND_AT_HEADER and send_time
# v0.0.86: Export without_bcc
# v0.0.5: Export the parser, the builder and the message model
# v0.0.2: Initial skeleton

__all__ = [
    "REPEAT_HEADER",
    "SEND_AT_HEADER",
    "Address",
    "Attachment",
    "OutgoingMessage",
    "ParsedMessage",
    "build_message",
    "delivery_addresses",
    "header_values",
    "envelope_recipients",
    "with_extra_headers",
    "as_it_goes_out", "without_bcc",
    "parse_message",
    "send_time",
]
