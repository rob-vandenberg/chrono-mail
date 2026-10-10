import re
from html.parser import HTMLParser
from typing import override

# --- Version ------------------------------------------------------------
__version__ = "text.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: html_to_text can keep lines
# v0.0.8: Initial text extraction for the index

MAXIMUM_BODY_CHARACTERS: int = 100_000  # per message, for full-text search (D-025)
_WHITESPACE: re.Pattern[str] = re.compile(r"\s+")
_SPACES: re.Pattern[str] = re.compile(r"[ \t\r\f\v]+")
_SKIPPED_TAGS: frozenset[str] = frozenset({"script", "style", "head", "title"})
_PARAGRAPH_TAGS: frozenset[str] = frozenset({"p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "table", "ul", "ol", "hr"})
_LINE_TAGS: frozenset[str] = frozenset({"div", "li", "tr"})


class _TextExtractor(HTMLParser):
    def __init__(self, keep_lines: bool) -> None:
        super().__init__(convert_charrefs=True)
        self._keep_lines: bool = keep_lines
        self.parts: list[str] = []
        self._skip_depth: int = 0
        self._length: int = 0
        self._pending_breaks: int = 0  # line breaks wanted before the next text (keep_lines only)

    def _break(self, tag: str) -> None:
        if tag == "br":
            self._pending_breaks += 1
        elif tag in _PARAGRAPH_TAGS:
            self._pending_breaks = max(self._pending_breaks, 2)
        elif tag in _LINE_TAGS:
            self._pending_breaks = max(self._pending_breaks, 1)

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1
        elif self._keep_lines and self._skip_depth == 0:
            self._break(tag)

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif self._keep_lines and self._skip_depth == 0:
            self._break(tag)

    @override
    def handle_data(self, data: str) -> None:
        if self._skip_depth != 0 or self._length >= MAXIMUM_BODY_CHARACTERS:
            return
        if self._keep_lines:
            if not data.strip():
                if not self._pending_breaks and self.parts:
                    self.parts.append(" ")  # a space between two inline pieces of text
                return
            if self._pending_breaks and self.parts:
                self.parts.append("\n" * min(self._pending_breaks, 2))
            self._pending_breaks = 0
        self.parts.append(data)
        self._length += len(data)


def html_to_text(html: str, keep_lines: bool = False) -> str:
    """The visible text of an HTML body. Never raises: broken markup gives whatever text was found.

    With keep_lines, paragraphs, line breaks and list items start new lines (for quoting in a reply);
    otherwise the pieces of text are joined with spaces (for searching).
    """
    extractor = _TextExtractor(keep_lines)
    try:
        extractor.feed(html[: MAXIMUM_BODY_CHARACTERS * 20])
        extractor.close()
    except Exception:  # hostile markup must not break indexing
        pass
    if not keep_lines:
        return " ".join(extractor.parts)
    return "\n".join(_SPACES.sub(" ", line).strip() for line in "".join(extractor.parts).split("\n")).strip("\n")


def searchable_body(text_body: str | None, html_body: str | None) -> str:
    """The plain body when there is one, otherwise the text of the HTML body; whitespace collapsed and capped."""
    source = text_body if text_body else (html_to_text(html_body) if html_body else "")
    return _WHITESPACE.sub(" ", source[: MAXIMUM_BODY_CHARACTERS * 2]).strip()[:MAXIMUM_BODY_CHARACTERS]
