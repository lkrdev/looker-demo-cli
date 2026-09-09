from __future__ import annotations
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any, List, Optional
import typer
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from looker_demo_cli.config import (
    DEFAULT_GCP_PROJECT,
    DEFAULT_LOOKER_INSTANCE_URL,
    GEMINI_MCP_CONFIG,
    GEMINI_SKILLS_DIR,
)
from looker_demo_cli.precheck.env_checker import (
    RuntimeEnvironmentStatus,
    check_runtime_environment,
    init_workspace_venv,
)
from looker_demo_cli.precheck.gcp_auth import (
    get_gcp_active_context,
    inspect_gcp_accounts,
    list_available_gcp_projects,
)
from looker_demo_cli.precheck.looker_auth import (
    LKR_OAUTH_CLIENT_ID,
    LKR_OAUTH_CLIENT_PAYLOAD,
    LKR_OAUTH_REDIRECT_URI,
    check_looker_auth,
)
from looker_demo_cli.precheck.mcp_checker import check_mcp_servers, patch_mcp_config
from looker_demo_cli.precheck.skills_organizer import audit_and_organize_skills
from looker_demo_cli.utils.console import (
    console,
    print_banner,
    print_error,
    print_info,
    print_step_header,
    print_success,
    print_warning,
)
from looker_demo_cli.services.ge_service import (
    ensure_gemini_enterprise_configured,
    get_looker_auth_context,
    get_looker_ge_config,
    is_ge_configured,
    render_ge_status_table,
)
from looker_demo_cli.generators.embed_scaffolder import EmbedConfigOptions, EmbedScaffolder
from looker_demo_cli.generators.lookml_generator import LookMLGenerator, LookMLTableSpec
from looker_demo_cli.generators.schema_generator import (
    create_dynamic_blueprint_from_name,
    generate_domain_dataset,
)
from looker_demo_cli.services.agent_service import (
    extract_golden_queries_from_dashboard_id,
    provision_ca_agent,
    register_and_link_golden_queries,
)
from looker_demo_cli.services.knowledge_service import introspect_bq_table_specs
from looker_demo_cli.utils.bigquery_client import BigQueryHelper
from looker_demo_cli.workflow.runner import FlowRunner, extract_table_specs_from_parquet_dir
from looker_demo_cli.workflow.state import FlowState, load_flow_state, save_flow_state
from looker_demo_cli.workflow.steps.step_ca_agent import (
    extract_golden_queries_from_dashboards,
    publish_agent_to_ge,
)
from looker_demo_cli.services.optimizer_service import (
    optimize_lookml_project,
    render_optimization_report,
)
from looker_demo_cli.workflow.steps.step_looker_deploy import run_looker_deploy_step

app = typer.Typer(
    name="demo-create",
    help="End-to-end Looker demo creation orchestrator CLI for AI agents and developers.",
    no_args_is_help=True,
)


def version_callback(value: bool):
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
    """End-to-end Looker demo creation orchestrator CLI for AI agents and developers."""




def _render_env_tables(env_status: RuntimeEnvironmentStatus):
    """Render Rich tables for Python runtime and critical dependency pins."""
    console.print("\n[bold cyan]1. Python Runtime & Dependency Health[/bold cyan]")
    t_env = Table(show_header=True, header_style="bold blue")
    t_env.add_column("Property", style="dim")
    t_env.add_column("Value", style="bold")
    t_env.add_row("Python Executable", env_status.python_executable)
    t_env.add_row("Python Version", env_status.python_version)
    venv_str = f"[green]ACTIVE[/green] ({env_status.active_venv_path})" if env_status.is_virtualenv else "[red]NO (Bare System Python)[/red]"
    t_env.add_row("Virtual Environment", venv_str)
    t_env.add_row("uv Package Manager", f"[green]{env_status.uv_version}[/green]" if env_status.uv_installed else "[yellow]Not Found[/yellow]")
    console.print(t_env)

    t_deps = Table(show_header=True, header_style="bold blue")
    t_deps.add_column("Package")
    t_deps.add_column("Installed Version")
    t_deps.add_column("Required Pin")
    t_deps.add_column("Status")

    for dep in env_status.dependency_checks:
        status_label = "[green]VALID[/green]" if dep.is_satisfied else "[red]VIOLATION[/red]"
        inst_label = dep.installed_version or "[dim]Missing[/dim]"
        t_deps.add_row(dep.package_name, inst_label, dep.expected_constraint, status_label)
    console.print(t_deps)

    if not env_status.is_virtualenv:
        print_warning("Execution is running on bare system Python without an isolated virtual environment.")
        print_info("Run `demo-create env init` to bootstrap a local `.venv` or re-run with `demo-create pre-check --fix`.")



