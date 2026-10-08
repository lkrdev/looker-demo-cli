"""Exception classes for looker-demo-cli.

Provides unified exports matching errors.py for compatibility.
"""

from __future__ import annotations

from looker_demo_cli.errors import (
    ALL_ERROR_TYPES,
    USAGE_EXIT_CODE,
    AuthError,
    ConfigError,
    DemoCreateError,
    RemoteApiError,
    StateError,
    ValidationError,
    looker_not_authenticated,
    missing_option,
    no_looker_instance,
)

__all__ = [
    "ALL_ERROR_TYPES",
    "USAGE_EXIT_CODE",
    "AuthError",
    "ConfigError",
    "DemoCreateError",
    "RemoteApiError",
    "StateError",
    "ValidationError",
    "looker_not_authenticated",
    "missing_option",
    "no_looker_instance",
]
