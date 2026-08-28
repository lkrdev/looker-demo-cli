# SPDX-FileCopyrightText: Copyright (c) 2026 lkr.dev. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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
from looker_demo_cli.precheck.gcp_auth import inspect_gcp_accounts
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

    # 1. Inspect GCP & ADC
    gcp_accounts = inspect_gcp_accounts(target_project=gcp_project)
    
    # 2. Check MCP Servers
    mcp_statuses = check_mcp_servers()
    if fix:
        patch_mcp_config()
        mcp_statuses = check_mcp_servers()

    # 3. Organize Skills
    skill_statuses = audit_and_organize_skills(fix=fix)

    # 4. Check Looker Auth
    looker_status = check_looker_auth()

    if output_json:
        report = {
            "gcp_accounts": [a.model_dump() for a in gcp_accounts],
            "mcp_servers": [m.model_dump() for m in mcp_statuses],
            "skills": [s.model_dump() for s in skill_statuses],
            "looker_auth": looker_status.model_dump(),
        }
        typer.echo(json.dumps(report, indent=2))
        return

    # Render Visual Summary
    console.print("\n[bold cyan]1. GCP & ADC Credentials[/bold cyan]")
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

    console.print("\n[bold cyan]2. Global MCP Tool Configurations[/bold cyan]")
    t_mcp = Table(show_header=True, header_style="bold blue")
    t_mcp.add_column("MCP Server")
    t_mcp.add_column("Status")
    t_mcp.add_column("Details")

    for m in mcp_statuses:
        status_label = "[green]CONFIGURED[/green]" if m.is_configured else "[red]MISSING[/red]"
        details = ", ".join(m.issues) if m.issues else "Ready"
        t_mcp.add_row(m.server_name, status_label, details)
    console.print(t_mcp)

    console.print("\n[bold cyan]3. Intent-Organized Agent Skills (~/.gemini/config/skills/)[/bold cyan]")
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

    console.print("\n[bold cyan]4. Looker Instance Authentication[/bold cyan]")
    if looker_status.is_authenticated:
        print_success(f"Connected to {looker_status.instance_url} as {looker_status.user_name} ({looker_status.user_email})")
        if looker_status.has_default_bigquery_conn:
            print_success("Database connection `default_bigquery_connection` is configured.")
        else:
            print_warning("Connection `default_bigquery_connection` not found in available connections.")
    else:
        print_error(f"Failed to connect to Looker: {looker_status.error_message}")

    if not fix and any(not m.is_configured for m in mcp_statuses):
        print_info("\nTip: Run `demo-create pre-check --fix` to automatically repair missing MCP configs and organize skills.")


@app.command(name="run")
def run_flow(
    project_name: Annotated[str, typer.Option("--project", help="Looker project and dataset name")] = "logistics_analytics",
    dataset_name: Annotated[Optional[str], typer.Option("--dataset", help="Target BigQuery dataset ID")] = None,
    scope: Annotated[str, typer.Option("--scope", help="Demo scope: internal or external")] = "internal",
    gcp_project: Annotated[str, typer.Option("--gcp-project", help="Target GCP Project ID")] = DEFAULT_GCP_PROJECT,
    gcp_account: Annotated[Optional[str], typer.Option("--gcp-account", help="Specific authenticated GCP user account")] = None,
    scratch_dir: Annotated[Path, typer.Option("--scratch-dir", help="Local scratch work directory")] = Path.home() / "scratch" / "demo_create",
    agent_mode: Annotated[bool, typer.Option("--agent-mode", help="Non-interactive execution mode for AI agents")] = False,
):
    """Execute the full deterministic demo creation workflow."""
    ds_name = dataset_name or project_name
    demo_scope_val = "external_embed" if "ext" in scope.lower() else "internal_looker"

    state = FlowState(
        gcp_project_id=gcp_project,
        gcp_account=gcp_account,
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