@app.command(name="pre-check")
def pre_check(
    fix: Annotated[bool, typer.Option("--fix", help="Automatically install missing MCP configs and organize global skills")] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Emit raw JSON status report for agent programmatic consumption")] = False,
    gcp_project: Annotated[str, typer.Option("--gcp-project", help="Target Google Cloud Project ID")] = DEFAULT_GCP_PROJECT,
):
    """Inspect and configure GCP/ADC accounts, MCP server definitions, and intent-based skill subfolders."""
    if not output_json:
        print_banner("PRE-CHECK: ENVIRONMENT, MCP & SKILL AUDIT", f"Target GCP Project: {gcp_project}")

    # 0. Check Python Runtime & Dependency Health
    env_status = check_runtime_environment()
    if fix and not env_status.is_virtualenv:
        print_info("Fix flag enabled: initializing local workspace virtual environment...")
        init_workspace_venv(Path.cwd())
        env_status = check_runtime_environment()

    # 1. Inspect Active GCP & ADC Context
    gcp_context = get_gcp_active_context()
    effective_project = gcp_project or gcp_context.active_project or gcp_context.adc_project_id or ""
    gcp_accounts = inspect_gcp_accounts(target_project=effective_project)
    
    # 2. Query available GCP projects
    available_projects = list_available_gcp_projects()

    # 3. Check MCP Servers
    mcp_statuses = check_mcp_servers()
    if fix:
        patch_mcp_config()
        mcp_statuses = check_mcp_servers()

    # 4. Organize Skills
    skill_statuses = audit_and_organize_skills(fix=fix)

    # 5. Check Looker Auth
    looker_status = check_looker_auth()

    # Determine if re-auth or account setup is needed
    reauth_required = any(
        (a.is_active and not a.has_bigquery_access) or (a.is_active and "reauth" in (a.error_message or "").lower())
        for a in gcp_accounts
    )
    no_accounts_configured = len(gcp_accounts) == 0

    # Determine blocking conditions
    blocking_reasons: List[str] = []
    if no_accounts_configured:
        blocking_reasons.append("No Google Cloud accounts configured. Run `gcloud auth login` and `gcloud auth application-default login`.")
    elif reauth_required:
        blocking_reasons.append("Active GCP account requires reauthentication. Run `gcloud auth login` and `gcloud auth application-default login`.")
    elif not any(a.has_bigquery_access for a in gcp_accounts):
        blocking_reasons.append(f"No configured GCP account has BigQuery access on project '{effective_project or 'default'}'.")

    if not looker_status.is_authenticated:
        blocking_reasons.append(f"Looker is not authenticated ({looker_status.error_message or 'No active OAuth session or API key'}).")

    is_blocked = len(blocking_reasons) > 0

    if output_json:
        report = {
            "status": "BLOCKED" if is_blocked else "HEALTHY",
            "is_blocked": is_blocked,
            "blocking_reasons": blocking_reasons,
            "runtime_environment": env_status.model_dump(),
            "active_gcp_context": gcp_context.model_dump(),
            "gcp_accounts": [a.model_dump() for a in gcp_accounts],
            "available_gcp_projects": available_projects,
            "reauth_required": reauth_required,
            "mcp_servers": [m.model_dump() for m in mcp_statuses],
            "skills": [s.model_dump() for s in skill_statuses],
            "looker_auth": looker_status.model_dump(),
            "oauth_registration_payload": LKR_OAUTH_CLIENT_PAYLOAD,
            "agent_instructions": {
                "is_blocked": is_blocked,
                "blocking_reasons": blocking_reasons,
                "mandatory_stop_gate": (
                    "CRITICAL: STOP all further tool executions immediately after pre-check. "
                    "If is_blocked is true, do NOT proceed. Prompt the user with the required commands to authenticate. "
                    "If is_blocked is false, prompt the user (via ask_question) to confirm the 4 environment targets before taking any further action."
                ),
                "gcp_auth_commands": [
                    "gcloud auth login",
                    "gcloud auth application-default login",
                    "gcloud config set project <PROJECT_ID>",
                ],
                "looker_oauth_commands": [
                    "lkr auth login",
                    "Register OAuth client 'lkr-cli' in Looker API Explorer with redirect_uri 'http://localhost:8000/callback'",
                    "If remote/SSH, forward port: ssh -L 8000:localhost:8000 <remote_host>",
                    "If port 8000 conflict: lsof -ti:8000 | xargs kill -9",
                    "If browser cannot reach localhost, paste redirected URL in chat so agent can curl it locally",
                ],
                "gcp_account": "Prompt user to select/confirm the active GCP account from gcp_accounts.",
                "gcp_project": "Prompt user to select/confirm the target Google Cloud project from available_gcp_projects.",
                "looker_instance": "Prompt user to select/confirm the target Looker OAuth instance from available_oauth_instances.",
                "database_connection": "Prompt user to confirm the Looker database connection name.",
            },
        }
        typer.echo(json.dumps(report, indent=2))
        if is_blocked:
            raise typer.Exit(code=1)
        return

    # Render Visual Summary
    _render_env_tables(env_status)

    console.print("\n[bold cyan]2. GCP & ADC Context and Credentials[/bold cyan]")
    t_ctx = Table(show_header=True, header_style="bold blue")
    t_ctx.add_column("Setting", style="dim")
    t_ctx.add_column("Value", style="bold")
    t_ctx.add_row("Active gcloud Config", gcp_context.active_config_name or "[dim]None[/dim]")
    t_ctx.add_row("Active gcloud Account", gcp_context.active_account or "[dim]None[/dim]")
    t_ctx.add_row("Active gcloud Project", gcp_context.active_project or "[dim]None[/dim]")
    t_ctx.add_row("ADC Project", gcp_context.adc_project_id or "[dim]None[/dim]")
    t_ctx.add_row("ADC Quota Project", gcp_context.adc_quota_project_id or "[dim]None[/dim]")
    t_ctx.add_row("ADC Credentials File", f"{gcp_context.adc_file_path} ({'Found' if gcp_context.adc_file_exists else 'Missing'})")
    console.print(t_ctx)

    if gcp_accounts:
        t_gcp = Table(show_header=True, header_style="bold blue")
        t_gcp.add_column("Account", style="dim")
        t_gcp.add_column("Active in gcloud")
        t_gcp.add_column("BigQuery Access")
        t_gcp.add_column("Notes")

        for acc in gcp_accounts:
            bq_label = "[green]YES[/green]" if acc.has_bigquery_access else "[red]NO[/red]"
            active_label = "[green]ACTIVE[/green]" if acc.is_active else "[dim]INACTIVE[/dim]"
            t_gcp.add_row(acc.account_id, active_label, bq_label, acc.error_message or "Valid")
        console.print(t_gcp)
        if len(gcp_accounts) > 1:
            print_info("Multiple GCP accounts found. Agent/User Instruction: Confirm which GCP account to use for synthesis.")

    console.print("\n[bold cyan]3. Available Google Cloud Projects[/bold cyan]")
    if available_projects:
        t_proj = Table(show_header=True, header_style="bold blue")
        t_proj.add_column("Project ID", style="bold")
        t_proj.add_column("Name")
        t_proj.add_column("Project Number", style="dim")
        for p in available_projects:
            t_proj.add_row(p["project_id"], p["name"], p["project_number"])
        console.print(t_proj)
        print_info("Agent Instruction: Confirm with the user which of the available GCP projects above to use for demo synthesis and BigQuery datasets.")
    else:
        print_warning("No Google Cloud projects found or `gcloud projects list` returned empty.")
        console.print("[yellow]To set your active GCP project, run:[/yellow]")
        console.print("  [bold white]$ gcloud config set project <PROJECT_ID>[/bold white]")

    console.print("\n[bold cyan]4. Global MCP Tool Configurations[/bold cyan]")
    t_mcp = Table(show_header=True, header_style="bold blue")
    t_mcp.add_column("MCP Server")
    t_mcp.add_column("Status")
    t_mcp.add_column("Details")

    for m in mcp_statuses:
        status_label = "[green]CONFIGURED[/green]" if m.is_configured else "[red]MISSING[/red]"
        details = ", ".join(m.issues) if m.issues else "Ready"
        t_mcp.add_row(m.server_name, status_label, details)
    console.print(t_mcp)

    console.print("\n[bold cyan]5. Intent-Organized Agent Skills (~/.gemini/config/skills/)[/bold cyan]")
    t_skills = Table(show_header=True, header_style="bold blue")
    t_skills.add_column("Intent Category")
    t_skills.add_column("Skill Name")
    t_skills.add_column("Installed")
    t_skills.add_column("Source Found")

    for s in skill_statuses:
        inst_label = "[green]YES[/green]" if s.is_installed else "[yellow]NO[/yellow]"
        src_label = "[green]YES[/green]" if s.is_valid else "[red]NOT FOUND[/red]"
        t_skills.add_row(s.category, s.skill_name, inst_label, src_label)
    console.print(t_skills)

    console.print("\n[bold cyan]6. Looker Authentication (OAuth / API Key)[/bold cyan]")
    if looker_status.is_authenticated:
        auth_badge = f"[cyan]OAuth ({looker_status.oauth_account})[/cyan]" if looker_status.auth_method == "oauth" else "[cyan]API Key[/cyan]"
        print_success(f"Connected via {auth_badge} to {looker_status.instance_url} as {looker_status.user_name} ({looker_status.user_email})")
        if looker_status.has_default_bigquery_conn:
            print_success("Database connection `default_bigquery_connection` is configured.")
        else:
            print_warning("Connection `default_bigquery_connection` not found in available connections.")
    else:
        print_warning(f"Looker not authenticated: {looker_status.error_message}")
        if looker_status.available_oauth_instances:
            console.print(f"[dim]Found {len(looker_status.available_oauth_instances)} saved OAuth session(s) in ~/.lkr/auth.db[/dim]")

    if not fix and any(not m.is_configured for m in mcp_statuses):
        print_info("\nTip: Run `demo-create pre-check --fix` to automatically repair missing MCP configs and organize skills.")

    # Render Blocked Banner and Guidance if authentication is missing
    if is_blocked:
        console.print("\n")
        console.print(
            Panel.fit(
                "[bold white on red] 🛑 EXECUTION BLOCKED: AUTHENTICATION REQUIRED [/bold white on red]\n\n"
                + "\n".join(f"[bold red]❌ {r}[/bold red]" for r in blocking_reasons)
                + "\n\n[bold yellow]You must resolve the authentication requirements below before proceeding with demo creation.[/bold yellow]",
                title="[bold red]PRE-CHECK BLOCKED[/bold red]",
                border_style="red",
            )
        )

        if no_accounts_configured or reauth_required or not any(a.has_bigquery_access for a in gcp_accounts):
            console.print("\n[bold cyan]1. Google Cloud Authentication Commands:[/bold cyan]")
            console.print("  Authenticate your GCP user account and configure Application Default Credentials (ADC):")
            console.print("    [bold white]$ gcloud auth login[/bold white]")
            console.print("    [bold white]$ gcloud auth application-default login[/bold white]")
            console.print("    [bold white]$ gcloud config set project <PROJECT_ID>[/bold white]")

        if not looker_status.is_authenticated:
            console.print("\n[bold cyan]2. Looker Authentication & OAuth Setup:[/bold cyan]")
            console.print("  [bold]A. Login via OAuth (Recommended):[/bold]")
            console.print("     [bold white]$ lkr auth login[/bold white]")
            console.print("     (or: [bold white]$ uvx --from \"lkr-dev-cli[codemode]\" lkr-dev-cli auth login[/bold white])")

            console.print("\n  [bold]B. First-Time OAuth Client Registration in Looker:[/bold]")
            console.print("     If `lkr-cli` has not been registered on this Looker instance, an admin must register it once:")
            console.print("     [bold underline]API Explorer Method:[/bold underline]")
            console.print("     Open: [cyan]https://<your-looker-instance>/extensions/marketplace_extension_api_explorer::api-explorer/4.0/methods/Auth/register_oauth_client_app[/cyan]")
            console.print("     - [bold]client_id:[/bold] [green]lkr-cli[/green]")
            console.print("     - [bold]Body (JSON):[/bold]")
            oauth_payload_json = json.dumps(LKR_OAUTH_CLIENT_PAYLOAD, indent=2)
            console.print(Syntax(oauth_payload_json, "json", theme="monokai", line_numbers=False))
            console.print("     - Check [bold]\"I Understand\"[/bold] and click [bold]\"Run\"[/bold].")

            console.print("\n  [bold]C. Remote Host / Cloudtop / SSH Port Forwarding:[/bold]")
            console.print("     Because the OAuth callback redirects to [cyan]http://localhost:8000/callback[/cyan], forward port 8000 from your local machine:")
            console.print("     [bold white]$ ssh -L 8000:localhost:8000 <remote-host>[/bold white]")

            console.print("\n  [bold]D. Port 8000 Conflict Resolution:[/bold]")
            console.print("     If port 8000 is in use by another process, free it before running `lkr auth login`:")
            console.print("     [bold white]$ lsof -ti:8000 | xargs kill -9[/bold white]   [dim](or: fuser -k 8000/tcp)[/dim]")

            console.print("\n  [bold]E. Headless / Agent OAuth Callback Fallback:[/bold]")
            console.print("     If your browser redirects to [cyan]http://localhost:8000/callback?code=...[/cyan] and cannot load the page,")
            console.print("     copy the full URL from your browser address bar and paste it into chat.")
            console.print("     The AI agent will curl the callback URL locally on the remote machine to finish authentication.")

            console.print("\n  [bold]Alternatively, configure API Keys in your environment or `.env`:[/bold]")
            console.print("     LOOKERSDK_BASE_URL=https://<your-instance>.looker.com")
            console.print("     LOOKERSDK_CLIENT_ID=<client_id>")
            console.print("     LOOKERSDK_CLIENT_SECRET=<client_secret>")

        console.print("\n[bold red]Execution blocked. Please complete authentication and re-run `demo-create pre-check --fix`.[/bold red]\n")
        raise typer.Exit(code=1)


