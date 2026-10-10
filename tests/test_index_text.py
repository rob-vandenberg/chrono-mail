import pytest

from chrono_mail.index import MAXIMUM_BODY_CHARACTERS, html_to_text, searchable_body
from chrono_mail.index.text import clean_preview

# --- Version ------------------------------------------------------------
__version__ = "test_index_text.py 0.0.14"

# --- Version history ----------------------------------------------------
# v0.0.14: Changes after the first real use (see DEVLOG.md)
# v0.0.13: Changes for the user interface (see DEVLOG.md)
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


def test_html_to_text_can_keep_the_lines() -> None:
    html = "<html><head><title>T</title></head><body><p>First  <b>para</b></p><p>Second</p><ul><li>one</li><li>two</li></ul>a<br>b<script>x()</script></body></html>"
    assert html_to_text(html, keep_lines=True) == "First para\n\nSecond\n\none\ntwo\n\na\nb"
    assert searchable_body(None, html) == "First para Second one two a b"  # the default, used for searching, is unchanged
    for broken in ["<", "<p", "<p><p><p>", "<br>" * 50, "&#x110000;", "\x00<p>\x00"]:
        assert isinstance(html_to_text(broken, keep_lines=True), str)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("[<https://e37f53d8ce464359a9c1a684d6023df8.104.eu.prod.marketinguser.com/track?x=1> Ontdek de nieuwe collectie", "Ontdek de nieuwe collectie"),
        ("------------------------- HELLO Rob This is the final email in our onboarding series", "HELLO Rob This is the final email in our onboarding series"),
        ("Visit www.example.com/pricing now", "Visit now"),
        ("=====  ***** #### Sale! 50% off see http://x.y/z (details) [image] <https://a.b>", "Sale! 50% off see (details) image"),
        ("Normal text, e-mail a@b.com or 3.14 - fine; costs 5-6 euro.", "Normal text, e-mail a@b.com or 3.14 - fine; costs 5-6 euro."),
        ("https://only.a.link/path", ""),
        ("", ""),
        ("   spaced \t\n out   ", "spaced out"),
        ("[] <> () [ ]", ""),
    ],
)
def test_clean_preview(body: str, expected: str) -> None:
    assert clean_preview(body) == expected


def test_clean_preview_is_cut_after_cleaning_and_never_raises() -> None:
    assert len(clean_preview("word " * 200)) == 160 and clean_preview("x" * 500, 20) == "x" * 20
    assert clean_preview("https://a.b/" + "x" * 100000 + " visible") == "visible"  # a huge address is removed, the text after it stays
    for hostile in ["\x00" * 50, "-" * 100000, "[" * 100000, "http://" * 5000, "\ud800", "<<<<>>>>" * 1000]:
        assert isinstance(clean_preview(hostile), str)
