"""Structured error types for ``demo-create``.

Every failure the CLI can produce maps to one of the exceptions in this module,
and every exception maps to a distinct **process exit code**. This lets an AI
orchestrator branch on the outcome of a command without parsing prose:

.. code-block:: bash

    demo-create lookml deploy --looker-project retail
    case $? in
      0) echo "deployed" ;;
      3) echo "re-authenticate" ;;
      5) echo "Looker API is unhappy; retry" ;;
    esac

Exit code allocation
--------------------

======  =============================  ==========================================
Code    Meaning                        Raised by
======  =============================  ==========================================
``0``   Success                        --
``1``   Unexpected/unclassified error  Any uncaught exception
``2``   Usage error                    Click/Typer (reserved -- do not reuse)
``3``   Authentication or credentials  :class:`AuthError`
``4``   Configuration or arguments     :class:`ConfigError`
``5``   Remote API call failed         :class:`RemoteApiError`
``6``   Output failed validation       :class:`ValidationError`
``7``   Persisted state unusable       :class:`StateError`
======  =============================  ==========================================

``2`` is reserved by Click for argument-parsing failures. Nothing here may use
it, or callers could not distinguish "you typed the flag wrong" from a real
runtime failure.

Design notes
------------

Exceptions carry ``remediation`` -- the concrete next step a human or agent
should take. This replaces the previous pattern of embedding advice in an
error string, which made the advice unreadable to a machine. The single
handler in ``looker_demo_cli.cli`` converts any of these into the standard
JSON envelope; see :mod:`looker_demo_cli.output`.
"""

from __future__ import annotations

from typing import Any

# Reserved by Click for argument-parsing failures.
USAGE_EXIT_CODE = 2


class DemoCreateError(Exception):
    """Base class for every deliberate ``demo-create`` failure.

    Attributes:
        message: Human-readable description of what went wrong.
        remediation: The concrete next step to resolve it, if one is known.
        details: Structured context included verbatim in the JSON envelope.
        exit_code: Process exit code for this class of failure.
        code: Stable machine-readable identifier, e.g. ``AUTH_ERROR``.
    """

    exit_code: int = 1
    code: str = "ERROR"

    def __init__(
        self,
        message: str,
        *,
        remediation: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.remediation = remediation
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Render as the ``errors[]`` entry of the JSON envelope."""
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.remediation:
            payload["remediation"] = self.remediation
        if self.details:
            payload["details"] = self.details
        return payload


class AuthError(DemoCreateError):
    """Credentials are missing, expired, or insufficient.

    Covers both Google Cloud (ADC, gcloud) and Looker (OAuth, API key). Exit
    code ``3`` signals to an orchestrator that re-authentication -- not a
    retry -- is required.
    """

    exit_code = 3
    code = "AUTH_ERROR"


class ConfigError(DemoCreateError):
    """Required configuration is missing, ambiguous, or mutually inconsistent.

    Distinct from a usage error (exit ``2``): the arguments parsed fine, but
    the resulting configuration cannot be acted on -- for example, no dataset
    was supplied and none could be resolved from prior pipeline state.
    """

    exit_code = 4
    code = "CONFIG_ERROR"


class RemoteApiError(DemoCreateError):
    """A call to Looker, BigQuery, or Google Cloud failed.

    Attributes:
        status_code: HTTP status, when the failure came from a REST call.
    """

    exit_code = 5
    code = "REMOTE_API_ERROR"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        remediation: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = dict(details or {})
        if status_code is not None:
            merged.setdefault("status_code", status_code)
        super().__init__(message, remediation=remediation, details=merged)
        self.status_code = status_code


class ValidationError(DemoCreateError):
    """Generated or deployed artifacts failed verification.

    Raised when LookML validation reports errors or dashboard tile queries do
    not return HTTP 200 -- that is, the command ran but its output is not fit
    to promote to production.
    """

    exit_code = 6
    code = "VALIDATION_ERROR"


class StateError(DemoCreateError):
    """``.demo-state.json`` is missing, unreadable, or an incompatible version."""

    exit_code = 7
    code = "STATE_ERROR"


#: Every concrete error type, for documentation generation and tests.
ALL_ERROR_TYPES: tuple[type[DemoCreateError], ...] = (
    AuthError,
    ConfigError,
    RemoteApiError,
    ValidationError,
    StateError,
)


# ---------------------------------------------------------------------------
# Canonical messages
# ---------------------------------------------------------------------------
#
# Before Phase 2 the "no Looker instance configured" guard was duplicated
# across seven commands with three different human messages and two different
# JSON envelopes. These factories exist so that never happens again: there is
# exactly one spelling of each shared failure.


def no_looker_instance() -> AuthError:
    """The canonical "no Looker instance is configured" failure."""
    return AuthError(
        "No Looker instance URL configured.",
        remediation=("Authenticate with `lkr auth login`, or pass --instance <url> explicitly."),
    )


def looker_not_authenticated() -> AuthError:
    """The canonical "instance known, but no usable credentials" failure."""
    return AuthError(
        "Looker authentication required.",
        remediation=(
            "Run `lkr auth login` to start an OAuth session, or set LOOKERSDK_CLIENT_ID and LOOKERSDK_CLIENT_SECRET."
        ),
    )


def missing_option(option: str, *, purpose: str, hint: str | None = None) -> ConfigError:
    """A required value was neither passed nor resolvable from prior state.

    Args:
        option: The flag the user should supply, e.g. ``--dataset``.
        purpose: What the value is needed for, used in the message.
        hint: Optional additional recovery guidance.
    """
    remediation = f"Pass {option} explicitly."
    if hint:
        remediation = f"{remediation} {hint}"
    return ConfigError(
        f"No value for {option} ({purpose}), and none could be resolved from `.demo-state.json`.",
        remediation=remediation,
        details={"option": option},
    )