@app.command(name="run")
def run_flow(
    project_name: Annotated[str, typer.Option("--project", help="Looker project and dataset name")] = "logistics_analytics",
    dataset_name: Annotated[Optional[str], typer.Option("--dataset", help="Target BigQuery dataset ID")] = None,
    scope: Annotated[str, typer.Option("--scope", help="Demo scope: internal or external")] = "internal",
    gcp_project: Annotated[str, typer.Option("--gcp-project", help="Target GCP Project ID")] = DEFAULT_GCP_PROJECT,
    gcp_account: Annotated[Optional[str], typer.Option("--gcp-account", help="Specific authenticated GCP user account")] = None,
    looker_account: Annotated[Optional[str], typer.Option("--looker-account", help="Specific Looker OAuth account or instance alias")] = None,
    connection_name: Annotated[str, typer.Option("--connection", help="Target Looker database connection name")] = "default_bigquery_connection",
    scratch_dir: Annotated[Path, typer.Option("--scratch-dir", help="Local scratch work directory")] = Path.home() / "scratch" / "demo_create",
    agent_mode: Annotated[bool, typer.Option("--agent-mode", help="Non-interactive execution mode for AI agents")] = False,
):
    """Execute the full deterministic demo creation workflow."""
    ds_name = dataset_name or project_name
    demo_scope_val = "external_embed" if "ext" in scope.lower() else "internal_looker"

    state = FlowState(
        gcp_project_id=gcp_project,
        gcp_account=gcp_account,
        looker_account=looker_account,
        looker_connection_name=connection_name,
        looker_project_name=project_name,
        lookml_model_name=project_name,
        bq_dataset_id=ds_name,
        demo_scope=demo_scope_val,
    )

    runner = FlowRunner(
        state=state,
        scratch_dir=scratch_dir,
        target_base_dir=Path.home(),
    )

    final_state = runner.run()
    if final_state.status == "completed":
        print_success("\nDemo creation finished successfully!")
        if final_state.deployed_dashboard_url:
            print_info(f"Dashboard URL: {final_state.deployed_dashboard_url}")
        if final_state.embed_workspace_dir:
            print_info(f"Embed Workspace: {final_state.embed_workspace_dir}")


