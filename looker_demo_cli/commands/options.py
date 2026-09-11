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
