"""``demo-create`` entrypoint -- command registration only, no business logic.

This module exists to assemble the CLI surface and nothing else. Every command
lives in :mod:`looker_demo_cli.commands`; every shared runtime concern lives in
:mod:`looker_demo_cli.context` (resolved dependencies) or
:mod:`looker_demo_cli.error_boundary` (the single error-to-exit-code boundary).

Keeping this file free of implementation is what makes the command surface
readable at a glance: the registration block below is the complete inventory of
what the CLI can do.
"""

from __future__ import annotations

from typing import Annotated

import typer

from looker_demo_cli.commands import env, precheck, status
from looker_demo_cli.commands.agent import agent_app
from looker_demo_cli.commands.data import data_app
from looker_demo_cli.commands.embed import embed_app
from looker_demo_cli.commands.env import env_app
from looker_demo_cli.commands.ge import ge_app
from looker_demo_cli.commands.lookml import lookml_app
from looker_demo_cli.error_boundary import ErrorHandlingGroup
from looker_demo_cli.utils.console import console

# Re-exported: `ErrorHandlingGroup` is part of this module's public surface
# because callers reason about the CLI's error contract through its entrypoint.
__all__ = ["ErrorHandlingGroup", "app", "main"]

app = typer.Typer(
    name="demo-create",
    help="End-to-end Looker demo creation orchestrator CLI for AI agents and developers.",
    no_args_is_help=True,
    cls=ErrorHandlingGroup,
)

# Registration order is the order commands appear in `--help`, which is the
# first thing both a human and an agent reads. It follows the gate sequence
# (audit -> data -> model -> agent -> publish) rather than alphabetical order.
#
# `status` comes first because it is the orientation command: it reports which
# gates are done and prints the literal next command, so an agent that reads
# only the first entry of `--help` still finds its way.
status.register(app)
precheck.register(app)
env.register_root(app)
app.add_typer(agent_app, name="agent")
app.add_typer(ge_app, name="ge")
app.add_typer(env_app, name="env")
app.add_typer(data_app, name="data")
app.add_typer(lookml_app, name="lookml")
app.add_typer(embed_app, name="embed")


def version_callback(value: bool):
    """Print the installed version and exit.

    Args:
        value: True when ``--version`` was passed.

    Raises:
        typer.Exit: Immediately after printing, so that ``--version`` works
            without also supplying a subcommand.
    """
    if value:
        from looker_demo_cli import __version__

        console.print(f"[bold cyan]looker-demo-cli[/bold cyan] version [bold green]{__version__}[/bold green]")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            "-v",
            help="Show CLI version and exit.",
            callback=version_callback,
            is_eager=True,
        ),
    ] = None,
):
    """End-to-end Looker demo creation orchestrator CLI for AI agents and developers.

    Args:
        version: Handled eagerly by :func:`version_callback`.
    """
    # Deliberately does not build the AppContext. `get_context` creates it on
    # first use so that `--help` and `--version` never touch credentials, and so
    # that a test injecting a pre-built context via `ctx.obj` is not overwritten.


if __name__ == "__main__":
    app()
