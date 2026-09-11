"""Tests for the top-level ``demo-create`` commands.

Scope: the root callback (``--version`` / ``--help``), the
:class:`~looker_demo_cli.cli.ErrorHandlingGroup` exception boundary,
``pre-check``, the deprecated ``run`` command, ``skills``, ``run-script``, and
the ``env`` subgroup.

Phase 2 contract
----------------
Two things changed that this module now asserts as *deliberate* behavior rather
than pinning as accidents:

1. **stdout carries the JSON envelope and nothing else.** Rich output moved to
   stderr (``looker_demo_cli.utils.console``). Assertions on ``result.output``
   (Click's interleaved stdout+stderr view) still read naturally for human
   text; assertions on the machine-readable stream must use ``result.stdout``,
   which is why :func:`envelope` deliberately parses ``result.stdout``.
2. **Every deliberate failure has a dedicated exit code**
   (:mod:`looker_demo_cli.errors`). Tests assert ``SomeError.exit_code``, never
   a magic number, so renumbering the table is a one-line change.

Tests that still pin *unfixed* behavior keep ``@pytest.mark.characterization``
and carry a ``# BUG:`` note naming the phase that owns the fix. Everything else
is a plain ``@pytest.mark.unit`` assertion of the new contract.

Every test here is hermetic: the eight ``pre-check`` collaborators are patched
through the ``patch_cli`` fixture, which finds their import sites in whichever
command modules hold them, and ``fake_shell`` is installed as a backstop so that
any un-patched ``subprocess.run`` leak is recorded rather than executed against
the developer's real machine.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from looker_demo_cli import __version__
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
)
from looker_demo_cli.precheck.gcp_auth import GCPAccountInfo, GCPActiveContext
from looker_demo_cli.precheck.looker_auth import (
    LKR_OAUTH_CLIENT_PAYLOAD,
    LookerAuthStatus,
)
from looker_demo_cli.precheck.mcp_checker import MCPStatus
from looker_demo_cli.precheck.skills_organizer import SkillInstallStatus

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Local fixtures
#
# NOTE (report to conftest owner): ``precheck_doubles`` below is defined locally
# because ``tests/conftest.py`` has no fake for the ``looker_demo_cli.precheck.*``
# boundary. It is generally useful and is a good candidate for promotion.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_json_mode():
    """Keep the ``--json`` ContextVar from leaking between tests.

    ``output.set_json_mode`` writes to a ``ContextVar`` that ``CliRunner``
    shares with the test process. A command (or a boundary test below) that
    sets it to ``True`` would otherwise silently flip the rendering mode of a
    later test that never passed ``--json``.
    """
    set_json_mode(False)
    yield
    set_json_mode(False)


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


def _healthy_gcp_context() -> GCPActiveContext:
    return GCPActiveContext(
        active_account="demo-user@example.com",
        active_project="fake-active-project",
        active_config_name="default",
        adc_project_id="fake-adc-project",
        adc_quota_project_id="fake-quota-project",
        adc_file_path="/fake/home/.config/gcloud/application_default_credentials.json",
        adc_file_exists=True,
    )


def _healthy_accounts() -> list[GCPAccountInfo]:
    return [
        GCPAccountInfo(
            account_id="demo-user@example.com",
            is_active=True,
            project_id="fake-active-project",
            has_bigquery_access=True,
            error_message=None,
        )
    ]


def _healthy_looker() -> LookerAuthStatus:
    return LookerAuthStatus(
        is_authenticated=True,
        auth_method="oauth",
        user_name="Demo User",
        user_email="demo-user@example.com",
        instance_url="https://fake.cloud.looker.com",
        oauth_account="fake-instance",
        available_oauth_instances=[{"id": 1, "instance_name": "fake-instance"}],
        available_connections=["default_bigquery_connection"],
        has_default_bigquery_conn=True,
        error_message=None,
    )


@dataclass
class PrecheckDoubles:
    """Mutable, in-memory stand-ins for every ``pre-check`` collaborator."""

    env_status: RuntimeEnvironmentStatus = field(default_factory=_healthy_env_status)
    gcp_context: GCPActiveContext = field(default_factory=_healthy_gcp_context)
    gcp_accounts: list[GCPAccountInfo] = field(default_factory=_healthy_accounts)
    projects: list[dict[str, str]] = field(
        default_factory=lambda: [
            {"project_id": "fake-active-project", "name": "Fake Active", "project_number": "111"},
            {"project_id": "fake-other-project", "name": "Fake Other", "project_number": "222"},
        ]
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
    looker: LookerAuthStatus = field(default_factory=_healthy_looker)

    # Recording
    inspect_target_projects: list[str] = field(default_factory=list)
    mcp_check_calls: int = 0
    patch_mcp_calls: int = 0
    skills_fix_args: list[bool] = field(default_factory=list)
    venv_init_calls: list[Path] = field(default_factory=list)
    venv_init_result: tuple[bool, str] = (True, "source /fake/.venv/bin/activate")


@pytest.fixture
def precheck_doubles(patch_cli, fake_shell) -> PrecheckDoubles:
    """Patch all eight ``pre-check`` collaborators plus ``init_workspace_venv``.

    Uses ``patch_cli`` rather than naming a module: ``check_runtime_environment``
    and ``init_workspace_venv`` are imported by *both* ``commands.precheck`` and
    ``commands.env``, and patching only one leaves the other live.

    ``fake_shell`` is requested purely as a backstop: if any real ``gcloud`` or
    ``uv`` invocation leaks through, it is recorded instead of executed and the
    ``test_pre_check_never_touches_the_host`` assertion will catch it.
    """
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


def envelope(result: Any) -> dict[str, Any]:
    """Parse the JSON envelope a ``--json`` invocation wrote to stdout.

    Deliberately parses ``result.stdout`` rather than ``result.output``: the
    Phase 2 contract is that stdout carries the envelope *and nothing else*, so
    a bare ``json.loads`` succeeding is itself the assertion that no Rich
    output leaked onto the machine-readable stream.
    """
    return json.loads(result.stdout)


def report(result: Any) -> dict[str, Any]:
    """Return the ``pre-check`` report, which now lives under ``data``."""
    return envelope(result)["data"]


# ---------------------------------------------------------------------------
# Root callback: --version / --help
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--version", "-v"])
def test_version_flag_prints_version_and_exits_zero(invoke, flag: str) -> None:
    """The version banner is human output, so it must not pollute stdout."""
    result = invoke([flag])

    assert result.exit_code == 0
    assert "looker-demo-cli" in result.output
    assert __version__ in result.output
    # Phase 2: Rich goes to stderr. stdout is reserved for the JSON envelope,
    # so a caller piping `demo-create --version` into jq gets an empty stream
    # rather than a syntax error.
    assert result.stdout == ""


def test_root_help_exits_zero_and_lists_command_groups(invoke) -> None:
    """Registration smoke test: every command group is still reachable."""
    result = invoke(["--help"])

    assert result.exit_code == 0
    for name in ("status", "pre-check", "skills", "env", "ge", "data", "lookml", "agent", "embed"):
        assert name in result.output


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["env", "--help"], id="env"),
        pytest.param(["ge", "--help"], id="ge"),
        pytest.param(["data", "--help"], id="data"),
        pytest.param(["lookml", "--help"], id="lookml"),
        pytest.param(["agent", "--help"], id="agent"),
        pytest.param(["embed", "--help"], id="embed"),
        pytest.param(["status", "--help"], id="status"),
        pytest.param(["pre-check", "--help"], id="pre-check"),
        pytest.param(["skills", "--help"], id="skills"),
        pytest.param(["run-script", "--help"], id="run-script"),
        pytest.param(["python", "--help"], id="python"),
        pytest.param(["env", "init", "--help"], id="env-init"),
        pytest.param(["env", "info", "--help"], id="env-info"),
    ],
)
def test_every_registered_command_help_exits_zero(invoke, argv: list[str]) -> None:
    """Cheap registration-regression net: every group/command must still resolve.

    Installing ``ErrorHandlingGroup`` as the root ``cls`` replaces the class
    Typer builds the command tree from, so a mistake there would break
    resolution for every command at once. This is the canary for that.
    """
    result = invoke(argv)

    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


def test_unknown_command_exits_with_the_reserved_usage_code(invoke) -> None:
    """Click's exit ``2`` must survive the boundary, or callers cannot tell a
    typo from a runtime failure.

    ``ErrorHandlingGroup`` only intercepts :class:`DemoCreateError`; a
    ``UsageError`` has to pass straight through with Click's reserved code so
    that "you spelled the command wrong" stays distinguishable from
    "authentication failed" (3), "bad config" (4), and so on.
    """
    result = invoke(["definitely-not-a-command"])

    assert result.exit_code == USAGE_EXIT_CODE


# ---------------------------------------------------------------------------
# ErrorHandlingGroup: the single exception boundary
#
# Every command's error contract rests on this class, so it is exercised
# directly rather than only through the commands that happen to raise. A
# throwaway Typer app is used so the assertions describe the *boundary*, not
# the semantics of whichever command was convenient to make fail.
#
# Non-obvious mechanism worth stating once: `cls=ErrorHandlingGroup` is
# installed on EVERY group in cli.py -- the root app and each sub-Typer
# (`env`, `ge`, `data`, `lookml`, `agent`, `embed`) -- not only the root. Click
# unwinds a raised exception through each enclosing group's `invoke`, and only
# the innermost one knows the leaf command name, which is what lets the
# envelope report "data inspect" rather than "data". The handler re-raises
# `typer.Exit` rather than the original error so outer groups skip it and
# exactly one envelope reaches stdout.
#
# Consequence for future work: a NEW sub-Typer added without
# `cls=ErrorHandlingGroup` still gets its errors caught (by the root), but they
# will be mislabelled with the group name. The nested tests below are the net
# for that.
# ---------------------------------------------------------------------------

BOUNDARY_ERRORS = [
    pytest.param(AuthError("creds are stale", remediation="run gcloud auth login"), id="auth"),
    pytest.param(ConfigError("no dataset resolvable", remediation="pass --dataset"), id="config"),
    pytest.param(RemoteApiError("Looker said no", status_code=503), id="remote-api"),
    pytest.param(ValidationError("3 tiles returned non-200"), id="validation"),
    pytest.param(StateError(".demo-state.json is unreadable"), id="state"),
]


def _boundary_app(exc: DemoCreateError) -> typer.Typer:
    """Build a minimal app wired to the real :class:`ErrorHandlingGroup`.

    Two structural details are load-bearing and mirror ``cli.py`` exactly:

    * The root callback is required. Typer only honours ``cls`` when the app
      actually resolves to a *group*, and a single-command app with no callback
      collapses into a bare ``Command`` -- which would silently bypass the
      handler under test.
    * ``cls`` is set on the **nested** ``typer.Typer`` constructor and the group
      is then attached with ``add_typer``. Typer resolves an added sub-app's
      ``cls`` from the sub-app's own ``info``, so this is what makes the
      innermost group handle the error and see the full command path.

    Args:
        exc: The error both ``boom`` and ``nest deep`` will raise.

    Returns:
        An app exposing ``boom`` at the root and ``nest deep`` one level down.
    """
    from looker_demo_cli.cli import ErrorHandlingGroup

    boundary_app = typer.Typer(name="boundary", cls=ErrorHandlingGroup)
    nested_app = typer.Typer(name="nest", cls=ErrorHandlingGroup)
    boundary_app.add_typer(nested_app, name="nest")

    @boundary_app.callback()
    def _root() -> None:
        """Present solely to force Typer to build a group."""

    @boundary_app.command(name="boom")
    def _boom(
        output_json: bool = typer.Option(False, "--json"),
    ) -> None:
        """Raise the error under test the way a real command would."""
        set_json_mode(output_json)
        raise exc

    @nested_app.command(name="deep")
    def _deep(
        output_json: bool = typer.Option(False, "--json"),
    ) -> None:
        """Same failure, one group deeper, to exercise ``_command_path``."""
        set_json_mode(output_json)
        raise exc

    return boundary_app


@pytest.mark.parametrize("exc", BOUNDARY_ERRORS)
def test_boundary_maps_each_error_to_its_dedicated_exit_code(exc: DemoCreateError) -> None:
    """An orchestrator branches on ``$?``, so the code must come from the
    exception and not from a generic ``1``.

    Without this the five failure classes are indistinguishable to a caller
    and every recovery path degenerates into parsing English prose.
    """
    result = CliRunner().invoke(_boundary_app(exc), ["boom"])

    assert result.exit_code == exc.exit_code


@pytest.mark.parametrize("exc", BOUNDARY_ERRORS)
def test_boundary_json_mode_emits_a_parseable_envelope_on_stdout(exc: DemoCreateError) -> None:
    """A failure must be as machine-readable as a success.

    Previously an exception escaped as a traceback or a Rich panel, so a
    caller that had asked for ``--json`` got something it could not parse
    precisely when it most needed the structured detail.
    """
    result = CliRunner().invoke(_boundary_app(exc), ["boom", "--json"])

    assert result.exit_code == exc.exit_code
    payload = envelope(result)
    assert payload["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert payload["status"] == "FAILED"
    assert payload["command"] == "boom"
    assert len(payload["errors"]) == 1
    error = payload["errors"][0]
    assert error["code"] == exc.code
    assert error["message"] == exc.message
    assert error["remediation"] == exc.remediation
    assert error["details"] == exc.details


def test_boundary_json_mode_preserves_structured_details() -> None:
    """``details`` is the part a machine acts on, so it must survive verbatim.

    ``RemoteApiError`` folds ``status_code`` into ``details``; a caller
    retrying on 5xx but not on 4xx needs that value in the envelope rather
    than embedded in the message text.
    """
    exc = RemoteApiError("Looker said no", status_code=503, remediation="retry in a minute")

    result = CliRunner().invoke(_boundary_app(exc), ["boom", "--json"])

    error = envelope(result)["errors"][0]
    assert error["code"] == "REMOTE_API_ERROR"
    assert error["message"] == "Looker said no"
    assert error["remediation"] == "retry in a minute"
    assert error["details"] == {"status_code": 503}


def test_boundary_human_mode_renders_message_and_remediation_to_stderr() -> None:
    """A human needs the fix, not just the failure -- and stdout stays clean.

    ``remediation`` exists so advice is a structured field rather than prose
    glued onto the message; the human renderer is what proves it is actually
    surfaced when ``--json`` was not requested.
    """
    exc = ConfigError("no dataset resolvable", remediation="pass --dataset explicitly")

    result = CliRunner().invoke(_boundary_app(exc), ["boom"])

    assert result.exit_code == ConfigError.exit_code
    assert "no dataset resolvable" in result.output
    assert "pass --dataset explicitly" in result.output
    # No JSON was requested, so nothing at all belongs on stdout.
    assert result.stdout == ""


def test_boundary_lets_successful_commands_through_untouched() -> None:
    """The boundary must be invisible on the happy path.

    Overriding ``TyperGroup.invoke`` is intrusive; this pins that a command
    which raises nothing still returns normally and exits 0.
    """
    boundary_app = _boundary_app(ConfigError("never raised"))

    @boundary_app.command(name="fine")
    def _fine() -> None:
        """A command that simply succeeds."""
        typer.echo("all good")

    result = CliRunner().invoke(boundary_app, ["fine"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "all good"


def test_boundary_is_installed_on_the_real_app(invoke) -> None:
    """The handler is only worth anything if the shipped app actually uses it.

    ``run-script`` with a missing file is the shortest real path to a
    ``DemoCreateError``: nothing catches it locally, so reaching exit 4 proves
    the root group -- not the command -- did the conversion.
    """
    result = invoke(["run-script", "definitely-missing.py"])

    assert result.exit_code == ConfigError.exit_code
    assert "Script file not found: definitely-missing.py" in result.output


def test_boundary_on_the_real_app_honours_json_mode(invoke, fake_bigquery) -> None:
    """End-to-end proof that ``--json`` survives an exception on the real app.

    Uses ``data inspect`` because it is the cheapest shipped command that both
    calls ``set_json_mode`` and raises a ``DemoCreateError`` without any
    network access. The assertion is about the boundary, not about BigQuery.
    """
    result = invoke(["data", "inspect", "--dataset", "nope", "--gcp-project", "proj", "--json"])

    assert result.exit_code == RemoteApiError.exit_code
    payload = envelope(result)
    assert payload["status"] == "FAILED"
    assert payload["errors"][0]["code"] == "REMOTE_API_ERROR"
    assert payload["errors"][0]["details"]["dataset"] == "nope"


def test_boundary_reports_the_full_command_path_for_nested_subcommands(invoke, fake_bigquery) -> None:
    """A raised failure and a returned failure now agree on ``command``.

    ``data inspect`` builds its own envelope on the PARTIAL path and labels it
    ``"data inspect"``. Before the fix, the *same* command failing by raising
    was labelled just ``"data"``, because the handler read
    ``ctx.invoked_subcommand`` off the root context, where the leaf is not yet
    known. A caller keying metrics or retry policy off ``command`` therefore
    saw two different identities for one command depending on which internal
    path it happened to take.

    Two changes make this hold: ``_command_path`` joins ``ctx.command_path``
    with ``invoked_subcommand``, and ``cls=ErrorHandlingGroup`` is installed on
    every nested group so the *innermost* group -- the only one that knows the
    leaf name -- handles the error first.
    """
    result = invoke(["data", "inspect", "--dataset", "nope", "--gcp-project", "proj", "--json"])

    assert envelope(result)["command"] == "data inspect"


def test_boundary_on_a_nested_group_reports_the_full_path() -> None:
    """``_command_path`` is covered directly, not only through the real CLI.

    Exercised on the throwaway app so a regression is attributed to the
    boundary itself rather than to whatever the shipped command tree happens to
    look like at the time.
    """
    result = CliRunner().invoke(_boundary_app(ConfigError("nested failure")), ["nest", "deep", "--json"])

    assert result.exit_code == ConfigError.exit_code
    assert envelope(result)["command"] == "nest deep"


def test_boundary_on_a_nested_group_is_not_double_handled() -> None:
    """The outer group must not re-render an error the inner group already emitted.

    With the handler installed at every level, an error raised two groups deep
    passes through two ``invoke`` frames. The inner one re-raises ``typer.Exit``
    rather than the original ``DemoCreateError`` precisely so the outer one
    ignores it -- otherwise stdout would carry two concatenated envelopes and
    ``json.loads`` would fail on the second document.
    """
    result = CliRunner().invoke(_boundary_app(ConfigError("nested failure")), ["nest", "deep", "--json"])

    # Parses at all => exactly one document was written.
    payload = envelope(result)
    assert payload["errors"][0]["message"] == "nested failure"
    assert result.stdout.count('"schema_version"') == 1


# ---------------------------------------------------------------------------
# pre-check: --json envelope contract
# ---------------------------------------------------------------------------

EXPECTED_ENVELOPE_KEYS = {
    "schema_version",
    "command",
    "status",
    "data",
    "errors",
    "warnings",
    "next_actions",
}

EXPECTED_REPORT_KEYS = {
    "is_blocked",
    "blocking_reasons",
    "runtime_environment",
    "active_gcp_context",
    "gcp_accounts",
    "available_gcp_projects",
    "reauth_required",
    "mcp_servers",
    "skills",
    "looker_auth",
    "oauth_registration_payload",
    "agent_instructions",
}

EXPECTED_AGENT_INSTRUCTION_KEYS = {
    "is_blocked",
    "blocking_reasons",
    "mandatory_stop_gate",
    "gcp_auth_commands",
    "looker_oauth_commands",
    "gcp_account",
    "gcp_project",
    "looker_instance",
    "database_connection",
}


def test_pre_check_json_emits_the_standard_envelope(invoke, precheck_doubles) -> None:
    """``pre-check`` no longer has a bespoke report shape.

    The whole report moved under ``data`` so that one parser handles every
    command. ``pre-check`` was the last holdout with its own top-level schema,
    which forced orchestrators to special-case gate 0.
    """
    result = invoke(["pre-check", "--json"])

    assert result.exit_code == 0
    payload = envelope(result)
    assert set(payload.keys()) == EXPECTED_ENVELOPE_KEYS
    assert payload["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert payload["command"] == "pre-check"
    assert payload["status"] == "SUCCESS"
    assert payload["errors"] == []
    assert payload["warnings"] == []


def test_pre_check_json_report_lives_under_data(invoke, precheck_doubles) -> None:
    """The report keys are pinned exhaustively; the old ``status`` key is gone.

    The envelope owns status now. Leaving a second, differently-spelled
    ``status`` inside ``data`` ("HEALTHY"/"BLOCKED") would give callers two
    sources of truth that could drift apart.
    """
    result = invoke(["pre-check", "--json"])

    payload_data = report(result)
    assert set(payload_data.keys()) == EXPECTED_REPORT_KEYS
    assert "status" not in payload_data


def test_pre_check_json_healthy_run_suggests_the_next_gate(invoke, precheck_doubles) -> None:
    """A clean environment tells the orchestrator what to do next, and that a
    human must approve it first.

    Gate 0's whole purpose is to stop an agent from silently choosing a GCP
    project and Looker instance. Encoding that as a structured
    ``requires_human_confirmation`` flag makes the gate enforceable instead of
    merely documented in a prompt file.
    """
    result = invoke(["pre-check", "--json"])

    actions = envelope(result)["next_actions"]
    assert len(actions) == 1
    assert actions[0]["gate"] == 1
    assert actions[0]["requires_human_confirmation"] is True
    assert "four environment targets" in actions[0]["description"]


def test_pre_check_json_nested_model_shapes(invoke, precheck_doubles) -> None:
    """The nested payloads are a published contract; pin them field by field."""
    payload_data = report(invoke(["pre-check", "--json"]))

    assert set(payload_data["runtime_environment"].keys()) == {
        "is_virtualenv",
        "python_executable",
        "python_version",
        "uv_installed",
        "uv_version",
        "dependency_checks",
        "is_healthy",
        "active_venv_path",
    }
    assert set(payload_data["runtime_environment"]["dependency_checks"][0].keys()) == {
        "package_name",
        "installed_version",
        "expected_constraint",
        "is_satisfied",
        "notes",
    }
    assert set(payload_data["active_gcp_context"].keys()) == {
        "active_account",
        "active_project",
        "active_config_name",
        "adc_project_id",
        "adc_quota_project_id",
        "adc_file_path",
        "adc_file_exists",
    }
    assert set(payload_data["gcp_accounts"][0].keys()) == {
        "account_id",
        "is_active",
        "project_id",
        "has_bigquery_access",
        "error_message",
    }
    assert set(payload_data["mcp_servers"][0].keys()) == {"server_name", "is_configured", "details", "issues"}
    assert set(payload_data["skills"][0].keys()) == {
        "category",
        "skill_name",
        "source_path",
        "target_path",
        "is_installed",
        "is_valid",
    }
    assert set(payload_data["looker_auth"].keys()) == {
        "is_authenticated",
        "auth_method",
        "user_name",
        "user_email",
        "instance_url",
        "oauth_account",
        "available_oauth_instances",
        "available_connections",
        "has_default_bigquery_conn",
        "error_message",
    }
    assert payload_data["available_gcp_projects"] == precheck_doubles.projects
    assert payload_data["oauth_registration_payload"] == LKR_OAUTH_CLIENT_PAYLOAD


def test_pre_check_json_agent_instructions_block(invoke, precheck_doubles) -> None:
    """The literal remediation commands an agent replays to the user.

    These strings are the recovery path for a blocked pipeline, so a typo here
    is a dead end for the user rather than a cosmetic regression.
    """
    instructions = report(invoke(["pre-check", "--json"]))["agent_instructions"]

    assert set(instructions.keys()) == EXPECTED_AGENT_INSTRUCTION_KEYS
    assert instructions["is_blocked"] is False
    assert instructions["blocking_reasons"] == []
    assert instructions["mandatory_stop_gate"].startswith(
        "CRITICAL: STOP all further tool executions immediately after pre-check."
    )
    assert "ask_question" in instructions["mandatory_stop_gate"]
    assert instructions["gcp_auth_commands"] == [
        "gcloud auth login",
        "gcloud auth application-default login",
        "gcloud config set project <PROJECT_ID>",
    ]
    assert len(instructions["looker_oauth_commands"]) == 5
    assert instructions["looker_oauth_commands"][0] == "lkr auth login"
    assert instructions["gcp_account"] == ("Prompt user to select/confirm the active GCP account from gcp_accounts.")
    assert instructions["gcp_project"] == (
        "Prompt user to select/confirm the target Google Cloud project from available_gcp_projects."
    )
    assert instructions["looker_instance"] == (
        "Prompt user to select/confirm the target Looker OAuth instance from available_oauth_instances."
    )
    assert instructions["database_connection"] == "Prompt user to confirm the Looker database connection name."


def test_pre_check_json_healthy_path(invoke, precheck_doubles) -> None:
    """A healthy environment is an unqualified success: exit 0, no errors."""
    result = invoke(["pre-check", "--json"])
    payload_data = report(result)

    assert result.exit_code == 0
    assert payload_data["is_blocked"] is False
    assert payload_data["blocking_reasons"] == []
    assert payload_data["reauth_required"] is False


def test_pre_check_json_writes_nothing_but_json_to_stdout(invoke, precheck_doubles) -> None:
    """``demo-create pre-check --json | jq .`` must always work.

    The banner is suppressed in JSON mode *and* every other human line now
    goes to stderr, so stdout is exactly one JSON document.
    """
    result = invoke(["pre-check", "--json"])

    assert "PRE-CHECK: ENVIRONMENT" not in result.output
    assert result.stdout.lstrip().startswith("{")
    # Round-trips: nothing was appended after the closing brace either.
    assert json.loads(result.stdout)["command"] == "pre-check"


def _raises(exc: DemoCreateError):
    """Return a zero-argument collaborator double that raises ``exc``.

    Used to inject a failure at a seam ``pre_check`` calls positionally, where
    a lambda would have to smuggle a ``raise`` into an expression.
    """

    def _fail():
        raise exc

    return _fail


def test_pre_check_unexpected_failure_json_still_emits_an_envelope(
    invoke, precheck_doubles, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error *raised* beneath ``pre-check --json`` is still machine-readable.

    ``pre_check`` builds its success envelope by passing ``json_output``
    straight to ``emit``, so for a while it never called ``set_json_mode``. The
    happy path worked, but an unexpected failure -- say Looker's API being
    unreachable during the audit -- reached the error boundary with the flag
    unset and was rendered as a Rich panel with an *empty* stdout, even though
    the caller had explicitly asked for JSON. Gate 0 is the one command an
    orchestrator always runs unattended, so that was the worst possible place
    to lose the envelope.

    ``check_looker_auth`` is the seam because it is the last collaborator
    ``pre_check`` calls before assembling the report, i.e. the deepest point
    that can still fail.
    """
    monkeypatch.setattr(
        "looker_demo_cli.commands.precheck.check_looker_auth",
        _raises(RemoteApiError("Looker unreachable", status_code=502)),
    )

    result = invoke(["pre-check", "--json"])

    assert result.exit_code == RemoteApiError.exit_code
    payload = envelope(result)
    assert payload["status"] == "FAILED"
    assert payload["command"] == "pre-check"
    assert payload["errors"][0]["code"] == "REMOTE_API_ERROR"
    assert payload["errors"][0]["details"] == {"status_code": 502}


