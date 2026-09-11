"""The single output contract for every ``demo-create`` command.

Phase 1 characterization found that ``--json`` was unusable on most commands:
Rich banners were printed to stdout ahead of the payload, so ``json.loads()``
failed outright; five commands had no ``--json`` at all; and two incompatible
error envelopes were in circulation.

This module fixes that with one rule:

.. important::
   **stdout carries the JSON envelope and nothing else. All human-readable
   output goes to stderr.**

Because the streams are separated rather than interleaved, this always holds::

    demo-create <any command> --json | jq .

and this still shows a human the progress narration::

    demo-create <any command> --json > result.json

Envelope shape
--------------

.. code-block:: json

    {
      "schema_version": 1,
      "command": "lookml deploy",
      "status": "SUCCESS",
      "data": {"...": "command-specific payload"},
      "errors": [],
      "warnings": [],
      "next_actions": [
        {
          "description": "Provision the Conversational Analytics agent",
          "command": "demo-create agent create --model retail --explore orders",
          "gate": 4,
          "requires_human_confirmation": true
        }
      ]
    }

``next_actions`` is what lets an orchestrator walk the pipeline without
re-reading a prompt file: each command states the literal next command to run
and whether a human must approve first. Phase 5 extends this with a
gate-aware ``demo-create status``.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any, Literal

import typer
from pydantic import BaseModel, Field

from looker_demo_cli.errors import ALL_ERROR_TYPES, DemoCreateError

#: Bumped only on a breaking change to the envelope itself, never for a change
#: to a command's ``data`` payload.
ENVELOPE_SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Output mode
# ---------------------------------------------------------------------------
#
# The top-level error handler needs to know whether the *currently executing*
# command was invoked with --json, so a raised exception is rendered in the
# same format a successful result would have been. The flag lives in a
# ContextVar rather than a module global so it cannot leak across concurrently
# executing commands.
#
# Also mirrored on `AppContext.set_json_mode`, which is what commands call.
# The ContextVar remains the transport because the error boundary must know the
# output mode even when a command raises before its context is resolved.
_JSON_MODE: ContextVar[bool] = ContextVar("demo_create_json_mode", default=False)


def set_json_mode(enabled: bool) -> None:
    """Record whether the current command should emit JSON."""
    _JSON_MODE.set(enabled)


def is_json_mode() -> bool:
    """Whether the current command was invoked with ``--json``."""
    return _JSON_MODE.get()


Status = Literal["SUCCESS", "FAILED", "PARTIAL", "DRY_RUN", "BLOCKED"]

#: Statuses that must produce a non-zero exit code.
#:
#: ``PARTIAL`` is included deliberately. Phase 1 found `clean-root` returning
#: ``PARTIAL`` after failed deletions while exiting 0 and printing a green
#: success line -- an orchestrator chaining on `&&` treated a partial failure
#: as a success.
FAILURE_STATUSES: frozenset[str] = frozenset({"FAILED", "PARTIAL", "BLOCKED"})


class ErrorDetail(BaseModel):
    """One structured failure inside an envelope."""

    code: str = Field(description="Stable machine-readable identifier, e.g. AUTH_ERROR.")
    message: str = Field(description="Human-readable description of the failure.")
    remediation: str | None = Field(default=None, description="The concrete next step to resolve it.")
    details: dict[str, Any] = Field(default_factory=dict, description="Structured context.")


class NextAction(BaseModel):
    """A suggested follow-up step, addressed to an AI orchestrator."""

    description: str = Field(description="What this step accomplishes.")
    command: str | None = Field(default=None, description="The literal shell command to run.")
    gate: int | None = Field(default=None, description="Workflow gate number, if applicable.")
    requires_human_confirmation: bool = Field(
        default=False,
        description="Whether the orchestrator must prompt the user before proceeding.",
    )


class CommandResult(BaseModel):
    """The value every command returns, and the shape ``--json`` emits."""

    schema_version: int = ENVELOPE_SCHEMA_VERSION
    command: str = Field(description="Dotted command path, e.g. 'lookml deploy'.")
    status: Status = "SUCCESS"
    data: dict[str, Any] = Field(default_factory=dict, description="Command-specific payload.")
    errors: list[ErrorDetail] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether this result represents an unqualified success."""
        return self.status not in FAILURE_STATUSES

    @property
    def exit_code(self) -> int:
        """The process exit code implied by this result.

        Uses the first structured error's code when present, so that a failure
        surfaced through the envelope exits identically to the same failure
        raised as an exception.
        """
        if self.ok:
            return 0
        for error in self.errors:
            mapped = _EXIT_CODE_BY_ERROR_CODE.get(error.code)
            if mapped is not None:
                return mapped
        return 1

    # -- construction helpers ------------------------------------------------

    @classmethod
    def success(
        cls,
        command: str,
        data: dict[str, Any] | None = None,
        *,
        warnings: list[str] | None = None,
        next_actions: list[NextAction] | None = None,
    ) -> CommandResult:
        """Build a successful result."""
        return cls(
            command=command,
            status="SUCCESS",
            data=data or {},
            warnings=warnings or [],
            next_actions=next_actions or [],
        )

    @classmethod
    def failure(
        cls,
        command: str,
        error: DemoCreateError | ErrorDetail,
        *,
        status: Status = "FAILED",
        data: dict[str, Any] | None = None,
    ) -> CommandResult:
        """Build a failed result from a structured error."""
        detail = ErrorDetail(**error.to_dict()) if isinstance(error, DemoCreateError) else error
        return cls(command=command, status=status, data=data or {}, errors=[detail])

    def add_next_action(
        self,
        description: str,
        command: str | None = None,
        *,
        gate: int | None = None,
        requires_human_confirmation: bool = False,
    ) -> CommandResult:
        """Append a follow-up step. Returns ``self`` for chaining."""
        self.next_actions.append(
            NextAction(
                description=description,
                command=command,
                gate=gate,
                requires_human_confirmation=requires_human_confirmation,
            )
        )
        return self


