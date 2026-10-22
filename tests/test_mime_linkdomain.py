import pytest

from chrono_mail.mime.linkdomain import MAXIMUM_LINK_LENGTH, leads_elsewhere, link_domain, main_domain

# --- Version ------------------------------------------------------------
__version__ = "test_mime_linkdomain.py 1.5.257"

# --- Version history ----------------------------------------------------
# v1.5.257: Initial: the main domain of a link and of the sender, and when a link gets the warning (D-215)


def test_the_main_domain_is_the_part_someone_registers() -> None:
    assert main_domain("business.alianz.nl") == main_domain("alianz.nl") == "alianz.nl"  # the owner's example
    assert main_domain("www.Example.CO.UK.") == "example.co.uk"
    assert main_domain("co.uk") is None and main_domain("localhost") is None and main_domain("") is None
    assert main_domain("a" * 300 + ".com") is None  # longer than DNS allows


@pytest.mark.parametrize(("link", "sender"), [
    ("https://business.alianz.nl/policy", "service@alianz.nl"),
    ("https://alianz.nl", "Info@Mail.Alianz.NL"),
    ("https://www.example.co.uk/invoice?id=1", "billing@mail.example.co.uk"),
    ("mailto:help@support.example.com?subject=hi", "news@example.com"),
])
def test_the_same_main_domain_gives_no_warning(link: str, sender: str) -> None:
    assert not leads_elsewhere(link, sender)


@pytest.mark.parametrize(("link", "sender"), [
    ("https://example.co.uk.evil.com/login", "billing@example.co.uk"),  # looks like the sender's, is evil.com
    ("https://paypa1.com/", "service@paypal.com"),
    ("http://192.168.1.1/admin", "it@example.com"),  # a bare number address
    ("http://[2001:db8::1]/", "it@example.com"),
    ("https://xn--pypal-4ve.com/", "service@paypal.com"),  # look-alike letters, compared in plain letters
    ("https://рaypal.com/", "service@paypal.com"),  # the first letter is Cyrillic
    ("mailto:someone@other.org", "news@example.com"),
    ("https://example.com/", ""),  # no sender address: the same domain cannot be confirmed
    ("https://intranet/", "it@example.com"),  # no main domain to compare
    ("http://host:port/", "it@example.com"),
])
def test_another_main_domain_gives_the_warning(link: str, sender: str) -> None:
    assert leads_elsewhere(link, sender)


@pytest.mark.parametrize("link", ["#top", "cid:picture1@x", "/relative/path", "", "https://[::1", "javascript:void(0)"])
def test_a_link_without_a_domain_never_gets_the_warning(link: str) -> None:
    assert link_domain(link) is None
    assert not leads_elsewhere(link, "someone@example.com")


def test_a_hostile_link_is_capped() -> None:
    assert link_domain("https://example.com/" + "a" * (MAXIMUM_LINK_LENGTH * 2)) == "example.com"
