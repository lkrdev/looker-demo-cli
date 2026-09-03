from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated, List, Optional
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
from looker_demo_cli.workflow.runner import FlowRunner
from looker_demo_cli.workflow.state import FlowState

app = typer.Typer(
    name="demo-create",
    help="End-to-end Looker demo creation orchestrator CLI for AI agents and developers.",
    no_args_is_help=True,
)


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
        (a.is_active and not a.has_bigquery_access) or ("reauth" in (a.error_message or "").lower())
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

