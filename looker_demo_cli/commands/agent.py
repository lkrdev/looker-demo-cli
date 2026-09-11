"""``demo-create agent`` -- Conversational Analytics agent provisioning."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from looker_demo_cli.commands.options import StateFileOption
from looker_demo_cli.context import AppContext, get_context
from looker_demo_cli.error_boundary import ErrorHandlingGroup
from looker_demo_cli.errors import RemoteApiError, missing_option
from looker_demo_cli.output import CommandResult, ErrorDetail, emit
from looker_demo_cli.services.agent_service import (
    extract_golden_queries_from_dashboard_id,
    provision_ca_agent,
    register_and_link_golden_queries,
)
from looker_demo_cli.services.ca_agent_service import (
    extract_golden_queries_from_dashboards,
    publish_agent_to_ge,
)
from looker_demo_cli.services.ge_service import ensure_gemini_enterprise_configured
from looker_demo_cli.state import FlowState
from looker_demo_cli.utils.console import print_error, print_info, print_success

agent_app = typer.Typer(
    name="agent",
    help="Provision Looker Conversational Analytics AI agents, ground golden queries, and publish to GE.",
    no_args_is_help=True,
    cls=ErrorHandlingGroup,
)


def _resolve_dashboard_dir(
    state: FlowState,
    dashboard_file: Path | None,
    dashboards_dir: Path | None,
) -> Path | None:
    """Pick the directory to mine for ``*.dashboard.lookml`` files.

    Precedence is explicit file, explicit directory, then the ``dashboards/``
    subdirectory of whatever LookML output directory prior state recorded.

    Args:
        state: Current flow state, consulted for the fallback location.
        dashboard_file: An explicit single dashboard file.
        dashboards_dir: An explicit directory of dashboard files.

    Returns:
        The directory to scan, or ``None`` if no candidate exists on disk.
        Callers that were given a *file* still receive its parent, because the
        extractor works on directories and filters afterwards.
    """
    candidate = (
        dashboard_file
        or dashboards_dir
        or (state.lookml_output_dir / "dashboards" if state.lookml_output_dir else None)
    )
    if not candidate or not candidate.exists():
        return None
    directory = candidate if candidate.is_dir() else candidate.parent
    # The extractor expects the LookML *project* root, not the dashboards
    # subfolder, since it also resolves models and explores relative to it.
    return directory.parent if directory.name == "dashboards" else directory


def publish_agent(
    app_ctx: AppContext,
    *,
    command: str,
    agent_id: str | None,
    non_interactive: bool,
    account: str | None,
    instance_url: str | None,
    output_json: bool,
) -> CommandResult:
    """Publish a CA agent to Gemini Enterprise.

    Shared by ``agent publish`` and its ``ge publish`` alias. Previously
    ``ge publish`` was a literal Python call to the ``agent publish`` command
    function, which held together only because both declared the same five
    options in the same order -- and which mislabelled ``ge publish``'s success
    envelope as ``"agent publish"`` while its *failure* envelope (produced by
    the error boundary) correctly said ``"ge publish"``.

    Args:
        app_ctx: Resolved invocation context.
        command: The envelope's ``command`` label -- the command the user
            actually typed.
        agent_id: Target CA agent. Falls back to the agent in prior state.
        non_interactive: Never prompt during GE configuration.
        account: Saved ``lkr`` OAuth account alias.
        instance_url: Looker instance base URL.
        output_json: Emit the JSON envelope on stdout.

    Returns:
        The emitted result envelope.

    Raises:
        ConfigError: No agent ID could be resolved.
        AuthError: No usable Looker credentials.
        RemoteApiError: Gemini Enterprise rejected the publish.
    """
    state = app_ctx.state
    target_id = agent_id or state.ca_agent_id
    if not target_id:
        raise missing_option(
            "--agent-id",
            purpose="the CA agent to publish",
            hint="Or run `demo-create agent create` first.",
        )

    auth = app_ctx.looker_auth(instance_url, account)

    state = ensure_gemini_enterprise_configured(state, auth.headers, interactive=not non_interactive)
    app_ctx.set_state(state)
    published = publish_agent_to_ge(auth.base_url, target_id, auth.headers)
    state.published_to_ge = published
    saved_path = app_ctx.save_state()

    if not published:
        raise RemoteApiError(
            f"Failed to publish CA Agent `{target_id}` to Gemini Enterprise.",
            remediation=(
                "Run `demo-create ge status --json` to confirm the instance is configured, and verify the "
                "Looker service account holds roles/discoveryengine.admin."
            ),
            details={
                "agent_id": target_id,
                "ge_configured": state.ge_configured,
                "ge_instance_id": state.ge_instance_id,
            },
        )

    result = CommandResult.success(
        command,
        data={
            "agent_id": target_id,
            "ge_configured": state.ge_configured,
            "ge_instance_id": state.ge_instance_id,
            "published_to_ge": True,
            "state_file": str(saved_path),
        },
    )

    def render(_: CommandResult) -> None:
        print_success(f"Agent `{target_id}` published to Gemini Enterprise app `{state.ge_instance_id}`.")
        print_info(f"Updated state saved to `{saved_path}`")

    return emit(result, json_output=output_json, human_renderer=render)


@agent_app.command(name="create")
def agent_create(
    ctx: typer.Context,
    model: Annotated[str | None, typer.Option("--model", help="LookML model name")] = None,
    explore: Annotated[str | None, typer.Option("--explore", help="Primary explore name")] = None,
    dashboards_dir: Annotated[
        Path | None,
        typer.Option("--dashboards-dir", help="Directory with *.dashboard.lookml files"),
    ] = None,
    dashboard_file: Annotated[
        Path | None,
        typer.Option("--dashboard-file", help="Specific *.dashboard.lookml file"),
    ] = None,
    dashboard_id: Annotated[
        str | None,
        typer.Option("--dashboard-id", help="Deployed Looker dashboard ID for query extraction"),
    ] = None,
    name: Annotated[str | None, typer.Option("--name", help="Custom Assistant name")] = None,
    instructions: Annotated[
        str | None,
        typer.Option("--instructions", help="Custom system prompt instructions"),
    ] = None,
    publish_ge: Annotated[
        bool,
        typer.Option("--publish-ge", help="Automatically configure and publish to Gemini Enterprise"),
    ] = False,
    non_interactive: Annotated[
        bool,
        typer.Option("--non-interactive", help="Run non-interactively without prompting for GE reconfigurations"),
    ] = False,
    account: Annotated[
        str | None,
        typer.Option("--looker-account", help="Saved Looker OAuth account alias"),
    ] = None,
    instance_url: Annotated[str | None, typer.Option("--instance", help="Looker instance base URL")] = None,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Create a Conversational Analytics agent and ground it with golden queries.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        model: LookML model the agent queries.
        explore: Primary explore the agent is grounded on.
        dashboards_dir: Directory of ``*.dashboard.lookml`` files to mine for queries.
        dashboard_file: A single ``*.dashboard.lookml`` file to mine for queries.
        dashboard_id: A deployed dashboard ID to mine for queries.
        name: Custom assistant display name.
        instructions: Custom system prompt instructions.
        publish_ge: Also configure and publish to Gemini Enterprise.
        non_interactive: Never prompt during GE configuration.
        account: Saved ``lkr`` OAuth account alias.
        instance_url: Looker instance base URL.
        output_json: Emit the JSON envelope on stdout.
        state_file: Optional explicit path to ``.demo-state.json``.

    Raises:
        ConfigError: No LookML model could be resolved.
        AuthError: No usable Looker credentials.
        RemoteApiError: Looker refused to provision the agent.
    """
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)

    state = app_ctx.state
    auth = app_ctx.looker_auth(instance_url, account)
    state.looker_instance_url = auth.base_url

    model_name = model or state.lookml_model_name
    if not model_name:
        raise missing_option(
            "--model",
            purpose="the LookML model the agent queries",
            hint="Or run `demo-create lookml model` first, which records the model name it generated.",
        )
    # The first generated table is the conventional base explore of a generated
    # model; falling back to the model name after that matches the single-explore
    # convention `lookml model` emits for a one-table dataset.
    explore_name = explore or (state.generated_tables[0] if state.generated_tables else model_name)

    print_info(f"Provisioning Looker CA Agent for model `{model_name}` on explore `{explore_name}`...")
    agent_id = provision_ca_agent(
        instance_url=auth.base_url,
        headers=auth.headers,
        model_name=model_name,
        explore_name=explore_name,
        agent_name=name,
        custom_instructions=instructions,
    )

    if not agent_id:
        raise RemoteApiError(
            "Failed to provision Conversational Analytics Agent.",
            remediation="Confirm the model and explore are deployed to production and CA is enabled on the instance.",
            details={"model": model_name, "explore": explore_name},
        )

    state.ca_agent_id = agent_id
    state.ca_agent_name = name or f"{model_name.replace('_', ' ').title()} Assistant"

    golden_queries: list[dict[str, Any]] = []
    if dashboard_id:
        print_info(f"Extracting Golden Queries from deployed dashboard `{dashboard_id}`...")
        golden_queries.extend(extract_golden_queries_from_dashboard_id(auth.base_url, auth.headers, dashboard_id))

    project_dir = _resolve_dashboard_dir(state, dashboard_file, dashboards_dir)
    if project_dir:
        print_info(f"Extracting Golden Queries from local dashboard files in `{project_dir}`...")
        golden_queries.extend(
            extract_golden_queries_from_dashboards(
                lookml_dir=project_dir,
                default_model=model_name,
                default_explore=explore_name,
                dashboard_file=dashboard_file if (dashboard_file and dashboard_file.is_file()) else None,
            )
        )

    linked_count = 0
    if golden_queries:
        print_info(f"Registering {len(golden_queries)} Golden Queries to CA Agent `{agent_id}`...")
        linked_count = register_and_link_golden_queries(auth.base_url, auth.headers, agent_id, golden_queries)
        state.golden_queries_count = linked_count

    if publish_ge:
        state = ensure_gemini_enterprise_configured(state, auth.headers, interactive=not non_interactive)
        app_ctx.set_state(state)
        state.published_to_ge = publish_agent_to_ge(auth.base_url, agent_id, auth.headers)

    saved_path = app_ctx.save_state()
    chat_url = f"{auth.base_url}/conversational-analytics/agents/{agent_id}"
    data = {
        "agent_id": agent_id,
        "agent_name": state.ca_agent_name,
        "model": model_name,
        "explore": explore_name,
        "chat_url": chat_url,
        "golden_queries_linked": linked_count,
        "published_to_ge": state.published_to_ge,
        "state_file": str(saved_path),
    }

    if publish_ge and not state.published_to_ge:
        # PARTIAL, not SUCCESS: the agent exists but is not reachable from
        # Gemini Enterprise. This previously exited 0 with a success banner.
        result = CommandResult(
            command="agent create",
            status="PARTIAL",
            data=data,
            errors=[
                ErrorDetail(
                    code="REMOTE_API_ERROR",
                    message=f"Agent `{agent_id}` was created but could not be published to Gemini Enterprise.",
                    remediation=(
                        "Run `demo-create ge configure --gcp-project <id>`, then "
                        f"`demo-create agent publish --agent-id {agent_id}`."
                    ),
                )
            ],
        )
    else:
        result = CommandResult.success("agent create", data=data)
        if not publish_ge:
            result.add_next_action(
                "Publish the agent to Gemini Enterprise",
                f"demo-create agent publish --agent-id {agent_id}",
                gate=5,
                requires_human_confirmation=True,
            )

    def render(res: CommandResult) -> None:
        print_success(f"Conversational Analytics Agent Chat URL: {chat_url}")
        print_info(f"Updated state saved to `{saved_path}`")
        for error in res.errors:
            print_error(error.message)

    return emit(result, json_output=output_json, human_renderer=render)


