"""Command groups, one module per ``demo-create`` subcommand group.

Each module owns exactly one ``typer.Typer`` and contains **no business
logic**: a command function resolves its inputs through
:class:`~looker_demo_cli.context.AppContext`, calls into ``services/`` or
``generators/``, wraps the outcome in a
:class:`~looker_demo_cli.output.CommandResult`, and hands it to
:func:`~looker_demo_cli.output.emit`.

==================  ==============================================
Module              Group
==================  ==============================================
:mod:`precheck`     ``demo-create pre-check`` (and ``skills``)
:mod:`data`         ``demo-create data …``
:mod:`lookml`       ``demo-create lookml …``
:mod:`agent`        ``demo-create agent …``
:mod:`ge`           ``demo-create ge …``
:mod:`embed`        ``demo-create embed …``
:mod:`env`          ``demo-create env …`` (and the script runners)
==================  ==============================================

:mod:`looker_demo_cli.cli` does nothing but import these and register them.
"""

from __future__ import annotations