@app.command(name="skills")
def list_skills(
    fix: Annotated[bool, typer.Option("--fix", help="Symlink and organize skills into intent subfolders")] = False,
):
    """View and manage intent-based global agent skills."""
    statuses = audit_and_organize_skills(fix=fix)
    print_success(f"Organized {len(statuses)} skills across intent categories into {GEMINI_SKILLS_DIR}")


env_app = typer.Typer(
    name="env",
    help="Manage local demo workspace virtual environment and runtime health.",
    no_args_is_help=True,
)
app.add_typer(env_app, name="env")


@env_app.command(name="init")
def env_init(
    target_dir: Annotated[Path, typer.Option("--dir", help="Directory where .venv will be created")] = Path.cwd(),
):
    """Initialize a dedicated .venv in the target directory with all pinned tools."""
    success, msg = init_workspace_venv(target_dir=target_dir)
    if success:
        print_success(f"Workspace environment ready! Activate it with:\n  $ {msg}")
    else:
        print_error(f"Failed to initialize environment: {msg}")
        raise typer.Exit(code=1)


@env_app.command(name="info")
def env_info():
    """Display runtime environment details and critical dependency pin health."""
    env_status = check_runtime_environment()
    _render_env_tables(env_status)


ge_app = typer.Typer(
    name="ge",
    help="Inspect and configure Looker Gemini Enterprise (GE) integration.",
    no_args_is_help=True,
)
app.add_typer(ge_app, name="ge")


@ge_app.command(name="status")
def ge_status(
    instance_url: Annotated[Optional[str], typer.Option("--instance", help="Looker instance base URL")] = None,
    account: Annotated[Optional[str], typer.Option("--looker-account", help="Saved Looker OAuth account alias")] = None,
):
    """Fetch and display current Looker Gemini enablement and GE configuration."""
    headers, base_url = get_looker_auth_context(instance_url=instance_url, preferred_account=account)
    if not base_url:
        print_error("No Looker instance URL configured. Provide --instance or authenticate with `lkr auth login`.")
        raise typer.Exit(code=1)

    config = get_looker_ge_config(base_url, headers)
    if not config:
        print_error("Failed to retrieve Gemini enablement configuration from Looker.")
        raise typer.Exit(code=1)

    render_ge_status_table(config)
    if is_ge_configured(config):
        print_success("Gemini Enterprise is fully configured in Looker.")
    else:
        print_warning("Gemini Enterprise is NOT fully configured. Run `demo-create ge configure` to set it up.")


