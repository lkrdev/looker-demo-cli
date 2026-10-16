"""Shared command lifecycle runner, skip handler, and ``@gated_command`` decorator.

Eliminates repeated command-layer ceremony across ``looker_demo_cli/commands/*.py``:
- Initializing :class:`~looker_demo_cli.context.AppContext` (``use_state_file``, ``set_json_mode``).
- Enforcing upstream precondition gates declaratively.
- Handling ``--skip`` uniformly across optional gates via :class:`SkipSpec` and :func:`handle_gate_skip`.
- Applying state updates, persisting ``.demo-state.json``, attaching ``next_actions``, and calling :func:`emit`.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer

from looker_demo_cli.context import AppContext, get_context
from looker_demo_cli.errors import StateError
from looker_demo_cli.gates import GATES, attach_next_gate_action
from looker_demo_cli.output import CommandResult, Status, emit
from looker_demo_cli.state import FlowState, GateLifecycle
from looker_demo_cli.utils.console import print_info

_GATES_BY_ID = {gate.id: gate for gate in GATES}


@dataclass(frozen=True)
class SkipSpec:
    """Declarative specification for ``--skip`` on an optional gate."""

    state_updates: dict[str, Any]
    message: str | Callable[[FlowState, Path], str]
    gate_id: str | None = None
    extra_data: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    include_state_file_notice: bool = True


@dataclass
class GateOutcome:
    """Structured return value from a ``@gated_command`` body."""

    data: dict[str, Any] = field(default_factory=dict)
    state_updates: dict[str, Any] = field(default_factory=dict)
    gate_id: str | None = None
    gate_status: GateLifecycle = GateLifecycle.COMPLETED
    gate_substage: str | None = None
    invalidate_downstream: tuple[str, ...] = ()
    status: Status = "SUCCESS"
    warnings: list[str] = field(default_factory=list)
    render: Callable[[CommandResult], None] | None = None
    save_state: bool = True
    attach_next_gate: bool = True
    print_state_saved: bool = True


def init_command_context(
    ctx: typer.Context | None,
    *,
    state_file: Path | None = None,
    output_json: bool = False,
) -> tuple[AppContext, FlowState]:
    """Initialize :class:`AppContext` for a CLI command and return ``(app_ctx, state)``."""
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)
    return app_ctx, app_ctx.state


def handle_gate_skip(
    app_ctx: AppContext,
    command_name: str,
    skip_spec: SkipSpec,
    *,
    output_json: bool = False,
) -> CommandResult:
    """Execute uniform ``--skip`` state transition, persistence, next-action attachment, and emit."""
    state = app_ctx.state
    for key, val in skip_spec.state_updates.items():
        setattr(state, key, val)
    if skip_spec.gate_id:
        state.mark_gate(skip_spec.gate_id, GateLifecycle.SKIPPED)

    saved_path = app_ctx.save_state()
    payload: dict[str, Any] = {
        "skipped": True,
        **skip_spec.state_updates,
        **skip_spec.extra_data,
        "state_file": str(saved_path),
    }
    result = CommandResult.success(
        command_name,
        data=payload,
        warnings=list(skip_spec.warnings),
    )
    attach_next_gate_action(result, state)

    def _render(_: CommandResult) -> None:
        if isinstance(skip_spec.message, str):
            suffix = f" Updated state saved to `{saved_path}`" if skip_spec.include_state_file_notice else ""
            print_info(f"{skip_spec.message}{suffix}")
        else:
            text = skip_spec.message(state, saved_path)
            print_info(text)

    return emit(result, json_output=output_json, human_renderer=_render)


def check_required_gates(state: FlowState, requires_gates: tuple[str, ...]) -> None:
    """Verify upstream required gates when running inside a tracked pipeline (``precheck_passed``)."""
    if not state.precheck_passed:
        return
    for req_id in requires_gates:
        gate = _GATES_BY_ID.get(req_id)
        if gate is not None and not gate.is_complete(state):
            raise StateError(
                f"Prerequisite Gate {gate.number} ({gate.id}: {gate.title}) is not complete.",
                remediation=f"Run `{gate.command(state)}` first.",
            )


def gated_command(
    app: typer.Typer,
    name: str,
    *,
    command_label: str | None = None,
    gate_id: str | None = None,
    requires_gates: tuple[str, ...] = (),
    skip_spec: SkipSpec | None = None,
) -> Callable[[Callable[..., GateOutcome | CommandResult]], Callable[..., CommandResult]]:
    """Register a Typer command with automatic context setup, ``--skip`` handling, state save, and ``emit``."""

    def decorator(fn: Callable[..., GateOutcome | CommandResult]) -> Callable[..., CommandResult]:
        @app.command(name=name)
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> CommandResult:
            ctx: typer.Context | None = kwargs.get("ctx") or (
                args[0] if args and isinstance(args[0], typer.Context) else None
            )
            state_file: Path | None = kwargs.get("state_file")
            output_json = bool(kwargs.get("output_json", False))
            skip = bool(kwargs.get("skip", False))

            app_ctx, state = init_command_context(ctx, state_file=state_file, output_json=output_json)
            cmd_name = command_label or (
                " ".join(ctx.command_path.split()[1:]) if ctx and getattr(ctx, "command_path", None) else name
            )

            check_required_gates(state, requires_gates)

            if skip and skip_spec is not None:
                effective_skip = (
                    skip_spec
                    if skip_spec.gate_id is not None or gate_id is None
                    else SkipSpec(
                        state_updates=skip_spec.state_updates,
                        message=skip_spec.message,
                        gate_id=gate_id,
                        extra_data=skip_spec.extra_data,
                        warnings=skip_spec.warnings,
                        include_state_file_notice=skip_spec.include_state_file_notice,
                    )
                )
                return handle_gate_skip(app_ctx, cmd_name, effective_skip, output_json=output_json)

            outcome = fn(*args, **kwargs)
            if isinstance(outcome, CommandResult):
                return outcome

            state = app_ctx.state
            for key, val in outcome.state_updates.items():
                setattr(state, key, val)

            target_gate_id = outcome.gate_id or gate_id
            if target_gate_id:
                state.mark_gate(
                    target_gate_id,
                    outcome.gate_status,
                    substage=outcome.gate_substage,
                    invalidate_downstream=outcome.invalidate_downstream,
                )

            payload = dict(outcome.data)
            saved_path_str: str | None = None
            if outcome.save_state:
                saved_path_str = str(app_ctx.save_state())
                payload.setdefault("state_file", saved_path_str)

            result = CommandResult(
                command=cmd_name,
                status=outcome.status,
                data=payload,
                warnings=list(outcome.warnings),
            )
            if outcome.attach_next_gate and result.ok:
                attach_next_gate_action(result, state)

            def _human_render(res: CommandResult) -> None:
                if outcome.render is not None:
                    outcome.render(res)
                if saved_path_str and outcome.print_state_saved:
                    print_info(f"Updated state saved to `{saved_path_str}`")

            return emit(result, json_output=output_json, human_renderer=_human_render)

        return wrapper

    return decorator
