"""``demo-create status`` -- where the build is, and the exact next command.

This is the command that makes the CLI self-describing. An orchestrating agent
runs it, reads ``next_command``, branches on ``requires_human_confirmation``,
and never needs the workflow prose at all::

    demo-create status --json | jq -r '.data.next_command'

All of the reasoning lives in :mod:`looker_demo_cli.gates`, which is pure. This
module is the I/O shell: load the state, ask the gate model, emit the envelope.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.table import Table

from looker_demo_cli.commands.options import StateFileOption
from looker_demo_cli.context import get_context
from looker_demo_cli.gates import GateStatus, current_gate, evaluate_gates
from looker_demo_cli.output import CommandResult, emit
from looker_demo_cli.state import get_default_state_path
from looker_demo_cli.utils.console import console, print_success

#: Leading marker per gate row. A glyph rather than a word so the three states
#: are distinguishable at a glance in a six-row table.
_MARKER_COMPLETE = "[green]✓[/green]"
_MARKER_CURRENT = "[bold yellow]▶[/bold yellow]"
_MARKER_PENDING = "[dim]·[/dim]"


def register(app: typer.Typer) -> None:
    """Register this module's root-level command on the given app.

    ``status`` sits directly under ``demo-create`` rather than in a command
    group, so there is no ``typer.Typer`` to hand to ``add_typer``. Registering
    here rather than decorating at definition keeps the module free of a
    module-level dependency on the root app, which would be a circular import.

    Args:
        app: The root ``demo-create`` Typer application.
    """
    app.command(name="status")(status)


def _build_data(statuses: list[GateStatus], state_file: str) -> dict[str, Any]:
    """Assemble the machine-readable payload.

    Args:
        statuses: Every gate judged against the loaded state.
        state_file: The resolved ``.demo-state.json`` path, reported so a
            caller can tell *which* state produced this answer -- the file is
            discovered from the working directory, so two invocations in
            different directories legitimately disagree.

    Returns:
        The envelope's ``data`` payload.
    """
    current = next((s for s in statuses if s.is_current), None)
    return {
        "gates": [s.to_dict() for s in statuses],
        "completed_gates": [s.gate.id for s in statuses if s.complete],
        "current_gate": current.to_dict() if current else None,
        "next_command": current.command if current else None,
        # Hoisted out of `current_gate` so the one field an orchestrator
        # branches on to decide whether to call ask_question is top level, and
        # is a plain bool even when the pipeline is finished.
        "requires_human_confirmation": bool(current and current.gate.requires_human_confirmation),
        "human_checkpoint": current.gate.human_checkpoint if current else None,
        "state_file": state_file,
        "is_complete": current is None,
    }


def _render(statuses: list[GateStatus], state_file: str, *, is_complete: bool) -> None:
    """Render the six-gate table to stderr.

    Deliberately does not print the next command: :func:`emit` prints every
    ``next_actions`` entry immediately after this returns, and the command
    appearing twice reads like two different instructions.

    Args:
        statuses: Every gate judged against the loaded state.
        state_file: The resolved state file path, shown as the table caption.
        is_complete: Whether every gate is done.
    """
    table = Table(show_header=True, header_style="bold blue", caption=f"state: {state_file}")
    table.add_column("", width=1)
    table.add_column("Gate", style="dim", justify="right")
    table.add_column("Stage")
    table.add_column("Status")
    table.add_column("Human gate")

    for status in statuses:
        if status.complete:
            marker, verdict = _MARKER_COMPLETE, "[green]complete[/green]"
        elif status.is_current:
            marker, verdict = _MARKER_CURRENT, "[bold yellow]current[/bold yellow]"
        else:
            marker, verdict = _MARKER_PENDING, "[dim]pending[/dim]"
        human = "[yellow]confirm[/yellow]" if status.gate.requires_human_confirmation else "[dim]--[/dim]"
        table.add_row(marker, str(status.gate.number), status.gate.title, verdict, human)

    console.print(table)

    if is_complete:
        print_success("All gates complete. The demo is deployed and published.")


def status(
    ctx: typer.Context,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Report the completed gates, the current gate, and the exact next command.

    Read-only: it loads ``.demo-state.json`` and writes nothing back, so it is
    safe to call between every other command.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        output_json: Emit the JSON envelope on stdout instead of a Rich table.
        state_file: Optional explicit path to ``.demo-state.json``.

    Returns:
        The emitted result envelope.
    """
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)

    state = app_ctx.state
    # `AppContext.state_path` is None when the location was discovered rather
    # than passed, so re-run the same discovery the load used instead of
    # reporting a null path the caller cannot act on.
    resolved_state_file = str(app_ctx.state_path or get_default_state_path())

    statuses = evaluate_gates(state)
    current = current_gate(state)
    data = _build_data(statuses, resolved_state_file)

    result = CommandResult.success("status", data=data)
    if current is not None:
        result.add_next_action(
            f"Gate {current.number}: {current.title}",
            current.command(state),
            gate=current.number,
            requires_human_confirmation=current.requires_human_confirmation,
        )

    def render(_: CommandResult) -> None:
        _render(statuses, resolved_state_file, is_complete=current is None)

    return emit(result, json_output=output_json, human_renderer=render)