@ge_app.command(name="configure")
def ge_configure(
    gcp_project: Annotated[str, typer.Option("--gcp-project", help="Target Google Cloud Project ID")] = DEFAULT_GCP_PROJECT,
    instance_url: Annotated[Optional[str], typer.Option("--instance", help="Looker instance base URL")] = None,
    account: Annotated[Optional[str], typer.Option("--looker-account", help="Saved Looker OAuth account alias")] = None,
    app_id: Annotated[Optional[str], typer.Option("--app-id", help="Gemini Enterprise App/Engine ID")] = None,
    location: Annotated[str, typer.Option("--location", help="Gemini Enterprise Location/Region")] = "global",
):
    """Interactively or explicitly configure Gemini Enterprise settings in Looker and grant IAM role."""
    headers, base_url = get_looker_auth_context(instance_url=instance_url, preferred_account=account)
    if not base_url:
        print_error("No Looker instance URL configured. Provide --instance or authenticate with `lkr auth login`.")
        raise typer.Exit(code=1)

    state = FlowState(
        gcp_project_id=gcp_project,
        looker_instance_url=base_url,
        looker_account=account,
        ge_instance_id=app_id,
        ge_location=location,
    )

    updated_state = ensure_gemini_enterprise_configured(
        state=state,
        headers=headers,
        interactive=True,
        allow_reconfigure=True,
    )

    if updated_state.ge_configured:
        print_success(
            f"Gemini Enterprise successfully configured for app `{updated_state.ge_instance_id}` on project `{updated_state.gcp_project_id}`."
        )
    else:
        print_error("Failed to complete Gemini Enterprise configuration.")
        raise typer.Exit(code=1)


# -------------------------------------------------------------------------
# DATA GROUP: demo-create data [generate | upload | inspect]
# -------------------------------------------------------------------------
data_app = typer.Typer(
    name="data",
    help="Design, synthesize, inspect, and upload BigQuery demo datasets.",
    no_args_is_help=True,
)
app.add_typer(data_app, name="data")


@data_app.command(name="generate")
def data_generate(
    domain: Annotated[str, typer.Option("--domain", help="Domain theme name (e.g. supply_chain, trucking_iot)")] = "logistics_analytics",
    row_count: Annotated[int, typer.Option("--row-count", help="Target fact row count")] = 1000,
    output_dir: Annotated[Optional[Path], typer.Option("--output-dir", help="Local directory to write Parquet files")] = None,
    upload: Annotated[bool, typer.Option("--upload", help="Automatically upload synthesized Parquet tables to BigQuery")] = False,
    gcp_project: Annotated[str, typer.Option("--gcp-project", help="Target GCP Project ID if uploading")] = DEFAULT_GCP_PROJECT,
    dataset: Annotated[Optional[str], typer.Option("--dataset", help="Target BigQuery dataset ID if uploading")] = None,
):
    """Synthesize high-fidelity relational Parquet dataset tables locally."""
    state = load_flow_state()
    target_dir = output_dir or (Path.home() / "scratch" / "demo_create" / (dataset or domain))
    target_dir.mkdir(parents=True, exist_ok=True)

    print_info(f"Synthesizing dataset for domain `{domain}` into `{target_dir}`...")
    blueprint = create_dynamic_blueprint_from_name(domain)
    for entity in blueprint.entities:
        if entity.table_type == "fact":
            entity.row_count = row_count

    specs = generate_domain_dataset(target=blueprint, output_dir=target_dir)
    table_names = [s.table_name for s in specs]

    state.domain_name = domain
    state.bq_dataset_id = dataset or domain
    state.generated_parquet_dir = target_dir
    state.generated_tables = table_names
    state.gcp_project_id = gcp_project or state.gcp_project_id

    print_success(f"Generated {len(table_names)} tables in `{target_dir}`: {table_names}")

    if upload:
        print_info(f"Uploading generated tables to BigQuery dataset `{state.bq_dataset_id}`...")
        bq_helper = BigQueryHelper(project_id=state.gcp_project_id, location=state.gcp_location)
        bq_helper.ensure_dataset(state.bq_dataset_id)
        for t_name in table_names:
            p_file = target_dir / f"{t_name}.parquet"
            if p_file.exists():
                cnt = bq_helper.load_parquet_table(state.bq_dataset_id, t_name, p_file)
                print_success(f"Loaded `{t_name}` ({cnt:,} rows) into BigQuery")
        state.dataset_exists = True

    saved_path = save_flow_state(state)
    print_info(f"Updated state saved to `{saved_path}`")


@data_app.command(name="upload")
def data_upload(
    parquet_dir: Annotated[Optional[Path], typer.Option("--parquet-dir", help="Directory containing Parquet files")] = None,
    dataset: Annotated[Optional[str], typer.Option("--dataset", help="Target BigQuery dataset ID")] = None,
    gcp_project: Annotated[str, typer.Option("--gcp-project", help="Target GCP Project ID")] = DEFAULT_GCP_PROJECT,
    location: Annotated[str, typer.Option("--location", help="BigQuery dataset location")] = "US",
):
    """Upload local Parquet tables into a BigQuery dataset."""
    state = load_flow_state()
    p_dir = parquet_dir or state.generated_parquet_dir
    if not p_dir or not p_dir.exists():
        print_error("No Parquet directory found. Specify --parquet-dir or run `demo-create data generate` first.")
        raise typer.Exit(code=1)

    ds_id = dataset or state.bq_dataset_id
    proj_id = gcp_project or state.gcp_project_id

    bq_helper = BigQueryHelper(project_id=proj_id, location=location)
    bq_helper.ensure_dataset(ds_id)

    parquet_files = sorted(list(p_dir.glob("*.parquet")))
    if not parquet_files:
        print_warning(f"No .parquet files found in `{p_dir}`.")
        raise typer.Exit(code=1)

    print_info(f"Loading {len(parquet_files)} Parquet files into `{proj_id}.{ds_id}`...")
    for pf in parquet_files:
        t_name = pf.stem
        rows = bq_helper.load_parquet_table(ds_id, t_name, pf)
        print_success(f"Loaded `{t_name}` ({rows:,} rows)")
        if t_name not in state.generated_tables:
            state.generated_tables.append(t_name)

    state.dataset_exists = True
    state.bq_dataset_id = ds_id
    state.gcp_project_id = proj_id
    state.gcp_location = location
    saved_path = save_flow_state(state)
    print_info(f"Updated state saved to `{saved_path}`")


