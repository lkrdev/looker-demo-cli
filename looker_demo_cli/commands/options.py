"""Option types shared across command modules.

Defining a widely-repeated option once means its flag name, help text, and type
cannot drift between the seventeen commands that accept it -- which is exactly
what happened to ``--project`` before it was split into ``--gcp-project`` and
``--looker-project``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

#: Every command accepts ``--state-file``.
#:
#: The default is discovery (``.demo-state.json`` in the working directory, then
#: the scratch directory), which is what makes the gated workflow ergonomic: run
#: the gates in order from one directory and each inherits the last one's
#: answers. The flag exists for the case that breaks -- several agents building
#: different demos from the same directory would otherwise share one file and
#: overwrite each other's progress.
StateFileOption = Annotated[
    Path | None,
    typer.Option(
        "--state-file",
        help="Path to .demo-state.json. Defaults to discovering it in the current directory.",
    ),
]

#: Emit the command result envelope as JSON on stdout instead of human-readable Rich output on stderr.
JsonOption = Annotated[
    bool,
    typer.Option("--json", help="Emit the result envelope as JSON on stdout"),
]

#: Explicitly skip an optional workflow gate and advance the state machine to the next gate.
SkipOption = Annotated[
    bool,
    typer.Option("--skip", help="Skip this optional gate and advance to the next gate"),
]

#: Target Google Cloud Project ID for BigQuery and Dataplex operations.
GcpProjectOption = Annotated[
    str | None,
    typer.Option("--gcp-project", help="Target Google Cloud Project ID"),
]

#: Target BigQuery dataset ID where demo tables are created or inspected.
DatasetOption = Annotated[
    str | None,
    typer.Option("--dataset", help="Target BigQuery dataset ID"),
]

#: Looker project and model identifier used for LookML generation and deployment.
LookerProjectOption = Annotated[
    str | None,
    typer.Option("--looker-project", help="Looker project and model name"),
]

#: Saved Looker OAuth account alias managed by lkr-dev-cli.
LookerAccountOption = Annotated[
    str | None,
    typer.Option("--looker-account", help="Saved Looker OAuth account alias"),
]

#: Base URL of the target Looker instance.
LookerInstanceOption = Annotated[
    str | None,
    typer.Option("--instance", help="Looker instance base URL"),
]

#: Looker database connection name pointing at BigQuery.
ConnectionOption = Annotated[
    str | None,
    typer.Option("--connection", help="Looker database connection name"),
]

#: Local directory containing generated or customized LookML files.
LookmlDirOption = Annotated[
    Path | None,
    typer.Option("--lookml-dir", help="Directory containing LookML files"),
]