def test_pre_check_unexpected_failure_human_mode_keeps_stdout_clean(
    invoke, precheck_doubles, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same failure without ``--json`` renders for a human and writes no stdout.

    Confirms ``set_json_mode`` is tracking the flag rather than being pinned
    on: the mode must follow the invocation, not the other way round.
    """
    monkeypatch.setattr(
        "looker_demo_cli.commands.precheck.check_looker_auth",
        _raises(RemoteApiError("Looker unreachable", status_code=502)),
    )

    result = invoke(["pre-check"])

    assert result.exit_code == RemoteApiError.exit_code
    assert "Looker unreachable" in result.output
    assert result.stdout == ""


def test_pre_check_never_touches_the_host(invoke, precheck_doubles) -> None:
    """No real subprocess may escape: every collaborator is a double."""
    result = invoke(["pre-check", "--json"])

    assert result.exit_code == 0
    assert precheck_doubles.shell.calls == []


# ---------------------------------------------------------------------------
# pre-check: blocking conditions
# ---------------------------------------------------------------------------

_NO_ACCOUNTS_REASON = (
    "No Google Cloud accounts configured. Run `gcloud auth login` and `gcloud auth application-default login`."
)
_REAUTH_REASON = (
    "Active GCP account requires reauthentication. Run `gcloud auth login` and `gcloud auth application-default login`."
)


def _accounts_no_accounts() -> list[GCPAccountInfo]:
    return []


def _accounts_needing_reauth() -> list[GCPAccountInfo]:
    return [
        GCPAccountInfo(
            account_id="stale@example.com",
            is_active=True,
            project_id="fake-active-project",
            has_bigquery_access=True,
            error_message="Reauth required: token expired",
        )
    ]


def _accounts_without_bigquery() -> list[GCPAccountInfo]:
    # Deliberately INACTIVE: an *active* account without BigQuery access is
    # classified as "needs reauth" instead (see the BUG note below).
    return [
        GCPAccountInfo(
            account_id="nobq@example.com",
            is_active=False,
            project_id="fake-active-project",
            has_bigquery_access=False,
            error_message="403 Access Denied",
        )
    ]


@pytest.mark.parametrize(
    ("accounts_factory", "expected_reason"),
    [
        pytest.param(_accounts_no_accounts, _NO_ACCOUNTS_REASON, id="no-accounts"),
        pytest.param(_accounts_needing_reauth, _REAUTH_REASON, id="active-account-needs-reauth"),
        pytest.param(
            _accounts_without_bigquery,
            "No configured GCP account has BigQuery access on project 'fake-active-project'.",
            id="no-bigquery-access",
        ),
    ],
)
def test_pre_check_gcp_blocking_conditions(invoke, precheck_doubles, accounts_factory, expected_reason: str) -> None:
    """Each GCP blocker is reported as BLOCKED with a structured AUTH_ERROR.

    Previously the reason existed only as a string inside the report body, so
    a caller had to know ``pre-check``'s private schema to find it. Promoting
    it into ``errors[]`` means the same parser that reads any other command's
    failure reads this one.
    """
    precheck_doubles.gcp_accounts = accounts_factory()

    # ``--gcp-project`` is passed explicitly: its default is ``DEFAULT_GCP_PROJECT``,
    # which is frozen from ``$GOOGLE_CLOUD_PROJECT`` at *import* time and therefore
    # varies between developer machines.
    result = invoke(["pre-check", "--json", "--gcp-project", "fake-active-project"])
    payload = envelope(result)

    assert result.exit_code == AuthError.exit_code
    assert payload["status"] == "BLOCKED"
    assert payload["data"]["is_blocked"] is True
    assert payload["data"]["blocking_reasons"] == [expected_reason]
    # Looker is healthy in this fixture, so exactly one reason is reported.
    assert payload["data"]["agent_instructions"]["blocking_reasons"] == [expected_reason]
    assert [e["code"] for e in payload["errors"]] == ["AUTH_ERROR"]
    assert payload["errors"][0]["message"] == expected_reason


def test_pre_check_blocked_json_exits_with_the_auth_code(invoke, precheck_doubles) -> None:
    """A blocked gate 0 exits 3, not 1.

    Exit 1 means "something unexpected happened" and is indistinguishable from
    a crash. Exit 3 tells the orchestrator specifically to re-authenticate, so
    it can prompt the user for credentials instead of retrying or aborting.
    """
    precheck_doubles.gcp_accounts = []

    result = invoke(["pre-check", "--json"])

    assert result.exit_code == AuthError.exit_code
    assert envelope(result)["status"] == "BLOCKED"


def test_pre_check_blocked_human_mode_exits_with_the_same_auth_code(invoke, precheck_doubles) -> None:
    """The exit code must not depend on the output format.

    A wrapper script that runs ``pre-check`` without ``--json`` (so the user
    sees the remediation panel) still needs to branch on the same code as the
    machine-readable path. Divergent codes between modes would make the
    contract useless in exactly the interactive case it was written for.
    """
    precheck_doubles.gcp_accounts = []

    result = invoke(["pre-check"])

    assert result.exit_code == AuthError.exit_code


def test_pre_check_blocked_json_reports_every_reason_as_a_separate_error(invoke, precheck_doubles) -> None:
    """One ``errors[]`` entry per blocker, each with its own remediation.

    Collapsing several independent blockers into a single message forced the
    caller to re-split prose to find out whether GCP, Looker, or both needed
    attention.
    """
    precheck_doubles.gcp_accounts = []
    precheck_doubles.looker = LookerAuthStatus(is_authenticated=False, error_message="nope")

    result = invoke(["pre-check", "--json"])

    payload = envelope(result)
    assert result.exit_code == AuthError.exit_code
    assert [e["message"] for e in payload["errors"]] == [
        _NO_ACCOUNTS_REASON,
        "Looker is not authenticated (nope).",
    ]
    assert all(e["code"] == "AUTH_ERROR" for e in payload["errors"])
    assert all("demo-create pre-check --fix" in e["remediation"] for e in payload["errors"])


def test_pre_check_blocked_run_suggests_no_next_action(invoke, precheck_doubles) -> None:
    """A blocked gate must not hand the orchestrator a step to run.

    ``next_actions`` is advisory but agents follow it; emitting "go design the
    schema" while authentication is broken is precisely the failure mode gate
    0 exists to prevent.
    """
    precheck_doubles.gcp_accounts = []

    result = invoke(["pre-check", "--json"])

    assert envelope(result)["next_actions"] == []


def test_pre_check_looker_unauthenticated_blocks(invoke, precheck_doubles) -> None:
    """Looker credentials are a gate 0 blocker in their own right."""
    precheck_doubles.looker = LookerAuthStatus(
        is_authenticated=False,
        auth_method="none",
        instance_url="",
        error_message="No active OAuth session",
        available_oauth_instances=[{"id": 1, "instance_name": "fake-instance"}],
    )

    result = invoke(["pre-check", "--json"])
    payload_data = report(result)

    assert result.exit_code == AuthError.exit_code
    assert payload_data["is_blocked"] is True
    assert payload_data["blocking_reasons"] == ["Looker is not authenticated (No active OAuth session)."]


def test_pre_check_looker_unauthenticated_without_error_message_uses_fallback_text(invoke, precheck_doubles) -> None:
    """A missing upstream diagnostic must not produce a ``None`` in the reason."""
    precheck_doubles.looker = LookerAuthStatus(is_authenticated=False, error_message=None)

    result = invoke(["pre-check", "--json"])

    assert report(result)["blocking_reasons"] == ["Looker is not authenticated (No active OAuth session or API key)."]


def test_pre_check_gcp_and_looker_reasons_are_both_reported_in_order(invoke, precheck_doubles) -> None:
    """Ordering is stable (GCP first) so the user fixes prerequisites in order."""
    precheck_doubles.gcp_accounts = []
    precheck_doubles.looker = LookerAuthStatus(is_authenticated=False, error_message="nope")

    result = invoke(["pre-check", "--json"])

    assert result.exit_code == AuthError.exit_code
    assert report(result)["blocking_reasons"] == [
        _NO_ACCOUNTS_REASON,
        "Looker is not authenticated (nope).",
    ]


@pytest.mark.characterization
def test_pre_check_active_account_without_bigquery_is_reported_as_reauth(invoke, precheck_doubles) -> None:
    """# BUG: misleading blocking reason (unfixed; owned by a later phase).

    ``pre_check`` folds "active account lacks BigQuery access" into
    ``reauth_required``. A user whose token is perfectly valid but who simply
    lacks ``roles/bigquery.*`` on the project is told to re-run ``gcloud auth
    login``, which will never fix the problem. Phase 2 only reclassified the
    *exit code* (now 3 rather than 1); the misdiagnosis in the message itself
    is untouched, so it stays pinned here.
    """
    precheck_doubles.gcp_accounts = [
        GCPAccountInfo(
            account_id="valid-token-no-perms@example.com",
            is_active=True,
            project_id="fake-active-project",
            has_bigquery_access=False,
            error_message="403 Access Denied: bigquery.datasets.list",
        )
    ]

    result = invoke(["pre-check", "--json"])
    payload_data = report(result)

    assert payload_data["reauth_required"] is True
    assert payload_data["blocking_reasons"] == [_REAUTH_REASON]


def test_pre_check_effective_project_falls_back_to_active_gcloud_project(invoke, precheck_doubles) -> None:
    """An empty ``--gcp-project`` lets the gcloud/ADC context win.

    Pins the resolution chain ``gcp_project or active_project or
    adc_project_id or ""``. The flag is passed explicitly as ``""`` because its
    declared default, ``DEFAULT_GCP_PROJECT``, is read from
    ``$GOOGLE_CLOUD_PROJECT`` at import time and is therefore host-dependent.
    """
    precheck_doubles.gcp_accounts = _accounts_without_bigquery()

    result = invoke(["pre-check", "--json", "--gcp-project", ""])

    assert precheck_doubles.inspect_target_projects == ["fake-active-project"]
    assert "'fake-active-project'" in report(result)["blocking_reasons"][0]


def test_pre_check_effective_project_falls_back_to_adc_project(invoke, precheck_doubles) -> None:
    """Second fallback rung: ADC project id when gcloud has no active project."""
    precheck_doubles.gcp_context = GCPActiveContext(adc_project_id="fake-adc-project")
    precheck_doubles.gcp_accounts = _accounts_without_bigquery()

    result = invoke(["pre-check", "--json", "--gcp-project", ""])

    assert precheck_doubles.inspect_target_projects == ["fake-adc-project"]
    assert "'fake-adc-project'" in report(result)["blocking_reasons"][0]


def test_pre_check_explicit_gcp_project_overrides_context(invoke, precheck_doubles) -> None:
    """An explicit flag always wins: the user's confirmed target is honoured.

    Gate 0 asks the user to confirm a project; silently probing a different
    one would defeat the confirmation.
    """
    precheck_doubles.gcp_accounts = _accounts_without_bigquery()

    result = invoke(["pre-check", "--json", "--gcp-project", "explicit-project"])

    assert precheck_doubles.inspect_target_projects == ["explicit-project"]
    assert "'explicit-project'" in report(result)["blocking_reasons"][0]


def test_pre_check_effective_project_falls_back_to_literal_default_string(invoke, precheck_doubles) -> None:
    """With no project anywhere, the message renders the word ``default``.

    Pins ``{effective_project or 'default'}`` so the reason never degrades
    into ``on project ''``.
    """
    precheck_doubles.gcp_context = GCPActiveContext()
    precheck_doubles.gcp_accounts = _accounts_without_bigquery()

    result = invoke(["pre-check", "--json", "--gcp-project", ""])

    assert precheck_doubles.inspect_target_projects == [""]
    assert report(result)["blocking_reasons"] == ["No configured GCP account has BigQuery access on project 'default'."]


# ---------------------------------------------------------------------------
# pre-check: Rich (non-JSON) rendering
# ---------------------------------------------------------------------------


def test_pre_check_rich_output_renders_all_six_sections(invoke, precheck_doubles) -> None:
    """The interactive audit is the user-facing half of gate 0."""
    result = invoke(["pre-check"])

    assert result.exit_code == 0
    for heading in (
        "1. Python Runtime",
        "2. GCP & ADC Context",
        "3. Available Google Cloud Projects",
        "4. Global MCP Tool Configurations",
        "5. Intent-Organized Agent Skills",
        "6. Looker Authentication",
    ):
        assert heading in result.output, f"missing section heading {heading!r}"


def test_pre_check_human_mode_writes_nothing_to_stdout(invoke, precheck_doubles) -> None:
    """Human mode leaves stdout empty so it can be redirected safely.

    ``demo-create pre-check > report.json`` used to capture Rich tables; now
    the tables stay on the terminal via stderr and only ``--json`` produces a
    file worth keeping.
    """
    result = invoke(["pre-check"])

    assert result.exit_code == 0
    assert "1. Python Runtime" in result.output
    assert result.stdout == ""


def test_pre_check_rich_output_renders_banner_and_fix_tip(invoke, precheck_doubles) -> None:
    """An unconfigured MCP server should advertise the one-command repair."""
    result = invoke(["pre-check"])

    assert "PRE-CHECK: ENVIRONMENT" in result.output
    # One MCP server is unconfigured in the default doubles -> tip is shown.
    assert "demo-create pre-check --fix" in result.output


def test_pre_check_rich_output_blocked_banner_and_remediation(invoke, precheck_doubles) -> None:
    """The blocked panel must spell out both remediation paths.

    This panel is the only thing an interactive user sees; if it omits either
    the gcloud or the Looker instructions they are stuck with no way forward.
    """
    precheck_doubles.gcp_accounts = []
    precheck_doubles.looker = LookerAuthStatus(is_authenticated=False, error_message="nope")

    result = invoke(["pre-check"])

    assert result.exit_code == AuthError.exit_code
    assert "EXECUTION BLOCKED" in result.output
    assert "1. Google Cloud Authentication Commands:" in result.output
    assert "2. Looker Authentication & OAuth Setup:" in result.output
    assert "Execution blocked." in result.output


def test_pre_check_rich_output_warns_on_bare_system_python(invoke, precheck_doubles) -> None:
    """Bare system Python is a caveat, not a blocker: warn and continue."""
    precheck_doubles.env_status = _healthy_env_status()
    precheck_doubles.env_status.is_virtualenv = False

    result = invoke(["pre-check"])

    assert "bare system Python" in result.output
    assert "demo-create env init" in result.output


def test_pre_check_rich_output_warns_when_no_projects_available(invoke, precheck_doubles) -> None:
    """With no projects listed, the user needs the command that fixes it."""
    precheck_doubles.projects = []

    result = invoke(["pre-check"])

    assert "No Google Cloud projects found" in result.output
    assert "gcloud config set project <PROJECT_ID>" in result.output


def test_pre_check_rich_output_hints_when_multiple_accounts(invoke, precheck_doubles) -> None:
    """Ambiguity must be surfaced: gate 0 requires an explicitly chosen account."""
    precheck_doubles.gcp_accounts = [
        GCPAccountInfo(account_id="a@example.com", is_active=True, has_bigquery_access=True),
        GCPAccountInfo(account_id="b@example.com", is_active=False, has_bigquery_access=True),
    ]

    result = invoke(["pre-check"])

    assert "Multiple GCP accounts found" in result.output


# ---------------------------------------------------------------------------
# pre-check: --fix
# ---------------------------------------------------------------------------


def test_pre_check_fix_patches_mcp_and_organizes_skills(invoke, precheck_doubles) -> None:
    """``--fix`` repairs MCP config and re-reads it, so the report is post-fix."""
    result = invoke(["pre-check", "--fix", "--json"])

    assert result.exit_code == 0
    assert precheck_doubles.patch_mcp_calls == 1
    assert precheck_doubles.skills_fix_args == [True]
    # MCP servers are re-checked after patching so the report reflects reality.
    assert precheck_doubles.mcp_check_calls == 2


def test_pre_check_without_fix_does_not_mutate_anything(invoke, precheck_doubles) -> None:
    """The default invocation is a pure audit; mutation requires opting in."""
    invoke(["pre-check", "--json"])

    assert precheck_doubles.patch_mcp_calls == 0
    assert precheck_doubles.skills_fix_args == [False]
    assert precheck_doubles.mcp_check_calls == 1
    assert precheck_doubles.venv_init_calls == []


def test_pre_check_fix_bootstraps_venv_when_not_in_virtualenv(invoke, precheck_doubles) -> None:
    """``--fix`` creates the workspace venv rather than merely reporting its absence."""
    precheck_doubles.env_status = _healthy_env_status()
    precheck_doubles.env_status.is_virtualenv = False

    result = invoke(["pre-check", "--fix"])

    assert result.exit_code == 0
    assert precheck_doubles.venv_init_calls == [Path.cwd()]


def test_pre_check_fix_json_stays_parseable_while_narrating_progress(invoke, precheck_doubles) -> None:
    """Progress narration no longer corrupts the machine-readable stream.

    ``--fix`` calls ``print_info`` unconditionally while bootstrapping the
    venv. That prose used to be written to stdout ahead of the report, so
    ``json.loads(stdout)`` failed and gate 0 was unusable for an orchestrator
    on exactly the machines that most needed ``--fix``. Rich now writes to
    stderr, so the user still sees the narration while stdout holds only the
    envelope.
    """
    precheck_doubles.env_status = _healthy_env_status()
    precheck_doubles.env_status.is_virtualenv = False

    result = invoke(["pre-check", "--fix", "--json"])

    assert result.exit_code == 0
    # The narration happened...
    assert "Fix flag enabled" in result.output
    # ...but not on stdout, which is a single parseable document.
    assert "Fix flag enabled" not in result.stdout
    assert result.stdout.lstrip().startswith("{")
    payload = envelope(result)
    assert payload["status"] == "SUCCESS"
    assert payload["command"] == "pre-check"


def test_pre_check_fix_reuses_stale_env_status_after_venv_init(invoke, precheck_doubles) -> None:
    """``check_runtime_environment`` is re-invoked after bootstrapping the venv.

    The double keeps reporting ``is_virtualenv=False``, which proves the second
    call really happened: the warning is derived from the refreshed status
    rather than from the value captured before the fix.
    """
    precheck_doubles.env_status = _healthy_env_status()
    precheck_doubles.env_status.is_virtualenv = False

    result = invoke(["pre-check", "--fix"])

    assert "bare system Python" in result.output


# ---------------------------------------------------------------------------
# skills
# ---------------------------------------------------------------------------


def test_skills_reports_organized_count(invoke, precheck_doubles) -> None:
    """Without ``--fix`` the command audits rather than mutates."""
    result = invoke(["skills"])

    assert result.exit_code == 0
    assert precheck_doubles.skills_fix_args == [False]
    assert "Organized 1 skills across intent categories" in result.output


def test_skills_fix_flag_is_forwarded(invoke, precheck_doubles) -> None:
    """``--fix`` is the only way skills get symlinked into place."""
    result = invoke(["skills", "--fix"])

    assert result.exit_code == 0
    assert precheck_doubles.skills_fix_args == [True]


def test_skills_reports_zero_when_nothing_found(invoke, precheck_doubles) -> None:
    """Even an empty audit is reported as a success, never as an error."""
    precheck_doubles.skills = []

    result = invoke(["skills"])

    assert result.exit_code == 0
    assert "Organized 0 skills" in result.output


# ---------------------------------------------------------------------------
# run-script
# ---------------------------------------------------------------------------


def test_run_script_missing_file_is_a_config_error(invoke) -> None:
    """A bad path is a configuration failure (exit 4), not a generic exit 1.

    The caller supplied an argument that parsed fine but cannot be acted on.
    Distinguishing that from a script that ran and exited 1 of its own accord
    is the entire point of reserving a dedicated code -- otherwise a wrapper
    cannot tell "you gave me the wrong path" from "your script failed".
    """
    result = invoke(["run-script", "nowhere/missing.py"])

    assert result.exit_code == ConfigError.exit_code
    assert "Script file not found: nowhere/missing.py" in result.output


def test_run_script_missing_file_offers_remediation(invoke) -> None:
    """The failure carries the fix, so the user is not left guessing."""
    result = invoke(["run-script", "missing.py"])

    assert "Pass a path that exists" in result.output


def test_run_script_missing_file_keeps_stdout_clean(invoke) -> None:
    """Even a failure must not emit prose on the machine-readable stream.

    ``run-script`` proxies a child process whose stdout is the user's actual
    payload; leaking a CLI error message into it would corrupt that stream.
    """
    result = invoke(["run-script", "missing.py"])

    assert result.stdout == ""


def test_run_script_forwards_exit_code_of_the_child(invoke, fake_shell) -> None:
    """A script that runs owns the exit code; the CLI must not overwrite it.

    This is what keeps exit 4 unambiguous: it can only ever mean "the CLI could
    not find your script", never "your script returned 4".
    """
    script = Path("real.py")
    script.write_text("print('hi')\n", encoding="utf-8")
    fake_shell.stub("real.py", returncode=7)

    result = invoke(["run-script", str(script)])

    assert result.exit_code == 7


# ---------------------------------------------------------------------------
# env init / env info
# ---------------------------------------------------------------------------


def test_env_init_success_branch(invoke, precheck_doubles, tmp_path: Path) -> None:
    """On success the user is handed the literal activation command."""
    target = tmp_path / "workspace"
    target.mkdir()

    result = invoke(["env", "init", "--dir", str(target)])

    assert result.exit_code == 0
    assert precheck_doubles.venv_init_calls == [target]
    assert "Workspace environment ready!" in result.output
    assert "source /fake/.venv/bin/activate" in result.output


@pytest.mark.characterization
def test_env_init_failure_branch_exits_one(invoke, precheck_doubles, tmp_path: Path) -> None:
    """# BUG: unclassified exit 1 on a bootstrap failure (unfixed).

    ``env_init`` still raises a bare ``typer.Exit(code=1)`` instead of a
    ``ConfigError``/``StateError``, so this failure is indistinguishable from
    an unexpected crash. It was left out of Phase 2 because ``env init`` has no
    ``--json`` mode and is not part of the orchestrated gate sequence; folding
    it into the error contract belongs with the Phase 3 context refactor.
    """
    precheck_doubles.venv_init_result = (False, "uv venv exploded")

    result = invoke(["env", "init", "--dir", str(tmp_path)])

    assert result.exit_code == 1
    assert result.exit_code not in {e.exit_code for e in (AuthError, ConfigError, RemoteApiError, StateError)}
    assert "Failed to initialize environment: uv venv exploded" in result.output


def test_env_init_dir_is_resolved_at_call_time_not_import_time(invoke, precheck_doubles, tmp_path) -> None:
    """``env init`` must bootstrap the ``.venv`` in the *live* working directory.

    The option used to default to ``Path.cwd()`` in the signature, which is
    evaluated once when the module is imported. A long-lived process -- or a
    test session -- that changed directory afterwards would silently create the
    ``.venv`` in whichever directory the interpreter happened to start in.

    It also leaked the generating machine's absolute path into
    ``docs/COMMANDS.md``, which is how the docs drift test caught it.

    The default is now ``None`` and the CWD is read inside the command.
    """
    from looker_demo_cli.commands.env import env_init

    # The signature must carry no path at all; a non-None default here would
    # mean the import-time capture had returned.
    assert inspect.signature(env_init).parameters["target_dir"].default is None

    invoke(["env", "init"])

    assert precheck_doubles.venv_init_calls == [Path.cwd()]


def test_env_init_dir_flag_overrides_the_working_directory(invoke, precheck_doubles, tmp_path) -> None:
    """An explicit ``--dir`` must still win over the resolved default.

    Pins the other side of the fallback: making the default lazy must not make
    the flag itself inert.
    """
    invoke(["env", "init", "--dir", str(tmp_path)])

    assert precheck_doubles.venv_init_calls == [tmp_path]


def test_env_info_renders_runtime_tables(invoke, precheck_doubles) -> None:
    """Dependency pin violations must be visible, since they cause later failures."""
    result = invoke(["env", "info"])

    assert result.exit_code == 0
    assert "1. Python Runtime" in result.output
    assert "/fake/.venv/bin/python" in result.output
    assert "3.12.4" in result.output
    assert "uv 0.4.20" in result.output
    # Dependency pin table
    assert "mcp" in result.output
    assert "lkr-dev-cli" in result.output
    assert "VIOLATION" in result.output


def test_env_info_writes_nothing_to_stdout(invoke, precheck_doubles) -> None:
    """``env info`` is pure diagnostics, so it belongs entirely on stderr."""
    result = invoke(["env", "info"])

    assert result.exit_code == 0
    assert "1. Python Runtime" in result.output
    assert result.stdout == ""


def test_env_info_warns_when_not_in_virtualenv(invoke, precheck_doubles) -> None:
    """Bare system Python is the most common cause of dependency-pin surprises."""
    precheck_doubles.env_status = _healthy_env_status()
    precheck_doubles.env_status.is_virtualenv = False

    result = invoke(["env", "info"])

    assert result.exit_code == 0
    assert "NO (Bare System Python)" in result.output
    assert "bare system Python" in result.output


def test_env_group_with_no_subcommand_shows_help(invoke) -> None:
    """``no_args_is_help`` keeps a bare group invocation discoverable."""
    result = invoke(["env"])

    assert "Usage:" in result.output
    assert "init" in result.output
    assert "info" in result.output