@data_app.command(name="inspect")
def data_inspect(
    dataset: Annotated[Optional[str], typer.Option("--dataset", help="BigQuery dataset ID")] = None,
    gcp_project: Annotated[str, typer.Option("--gcp-project", help="Target GCP Project ID")] = DEFAULT_GCP_PROJECT,
    location: Annotated[str, typer.Option("--location", help="BigQuery dataset location")] = "US",
):
    """Inspect tables, schemas, and metadata in a BigQuery dataset."""
    state = load_flow_state()
    ds_id = dataset or state.bq_dataset_id
    proj_id = gcp_project or state.gcp_project_id

    bq_helper = BigQueryHelper(project_id=proj_id, location=location)
    if not bq_helper.dataset_exists(ds_id):
        print_error(f"Dataset `{proj_id}.{ds_id}` does not exist.")
        raise typer.Exit(code=1)

    tables = bq_helper.list_tables(ds_id)
    if not tables:
        print_warning(f"Dataset `{ds_id}` exists but has no tables.")
        return

    table_report = Table(title=f"BigQuery Dataset: {proj_id}.{ds_id}", show_header=True, header_style="bold blue")
    table_report.add_column("Table Name", style="bold")
    table_report.add_column("Type", style="cyan")
    table_report.add_column("Columns", justify="right")
    table_report.add_column("Rows", justify="right")

    for t_id in tables:
        tbl_ref = bq_helper.client.dataset(ds_id).table(t_id)
        try:
            tbl = bq_helper.client.get_table(tbl_ref)
            table_report.add_row(t_id, tbl.table_type, str(len(tbl.schema)), f"{tbl.num_rows:,}")
        except Exception:
            table_report.add_row(t_id, "UNKNOWN", "?", "?")

    console.print(table_report)


# -------------------------------------------------------------------------
# LOOKML GROUP: demo-create lookml [model | deploy]
# -------------------------------------------------------------------------
lookml_app = typer.Typer(
    name="lookml",
    help="Generate LookML models from BigQuery/Knowledge Catalog or Parquet, and deploy.",
    no_args_is_help=True,
)
app.add_typer(lookml_app, name="lookml")


@lookml_app.command(name="model")
def lookml_model(
    dataset: Annotated[Optional[str], typer.Option("--dataset", help="Existing BigQuery dataset ID to model")] = None,
    tables: Annotated[Optional[str], typer.Option("--tables", help="Comma-separated list of table names to include")] = None,
    parquet_dir: Annotated[Optional[Path], typer.Option("--parquet-dir", help="Directory containing Parquet files")] = None,
    project: Annotated[Optional[str], typer.Option("--project", help="Looker project and model name")] = None,
    connection: Annotated[str, typer.Option("--connection", help="Looker database connection name")] = "default_bigquery_connection",
    output_dir: Annotated[Optional[Path], typer.Option("--output-dir", help="Directory to output generated LookML files")] = None,
    gcp_project: Annotated[str, typer.Option("--gcp-project", help="Target GCP Project ID")] = DEFAULT_GCP_PROJECT,
):
    """Generate LookML views, explores, and dashboards from BigQuery/Knowledge Catalog or Parquet."""
    state = load_flow_state()
    proj_name = project or state.looker_project_name or "logistics_analytics"
    ds_name = dataset or state.bq_dataset_id or proj_name
    conn_name = connection or state.looker_connection_name
    gcp_proj = gcp_project or state.gcp_project_id
    out_dir = output_dir or (Path.home() / "scratch" / "demo_create" / f"lookml_{proj_name}")
    out_dir.mkdir(parents=True, exist_ok=True)

    table_filter = [t.strip() for t in tables.split(",") if t.strip()] if tables else None
    table_specs: list[LookMLTableSpec] = []

    # 1. Source: Live BigQuery Dataset with Knowledge Catalog semantics
    if dataset or (state.dataset_exists and not parquet_dir):
        print_info(f"Introspecting BigQuery dataset `{gcp_proj}.{ds_name}` and querying Knowledge Catalog semantics...")
        table_specs = introspect_bq_table_specs(
            project_id=gcp_proj,
            dataset_id=ds_name,
            location=state.gcp_location,
            table_filter=table_filter,
        )
        if table_specs:
            print_success(f"Discovered and enriched {len(table_specs)} table(s) via BigQuery & Knowledge Catalog.")

    # 2. Source: Local Parquet files
    if not table_specs:
        p_dir = parquet_dir or state.generated_parquet_dir
        if p_dir and p_dir.exists() and any(p_dir.glob("*.parquet")):
            print_info(f"Extracting table specifications from Parquet directory: `{p_dir}`...")
            all_specs = extract_table_specs_from_parquet_dir(p_dir)
            table_specs = [s for s in all_specs if not table_filter or s.table_name in table_filter]
            print_success(f"Extracted {len(table_specs)} table spec(s) from Parquet files.")

    if not table_specs:
        print_error("No tables found to model. Provide --dataset or --parquet-dir, or run `demo-create data generate`.")
        raise typer.Exit(code=1)

    print_info(f"Generating LookML files into `{out_dir}`...")
    gen = LookMLGenerator(project_id=gcp_proj, dataset_id=ds_name, connection_name=conn_name)
    written = gen.write_lookml_project_files(
        output_dir=out_dir,
        model_name=proj_name,
        tables=table_specs,
    )
    print_success(f"Generated {len(written)} LookML files in `{out_dir}`:")
    for f in written:
        console.print(f"  • {f.relative_to(out_dir)}")

    state.looker_project_name = proj_name
    state.lookml_model_name = proj_name
    state.bq_dataset_id = ds_name
    state.looker_connection_name = conn_name
    state.lookml_output_dir = out_dir
    state.gcp_project_id = gcp_proj
    state.existing_tables = [s.table_name for s in table_specs]
    saved_path = save_flow_state(state)
    print_info(f"Updated state saved to `{saved_path}`")


@lookml_app.command(name="deploy")
def lookml_deploy(
    lookml_dir: Annotated[Optional[Path], typer.Option("--lookml-dir", help="Directory containing LookML files")] = None,
    project: Annotated[Optional[str], typer.Option("--project", help="Looker project name")] = None,
    account: Annotated[Optional[str], typer.Option("--looker-account", help="Saved Looker OAuth account alias")] = None,
):
    """Push local LookML files to dev workspace, validate, and deploy to Looker production."""
    state = load_flow_state()
    if lookml_dir:
        state.lookml_output_dir = lookml_dir
    if project:
        state.looker_project_name = project
        state.lookml_model_name = project
    if account:
        state.looker_account = account

    if not state.lookml_output_dir or not state.lookml_output_dir.exists():
        print_error("No LookML directory found to deploy. Provide --lookml-dir or run `demo-create lookml model`.")
        raise typer.Exit(code=1)

    final_state = run_looker_deploy_step(state)
    saved_path = save_flow_state(final_state)
    print_info(f"Updated state saved to `{saved_path}`")


