"""``demo-create pre-check`` -- environment, credential, and skill-tree audit."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from looker_demo_cli.commands.env import render_env_tables
from looker_demo_cli.commands.options import StateFileOption
from looker_demo_cli.config import DEFAULT_GCP_PROJECT, GEMINI_SKILLS_DIR
from looker_demo_cli.context import AppContext, get_context
from looker_demo_cli.errors import AuthError
from looker_demo_cli.output import CommandResult, ErrorDetail, emit
from looker_demo_cli.precheck.env_checker import (
    RuntimeEnvironmentStatus,
    check_runtime_environment,
    init_workspace_venv,
)
from looker_demo_cli.precheck.gcp_auth import (
    GCPAccountInfo,
    GCPActiveContext,
    get_gcp_active_context,
    inspect_gcp_accounts,
    list_available_gcp_projects,
)
from looker_demo_cli.precheck.looker_auth import (
    LKR_OAUTH_CLIENT_PAYLOAD,
    LookerAuthStatus,
    check_looker_auth,
)
from looker_demo_cli.precheck.mcp_checker import MCPStatus, check_mcp_servers, patch_mcp_config
from looker_demo_cli.precheck.skills_organizer import SkillInstallStatus, audit_and_organize_skills
from looker_demo_cli.utils.console import (
    console,
    print_banner,
    print_info,
    print_success,
    print_warning,
)


def register(app: typer.Typer) -> None:
    """Register this module's root-level commands on the given app.

    ``pre-check`` and ``skills`` are not a command *group* -- they sit directly
    under ``demo-create`` -- so there is no ``typer.Typer`` to hand to
    ``add_typer``. Registering here rather than decorating at definition keeps
    the module free of a module-level dependency on the root app, which would
    be a circular import.

    Args:
        app: The root ``demo-create`` Typer application.
    """
    app.command(name="pre-check")(pre_check)
    app.command(name="skills")(list_skills)


# ---------------------------------------------------------------------------
# Audit collection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AuditFindings:
    """Everything gate 0 observed, before any of it is rendered or serialized.

    Collecting the raw observations into one value keeps the derived
    conclusions -- ``reauth_required``, ``blocking_reasons``, ``is_blocked`` --
    computed in exactly one place. Both output modes then read the same
    conclusions rather than each re-deriving them, which is how the two paths
    used to drift.

    Attributes:
        env_status: Python runtime and dependency pin health.
        gcp_context: Active gcloud configuration and ADC pointers.
        effective_project: The project BigQuery access was probed against.
        gcp_accounts: Every configured gcloud account, with access verdicts.
        available_projects: Projects the caller can see, for target selection.
        mcp_statuses: Per-server MCP configuration state.
        skill_statuses: Per-skill install state under the global skills tree.
        looker_status: Looker OAuth/API-key authentication state.
    """

    env_status: RuntimeEnvironmentStatus
    gcp_context: GCPActiveContext
    effective_project: str
    gcp_accounts: list[GCPAccountInfo]
    available_projects: list[dict[str, str]]
    mcp_statuses: list[MCPStatus]
    skill_statuses: list[SkillInstallStatus]
    looker_status: LookerAuthStatus

    @property
    def no_accounts_configured(self) -> bool:
        """Whether gcloud knows about no accounts at all."""
        return len(self.gcp_accounts) == 0

    @property
    def reauth_required(self) -> bool:
        """Whether the active GCP account needs re-authentication.

        .. note::
           An active account that merely *lacks BigQuery IAM* also lands here.
           That misdiagnosis is pinned by an existing characterization test and
           is deliberately preserved.
        """
        return any(
            (a.is_active and not a.has_bigquery_access) or (a.is_active and "reauth" in (a.error_message or "").lower())
            for a in self.gcp_accounts
        )

    @property
    def has_any_bigquery_access(self) -> bool:
        """Whether any configured account can reach BigQuery on the target."""
        return any(a.has_bigquery_access for a in self.gcp_accounts)

    @property
    def blocking_reasons(self) -> list[str]:
        """The ordered reasons the pipeline must not proceed.

        GCP is reported before Looker so a user resolves prerequisites in
        dependency order, and the three GCP conditions are mutually exclusive:
        "no accounts" subsumes "needs reauth", which subsumes "no access".
        Reporting all three would tell the user to fix problems that only exist
        because of the first one.

        Returns:
            One human-readable sentence per blocker; empty when unblocked.
        """
        reasons: list[str] = []
        if self.no_accounts_configured:
            reasons.append(
                "No Google Cloud accounts configured. Run `gcloud auth login` and "
                "`gcloud auth application-default login`."
            )
        elif self.reauth_required:
            reasons.append(
                "Active GCP account requires reauthentication. Run `gcloud auth login` and "
                "`gcloud auth application-default login`."
            )
        elif not self.has_any_bigquery_access:
            reasons.append(
                f"No configured GCP account has BigQuery access on project '{self.effective_project or 'default'}'."
            )

        if not self.looker_status.is_authenticated:
            reasons.append(
                "Looker is not authenticated "
                f"({self.looker_status.error_message or 'No active OAuth session or API key'})."
            )
        return reasons

    @property
    def is_blocked(self) -> bool:
        """Whether any blocker was found."""
        return len(self.blocking_reasons) > 0


def _audit_runtime_environment(fix: bool) -> RuntimeEnvironmentStatus:
    """Inspect the Python runtime, optionally bootstrapping a workspace venv.

    Args:
        fix: When true and the caller is on bare system Python, create a local
            ``.venv`` and re-inspect, so the report describes the environment
            as it now stands rather than as it was found.

    Returns:
        The (possibly post-remediation) runtime status.
    """
    env_status = check_runtime_environment()
    if fix and not env_status.is_virtualenv:
        print_info("Fix flag enabled: initializing local workspace virtual environment...")
        init_workspace_venv(Path.cwd())
        env_status = check_runtime_environment()
    return env_status


def _resolve_effective_project(gcp_project: str, gcp_context: GCPActiveContext) -> str:
    """Pick the project to probe BigQuery access against.

    Args:
        gcp_project: The ``--gcp-project`` value, which always wins so that a
            target the user confirmed at gate 0 is never silently swapped.
        gcp_context: Active gcloud/ADC context, used as the fallback chain.

    Returns:
        The resolved project ID, or ``""`` when nothing is configured anywhere.
    """
    return gcp_project or gcp_context.active_project or gcp_context.adc_project_id or ""


def _audit_mcp_servers(fix: bool) -> list[MCPStatus]:
    """Inspect global MCP server definitions, optionally repairing them.

    Args:
        fix: When true, patch missing server definitions and re-read the
            config so the report reflects the repaired state.

    Returns:
        One status per expected MCP server.
    """
    mcp_statuses = check_mcp_servers()
    if fix:
        patch_mcp_config()
        mcp_statuses = check_mcp_servers()
    return mcp_statuses


def _collect_findings(*, fix: bool, gcp_project: str) -> _AuditFindings:
    """Run every gate 0 probe and gather the results.

    The call order is part of the contract: GCP context must be resolved before
    accounts are inspected (it supplies the fallback project), and Looker is
    probed last because it is the only network-bound check that can hang.

    Args:
        fix: Apply remediations (venv bootstrap, MCP patch, skill symlinks)
            rather than only reporting on what is missing.
        gcp_project: The ``--gcp-project`` value.

    Returns:
        The complete set of observations.
    """
    env_status = _audit_runtime_environment(fix)

    gcp_context = get_gcp_active_context()
    effective_project = _resolve_effective_project(gcp_project, gcp_context)
    gcp_accounts = inspect_gcp_accounts(target_project=effective_project)
    available_projects = list_available_gcp_projects()

    mcp_statuses = _audit_mcp_servers(fix)
    skill_statuses = audit_and_organize_skills(fix=fix)
    looker_status = check_looker_auth()

    return _AuditFindings(
        env_status=env_status,
        gcp_context=gcp_context,
        effective_project=effective_project,
        gcp_accounts=gcp_accounts,
        available_projects=available_projects,
        mcp_statuses=mcp_statuses,
        skill_statuses=skill_statuses,
        looker_status=looker_status,
    )


# ---------------------------------------------------------------------------
# Machine-readable report
# ---------------------------------------------------------------------------


def _build_agent_instructions(is_blocked: bool, blocking_reasons: list[str]) -> dict[str, Any]:
    """Build the block of literal instructions an AI orchestrator replays.

    These strings are the recovery path for a blocked pipeline -- an agent
    reads them and relays them verbatim to the user -- so they are a published
    contract rather than prose.

    Args:
        is_blocked: Whether the audit found any blocker.
        blocking_reasons: The blockers, repeated here so an agent reading only
            this sub-object still sees them.

    Returns:
        The ``agent_instructions`` payload.
    """
    return {
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
    }


def _build_report(findings: _AuditFindings) -> dict[str, Any]:
    """Serialize the audit into the envelope's ``data`` payload.

    Args:
        findings: The collected observations.

    Returns:
        The full machine-readable report.
    """
    is_blocked = findings.is_blocked
    blocking_reasons = findings.blocking_reasons
    return {
        "is_blocked": is_blocked,
        "blocking_reasons": blocking_reasons,
        "runtime_environment": findings.env_status.model_dump(),
        "active_gcp_context": findings.gcp_context.model_dump(),
        "gcp_accounts": [a.model_dump() for a in findings.gcp_accounts],
        "available_gcp_projects": findings.available_projects,
        "reauth_required": findings.reauth_required,
        "mcp_servers": [m.model_dump() for m in findings.mcp_statuses],
        "skills": [s.model_dump() for s in findings.skill_statuses],
        "looker_auth": findings.looker_status.model_dump(),
        "oauth_registration_payload": LKR_OAUTH_CLIENT_PAYLOAD,
        "agent_instructions": _build_agent_instructions(is_blocked, blocking_reasons),
    }


def _build_json_result(findings: _AuditFindings) -> CommandResult:
    """Wrap the report in the standard envelope.

    Each blocker becomes its own ``errors[]`` entry rather than one collapsed
    message, so a caller can tell whether GCP, Looker, or both need attention
    without re-splitting prose.

    Args:
        findings: The collected observations.

    Returns:
        A ``BLOCKED`` envelope (which exits with :class:`AuthError`'s code, 3)
        when anything blocks, otherwise a ``SUCCESS`` envelope carrying the
        gate 1 follow-up.
    """
    blocking_reasons = findings.blocking_reasons
    is_blocked = findings.is_blocked

    # BLOCKED is a failure status, so this exits with AuthError's code (3):
    # an orchestrator can branch on "re-authenticate" without reading prose.
    result = CommandResult(
        command="pre-check",
        status="BLOCKED" if is_blocked else "SUCCESS",
        data=_build_report(findings),
        errors=[
            ErrorDetail(
                code="AUTH_ERROR",
                message=reason,
                remediation="Complete the commands in data.agent_instructions, then re-run `demo-create pre-check --fix`.",
            )
            for reason in blocking_reasons
        ],
    )
    if not is_blocked:
        result.add_next_action(
            "Confirm the four environment targets with the user, then design the schema",
            gate=1,
            requires_human_confirmation=True,
        )
    return result


# ---------------------------------------------------------------------------
# Rich (human) report
# ---------------------------------------------------------------------------


def _render_gcp_section(gcp_context: GCPActiveContext, gcp_accounts: list[GCPAccountInfo]) -> None:
    """Render section 2: active gcloud/ADC context and per-account verdicts.

    Args:
        gcp_context: Active gcloud configuration and ADC pointers.
        gcp_accounts: Configured accounts. An empty list renders no table --
            the blocked panel below already says there are none.
    """
    console.print("\n[bold cyan]2. GCP & ADC Context and Credentials[/bold cyan]")
    t_ctx = Table(show_header=True, header_style="bold blue")
    t_ctx.add_column("Setting", style="dim")
    t_ctx.add_column("Value", style="bold")
    t_ctx.add_row("Active gcloud Config", gcp_context.active_config_name or "[dim]None[/dim]")
    t_ctx.add_row("Active gcloud Account", gcp_context.active_account or "[dim]None[/dim]")
    t_ctx.add_row("Active gcloud Project", gcp_context.active_project or "[dim]None[/dim]")
    t_ctx.add_row("ADC Project", gcp_context.adc_project_id or "[dim]None[/dim]")
    t_ctx.add_row("ADC Quota Project", gcp_context.adc_quota_project_id or "[dim]None[/dim]")
    t_ctx.add_row(
        "ADC Credentials File", f"{gcp_context.adc_file_path} ({'Found' if gcp_context.adc_file_exists else 'Missing'})"
    )
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
            print_info(
                "Multiple GCP accounts found. Agent/User Instruction: Confirm which GCP account to use for synthesis."
            )


def _render_projects_section(available_projects: list[dict[str, str]]) -> None:
    """Render section 3: the projects the caller may target.

    Args:
        available_projects: Projects from ``gcloud projects list``. When empty
            the user is given the command that fixes it, since an empty list is
            far more often an unset active project than a genuinely empty org.
    """
    console.print("\n[bold cyan]3. Available Google Cloud Projects[/bold cyan]")
    if available_projects:
        t_proj = Table(show_header=True, header_style="bold blue")
        t_proj.add_column("Project ID", style="bold")
        t_proj.add_column("Name")
        t_proj.add_column("Project Number", style="dim")
        for p in available_projects:
            t_proj.add_row(p["project_id"], p["name"], p["project_number"])
        console.print(t_proj)
        print_info(
            "Agent Instruction: Confirm with the user which of the available GCP projects above to use for demo synthesis and BigQuery datasets."
        )
    else:
        print_warning("No Google Cloud projects found or `gcloud projects list` returned empty.")
        console.print("[yellow]To set your active GCP project, run:[/yellow]")
        console.print("  [bold white]$ gcloud config set project <PROJECT_ID>[/bold white]")


def _render_mcp_section(mcp_statuses: list[MCPStatus]) -> None:
    """Render section 4: global MCP tool configuration state.

    Args:
        mcp_statuses: One status per expected MCP server.
    """
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


def _render_skills_section(skill_statuses: list[SkillInstallStatus]) -> None:
    """Render section 5: the intent-organized global agent skill tree.

    Args:
        skill_statuses: One status per skill, install and source state.
    """
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


def _render_looker_section(looker_status: LookerAuthStatus) -> None:
    """Render section 6: Looker authentication and connection availability.

    Args:
        looker_status: Looker OAuth/API-key authentication state.
    """
    console.print("\n[bold cyan]6. Looker Authentication (OAuth / API Key)[/bold cyan]")
    if looker_status.is_authenticated:
        auth_badge = (
            f"[cyan]OAuth ({looker_status.oauth_account})[/cyan]"
            if looker_status.auth_method == "oauth"
            else "[cyan]API Key[/cyan]"
        )
        print_success(
            f"Connected via {auth_badge} to {looker_status.instance_url} as {looker_status.user_name} ({looker_status.user_email})"
        )
        if looker_status.has_default_bigquery_conn:
            print_success("Database connection `default_bigquery_connection` is configured.")
        else:
            print_warning("Connection `default_bigquery_connection` not found in available connections.")
    else:
        print_warning(f"Looker not authenticated: {looker_status.error_message}")
        if looker_status.available_oauth_instances:
            console.print(
                f"[dim]Found {len(looker_status.available_oauth_instances)} saved OAuth session(s) in ~/.lkr/auth.db[/dim]"
            )


def _render_human_report(findings: _AuditFindings, *, fix: bool) -> None:
    """Render the full six-section interactive audit to stderr.

    Args:
        findings: The collected observations.
        fix: Whether remediations were already applied. When they were not and
            something is repairable, the one-command repair is advertised.
    """
    render_env_tables(findings.env_status)
    _render_gcp_section(findings.gcp_context, findings.gcp_accounts)
    _render_projects_section(findings.available_projects)
    _render_mcp_section(findings.mcp_statuses)
    _render_skills_section(findings.skill_statuses)
    _render_looker_section(findings.looker_status)

    if not fix and any(not m.is_configured for m in findings.mcp_statuses):
        print_info(
            "\nTip: Run `demo-create pre-check --fix` to automatically repair missing MCP configs and organize skills."
        )


# ---------------------------------------------------------------------------
# Blocked guidance
# ---------------------------------------------------------------------------


def _render_blocked_panel(blocking_reasons: list[str]) -> None:
    """Render the red "execution blocked" banner listing every blocker.

    Args:
        blocking_reasons: The blockers, one bullet each.
    """
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


def _render_gcp_remediation() -> None:
    """Print the gcloud login and ADC commands that unblock GCP."""
    console.print("\n[bold cyan]1. Google Cloud Authentication Commands:[/bold cyan]")
    console.print("  Authenticate your GCP user account and configure Application Default Credentials (ADC):")
    console.print("    [bold white]$ gcloud auth login[/bold white]")
    console.print("    [bold white]$ gcloud auth application-default login[/bold white]")
    console.print("    [bold white]$ gcloud config set project <PROJECT_ID>[/bold white]")


def _render_looker_remediation() -> None:
    """Print the full Looker OAuth setup runbook.

    Covers first-time client registration, SSH port forwarding, port conflicts,
    and the headless callback fallback, because on a remote workstation the
    plain ``lkr auth login`` path fails at one of those four points far more
    often than it succeeds.
    """
    console.print("\n[bold cyan]2. Looker Authentication & OAuth Setup:[/bold cyan]")
    console.print("  [bold]A. Login via OAuth (Recommended):[/bold]")
    console.print("     [bold white]$ lkr auth login[/bold white]")
    console.print('     (or: [bold white]$ uvx --from "lkr-dev-cli[codemode]" lkr-dev-cli auth login[/bold white])')

    console.print("\n  [bold]B. First-Time OAuth Client Registration in Looker:[/bold]")
    console.print("     If `lkr-cli` has not been registered on this Looker instance, an admin must register it once:")
    console.print("     [bold underline]API Explorer Method:[/bold underline]")
    console.print(
        "     Open: [cyan]https://<your-looker-instance>/extensions/marketplace_extension_api_explorer::api-explorer/4.0/methods/Auth/register_oauth_client_app[/cyan]"
    )
    console.print("     - [bold]client_id:[/bold] [green]lkr-cli[/green]")
    console.print("     - [bold]Body (JSON):[/bold]")
    oauth_payload_json = json.dumps(LKR_OAUTH_CLIENT_PAYLOAD, indent=2)
    console.print(Syntax(oauth_payload_json, "json", theme="monokai", line_numbers=False))
    console.print('     - Check [bold]"I Understand"[/bold] and click [bold]"Run"[/bold].')

    console.print("\n  [bold]C. Remote Host /   / SSH Port Forwarding:[/bold]")
    console.print(
        "     Because the OAuth callback redirects to [cyan]http://localhost:8000/callback[/cyan], forward port 8000 from your local machine:"
    )
    console.print("     [bold white]$ ssh -L 8000:localhost:8000 <remote-host>[/bold white]")

    console.print("\n  [bold]D. Port 8000 Conflict Resolution:[/bold]")
    console.print("     If port 8000 is in use by another process, free it before running `lkr auth login`:")
    console.print("     [bold white]$ lsof -ti:8000 | xargs kill -9[/bold white]   [dim](or: fuser -k 8000/tcp)[/dim]")

    console.print("\n  [bold]E. Headless / Agent OAuth Callback Fallback:[/bold]")
    console.print(
        "     If your browser redirects to [cyan]http://localhost:8000/callback?code=...[/cyan] and cannot load the page,"
    )
    console.print("     copy the full URL from your browser address bar and paste it into chat.")
    console.print(
        "     The AI agent will curl the callback URL locally on the remote machine to finish authentication."
    )

    console.print("\n  [bold]Alternatively, configure API Keys in your environment or `.env`:[/bold]")
    console.print("     LOOKERSDK_BASE_URL=https://<your-instance>.looker.com")
    console.print("     LOOKERSDK_CLIENT_ID=<client_id>")
    console.print("     LOOKERSDK_CLIENT_SECRET=<client_secret>")


def _render_blocked_guidance(findings: _AuditFindings) -> None:
    """Render the blocked banner plus whichever remediation runbooks apply.

    Args:
        findings: The collected observations, consulted to decide which of the
            two runbooks to print.
    """
    _render_blocked_panel(findings.blocking_reasons)

    if findings.no_accounts_configured or findings.reauth_required or not findings.has_any_bigquery_access:
        _render_gcp_remediation()

    if not findings.looker_status.is_authenticated:
        _render_looker_remediation()

    console.print(
        "\n[bold red]Execution blocked. Please complete authentication and re-run `demo-create pre-check --fix`.[/bold red]\n"
    )


def _persist_gate_zero_verdict(app_ctx: AppContext, findings: _AuditFindings) -> None:
    """Record the gate 0 verdict so ``demo-create status`` can read it.

    Written on *both* outcomes, not just success: an environment that passed
    yesterday and is blocked today must flip back to blocked, otherwise the
    status command reports a stale green and an orchestrating agent walks
    straight into gate 1 with dead credentials.

    The confirmed project is persisted alongside it because every later gate
    needs it, and re-deriving it from ``$GOOGLE_CLOUD_PROJECT`` in each command
    is exactly how the target drifts mid-build.

    Args:
        app_ctx: The resolved application context owning the state file.
        findings: The completed gate 0 audit.
    """
    state = app_ctx.state
    state.precheck_passed = not findings.is_blocked
    if findings.effective_project:
        state.gcp_project_id = findings.effective_project
    if findings.gcp_context.active_account:
        state.gcp_account = findings.gcp_context.active_account
    app_ctx.save_state()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def pre_check(
    ctx: typer.Context,
    fix: Annotated[
        bool, typer.Option("--fix", help="Automatically install missing MCP configs and organize global skills")
    ] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    gcp_project: Annotated[
        str, typer.Option("--gcp-project", help="Target Google Cloud Project ID")
    ] = DEFAULT_GCP_PROJECT,
    state_file: StateFileOption = None,
):
    """Audit GCP/ADC credentials, MCP server definitions, and agent skill folders.

    This is Gate 0: no other command should run until this reports unblocked.
    The verdict is persisted to the state file as ``precheck_passed`` so that
    ``demo-create status`` can report gate 0 as cleared without re-running the
    (slow, network-bound) audit.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        fix: Install missing MCP configs, bootstrap a local venv, and organize
            global skills, rather than only reporting on them.
        output_json: Emit the JSON envelope on stdout.
        gcp_project: Google Cloud project to check BigQuery access against.
        state_file: Optional explicit path to ``.demo-state.json``.

    Returns:
        The emitted result envelope in ``--json`` mode; ``None`` in human mode,
        where the Rich report is the output.

    Raises:
        typer.Exit: With :attr:`AuthError.exit_code` when the environment is
            blocked, in both output modes.
    """
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    # Must be set even though the success path passes json_output to emit()
    # explicitly: it is what tells the top-level error boundary which format to
    # render in if anything beneath this command raises.
    app_ctx.set_json_mode(output_json)
    if not output_json:
        print_banner("PRE-CHECK: ENVIRONMENT, MCP & SKILL AUDIT", f"Target GCP Project: {gcp_project}")

    findings = _collect_findings(fix=fix, gcp_project=gcp_project)
    _persist_gate_zero_verdict(app_ctx, findings)

    if output_json:
        return emit(_build_json_result(findings), json_output=True)

    _render_human_report(findings, fix=fix)

    if findings.is_blocked:
        _render_blocked_guidance(findings)
        # Matches the --json path: blocked means "re-authenticate", exit 3.
        raise typer.Exit(code=AuthError.exit_code)


def list_skills(
    ctx: typer.Context,
    fix: Annotated[bool, typer.Option("--fix", help="Symlink and organize skills into intent subfolders")] = False,
):
    """View and manage intent-based global agent skills.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        fix: Symlink skills into their intent subfolders rather than only
            reporting on what is missing.
    """
    statuses = audit_and_organize_skills(fix=fix)
    print_success(f"Organized {len(statuses)} skills across intent categories into {GEMINI_SKILLS_DIR}")
