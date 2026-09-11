"""``demo-create embed`` -- standalone embedded-analytics portal scaffolding."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from looker_demo_cli.commands.options import StateFileOption
from looker_demo_cli.context import get_context
from looker_demo_cli.error_boundary import ErrorHandlingGroup
from looker_demo_cli.errors import missing_option
from looker_demo_cli.generators.embed_scaffolder import EmbedConfigOptions, EmbedScaffolder
from looker_demo_cli.output import CommandResult, emit
from looker_demo_cli.utils.console import print_info, print_success

embed_app = typer.Typer(
    name="embed",
    help="Scaffold standalone Embedded Analytics web applications and portals.",
    no_args_is_help=True,
    cls=ErrorHandlingGroup,
)


@embed_app.command(name="scaffold")
def embed_scaffold(
    ctx: typer.Context,
    looker_project: Annotated[
        str | None,
        typer.Option("--looker-project", help="Looker/demo project name"),
    ] = None,
    target_dir: Annotated[
        Path | None, typer.Option("--target-dir", help="Directory where the web app will be scaffolded")
    ] = None,
    dashboard_id: Annotated[str | None, typer.Option("--dashboard-id", help="Looker dashboard ID to embed")] = None,
    brand_name: Annotated[str | None, typer.Option("--brand-name", help="Customer brand display name")] = None,
    instance_url: Annotated[str | None, typer.Option("--instance", help="Looker instance URL")] = None,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Scaffold a full-stack React/Vite analytics embed portal workspace.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        looker_project: Looker/demo project name, used for naming and defaults.
            Spelled ``--looker-project`` rather than ``--project``, which read
            as a Google Cloud project next to ``--gcp-project`` elsewhere in
            the CLI.
        target_dir: Where to scaffold the workspace.
        dashboard_id: Looker dashboard to embed on the portal's home view.
        brand_name: Customer brand display name.
        instance_url: Looker instance URL baked into the generated ``.env``.
        output_json: Emit the JSON envelope on stdout.
        state_file: Optional explicit path to ``.demo-state.json``.

    Returns:
        The emitted result envelope.

    Raises:
        ConfigError: No Looker project could be resolved.
    """
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)
    state = app_ctx.state
    proj_name = looker_project or state.looker_project_name
    if not proj_name:
        # Everything below is named after this: the workspace directory, the
        # brand, the default dashboard ID, and the model the portal embeds.
        raise missing_option(
            "--looker-project",
            purpose="the demo project the portal is scaffolded around",
            hint="Or run `demo-create lookml model` first, which records the project name it generated.",
        )
    inst_url = instance_url or state.looker_instance_url
    dash_id = dashboard_id or state.deployed_dashboard_id or f"{proj_name}::{proj_name}_overview"
    b_name = brand_name or proj_name.replace("_", " ").title()

    dest = target_dir or (Path.home() / f"looker-embed-{proj_name}")

    opts = EmbedConfigOptions(
        demo_name=proj_name,
        target_dir=dest,
        brand_name=b_name,
        brand_title=f"{b_name} Intelligence Portal",
        looker_instance_url=inst_url,
        looker_project_name=proj_name,
        lookml_model_name=state.lookml_model_name or proj_name,
        dashboard_id=dash_id,
    )

    scaffolded_dir = EmbedScaffolder.scaffold_demo_workspace(opts)
    state.embed_workspace_dir = scaffolded_dir
    state.embed_portal_url = "http://localhost:8008"
    state.demo_scope = "external_embed"
    saved_path = app_ctx.save_state()

    result = CommandResult.success(
        "embed scaffold",
        data={
            "workspace_dir": str(scaffolded_dir),
            "portal_url": state.embed_portal_url,
            "looker_project": proj_name,
            "brand_name": b_name,
            "dashboard_id": dash_id,
            "instance_url": inst_url,
            "state_file": str(saved_path),
        },
    )

    def render(_: CommandResult) -> None:
        print_success(f"External Embed Portal configured at: `{scaffolded_dir}`")
        print_info(f"Updated state saved to `{saved_path}`")

    return emit(result, json_output=output_json, human_renderer=render)