@lookml_app.command(name="optimize")
def lookml_optimize(
    lookml_dir: Annotated[
        Optional[Path],
        typer.Option("--lookml-dir", help="Directory containing LookML files"),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Output machine-readable JSON report")
    ] = False,
):
    """Scan and patch staged LookML files in-place with Google Cloud Server Performance Best Practices."""
    state = load_flow_state()
    target_dir = lookml_dir or state.lookml_output_dir or Path("lookml")
    if not target_dir.exists():
        print_error(
            f"LookML directory `{target_dir}` does not exist. Provide --lookml-dir or model first."
        )
        raise typer.Exit(code=1)

    print_info(f"Auditing and optimizing LookML files in `{target_dir}`...")
    res = optimize_lookml_project(target_dir)
    if json_output:
        import json

        print(json.dumps(res, indent=2))
    else:
        render_optimization_report(res)


# -------------------------------------------------------------------------
# AGENT GROUP: demo-create agent [create | golden-queries | publish]
# -------------------------------------------------------------------------
agent_app = typer.Typer(
    name="agent",
    help="Provision Looker Conversational Analytics AI agents, ground golden queries, and publish to GE.",
    no_args_is_help=True,
)
app.add_typer(agent_app, name="agent")


@agent_app.command(name="create")
def agent_create(
    model: Annotated[
        Optional[str], typer.Option("--model", help="LookML model name")
    ] = None,
    explore: Annotated[
        Optional[str], typer.Option("--explore", help="Primary explore name")
    ] = None,
    dashboards_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--dashboards-dir", help="Directory with *.dashboard.lookml files"
        ),
    ] = None,
    dashboard_file: Annotated[
        Optional[Path],
        typer.Option("--dashboard-file", help="Specific *.dashboard.lookml file"),
    ] = None,
    dashboard_id: Annotated[
        Optional[str],
        typer.Option(
            "--dashboard-id", help="Deployed Looker dashboard ID for query extraction"
        ),
    ] = None,
    name: Annotated[
        Optional[str], typer.Option("--name", help="Custom Assistant name")
    ] = None,
    instructions: Annotated[
        Optional[str],
        typer.Option("--instructions", help="Custom system prompt instructions"),
    ] = None,
    publish_ge: Annotated[
        bool,
        typer.Option(
            "--publish-ge",
            help="Automatically configure and publish to Gemini Enterprise",
        ),
    ] = False,
    non_interactive: Annotated[
        bool,
        typer.Option(
            "--non-interactive",
            help="Run non-interactively without prompting for GE reconfigurations",
        ),
    ] = False,
    account: Annotated[
        Optional[str],
        typer.Option("--looker-account", help="Saved Looker OAuth account alias"),
    ] = None,
    instance_url: Annotated[
        Optional[str], typer.Option("--instance", help="Looker instance base URL")
    ] = None,
):
    """Create a Conversational Analytics agent, ground with dashboard golden queries, and optionally publish to GE."""
    state = load_flow_state()
    headers, base_url = get_looker_auth_context(instance_url=instance_url or state.looker_instance_url, preferred_account=account or state.looker_account)
    if not base_url or not headers.get("Authorization"):
        print_error("Looker authentication required. Run `lkr auth login` or provide credentials.")
        raise typer.Exit(code=1)

    state.looker_instance_url = base_url
    model_name = model or state.lookml_model_name
    explore_name = explore or (state.generated_tables[0] if state.generated_tables else model_name)

    print_info(f"Provisioning Looker CA Agent for model `{model_name}` on explore `{explore_name}`...")
    agent_id = provision_ca_agent(
        instance_url=base_url,
        headers=headers,
        model_name=model_name,
        explore_name=explore_name,
        agent_name=name,
        custom_instructions=instructions,
    )

    if not agent_id:
        print_error("Failed to provision Conversational Analytics Agent.")
        raise typer.Exit(code=1)

    state.ca_agent_id = agent_id
    state.ca_agent_name = name or f"{model_name.replace('_', ' ').title()} Assistant"

    # Extract & Link Golden Queries
    golden_queries: list[dict[str, Any]] = []

    # From deployed dashboard ID
    if dashboard_id:
        print_info(f"Extracting Golden Queries from deployed dashboard `{dashboard_id}`...")
        golden_queries.extend(extract_golden_queries_from_dashboard_id(base_url, headers, dashboard_id))

    # From local dashboard files
    dash_path = dashboard_file or dashboards_dir or (state.lookml_output_dir / "dashboards" if state.lookml_output_dir else None)
    if dash_path and dash_path.exists():
        d_dir = dash_path if dash_path.is_dir() else dash_path.parent
        print_info(f"Extracting Golden Queries from local dashboard files in `{d_dir}`...")
        golden_queries.extend(
            extract_golden_queries_from_dashboards(
                lookml_dir=d_dir.parent if d_dir.name == "dashboards" else d_dir,
                default_model=model_name,
                default_explore=explore_name,
                dashboard_file=dashboard_file if (dashboard_file and dashboard_file.is_file()) else None,
            )
        )

    if golden_queries:
        print_info(f"Registering {len(golden_queries)} Golden Queries to CA Agent `{agent_id}`...")
        linked_count = register_and_link_golden_queries(base_url, headers, agent_id, golden_queries)
        state.golden_queries_count = linked_count

    # Optional: Gemini Enterprise Enablement & Publish
    if publish_ge:
        state = ensure_gemini_enterprise_configured(
            state, headers, interactive=not non_interactive
        )
        state.published_to_ge = publish_agent_to_ge(base_url, agent_id, headers)

    saved_path = save_flow_state(state)
    chat_url = f"{base_url}/conversational-analytics/agents/{agent_id}"
    print_success(f"Conversational Analytics Agent Chat URL: {chat_url}")
    print_info(f"Updated state saved to `{saved_path}`")


