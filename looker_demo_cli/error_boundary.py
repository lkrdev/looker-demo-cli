"""The CLI's single exception boundary.

Lives in its own module so that every command group can install it without
importing :mod:`looker_demo_cli.cli`, which imports them.
"""

from __future__ import annotations

from typing import Any

import typer

# Typer vendors a fork of Click; the context passed to `Group.invoke` is
# `typer._click.core.Context`, NOT `click.Context`. Annotating with the latter
# is an incompatible override and mypy will (correctly) reject it.
from typer._click.core import Context as TyperContext
from typer.core import TyperGroup

from looker_demo_cli.errors import DemoCreateError
from looker_demo_cli.output import CommandResult, emit, is_json_mode


class ErrorHandlingGroup(TyperGroup):
    """Typer group that converts structured errors into the output envelope.

    Any :class:`~looker_demo_cli.errors.DemoCreateError` raised anywhere beneath
    a command surfaces here, is rendered in whichever format the command was
    invoked with, and exits with that error's dedicated code.

    Implemented as a group class rather than a wrapper around the console
    entrypoint so that ``CliRunner`` -- which invokes the group directly --
    exercises the same path the installed ``demo-create`` binary does.
    Otherwise the handler would be untestable.

    Installed on every group, root and nested. The innermost group wins, which
    is what lets :meth:`_command_path` report ``"data inspect"`` rather than
    just ``"data"``. Because the handler re-raises ``typer.Exit`` rather than
    the original error, an outer group never double-handles it.

    .. warning::
       A new sub-``Typer`` added *without* ``cls=ErrorHandlingGroup`` still has
       its errors caught -- by the root group -- but they are labelled with the
       group name instead of the full command path. That is a quiet failure,
       not a loud one.
    """

    @staticmethod
    def _command_path(ctx: TyperContext) -> str:
        """Reconstruct the command path an error occurred under.

        ``ctx.command_path`` is the *group* path including the program name
        (e.g. ``"demo-create data"``); the leaf lives in ``invoked_subcommand``.
        Joining them and dropping the program name yields the same string the
        command's own success envelope reports, so a failure that is raised and
        one that is returned agree on what to call themselves.
        """
        parts = ctx.command_path.split()[1:]
        if ctx.invoked_subcommand:
            parts.append(ctx.invoked_subcommand)
        return " ".join(parts) or ctx.info_name or "demo-create"

    def invoke(self, ctx: TyperContext) -> Any:
        """Invoke the subcommand, converting structured errors to envelopes."""
        try:
            return super().invoke(ctx)
        except DemoCreateError as exc:
            result = CommandResult.failure(self._command_path(ctx), exc)
            # exit_on_failure=False: we raise typer.Exit ourselves so the exit
            # code comes from the exception rather than the envelope's fallback.
            emit(result, json_output=is_json_mode(), exit_on_failure=False)
            raise typer.Exit(code=exc.exit_code) from exc
