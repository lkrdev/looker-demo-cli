"""Tests for top-level `demo-create` commands, the central error/output boundary, `pre-check`, `skills`, `run-script`, and `env`."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from looker_demo_cli import __version__
from looker_demo_cli.config import DEPRECATED_SKILLS, INTENT_SKILL_DEFINITIONS
from looker_demo_cli.errors import (
    USAGE_EXIT_CODE,
    AuthError,
    ConfigError,
    DemoCreateError,
    RemoteApiError,
    StateError,
    ValidationError,
)
from looker_demo_cli.output import ENVELOPE_SCHEMA_VERSION, set_json_mode
from looker_demo_cli.precheck.env_checker import (
    DependencyCheckResult,
    RuntimeEnvironmentStatus,
    ensure_gitignore,
)
from looker_demo_cli.precheck.gcp_auth import GCPAccountInfo, GCPActiveContext
from looker_demo_cli.precheck.looker_auth import LookerAuthStatus
from looker_demo_cli.precheck.mcp_checker import (
    DEPRECATED_MCP_SERVERS,
    MCPStatus,
    check_mcp_servers,
    patch_mcp_config,
)
from looker_demo_cli.precheck.skills_organizer import (
    SkillInstallStatus,
    prune_deprecated_skills,
)

try:
    from conftest import envelope
except ImportError:  # pragma: no cover
    from tests.conftest import envelope

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Pre-check collaborators fixture
# ---------------------------------------------------------------------------


def _healthy_env_status() -> RuntimeEnvironmentStatus:
    return RuntimeEnvironmentStatus(
        is_virtualenv=True,
        python_executable="/fake/.venv/bin/python",
        python_version="3.12.4",
        uv_installed=True,
        uv_version="uv 0.4.20",
        dependency_checks=[
            DependencyCheckResult(
                package_name="mcp",
                installed_version="1.9.0",
                expected_constraint="<2.0.0",
                is_satisfied=True,
                notes="OK",
            ),
            DependencyCheckResult(
                package_name="lkr-dev-cli",
                installed_version=None,
                expected_constraint=">=0.0.50",
                is_satisfied=False,
                notes="Not installed",
            ),
        ],
        is_healthy=True,
        active_venv_path="/fake/.venv",
    )


@dataclass
class PrecheckDoubles:
    """In-memory stand-ins for `pre-check` and `env` collaborators."""

    env_status: RuntimeEnvironmentStatus = field(default_factory=_healthy_env_status)
    gcp_context: GCPActiveContext = field(
        default_factory=lambda: GCPActiveContext(
            active_account="demo-user@example.com",
            active_project="fake-active-project",
            active_config_name="default",
            adc_project_id="fake-adc-project",
            adc_quota_project_id="fake-quota-project",
            adc_file_path="/fake/home/.config/gcloud/application_default_credentials.json",
            adc_file_exists=True,
        )
    )
    gcp_accounts: list[GCPAccountInfo] = field(
        default_factory=lambda: [
            GCPAccountInfo(
                account_id="demo-user@example.com",
                is_active=True,
                project_id="fake-active-project",
                has_bigquery_access=True,
            )
        ]
    )
    projects: list[dict[str, str]] = field(
        default_factory=lambda: [{"project_id": "fake-active-project", "name": "Fake Active", "project_number": "111"}]
    )
    mcp: list[MCPStatus] = field(
        default_factory=lambda: [
            MCPStatus(server_name="data-designer", is_configured=True, details={}, issues=[]),
            MCPStatus(server_name="bigquery", is_configured=False, details={}, issues=["Server not defined"]),
        ]
    )
    skills: list[SkillInstallStatus] = field(
        default_factory=lambda: [
            SkillInstallStatus(
                category="modeling",
                skill_name="lookml-view",
                source_path="/fake/src/lookml-view",
                target_path="/fake/dst/lookml-view",
                is_installed=True,
                is_valid=True,
            )
        ]
    )
    looker: LookerAuthStatus = field(
        default_factory=lambda: LookerAuthStatus(
            is_authenticated=True,
            auth_method="oauth",
            user_name="Demo User",
            user_email="demo-user@example.com",
            instance_url="https://fake.cloud.looker.com",
            oauth_account="fake-instance",
            available_oauth_instances=[{"id": 1, "instance_name": "fake-instance"}],
            available_connections=["default_bigquery_connection"],
            has_default_bigquery_conn=True,
        )
    )
    inspect_target_projects: list[str] = field(default_factory=list)
    mcp_check_calls: int = 0
    patch_mcp_calls: int = 0
    skills_fix_args: list[bool] = field(default_factory=list)
    venv_init_calls: list[Path] = field(default_factory=list)
    venv_init_result: tuple[bool, str] = (True, "source /fake/.venv/bin/activate")


@pytest.fixture
def precheck_doubles(patch_cli, fake_shell) -> PrecheckDoubles:
    doubles = PrecheckDoubles()

    def _inspect(target_project: str = "", **_kw: Any) -> list[GCPAccountInfo]:
        doubles.inspect_target_projects.append(target_project)
        return doubles.gcp_accounts

    def _check_mcp() -> list[MCPStatus]:
        doubles.mcp_check_calls += 1
        return doubles.mcp

    def _patch_mcp() -> bool:
        doubles.patch_mcp_calls += 1
        return True

    def _audit(fix: bool = False) -> list[SkillInstallStatus]:
        doubles.skills_fix_args.append(fix)
        return doubles.skills

    def _init_venv(target_dir: Path, install_self: bool = True) -> tuple[bool, str]:
        doubles.venv_init_calls.append(target_dir)
        return doubles.venv_init_result

    patches = {
        "check_runtime_environment": lambda: doubles.env_status,
        "get_gcp_active_context": lambda: doubles.gcp_context,
        "inspect_gcp_accounts": _inspect,
        "list_available_gcp_projects": lambda: doubles.projects,
        "check_mcp_servers": _check_mcp,
        "patch_mcp_config": _patch_mcp,
        "audit_and_organize_skills": _audit,
        "check_looker_auth": lambda: doubles.looker,
        "init_workspace_venv": _init_venv,
    }
    for name, impl in patches.items():
        patch_cli(name, impl)

    doubles.shell = fake_shell  # type: ignore[attr-defined]
    return doubles


# ---------------------------------------------------------------------------
# Root callback & Central ErrorHandlingGroup boundary
# ---------------------------------------------------------------------------


def test_root_version_help_and_unknown_command(invoke) -> None:
    """Verify `--version` (on stderr), `--help` command inventory, and exit 2 on unknown commands."""
    ver = invoke(["--version"])
    assert ver.exit_code == 0
    assert __version__ in ver.output
    assert ver.stdout == ""

    hlp = invoke(["--help"])
    assert hlp.exit_code == 0
    for name in ("status", "pre-check", "skills", "env", "ge", "data", "lookml", "agent", "embed"):
        assert name in hlp.output

    bad = invoke(["definitely-not-a-command"])
    assert bad.exit_code == USAGE_EXIT_CODE


@pytest.mark.parametrize(
    "exc",
    [
        AuthError("creds stale", remediation="run gcloud auth login"),
        ConfigError("no dataset", remediation="pass --dataset"),
        RemoteApiError("Looker error", status_code=503),
        ValidationError("tile failed"),
        StateError("bad state"),
    ],
)
def test_error_boundary_maps_exit_codes_and_streams(exc: DemoCreateError) -> None:
    """`ErrorHandlingGroup` maps each `DemoCreateError` to its dedicated exit code and renders JSON/human cleanly."""
    from looker_demo_cli.cli import ErrorHandlingGroup

    boundary_app = typer.Typer(name="boundary", cls=ErrorHandlingGroup)
    nested_app = typer.Typer(name="nest", cls=ErrorHandlingGroup)
    boundary_app.add_typer(nested_app, name="nest")

    @boundary_app.callback()
    def _root() -> None:
        pass

    @nested_app.command(name="deep")
    def _deep(output_json: bool = typer.Option(False, "--json")) -> None:
        set_json_mode(output_json)
        raise exc

    # Human mode: message on stderr, stdout empty
    human = CliRunner().invoke(boundary_app, ["nest", "deep"])
    assert human.exit_code == exc.exit_code
    assert exc.message in human.output
    assert human.stdout == ""

    # JSON mode: single structured envelope on stdout with full command path
    machine = CliRunner().invoke(boundary_app, ["nest", "deep", "--json"])
    assert machine.exit_code == exc.exit_code
    payload = envelope(machine)
    assert payload["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert payload["status"] == "FAILED"
    assert payload["command"] == "nest deep"
    assert payload["errors"][0]["code"] == exc.code
    assert payload["errors"][0]["details"] == exc.details


# ---------------------------------------------------------------------------
# pre-check
# ---------------------------------------------------------------------------


def test_pre_check_json_and_human_happy_path(invoke, precheck_doubles) -> None:
    """`pre-check` emits the full report under `data` in `--json` mode and renders all 6 sections in human mode."""
    json_res = invoke(["pre-check", "--json"])
    assert json_res.exit_code == 0
    payload = envelope(json_res)
    assert payload["command"] == "pre-check"
    assert payload["status"] == "SUCCESS"
    assert payload["data"]["is_blocked"] is False
    assert payload["next_actions"][0]["gate"] == 1
    assert payload["next_actions"][0]["requires_human_confirmation"] is True

    human_res = invoke(["pre-check"])
    assert human_res.exit_code == 0
    assert human_res.stdout == ""
    for heading in (
        "1. Python Runtime",
        "2. GCP & ADC Context",
        "3. Available Google Cloud Projects",
        "4. Global MCP Tool Configurations",
        "5. Intent-Organized Agent Skills",
        "6. Looker Authentication",
    ):
        assert heading in human_res.output


def test_pre_check_blocking_conditions_and_fix_mode(invoke, precheck_doubles) -> None:
    """`pre-check` exits with `AuthError.exit_code` (3) when GCP/Looker auth is missing, and `--fix` repairs config."""
    # Blocked when no GCP accounts and Looker unauthenticated
    precheck_doubles.gcp_accounts = []
    precheck_doubles.looker = LookerAuthStatus(is_authenticated=False, error_message="No session")

    blocked = invoke(["pre-check", "--json"])
    assert blocked.exit_code == AuthError.exit_code
    payload = envelope(blocked)
    assert payload["status"] == "BLOCKED"
    assert len(payload["errors"]) == 2
    assert payload["next_actions"] == []

    # Human mode blocked panel
    blocked_human = invoke(["pre-check"])
    assert blocked_human.exit_code == AuthError.exit_code
    assert "EXECUTION BLOCKED" in blocked_human.output

    # --fix mode with healthy auth and non-virtualenv
    precheck_doubles.gcp_accounts = [
        GCPAccountInfo(account_id="u@example.com", is_active=True, has_bigquery_access=True)
    ]
    precheck_doubles.looker = LookerAuthStatus(is_authenticated=True)
    precheck_doubles.env_status.is_virtualenv = False

    fixed = invoke(["pre-check", "--fix", "--json"])
    assert fixed.exit_code == 0
    assert precheck_doubles.patch_mcp_calls == 1
    assert precheck_doubles.skills_fix_args == [False, False, True]
    assert precheck_doubles.venv_init_calls == [Path.cwd()]


# ---------------------------------------------------------------------------
# MCP pruning & Skills organization
# ---------------------------------------------------------------------------


def test_mcp_pruning_and_skills_registration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`patch_mcp_config` prunes deprecated MCP servers and `prune_deprecated_skills` removes legacy skill dirs."""
    fake_mcp_config = tmp_path / "mcp_config.json"
    fake_mcp_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "data-designer": {"command": "uvx"},
                    "bigquery": {"serverUrl": "https://bigquery.googleapis.com/mcp"},
                    "knowledge-catalog": {"serverUrl": "https://dataplex.googleapis.com/mcp"},
                    "lkr_codemode": {"command": "lkr"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("looker_demo_cli.precheck.mcp_checker.GEMINI_MCP_CONFIG", fake_mcp_config)

    assert not all(s.is_configured for s in check_mcp_servers())
    assert patch_mcp_config() is True
    updated_data = json.loads(fake_mcp_config.read_text(encoding="utf-8"))
    for dep in DEPRECATED_MCP_SERVERS:
        assert dep not in updated_data["mcpServers"]
    assert "lkr_codemode" in updated_data["mcpServers"]
    assert all(s.is_configured for s in check_mcp_servers())

    # Skills registration & deprecated skill pruning
    repo_root = Path(__file__).resolve().parent.parent
    data_design_defs = INTENT_SKILL_DEFINITIONS.get("data-design", {})
    for skill_name in ("synthetic-data-authoring", "bigquery-metadata", "knowledge-catalog-metadata"):
        assert (repo_root / "skills" / skill_name / "SKILL.md").exists()
        assert data_design_defs.get(skill_name) == ("local_cli", skill_name)

    for deprecated in DEPRECATED_SKILLS:
        fake_skill = tmp_path / deprecated
        fake_skill.mkdir(parents=True, exist_ok=True)
        (fake_skill / "SKILL.md").write_text("# legacy", encoding="utf-8")

    pruned = prune_deprecated_skills(tmp_path)
    assert {Path(p).name for p in pruned} == set(DEPRECATED_SKILLS)


def test_skills_and_run_script_commands(invoke, precheck_doubles, fake_shell) -> None:
    """Verify `skills` (--fix forwarding) and `run-script` (missing file ConfigError vs child exit code forwarding)."""
    assert invoke(["skills", "--fix"]).exit_code == 0
    assert precheck_doubles.skills_fix_args == [True]

    missing = invoke(["run-script", "missing.py"])
    assert missing.exit_code == ConfigError.exit_code

    script = Path("real.py")
    script.write_text("print('hi')\n", encoding="utf-8")
    fake_shell.stub("real.py", returncode=7)
    assert invoke(["run-script", str(script)]).exit_code == 7


# ---------------------------------------------------------------------------
# env init / env info / ensure_gitignore
# ---------------------------------------------------------------------------


def test_env_commands_and_gitignore(invoke, precheck_doubles, tmp_path: Path) -> None:
    """Verify `env init`, `env info`, and `ensure_gitignore` idempotency."""
    target = tmp_path / "workspace"
    target.mkdir()

    init_ok = invoke(["env", "init", "--dir", str(target)])
    assert init_ok.exit_code == 0
    assert precheck_doubles.venv_init_calls == [target]

    info_ok = invoke(["env", "info"])
    assert info_ok.exit_code == 0
    assert "1. Python Runtime" in info_ok.output
    assert info_ok.stdout == ""

    assert ensure_gitignore(tmp_path) is True
    assert ".env" in (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ensure_gitignore(tmp_path) is False