@agent_app.command(name="golden-queries")
def agent_golden_queries(
    ctx: typer.Context,
    agent_id: Annotated[str | None, typer.Option("--agent-id", help="Target CA Agent ID")] = None,
    dashboard_id: Annotated[str | None, typer.Option("--dashboard-id", help="Deployed Looker dashboard ID")] = None,
    dashboards_dir: Annotated[
        Path | None, typer.Option("--dashboards-dir", help="Directory with *.dashboard.lookml files")
    ] = None,
    dashboard_file: Annotated[
        Path | None, typer.Option("--dashboard-file", help="Specific *.dashboard.lookml file")
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="LookML model the extracted queries run against")] = None,
    explore: Annotated[str | None, typer.Option("--explore", help="Explore the extracted queries run against")] = None,
    account: Annotated[str | None, typer.Option("--looker-account", help="Saved Looker OAuth account alias")] = None,
    instance_url: Annotated[str | None, typer.Option("--instance", help="Looker instance base URL")] = None,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Extract dashboard queries and link them as Golden Queries to an existing agent.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        agent_id: Target CA agent. Falls back to the agent in prior state.
        dashboard_id: A deployed dashboard ID to mine for queries.
        dashboards_dir: Directory of ``*.dashboard.lookml`` files.
        dashboard_file: A single ``*.dashboard.lookml`` file.
        model: LookML model to qualify locally-extracted queries with. Falls
            back to the model in prior state.
        explore: Explore to qualify locally-extracted queries with. Falls back
            to the first generated table, then to the model name.
        account: Saved ``lkr`` OAuth account alias.
        instance_url: Looker instance base URL.
        output_json: Emit the JSON envelope on stdout.
        state_file: Optional explicit path to ``.demo-state.json``.

    Raises:
        ConfigError: No agent ID could be resolved, or dashboard files were
            found but no LookML model to qualify their queries with.
        AuthError: No usable Looker credentials.
    """
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)

    state = app_ctx.state
    target_id = agent_id or state.ca_agent_id
    if not target_id:
        raise missing_option(
            "--agent-id",
            purpose="the CA agent to attach golden queries to",
            hint="Or run `demo-create agent create` first.",
        )

    auth = app_ctx.looker_auth(instance_url, account)

    golden_queries = []
    if dashboard_id:
        golden_queries.extend(extract_golden_queries_from_dashboard_id(auth.base_url, auth.headers, dashboard_id))

    project_dir = _resolve_dashboard_dir(state, dashboard_file, dashboards_dir)
    if project_dir:
        # Guarded here rather than at the top of the command: only the local
        # extractor needs a model to qualify the queries it builds, so demanding
        # one up front would block the --dashboard-id path, which reads its
        # model off the deployed dashboard.
        model_name = model or state.lookml_model_name
        if not model_name:
            raise missing_option(
                "--model",
                purpose="the LookML model the extracted dashboard queries run against",
                hint="Or run `demo-create lookml model` first, which records the model name it generated.",
            )
        golden_queries.extend(
            extract_golden_queries_from_dashboards(
                lookml_dir=project_dir,
                default_model=model_name,
                # The first generated table is the conventional base explore of a
                # generated model, and is what `agent create` grounds on as well.
                default_explore=explore or (state.generated_tables[0] if state.generated_tables else model_name),
            )
        )

    if not golden_queries:
        # A no-op is still a success, but it must say so: this branch
        # previously returned in silence with no output at all.
        return emit(
            CommandResult.success(
                "agent golden-queries",
                data={"agent_id": target_id, "queries_found": 0, "queries_linked": 0},
                warnings=["No query tiles found to extract."],
            ),
            json_output=output_json,
        )

    count = register_and_link_golden_queries(auth.base_url, auth.headers, target_id, golden_queries)
    state.golden_queries_count = count
    saved_path = app_ctx.save_state()

    result = CommandResult.success(
        "agent golden-queries",
        data={
            "agent_id": target_id,
            "queries_found": len(golden_queries),
            "queries_linked": count,
            "state_file": str(saved_path),
        },
    )

    def render(_: CommandResult) -> None:
        print_success(f"Linked {count} of {len(golden_queries)} golden queries to agent `{target_id}`.")

    return emit(result, json_output=output_json, human_renderer=render)


@agent_app.command(name="publish")
def agent_publish(
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
        command="agent publish",
        agent_id=agent_id,
        non_interactive=non_interactive,
        account=account,
        instance_url=instance_url,
        output_json=output_json,
    )
