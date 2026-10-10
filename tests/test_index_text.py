from chrono_mail.index import MAXIMUM_BODY_CHARACTERS, html_to_text, searchable_body

# --- Version ------------------------------------------------------------
__version__ = "test_index_text.py 0.0.8"

# --- Version history ----------------------------------------------------
# v0.0.8: Initial index text tests


def test_html_text_is_extracted_without_markup_script_or_style() -> None:
    html = "<html><head><title>T</title><style>p{color:red}</style></head><body><p>Hello <b>wörld</b></p><script>alert(1)</script>&amp; more</body></html>"
    text = html_to_text(html)
    assert "Hello" in text and "wörld" in text and "& more" in text
    assert "alert" not in text and "color" not in text and "<" not in text


def test_broken_html_never_raises() -> None:
    for html in ["<", "<<<>>>", "<p", "<script>never closed", "&#x110000;", "&bogus;", "<a href=\"", "\x00\x01", "</p></p></p>", "<!--", "<![CDATA[x"]:
        assert isinstance(html_to_text(html), str)


def test_plain_text_wins_over_html() -> None:
    assert searchable_body("plain  text\n\nhere", "<p>html</p>") == "plain text here"
    assert searchable_body(None, "<p>only <i>html</i></p>") == "only html"
    assert searchable_body(None, None) == ""
    assert searchable_body("", "<p>x</p>") == "x"


def test_body_is_capped() -> None:
    assert len(searchable_body("word " * 200_000, None)) == MAXIMUM_BODY_CHARACTERS
    assert len(searchable_body(None, "<p>" + "word " * 200_000 + "</p>")) <= MAXIMUM_BODY_CHARACTERS


def test_huge_html_is_handled_in_reasonable_time() -> None:
    assert len(searchable_body(None, "<div>" * 500_000)) <= MAXIMUM_BODY_CHARACTERS
