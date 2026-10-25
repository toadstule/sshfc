#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""sshfc infrastructure.

Policies are normally TOML files (see ``sshfc.core.config``); this package's top level is also a Python API for
building and checking command specs directly.
"""

from sshfc.core.config import ConfigError, Policy, load_policy
from sshfc.core.parser import CommandRoot, InvalidCommandError, SpecError
from sshfc.core.spec import Command, Flag, Option, Positional, Validator
from sshfc.core.validators import CONTAINER_NAME, UNIT_NAME, GoTemplateFormat, JournalctlDate, Pattern

__all__ = [
    "CONTAINER_NAME",
    "UNIT_NAME",
    "Command",
    "CommandRoot",
    "ConfigError",
    "Flag",
    "GoTemplateFormat",
    "InvalidCommandError",
    "JournalctlDate",
    "Option",
    "Pattern",
    "Policy",
    "Positional",
    "SpecError",
    "Validator",
    "load_policy",
]
