import re
from html.parser import HTMLParser
from typing import override

# --- Version ------------------------------------------------------------
__version__ = "text.py 0.0.8"

# --- Version history ----------------------------------------------------
# v0.0.8: Initial text extraction for the index

MAXIMUM_BODY_CHARACTERS: int = 100_000  # per message, for full-text search (D-025)
_WHITESPACE: re.Pattern[str] = re.compile(r"\s+")
_SKIPPED_TAGS: frozenset[str] = frozenset({"script", "style", "head", "title"})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth: int = 0
        self._length: int = 0

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    @override
    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and self._length < MAXIMUM_BODY_CHARACTERS:
            self.parts.append(data)
            self._length += len(data)


def html_to_text(html: str) -> str:
    """The visible text of an HTML body. Never raises: broken markup gives whatever text was found."""
    extractor = _TextExtractor()
    try:
        extractor.feed(html[: MAXIMUM_BODY_CHARACTERS * 20])
        extractor.close()
    except Exception:  # hostile markup must not break indexing
        pass
    return " ".join(extractor.parts)


def searchable_body(text_body: str | None, html_body: str | None) -> str:
    """The plain body when there is one, otherwise the text of the HTML body; whitespace collapsed and capped."""
    source = text_body if text_body else (html_to_text(html_body) if html_body else "")
    return _WHITESPACE.sub(" ", source[: MAXIMUM_BODY_CHARACTERS * 2]).strip()[:MAXIMUM_BODY_CHARACTERS]
