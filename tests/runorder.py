"""The order of a normal test run, to show that no test depends on the ones before it (R-45).

    .venv/bin/python -m pytest -p tests.runorder --run-order reverse tests/ui/
    .venv/bin/python -m pytest -p tests.runorder --run-order shuffle --run-seed 7 tests/ui/
"""

import random

import pytest

# --- Version ------------------------------------------------------------
__version__ = "runorder.py 1.9.266"

# --- Version history ----------------------------------------------------
# v1.9.266: Initial order switch: normal, reverse, shuffle with a seed (R-45)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--run-order", choices=("normal", "reverse", "shuffle"), default="normal")
    parser.addoption("--run-seed", type=int, default=1)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    order = str(config.getoption("--run-order"))
    if order == "reverse":
        items.reverse()
    elif order == "shuffle":
        random.Random(int(config.getoption("--run-seed"))).shuffle(items)


def pytest_report_header(config: pytest.Config) -> str:
    return f"run order: {config.getoption('--run-order')}, seed {config.getoption('--run-seed')}"
