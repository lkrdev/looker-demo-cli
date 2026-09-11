"""``demo-create ge`` -- Looker Gemini Enterprise integration."""

from __future__ import annotations

from typing import Annotated

import typer

from looker_demo_cli.commands.agent import publish_agent
from looker_demo_cli.commands.options import StateFileOption
from looker_demo_cli.config import DEFAULT_GCP_PROJECT
from looker_demo_cli.context import get_context
from looker_demo_cli.error_boundary import ErrorHandlingGroup
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

ge_app = typer.Typer(
    name="ge",
    help="Inspect and configure Looker Gemini Enterprise (GE) integration.",
    no_args_is_help=True,
    cls=ErrorHandlingGroup,
)


@ge_app.command(name="status")
def ge_status(
    ctx: typer.Context,
    instance_url: Annotated[str | None, typer.Option("--instance", help="Looker instance base URL")] = None,
    account: Annotated[str | None, typer.Option("--looker-account", help="Saved Looker OAuth account alias")] = None,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
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
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)
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
            gate=5,
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
    gcp_project: Annotated[
        str, typer.Option("--gcp-project", help="Target Google Cloud Project ID")
    ] = DEFAULT_GCP_PROJECT,
    instance_url: Annotated[str | None, typer.Option("--instance", help="Looker instance base URL")] = None,
    account: Annotated[str | None, typer.Option("--looker-account", help="Saved Looker OAuth account alias")] = None,
    app_id: Annotated[str | None, typer.Option("--app-id", help="Gemini Enterprise App/Engine ID")] = None,
    location: Annotated[str, typer.Option("--location", help="Gemini Enterprise Location/Region")] = "global",
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
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
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)
    auth = app_ctx.looker_auth(instance_url, account)

    # A purpose-built state rather than the persisted one: this command
    # configures the *instance*, and reusing on-disk state would let a stale
    # ge_instance_id from a previous demo leak into the payload.
    state = FlowState(
        gcp_project_id=gcp_project,
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
            details={"gcp_project": gcp_project, "app_id": app_id, "location": location},
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
        gate=5,
        requires_human_confirmation=True,
    )

    def render(_: CommandResult) -> None:
        print_success(
            f"Gemini Enterprise configured for app `{updated_state.ge_instance_id}` "
            f"on project `{updated_state.gcp_project_id}`."
        )

    return emit(result, json_output=output_json, human_renderer=render)


@ge_app.command(name="publish")
def ge_publish(
    ctx: typer.Context,
    agent_id: Annotated[str | None, typer.Option("--agent-id", help="Target CA Agent ID to publish")] = None,
    non_interactive: Annotated[
        bool,
        typer.Option("--non-interactive", help="Run non-interactively without prompting for GE reconfigurations"),
    ] = False,
    account: Annotated[
        str | None,
        typer.Option("--looker-account", help="Saved Looker OAuth account alias"),
    ] = None,
    instance_url: Annotated[str | None, typer.Option("--instance", help="Looker instance base URL")] = None,
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Emit the result envelope as JSON on stdout"),
    ] = False,
    state_file: StateFileOption = None,
):
    """Verify Gemini Enterprise configuration and publish a CA agent to it (Gate 5).

    An alias for :func:`~looker_demo_cli.commands.agent.agent_publish`, kept
    because Gate 5 of the orchestration flow is about Gemini Enterprise and
    looking for the verb under ``ge`` is the natural instinct.

    Both spellings now share one implementation rather than one calling the
    other as a Python function -- an arrangement that held together only
    because the two declared identical options in identical order, and that
    reported ``"agent publish"`` in this command's *success* envelope while its
    *failure* envelope said ``"ge publish"``.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        agent_id: Target CA agent. Falls back to the agent in prior state.
        non_interactive: Never prompt during GE configuration.
        account: Saved ``lkr`` OAuth account alias.
        instance_url: Looker instance base URL.
        output_json: Emit the JSON envelope on stdout.
        state_file: Optional explicit path to ``.demo-state.json``.

    Raises:
        ConfigError: No agent ID could be resolved.
        AuthError: No usable Looker credentials.
        RemoteApiError: Gemini Enterprise rejected the publish.
    """
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)
    return publish_agent(
        app_ctx,
        command="ge publish",
        agent_id=agent_id,
        non_interactive=non_interactive,
        account=account,
        instance_url=instance_url,
        output_json=output_json,
    )
