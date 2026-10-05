"""``demo-create embed`` -- standalone embedded-analytics portal scaffolding."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from looker_demo_cli.commands.options import StateFileOption
from looker_demo_cli.context import get_context
from looker_demo_cli.error_boundary import ErrorHandlingGroup
from looker_demo_cli.errors import missing_option
from looker_demo_cli.gates import _resolve_primary_explore, attach_next_gate_action
from looker_demo_cli.generators.embed_scaffolder import (
    EmbedConfigOptions,
    EmbedScaffolder,
    provision_embed_instance,
    resolve_service_account_credentials,
)
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
    agent_id: Annotated[str | None, typer.Option("--agent-id", help="Looker CA Agent ID to embed")] = None,
    brand_name: Annotated[str | None, typer.Option("--brand-name", help="Customer brand display name")] = None,
    instance_url: Annotated[str | None, typer.Option("--instance", help="Looker instance URL")] = None,
    client_id: Annotated[
        str | None,
        typer.Option(
            "--client-id",
            help="Looker API Service Account client ID for headless embed token generation",
        ),
    ] = None,
    client_secret: Annotated[
        str | None,
        typer.Option(
            "--client-secret",
            help="Looker API Service Account client secret for headless embed token generation",
        ),
    ] = None,
    account: Annotated[
        str | None,
        typer.Option("--looker-account", help="Saved Looker OAuth account alias"),
    ] = None,
    skip: Annotated[
        bool,
        typer.Option("--skip", help="Skip external embed portal scaffolding and complete the pipeline"),
    ] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Scaffold a full-stack React/Vite analytics embed portal workspace and provision Looker embed settings.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        looker_project: Looker/demo project name, used for naming and defaults.
            Spelled ``--looker-project`` rather than ``--project``, which read
            as a Google Cloud project next to ``--gcp-project`` elsewhere in
            the CLI.
        target_dir: Where to scaffold the workspace.
        dashboard_id: Looker dashboard to embed on the portal's home view.
        agent_id: Looker Conversational Analytics Agent ID to embed on /conversational-analytics.
        brand_name: Customer brand display name.
        instance_url: Looker instance URL baked into the generated ``.env``.
        client_id: Looker API Service Account client ID written to ``backend/.env``.
        client_secret: Looker API Service Account client secret written to ``backend/.env``.
        account: Saved ``lkr`` OAuth account alias for instance provisioning calls.
        skip: Skip external embed portal scaffolding and complete the pipeline.
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

    if skip:
        state.embed_status = "skipped"
        saved_path = app_ctx.save_state()
        result = CommandResult.success(
            "embed scaffold",
            data={
                "skipped": True,
                "embed_status": "skipped",
                "state_file": str(saved_path),
            },
        )
        attach_next_gate_action(result, state)
        return emit(
            result,
            json_output=output_json,
            human_renderer=lambda _: print_info(
                f"Skipped external embed portal scaffolding. Updated state saved to `{saved_path}`"
            ),
        )

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
    model_name = state.lookml_model_name or proj_name
    dash_id = dashboard_id or state.deployed_dashboard_id or f"{model_name}::{proj_name}_overview"
    resolved_agent_id = agent_id or state.ca_agent_id or ""
    b_name = brand_name or proj_name.replace("_", " ").title()
    primary_explore = _resolve_primary_explore(state) or model_name
    explore_path = f"{model_name}/{primary_explore}"

    resolved_cid, resolved_csecret = resolve_service_account_credentials(
        client_id,
        client_secret,
        interactive=not output_json,
    )

    headers: dict[str, str] = {}
    try:
        auth = app_ctx.looker_auth(inst_url, account)
        headers = auth.headers
        if not inst_url:
            inst_url = auth.base_url
    except Exception:
        pass

    dest = target_dir or (Path.home() / f"looker-embed-{proj_name}")

    opts = EmbedConfigOptions(
        demo_name=proj_name,
        target_dir=dest,
        brand_name=b_name,
        brand_title=f"{b_name} Intelligence Portal",
        looker_instance_url=inst_url,
        looker_project_name=proj_name,
        lookml_model_name=model_name,
        dashboard_id=dash_id,
        agent_id=resolved_agent_id,
        explore_path=explore_path,
        client_id=resolved_cid,
        client_secret=resolved_csecret,
        connection_name=state.looker_connection_name or "default_bigquery_connection",
    )

    provisioning = provision_embed_instance(opts, headers=headers)
    scaffolded_dir = EmbedScaffolder.scaffold_demo_workspace(opts)

    state.embed_workspace_dir = scaffolded_dir
    state.embed_status = "scaffolded"
    state.embed_portal_url = "http://localhost:8008"
    state.demo_scope = "external_embed"
    state.embed_group_id = opts.group_id
    state.embed_folder_id = opts.folder_id
    state.embed_themes_created = list(provisioning.themes_created)
    state.embed_dashboard_moved = provisioning.dashboard_moved
    state.embed_agent_shared = provisioning.agent_shared
    state.embed_allowlist_configured = provisioning.allowlist_configured
    saved_path = app_ctx.save_state()

    result = CommandResult.success(
        "embed scaffold",
        data={
            "workspace_dir": str(scaffolded_dir),
            "portal_url": state.embed_portal_url,
            "looker_project": proj_name,
            "brand_name": b_name,
            "dashboard_id": dash_id,
            "agent_id": resolved_agent_id,
            "instance_url": inst_url,
            "group_id": opts.group_id,
            "folder_id": opts.folder_id,
            "themes_created": list(provisioning.themes_created),
            "dashboard_moved": provisioning.dashboard_moved,
            "agent_shared": provisioning.agent_shared,
            "allowlist_configured": provisioning.allowlist_configured,
            "sa_credentials_configured": provisioning.sa_credentials_configured,
            "state_file": str(saved_path),
        },
        warnings=list(provisioning.warnings),
    )
    attach_next_gate_action(result, state)

    def render(_: CommandResult) -> None:
        print_success(f"External Embed Portal configured at: `{scaffolded_dir}`")
        print_info(f"Embed Group ID: `{opts.group_id}` | Shared Folder ID: `{opts.folder_id}`")
        print_info(f"Updated state saved to `{saved_path}`")
        print_info(f"To run the portal: cd {scaffolded_dir}/frontend && pnpm install && pnpm dev")

    return emit(result, json_output=output_json, human_renderer=render)