@agent_app.command(name="golden-queries")
def agent_golden_queries(
    agent_id: Annotated[Optional[str], typer.Option("--agent-id", help="Target CA Agent ID")] = None,
    dashboard_id: Annotated[Optional[str], typer.Option("--dashboard-id", help="Deployed Looker dashboard ID")] = None,
    dashboards_dir: Annotated[Optional[Path], typer.Option("--dashboards-dir", help="Directory with *.dashboard.lookml files")] = None,
    dashboard_file: Annotated[Optional[Path], typer.Option("--dashboard-file", help="Specific *.dashboard.lookml file")] = None,
    account: Annotated[Optional[str], typer.Option("--looker-account", help="Saved Looker OAuth account alias")] = None,
    instance_url: Annotated[Optional[str], typer.Option("--instance", help="Looker instance base URL")] = None,
):
    """Extract queries from dashboard files or IDs and link as Golden Queries to an existing agent."""
    state = load_flow_state()
    target_id = agent_id or state.ca_agent_id
    if not target_id:
        print_error("No agent ID specified. Provide --agent-id or run `demo-create agent create` first.")
        raise typer.Exit(code=1)

    headers, base_url = get_looker_auth_context(instance_url=instance_url or state.looker_instance_url, preferred_account=account or state.looker_account)
    if not base_url:
        print_error("Looker instance URL not configured.")
        raise typer.Exit(code=1)

    golden_queries = []
    if dashboard_id:
        golden_queries.extend(extract_golden_queries_from_dashboard_id(base_url, headers, dashboard_id))

    dash_path = dashboard_file or dashboards_dir or (state.lookml_output_dir / "dashboards" if state.lookml_output_dir else None)
    if dash_path and dash_path.exists():
        d_dir = dash_path if dash_path.is_dir() else dash_path.parent
        golden_queries.extend(
            extract_golden_queries_from_dashboards(
                lookml_dir=d_dir.parent if d_dir.name == "dashboards" else d_dir,
                default_model=state.lookml_model_name,
                default_explore=state.generated_tables[0] if state.generated_tables else state.lookml_model_name,
            )
        )

    if not golden_queries:
        print_warning("No query tiles found to extract.")
        return

    count = register_and_link_golden_queries(base_url, headers, target_id, golden_queries)
    state.golden_queries_count = count
    save_flow_state(state)


@agent_app.command(name="publish")
def agent_publish(
    agent_id: Annotated[
        Optional[str], typer.Option("--agent-id", help="Target CA Agent ID to publish")
    ] = None,
    non_interactive: Annotated[
        bool,
        typer.Option(
            "--non-interactive",
            help="Run non-interactively without prompting for GE reconfigurations",
        ),
    ] = False,
    account: Annotated[
        Optional[str],
        typer.Option("--looker-account", help="Saved Looker OAuth account alias"),
    ] = None,
    instance_url: Annotated[
        Optional[str], typer.Option("--instance", help="Looker instance base URL")
    ] = None,
):
    """Verify Gemini Enterprise configuration and publish CA Agent to connected GE apps."""
    state = load_flow_state()
    target_id = agent_id or state.ca_agent_id
    if not target_id:
        print_error("No CA agent ID found. Provide --agent-id or run `demo-create agent create` first.")
        raise typer.Exit(code=1)

    headers, base_url = get_looker_auth_context(instance_url=instance_url or state.looker_instance_url, preferred_account=account or state.looker_account)
    if not base_url:
        print_error("Looker instance URL not configured.")
        raise typer.Exit(code=1)

    state = ensure_gemini_enterprise_configured(
        state, headers, interactive=not non_interactive
    )
    published = publish_agent_to_ge(base_url, target_id, headers)
    state.published_to_ge = published
    save_flow_state(state)


# -------------------------------------------------------------------------
# EMBED GROUP: demo-create embed [scaffold]
# -------------------------------------------------------------------------
embed_app = typer.Typer(
    name="embed",
    help="Scaffold standalone Embedded Analytics web applications and portals.",
    no_args_is_help=True,
)
app.add_typer(embed_app, name="embed")


@embed_app.command(name="scaffold")
def embed_scaffold(
    project: Annotated[Optional[str], typer.Option("--project", help="Demo project name")] = None,
    target_dir: Annotated[Optional[Path], typer.Option("--target-dir", help="Directory where the web app will be scaffolded")] = None,
    dashboard_id: Annotated[Optional[str], typer.Option("--dashboard-id", help="Looker dashboard ID to embed")] = None,
    brand_name: Annotated[Optional[str], typer.Option("--brand-name", help="Customer brand display name")] = None,
    instance_url: Annotated[Optional[str], typer.Option("--instance", help="Looker instance URL")] = None,
):
    """Scaffold a full-stack React/Vite analytics embed portal workspace."""
    state = load_flow_state()
    proj_name = project or state.looker_project_name
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

    print_success(f"External Embed Portal configured at: `{scaffolded_dir}`")
    saved_path = save_flow_state(state)
    print_info(f"Updated state saved to `{saved_path}`")


@app.command(
    name="run-script",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def run_script(
    script_path: Annotated[Path, typer.Argument(help="Path to Python script to execute with CLI environment")],
    ctx: typer.Context,
):
    """Execute a Python script using the CLI's bundled runtime environment and dependencies."""
    if not script_path.exists():
        print_error(f"Script file not found: {script_path}")
        raise typer.Exit(code=1)

    cmd = [sys.executable, str(script_path)] + ctx.args
    res = subprocess.run(cmd)
    raise typer.Exit(code=res.returncode)


@app.command(
    name="python",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def run_python(ctx: typer.Context):
    """Execute Python commands within the CLI's environment (e.g. demo-create python -c '...')."""
    cmd = [sys.executable] + ctx.args
    res = subprocess.run(cmd)
    raise typer.Exit(code=res.returncode)


if __name__ == "__main__":
    app()

