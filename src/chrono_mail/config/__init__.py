"""Settings, account definitions and credentials."""

import os

from chrono_mail.config.files import config_to_text, load_config, parse_config, save_config
from chrono_mail.config.model import (
    ACCOUNT_ID_PATTERN,
    AccountSettings,
    AppConfig,
    Pop3DeletePolicy,
    Pop3Options,
)
from chrono_mail.config.passwords import (
    InMemoryPasswordStore,
    KeyringPasswordStore,
    PasswordStore,
    ServerRole,
)
from chrono_mail.config.paths import AppDirectories, default_directories
from chrono_mail.config.rules import Action, Condition, Rule, RuleSet, load_rules, new_rule_id, parse_rules, rules_to_text, save_rules

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 0.0.33"

# --- Version history ----------------------------------------------------
# v0.0.33: Export the rules
# v0.0.9: Export the settings, the paths and the password stores
# v0.0.2: Initial skeleton


def new_account_id() -> str:
    """A random account id: 128 bits as 32 lower-case hex characters."""
    return os.urandom(16).hex()


__all__ = [
    "Action",
    "Condition",
    "Rule",
    "RuleSet",
    "load_rules",
    "new_rule_id",
    "parse_rules",
    "rules_to_text",
    "save_rules",
    "ACCOUNT_ID_PATTERN",
    "AccountSettings",
    "AppConfig",
    "AppDirectories",
    "InMemoryPasswordStore",
    "KeyringPasswordStore",
    "PasswordStore",
    "Pop3DeletePolicy",
    "Pop3Options",
    "ServerRole",
    "config_to_text",
    "default_directories",
    "load_config",
    "new_account_id",
    "parse_config",
    "save_config",
]