_EXIT_CODE_BY_ERROR_CODE: dict[str, int] = {error_type.code: error_type.exit_code for error_type in ALL_ERROR_TYPES}


def render_json(result: CommandResult) -> str:
    """Serialize an envelope to the exact string written to stdout."""
    return json.dumps(result.model_dump(mode="json"), indent=2)


def emit(
    result: CommandResult,
    *,
    json_output: bool,
    human_renderer: Any = None,
    exit_on_failure: bool = True,
) -> CommandResult:
    """Emit a result and, by default, exit with its implied code.

    This is the only place in the codebase permitted to write to stdout.

    Args:
        result: The envelope to emit.
        json_output: When true, write JSON to stdout. When false, invoke
            ``human_renderer`` (which writes Rich output to stderr).
        human_renderer: Callable taking ``result``, used when ``json_output``
            is false. When omitted, a default summary is rendered.
        exit_on_failure: When true, raise ``typer.Exit`` with
            :attr:`CommandResult.exit_code` for a failing result. Set false
            when the caller needs to keep going -- for example, one command
            delegating to another.

    Raises:
        typer.Exit: When the result is a failure and ``exit_on_failure`` is set.

    Returns:
        The result, so callers can inspect it when not exiting.
    """
    if json_output:
        # typer.echo, not print(): it handles encoding correctly and is the
        # documented Click-native way to write to stdout.
        typer.echo(render_json(result))
    else:
        if human_renderer is not None:
            human_renderer(result)
        else:
            _render_default(result)
        # Rendered here rather than inside _render_default so that a command
        # supplying its own Rich rendering still surfaces its follow-up steps.
        for action in result.next_actions:
            _print_next_action(action)

    if exit_on_failure and not result.ok:
        raise typer.Exit(code=result.exit_code)
    return result


def _render_default(result: CommandResult) -> None:
    """Render a minimal human summary to stderr.

    Used by commands that have no bespoke Rich rendering. Imported lazily so
    this module stays importable without pulling in the console singleton.
    """
    from looker_demo_cli.utils.console import print_error, print_success, print_warning

    for warning in result.warnings:
        print_warning(warning)

    if result.ok:
        print_success(f"{result.command}: {result.status}")
    else:
        for error in result.errors:
            print_error(error.message)
            if error.remediation:
                print_warning(f"  → {error.remediation}")
        if not result.errors:
            print_error(f"{result.command}: {result.status}")


def _print_next_action(action: NextAction) -> None:
    """Print a single suggested follow-up step to stderr."""
    from looker_demo_cli.utils.console import console

    suffix = " [dim](requires confirmation)[/dim]" if action.requires_human_confirmation else ""
    console.print(f"[bold blue]→[/bold blue] {action.description}{suffix}")
    if action.command:
        console.print(f"    [bold white]$ {action.command}[/bold white]")
