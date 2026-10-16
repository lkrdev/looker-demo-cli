"""``demo-create ge`` -- Looker Gemini Enterprise integration."""

from __future__ import annotations

from typing import Annotated

import typer

from looker_demo_cli.commands.agent import agent_publish
from looker_demo_cli.commands.options import (
    GcpProjectOption,
    JsonOption,
    LookerAccountOption,
    LookerInstanceOption,
    StateFileOption,
)
from looker_demo_cli.commands.runner import init_command_context
from looker_demo_cli.config import DEFAULT_GCP_PROJECT
from looker_demo_cli.error_boundary import make_typer
from looker_demo_cli.errors import RemoteApiError
from looker_demo_cli.output import CommandResult, emit
from looker_demo_cli.services.ge_service import (
    ensure_gemini_enterprise_configured,
    get_looker_ge_config,
    is_ge_configured,
    render_ge_status_table,
)
from looker_demo_cli.state import FlowState
from looker_demo_cli.utils.console import print_success, print_warning

ge_app = make_typer(
    name="ge",
    help="Inspect and configure Looker Gemini Enterprise (GE) integration.",
)


@ge_app.command(name="status")
def ge_status(
    ctx: typer.Context,
    instance_url: LookerInstanceOption = None,
    account: LookerAccountOption = None,
    output_json: JsonOption = False,
    state_file: StateFileOption = None,
):
    """Fetch and display current Looker Gemini enablement and GE configuration.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        instance_url: Looker base URL. Falls back to the saved OAuth session.
        account: Saved ``lkr`` OAuth account alias to authenticate with.
        output_json: Emit the JSON envelope on stdout instead of a Rich table.
        state_file: Optional explicit path to ``.demo-state.json``.

    Raises:
        AuthError: No usable Looker credentials.
        RemoteApiError: Looker refused or failed the enablement lookup.
    """
    app_ctx, _state = init_command_context(ctx, state_file=state_file, output_json=output_json)
    auth = app_ctx.looker_auth(instance_url, account)

    config = get_looker_ge_config(auth.base_url, auth.headers)
    if not config:
        raise RemoteApiError(
            "Failed to retrieve Gemini enablement configuration from Looker.",
            remediation=(
                "Confirm the instance is reachable and the authenticated user holds the "
                "`gemini_in_looker` admin permission."
            ),
            details={"instance_url": auth.base_url},
        )

    configured = is_ge_configured(config)
    result = CommandResult.success(
        "ge status",
        data={"configured": configured, "instance_url": auth.base_url, "config": config},
    )
    if not configured:
        result.add_next_action(
            "Configure Gemini Enterprise in Looker",
            "demo-create ge configure --gcp-project <gcp-project-id>",
            gate=12,
            requires_human_confirmation=True,
        )

    def render(_: CommandResult) -> None:
        render_ge_status_table(config)
        if configured:
            print_success("Gemini Enterprise is fully configured in Looker.")
        else:
            print_warning("Gemini Enterprise is NOT fully configured.")

    return emit(result, json_output=output_json, human_renderer=render)


@ge_app.command(name="configure")
def ge_configure(
    ctx: typer.Context,
    gcp_project: GcpProjectOption = None,
    instance_url: LookerInstanceOption = None,
    account: LookerAccountOption = None,
    app_id: Annotated[str | None, typer.Option("--app-id", help="Gemini Enterprise App/Engine ID")] = None,
    location: Annotated[str, typer.Option("--location", help="Gemini Enterprise Location/Region")] = "global",
    output_json: JsonOption = False,
    state_file: StateFileOption = None,
):
    """Configure Gemini Enterprise settings in Looker and grant the required IAM role.

    Discovers Discovery Engine apps in the target project, patches Looker's
    ``gemini_enablement`` settings, and grants the Looker service account
    ``roles/discoveryengine.admin``.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        gcp_project: Google Cloud project hosting the Discovery Engine app.
        instance_url: Looker base URL. Falls back to the saved OAuth session.
        account: Saved ``lkr`` OAuth account alias to authenticate with.
        app_id: Gemini Enterprise engine ID. Discovered interactively if omitted.
        location: Gemini Enterprise region.
        output_json: Emit the JSON envelope on stdout. Implies non-interactive,
            since a prompt cannot be answered by a machine-readable caller.
        state_file: Optional explicit path to ``.demo-state.json``.

    Raises:
        AuthError: No usable Looker credentials.
        RemoteApiError: Looker or Discovery Engine rejected the configuration.
    """
    app_ctx, persisted_state = init_command_context(ctx, state_file=state_file, output_json=output_json)
    auth = app_ctx.looker_auth(instance_url, account)
    resolved_project = (
        gcp_project or persisted_state.ge_project_id or persisted_state.gcp_project_id or DEFAULT_GCP_PROJECT
    )

    # A purpose-built state rather than the persisted one: this command
    # configures the *instance*, and reusing on-disk state would let a stale
    # ge_instance_id from a previous demo leak into the payload.
    state = FlowState(
        gcp_project_id=resolved_project,
        looker_instance_url=auth.base_url,
        looker_account=account,
        ge_instance_id=app_id,
        ge_location=location,
    )

    updated_state = ensure_gemini_enterprise_configured(
        state=state,
        headers=auth.headers,
        # --json callers cannot answer a prompt, so never block on one.
        interactive=not output_json,
        allow_reconfigure=True,
    )

    if not updated_state.ge_configured:
        raise RemoteApiError(
            "Failed to complete Gemini Enterprise configuration.",
            remediation=(
                "Re-run with an explicit --app-id, or verify the Discovery Engine app "
                "exists in the target project and the Looker service account has "
                "roles/discoveryengine.admin."
            ),
            details={"gcp_project": resolved_project, "app_id": app_id, "location": location},
        )

    result = CommandResult.success(
        "ge configure",
        data={
            "gcp_project": updated_state.gcp_project_id,
            "app_id": updated_state.ge_instance_id,
            "location": updated_state.ge_location,
            "instance_url": auth.base_url,
            "configured": True,
        },
    ).add_next_action(
        "Publish the Conversational Analytics agent to Gemini Enterprise",
        "demo-create agent publish --agent-id <agent-id>",
        gate=12,
        requires_human_confirmation=True,
    )

    def render(_: CommandResult) -> None:
        print_success(
            f"Gemini Enterprise configured for app `{updated_state.ge_instance_id}` "
            f"on project `{updated_state.gcp_project_id}`."
        )

    return emit(result, json_output=output_json, human_renderer=render)


ge_publish = ge_app.command(name="publish")(agent_publish)
