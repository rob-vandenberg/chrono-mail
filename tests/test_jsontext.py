from collections.abc import Callable

import pytest

from chrono_mail.config import parse_config, parse_rules, parse_sidebar, parse_tags
from chrono_mail.core import ConfigurationError
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "test_jsontext.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: Initial: JSON nested without end is damaged JSON, never a crash (the review of 3.6.350, AGENTS.md 4.2)

DEEP: str = "[" * 100_000


def test_json_nested_without_end_is_damaged_json() -> None:
    with pytest.raises(ValueError, match="nested too deeply"):
        load_json(DEEP)
    assert load_json('{"a": [1]}') == {"a": [1]}


@pytest.mark.parametrize("parse", [parse_config, parse_rules, parse_sidebar, parse_tags], ids=["config", "rules", "sidebar", "tags"])
def test_a_settings_file_nested_without_end_is_a_damaged_file(parse: Callable[[str], object]) -> None:
    with pytest.raises((ConfigurationError, ValueError)):
        parse(DEEP)
