"""Tests for the pure gate model (`looker_demo_cli.gates`), Click command validity, and `demo-create status`."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import pytest
from typer.main import get_command

from looker_demo_cli.cli import app
from looker_demo_cli.errors import StateError
from looker_demo_cli.gates import (
    GATES,
    _resolve_primary_explore,
    completed_gates,
    current_gate,
    evaluate_gates,
    is_pipeline_complete,
)
from looker_demo_cli.output import ENVELOPE_SCHEMA_VERSION
from looker_demo_cli.state import STATE_FILE_NAME, FlowState

try:
    from conftest import envelope
except ImportError:  # pragma: no cover
    from tests.conftest import envelope

pytestmark = pytest.mark.unit

_CLI: Any = get_command(app)
_GATE_IDS: list[str] = [gate.id for gate in GATES]

_COMPLETION_SIGNAL: dict[str, dict[str, object]] = {
    "gate_0a_precheck": {"precheck_passed": True},
    "gate_0b_confirm_targets": {"targets_confirmed": True},
    "gate_1a_propose_schema": {"schema_proposed": True},
    "gate_1b_approve_schema": {"schema_approved": True},
    "gate_1c_generate_data": {"dataset_exists": True},
    "gate_2a_lookml_model": {"lookml_output_dir": Path("/scratch/lookml")},
    "gate_2b_certify_polish": {"polish_certified": True},
    "gate_3a_optimize": {"optimizer_status": "applied"},
    "gate_3b_deploy": {"deployed_dashboard_url": "https://acme.looker.com/dashboards/42"},
    "gate_3c_critique": {"critique_approved": True},
    "gate_4_agent": {"ca_agent_id": "agent-42", "ca_agent_status": "created"},
    "gate_5_publish": {"published_to_ge": True, "ge_publish_status": "published"},
    "gate_6_embed": {"embed_workspace_dir": Path("/scratch/embed"), "embed_status": "scaffolded"},
}


def _unstarted_state(**overrides: Any) -> FlowState:
    overrides.setdefault("gcp_project_id", "")
    return FlowState(**overrides)


def _finished_state(**overrides: Any) -> FlowState:
    fields: dict[str, Any] = {
        "precheck_passed": True,
        "targets_confirmed": True,
        "gcp_project_id": "acme-analytics",
        "gcp_account": "analyst@acme.com",
        "looker_account": "acme-looker",
        "looker_connection_name": "acme_bigquery",
        "schema_file_path": Path("/scratch/schema.json"),
        "schema_proposed": True,
        "schema_approved": True,
        "approved_row_count": 5000,
        "dataset_exists": True,
        "bq_dataset_id": "retail",
        "domain_name": "retail",
        "generated_parquet_dir": Path("/scratch/retail"),
        "generated_tables": ["dim_users", "fct_orders"],
        "looker_project_name": "retail_demo",
        "lookml_model_name": "retail_demo",
        "primary_explore_name": "fct_orders",
        "lookml_output_dir": Path("/scratch/lookml_retail_demo"),
        "polish_certified": True,
        "optimizer_status": "applied",
        "existing_tables": ["dim_users", "fct_orders"],
        "deployed_dashboard_url": "https://acme.looker.com/dashboards/42",
        "deployed_dashboard_id": "42",
        "critique_approved": True,
        "ca_agent_id": "agent-42",
        "ca_agent_status": "created",
        "published_to_ge": True,
        "ge_publish_status": "published",
        "embed_workspace_dir": Path("/scratch/embed_retail_demo"),
        "embed_status": "scaffolded",
    }
    fields.update(overrides)
    return FlowState(**fields)


def _state_satisfying(*gate_ids: str) -> FlowState:
    overrides: dict[str, object] = {}
    for gate_id in gate_ids:
        overrides.update(_COMPLETION_SIGNAL[gate_id])
    return _unstarted_state(**overrides)


# ===========================================================================
# Gate model invariants & evaluation
# ===========================================================================


def test_gate_structural_invariants() -> None:
    """Gates are numbered 0..12 contiguously, uniquely named, with explicit human confirmation checkpoints."""
    assert len(GATES) == 13
    assert [g.number for g in GATES] == list(range(13))
    assert len(set(_GATE_IDS)) == 13
    for gate in GATES:
        assert gate.id.startswith("gate_")
        assert gate.title.strip()
        assert (gate.human_checkpoint is not None) == gate.requires_human_confirmation

    non_pausing = [g.id for g in GATES if not g.requires_human_confirmation]
    assert non_pausing == [
        "gate_0a_precheck",
        "gate_1a_propose_schema",
        "gate_1c_generate_data",
        "gate_2a_lookml_model",
        "gate_3b_deploy",
    ]
    pausing = [g.id for g in GATES if g.requires_human_confirmation]
    assert pausing == [
        "gate_0b_confirm_targets",
        "gate_1b_approve_schema",
        "gate_2b_certify_polish",
        "gate_3a_optimize",
        "gate_3c_critique",
        "gate_4_agent",
        "gate_5_publish",
        "gate_6_embed",
    ]
    assert set(GATES[0].describe()) == {
        "number",
        "id",
        "title",
        "requires_human_confirmation",
        "human_checkpoint",
    }


@pytest.mark.parametrize(
    ("satisfied_count", "expected_current"),
    [
        (0, "gate_0a_precheck"),
        (1, "gate_0b_confirm_targets"),
        (2, "gate_1a_propose_schema"),
        (3, "gate_1b_approve_schema"),
        (4, "gate_1c_generate_data"),
        (5, "gate_2a_lookml_model"),
        (6, "gate_2b_certify_polish"),
        (7, "gate_3a_optimize"),
        (8, "gate_3b_deploy"),
        (9, "gate_3c_critique"),
        (10, "gate_4_agent"),
        (11, "gate_5_publish"),
        (12, "gate_6_embed"),
        (13, None),
    ],
)
def test_gate_progression_walk(satisfied_count: int, expected_current: str | None) -> None:
    """Completing each gate prefix advances current_gate strictly to the first incomplete gate."""
    state = _state_satisfying(*_GATE_IDS[:satisfied_count])
    current = current_gate(state)
    assert (current.id if current else None) == expected_current
    assert [g.id for g in completed_gates(state)] == _GATE_IDS[:satisfied_count]
    assert is_pipeline_complete(state) is (satisfied_count == 13)

    statuses = evaluate_gates(state)
    assert [s.gate.id for s in statuses] == _GATE_IDS
    assert [s.gate.id for s in statuses if s.is_current] == ([expected_current] if expected_current else [])
    assert set(statuses[0].to_dict()) == {
        "number",
        "id",
        "title",
        "complete",
        "is_current",
        "command",
        "requires_human_confirmation",
        "human_checkpoint",
    }


def test_out_of_order_signals_keep_first_incomplete_as_current() -> None:
    """Later gate signals do not skip earlier incomplete gates, while completed_gates preserves order."""
    state = _state_satisfying("gate_3b_deploy", "gate_2a_lookml_model")
    current = current_gate(state)
    assert current is not None and current.id == "gate_0a_precheck"
    assert [g.id for g in completed_gates(state)] == ["gate_2a_lookml_model", "gate_3b_deploy"]


def test_skip_terminal_states_satisfy_optional_gates() -> None:
    """Skipping optional gates (3A, 4, 5, 6) marks them complete so pipeline reaches is_pipeline_complete=True."""
    skipped_state = _finished_state(
        optimizer_status="skipped",
        ca_agent_id=None,
        ca_agent_status="skipped",
        published_to_ge=False,
        ge_publish_status="pending",  # ca_agent_status='skipped' also completes gate 5
        embed_workspace_dir=None,
        embed_status="skipped",
    )
    assert is_pipeline_complete(skipped_state) is True
    assert current_gate(skipped_state) is None


# ===========================================================================
# Gate command interpolation & branches
# ===========================================================================

_UNSTARTED_COMMANDS: dict[str, str] = {
    "gate_0a_precheck": "demo-create pre-check --fix --gcp-project <gcp-project>",
    "gate_0b_confirm_targets": (
        "demo-create confirm-targets --gcp-account <gcp-account> "
        "--gcp-project <gcp-project> --looker-account <looker-account> --connection <connection-name>"
    ),
    "gate_1a_propose_schema": "demo-create data propose-schema --schema-file <schema-file> --preview",
    "gate_1b_approve_schema": "demo-create data approve-schema --row-count <row-count> --dataset <dataset-id>",
    "gate_1c_generate_data": (
        "demo-create data generate --schema-file <schema-file> "
        "--row-count <row-count> --gcp-project <gcp-project> --dataset <dataset-id> "
        "--upload --json-scorecard"
    ),
    "gate_2a_lookml_model": (
        "demo-create lookml model --looker-project <looker-project> --dataset <dataset-id> "
        "--connection <connection-name> --gcp-project <gcp-project>"
    ),
    "gate_2b_certify_polish": "demo-create lookml certify-polish --lookml-dir <lookml-dir>",
    "gate_3a_optimize": "demo-create lookml optimize --lookml-dir <lookml-dir>",
    "gate_3b_deploy": "demo-create lookml deploy --looker-project <looker-project> --lookml-dir <lookml-dir>",
    "gate_3c_critique": "demo-create lookml approve-critique --looker-project <looker-project>",
    "gate_4_agent": "demo-create agent create --model <model-name> --explore <explore-name>",
    "gate_5_publish": "demo-create agent publish --agent-id <agent-id>",
    "gate_6_embed": "demo-create embed scaffold --looker-project <looker-project>",
}

_FINISHED_COMMANDS: dict[str, str] = {
    "gate_0a_precheck": "demo-create pre-check --fix --gcp-project acme-analytics",
    "gate_0b_confirm_targets": (
        "demo-create confirm-targets --gcp-account analyst@acme.com "
        "--gcp-project acme-analytics --looker-account acme-looker --connection acme_bigquery"
    ),
    "gate_1a_propose_schema": "demo-create data propose-schema --schema-file /scratch/schema.json --preview",
    "gate_1b_approve_schema": "demo-create data approve-schema --row-count 5000 --dataset retail",
    "gate_1c_generate_data": (
        "demo-create data upload --parquet-dir /scratch/retail --gcp-project acme-analytics --dataset retail"
    ),
    "gate_2a_lookml_model": (
        "demo-create lookml model --looker-project retail_demo --dataset retail "
        "--connection acme_bigquery --gcp-project acme-analytics"
    ),
    "gate_2b_certify_polish": "demo-create lookml certify-polish --lookml-dir /scratch/lookml_retail_demo",
    "gate_3a_optimize": "demo-create lookml optimize --lookml-dir /scratch/lookml_retail_demo",
    "gate_3b_deploy": (
        "demo-create lookml deploy --looker-project retail_demo "
        "--lookml-dir /scratch/lookml_retail_demo --looker-account acme-looker"
    ),
    "gate_3c_critique": "demo-create lookml approve-critique --looker-project retail_demo",
    "gate_4_agent": (
        "demo-create agent create --model retail_demo --explore fct_orders "
        "--dashboards-dir /scratch/lookml_retail_demo/dashboards"
    ),
    "gate_5_publish": "demo-create agent publish --agent-id agent-42",
    "gate_6_embed": "demo-create embed scaffold --looker-project retail_demo",
}


@pytest.mark.parametrize("gate", GATES, ids=_GATE_IDS)
def test_gate_commands_match_expected_unstarted_and_populated_strings(gate: Any) -> None:
    """Every gate produces exact command templates when unstarted and substitutes state when populated."""
    unstarted_cmd = gate.command(_unstarted_state())
    finished_cmd = gate.command(_finished_state())

    assert unstarted_cmd == _UNSTARTED_COMMANDS[gate.id]
    assert finished_cmd == _FINISHED_COMMANDS[gate.id]
    assert "<" not in finished_cmd
    assert "{" not in unstarted_cmd and "{" not in finished_cmd


def test_gate_command_branch_behaviors() -> None:
    """Verify Gate 1C generate/upload switch, Gate 3B account flag, Gate 4 fct_ explore preference, and blank handling."""
    # Gate 1C (index 4) switches from generate to upload once Parquet exists
    assert GATES[4].command(_unstarted_state()).startswith("demo-create data generate ")
    with_parquet = _unstarted_state(generated_parquet_dir=Path("/scratch/retail"), bq_dataset_id="retail")
    assert GATES[4].command(with_parquet).startswith("demo-create data upload ")

    # Gate 3B (index 8) omits --looker-account when None
    assert "--looker-account" not in GATES[8].command(_finished_state(looker_account=None))

    # Gate 4 (index 10) prefers primary_explore_name, then fct_ tables over dim_ tables
    byo_state = _finished_state(
        primary_explore_name=None,
        generated_tables=[],
        existing_tables=["dim_carriers", "fct_shipments"],
    )
    assert _resolve_primary_explore(byo_state) == "fct_shipments"
    assert "--explore fct_shipments" in GATES[10].command(byo_state)

    # Whitespace-only values render as placeholders
    assert "--gcp-project <gcp-project>" in GATES[0].command(_unstarted_state(gcp_project_id="   "))


# ===========================================================================
# Click command tree introspection (validates gate commands against real CLI)
# ===========================================================================


def _resolve_click_command(argv: list[str]) -> tuple[Any, list[str], list[str]]:
    node: Any = _CLI
    path: list[str] = []
    idx = 0
    while idx < len(argv) and not argv[idx].startswith("-"):
        commands = getattr(node, "commands", None)
        child = commands.get(argv[idx]) if isinstance(commands, dict) else None
        if child is None:
            break
        node = child
        path.append(argv[idx])
        idx += 1
    return node, path, argv[idx:]


def _declared_options(command: Any) -> set[str]:
    return {opt for param in command.params for opt in (*param.opts, *param.secondary_opts) if opt.startswith("-")}


@pytest.mark.parametrize("gate", GATES, ids=_GATE_IDS)
@pytest.mark.parametrize("state_builder", [_unstarted_state, _finished_state], ids=["unstarted", "populated"])
def test_every_gate_command_resolves_against_real_click_parser(gate: Any, state_builder: Any) -> None:
    """Every emitted gate command resolves to a real leaf subcommand with valid, non-duplicate flags and --json."""
    cmd_str = gate.command(state_builder())
    argv = shlex.split(cmd_str)
    assert argv[0] == "demo-create"

    resolved, path, remaining = _resolve_click_command(argv[1:])
    assert path, f"No subcommand resolved from {cmd_str!r}"
    assert not isinstance(getattr(resolved, "commands", None), dict), f"{cmd_str!r} stopped at group {path}"

    declared = _declared_options(resolved)
    assert "--json" in declared

    by_opt = {opt: p for p in resolved.params for opt in (*p.opts, *p.secondary_opts) if opt.startswith("-")}
    flags: list[str] = []
    positionals: list[str] = []
    i = 0
    while i < len(remaining):
        tok = remaining[i]
        if tok.startswith("-"):
            name, sep, _ = tok.partition("=")
            flags.append(name)
            param = by_opt.get(name)
            if param is not None and not sep and not getattr(param, "is_flag", False):
                i += 1
        else:
            positionals.append(tok)
        i += 1

    undeclared = set(flags) - declared
    assert not undeclared, f"{cmd_str!r} uses undeclared flags {undeclared}"
    assert len(flags) == len(set(flags)), f"{cmd_str!r} contains duplicate flags: {flags}"
    assert positionals == [], f"{cmd_str!r} contains unexpected positional args: {positionals}"


# ===========================================================================
# `demo-create status` CLI integration
# ===========================================================================


def test_status_json_fresh_and_finished_states(invoke, state_file, isolated_cwd: Path) -> None:
    """`status --json` reports gate 0a on a fresh workspace without creating state, and terminates when complete."""
    fresh = invoke(["status", "--json"])
    assert fresh.exit_code == 0
    assert fresh.stderr == ""
    body = envelope(fresh)
    assert body["command"] == "status"
    assert body["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert body["data"]["current_gate"]["id"] == "gate_0a_precheck"
    assert body["data"]["completed_gates"] == []
    assert body["data"]["is_complete"] is False
    assert body["next_actions"][0]["command"] == body["data"]["next_command"]
    assert not (isolated_cwd / STATE_FILE_NAME).exists()

    # Completed pipeline
    state_file(**_finished_state().model_dump())
    done = invoke(["status", "--json"])
    assert done.exit_code == 0
    done_body = envelope(done)
    assert done_body["data"]["is_complete"] is True
    assert done_body["data"]["current_gate"] is None
    assert done_body["data"]["next_command"] is None
    assert done_body["next_actions"] == []


def test_status_human_mode_renders_table_to_stderr(invoke, state_file) -> None:
    """`status` without `--json` renders gate table on stderr and leaves stdout empty."""
    state_file(
        precheck_passed=True,
        targets_confirmed=True,
        dataset_exists=True,
        bq_dataset_id="retail",
        looker_project_name="retail_demo",
    )
    result = invoke(["status"])

    assert result.exit_code == 0
    assert result.stdout == ""
    assert "complete" in result.stderr
    assert "current" in result.stderr
    assert "pending" in result.stderr
    for gate in GATES:
        assert gate.title in result.stderr


def test_status_state_file_redirection_and_corrupt_state_error(invoke, tmp_path: Path) -> None:
    """`--state-file` redirects reads and surfaces `StateError` (exit 7) on corrupt or newer files."""
    custom = tmp_path / "custom_state.json"
    custom.write_text(FlowState(precheck_passed=True).model_dump_json(), encoding="utf-8")

    ok = invoke(["status", "--json", "--state-file", str(custom)])
    assert ok.exit_code == 0
    assert envelope(ok)["data"]["current_gate"]["id"] == "gate_0b_confirm_targets"
    assert Path(envelope(ok)["data"]["state_file"]) == custom

    broken = tmp_path / "broken.json"
    broken.write_text("{not valid json", encoding="utf-8")
    err = invoke(["status", "--json", "--state-file", str(broken)])
    assert err.exit_code == StateError.exit_code
    assert envelope(err)["errors"][0]["code"] == "STATE_ERROR"
