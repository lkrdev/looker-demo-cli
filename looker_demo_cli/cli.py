from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional
import typer
from rich.table import Table

from looker_demo_cli.config import (
    DEFAULT_GCP_PROJECT,
    DEFAULT_LOOKER_INSTANCE_URL,
    GEMINI_MCP_CONFIG,
    GEMINI_SKILLS_DIR,
)
from looker_demo_cli.precheck.gcp_auth import (
    get_gcp_active_context,
    inspect_gcp_accounts,
    list_available_gcp_projects,
)
from looker_demo_cli.precheck.looker_auth import check_looker_auth
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


@app.command(name="pre-check")
def pre_check(
    fix: Annotated[bool, typer.Option("--fix", help="Automatically install missing MCP configs and organize global skills")] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Emit raw JSON status report for agent programmatic consumption")] = False,
    gcp_project: Annotated[str, typer.Option("--gcp-project", help="Target Google Cloud Project ID")] = DEFAULT_GCP_PROJECT,
):
    """Inspect and configure GCP/ADC accounts, MCP server definitions, and intent-based skill subfolders."""
    if not output_json:
        print_banner("PRE-CHECK: ENVIRONMENT, MCP & SKILL AUDIT", f"Target GCP Project: {gcp_project}")

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

    # Determine if re-auth is needed
    reauth_required = any(
        (a.is_active and not a.has_bigquery_access) or ("reauth" in (a.error_message or "").lower())
        for a in gcp_accounts
    )
    no_accounts_configured = len(gcp_accounts) == 0

    if output_json:
        report = {
            "active_gcp_context": gcp_context.model_dump(),
            "gcp_accounts": [a.model_dump() for a in gcp_accounts],
            "available_gcp_projects": available_projects,
            "reauth_required": reauth_required,
            "mcp_servers": [m.model_dump() for m in mcp_statuses],
            "skills": [s.model_dump() for s in skill_statuses],
            "looker_auth": looker_status.model_dump(),
            "agent_instructions": {
                "mandatory_stop_gate": "CRITICAL: STOP all further tool executions immediately after pre-check. Do NOT probe database connections, inspect models, or test Looker SDKs. You MUST prompt the user (via ask_question) to confirm the 4 environment targets below before taking any further action.",
                "gcp_account": "Prompt user to select/confirm the active GCP account from gcp_accounts.",
                "gcp_project": "Prompt user to select/confirm the target Google Cloud project from available_gcp_projects.",
                "looker_instance": "Prompt user to select/confirm the target Looker OAuth instance from available_oauth_instances (e.g. dev-looker.lukapuka.co vs dev-googledemo2).",
                "database_connection": "Prompt user to confirm the Looker database connection name (suggest defaults like looker_demo_bigquery / default_bigquery_connection, or allow write-in).",
                "reauth_action": "If reauth_required is true, prompt the user to run 'gcloud auth login' and 'gcloud auth application-default login'.",
                "setup_commands": "If no accounts or projects are configured on gcloud, prompt the user to run: 'gcloud auth login', 'gcloud auth application-default login', and 'gcloud config set project <PROJECT_ID>'.",
            },
        }
        typer.echo(json.dumps(report, indent=2))
        return

    # Render Visual Summary
    console.print("\n[bold cyan]1. GCP & ADC Context and Credentials[/bold cyan]")
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

    if no_accounts_configured:
        console.print("\n[bold red]⚠️  No GCP Accounts Configured[/bold red]")
        console.print("[yellow]Please run the following commands to authenticate with Google Cloud:[/yellow]")
        console.print("  [bold white]$ gcloud auth login[/bold white]")
        console.print("  [bold white]$ gcloud auth application-default login[/bold white]")
        console.print("  [bold white]$ gcloud config set project <PROJECT_ID>[/bold white]")

    elif reauth_required:
        console.print("\n[bold red]⚠️  GCP Reauthentication Required[/bold red]")
        console.print("[yellow]Agent & User Instruction:[/yellow] One or more active GCP accounts require reauthentication.")
        console.print("Please prompt the user to run the following in their terminal:")
        console.print("  [bold white]$ gcloud auth login[/bold white]")
        console.print("  [bold white]$ gcloud auth application-default login[/bold white]")

    console.print("\n[bold cyan]2. Available Google Cloud Projects[/bold cyan]")
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

    console.print("\n[bold cyan]3. Global MCP Tool Configurations[/bold cyan]")
    t_mcp = Table(show_header=True, header_style="bold blue")
    t_mcp.add_column("MCP Server")
    t_mcp.add_column("Status")
    t_mcp.add_column("Details")

    for m in mcp_statuses:
        status_label = "[green]CONFIGURED[/green]" if m.is_configured else "[red]MISSING[/red]"
        details = ", ".join(m.issues) if m.issues else "Ready"
        t_mcp.add_row(m.server_name, status_label, details)
    console.print(t_mcp)

    console.print("\n[bold cyan]4. Intent-Organized Agent Skills (~/.gemini/config/skills/)[/bold cyan]")
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

    console.print("\n[bold cyan]5. Looker Authentication (OAuth / API Key)[/bold cyan]")
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
        console.print("[yellow]To authenticate Looker, choose one of the following methods:[/yellow]")
        console.print("  1. [bold white]OAuth (Recommended):[/bold white] Run `uvx --from \"lkr-dev-cli[codemode]\" lkr-dev-cli auth login`")
        console.print("  2. [bold white]API Key:[/bold white] Set `LOOKERSDK_BASE_URL`, `LOOKERSDK_CLIENT_ID`, `LOOKERSDK_CLIENT_SECRET` (or add to `.env`)")
        console.print("[dim]Note: Code Mode commands execute directly via CLI (`uvx --from 'lkr-dev-cli[codemode]' lkr-dev-cli code-mode sandbox --code='...'`)[/dim]")

    if not fix and any(not m.is_configured for m in mcp_statuses):
        print_info("\nTip: Run `demo-create pre-check --fix` to automatically repair missing MCP configs and organize skills.")


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


if __name__ == "__main__":
    app()
