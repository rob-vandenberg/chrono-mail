import pytest

from chrono_mail.mime.linkdomain import MAXIMUM_LINK_LENGTH, leads_elsewhere, link_domain, mailto_addresses, main_domain

# --- Version ------------------------------------------------------------
__version__ = "test_mime_linkdomain.py 4.3.503"

# --- Version history ----------------------------------------------------
# v4.3.503: a mailto link to one of the person's own addresses never gets the warning; to anyone else it does, also an address after its ? (D-215)
# v1.5.257: Initial: the main domain of a link and of the sender, and when a link gets the warning (D-215)


def test_the_main_domain_is_the_part_someone_registers() -> None:
    assert main_domain("business.example.nl") == main_domain("example.nl") == "example.nl"
    assert main_domain("www.Example.CO.UK.") == "example.co.uk"
    assert main_domain("co.uk") is None and main_domain("localhost") is None and main_domain("") is None
    assert main_domain("a" * 300 + ".com") is None  # longer than DNS allows


@pytest.mark.parametrize(("link", "sender"), [
    ("https://business.example.nl/policy", "service@example.nl"),
    ("https://example.nl", "Info@Mail.Example.NL"),
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


OWN: frozenset[str] = frozenset({"me@example.nl", "work@example.org"})


def test_a_mailto_link_to_ones_own_address_never_gets_the_warning() -> None:
    sender = "nick@example.be"
    assert not leads_elsewhere("mailto:me@example.nl", sender, OWN)  # a quoted signature in a reply (the owner)
    assert not leads_elsewhere("mailto:Me@Example.NL?subject=Hello", sender, OWN)  # capitals and a subject count the same
    assert leads_elsewhere("mailto:me@example.nl", sender)  # without the person's addresses: the rule of before


def test_a_mailto_link_to_someone_else_and_a_web_link_keep_the_warning() -> None:
    sender = "nick@example.be"
    assert leads_elsewhere("mailto:stranger@example.nl", sender, OWN)  # the same domain as one's own, another person
    assert leads_elsewhere("https://example.nl/me@example.nl", sender, OWN)  # a web link is judged by its domain only
    assert not leads_elsewhere("mailto:office@example.be", sender, OWN)  # the sender's own domain, as before


def test_a_mailto_link_to_ones_own_address_and_anyone_else_keeps_the_warning() -> None:
    sender = "nick@example.be"
    for link in ("mailto:me@example.nl,stranger@evil.test", "mailto:me@example.nl?cc=stranger@evil.test", "mailto:me@example.nl?BCC=stranger@evil.test"):
        assert leads_elsewhere(link, sender, OWN), link  # the new message would also go to someone else
    assert not leads_elsewhere("mailto:me@example.nl?cc=work@example.org&subject=Hi", sender, OWN)  # only one's own addresses


def test_every_address_a_mailto_link_writes_to() -> None:
    assert mailto_addresses("MAILTO:Me@Example.NL?subject=x") == {"me@example.nl"}
    assert mailto_addresses("mailto:a@example.nl,%20b@example.nl?cc=c@example.nl,d@example.nl&bcc=e@example.nl") == {f"{c}@example.nl" for c in "abcde"}
    assert mailto_addresses("https://example.nl/") == frozenset() and mailto_addresses("mailto:") == frozenset()
    assert len(mailto_addresses("mailto:" + "a," * MAXIMUM_LINK_LENGTH)) == 1  # a hostile link is capped


def test_a_mailto_link_with_its_addresses_after_the_question_mark_is_judged_by_them() -> None:
    sender = "nick@example.be"
    assert leads_elsewhere("mailto:?to=billing@lookalike.test", sender)  # it never warned (the owner: it should)
    assert leads_elsewhere("mailto:?cc=billing@lookalike.test", sender, OWN)
    assert leads_elsewhere("mailto:office@example.be?cc=billing@lookalike.test", sender)  # the sender's own domain, and one more
    assert not leads_elsewhere("mailto:?to=office@example.be", sender)  # the sender's own domain
    assert not leads_elsewhere("mailto:?to=me@example.nl", sender, OWN)
    assert not leads_elsewhere("mailto:?subject=Hello", sender)  # writes to no one: nowhere to lead
    assert leads_elsewhere("mailto:billing@lookalike.test;office@example.be", sender)  # a ; between the addresses
    assert leads_elsewhere("mailto://billing@lookalike.test", sender)  # written as a web address
    assert leads_elsewhere("mailto:office@example.be" + "," * MAXIMUM_LINK_LENGTH + "billing@lookalike.test", sender)  # hidden past the cap
    assert leads_elsewhere("mailto:billing@lookalike.test%20office@example.be", sender)  # no single address
    assert leads_elsewhere("mailto:billing@lookalike.test%3F@example.be", sender)
    assert leads_elsewhere("mailto://[/billing@lookalike.test", sender)  # damaged: it cannot be read, so it cannot be trusted
    assert not leads_elsewhere("mailto:office@example.be?to=office+tag@example.be&cc=Office%2Bx@example.be", sender)  # a + stays a +
