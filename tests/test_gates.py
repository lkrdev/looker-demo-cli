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
    "gate_0_environment": {"precheck_passed": True},
    "gate_1_data": {"dataset_exists": True},
    "gate_2_model": {"lookml_output_dir": Path("/scratch/lookml")},
    "gate_3_deploy": {"deployed_dashboard_url": "https://acme.looker.com/dashboards/42"},
    "gate_4_agent": {"ca_agent_id": "agent-42"},
    "gate_5_publish": {"published_to_ge": True},
}


def _unstarted_state(**overrides: Any) -> FlowState:
    overrides.setdefault("gcp_project_id", "")
    return FlowState(**overrides)


def _finished_state(**overrides: Any) -> FlowState:
    fields: dict[str, Any] = {
        "precheck_passed": True,
        "gcp_project_id": "acme-analytics",
        "gcp_account": "analyst@acme.com",
        "looker_account": "acme-looker",
        "looker_connection_name": "acme_bigquery",
        "dataset_exists": True,
        "bq_dataset_id": "retail",
        "domain_name": "retail",
        "generated_parquet_dir": Path("/scratch/retail"),
        "generated_tables": ["fct_orders", "dim_users"],
        "looker_project_name": "retail_demo",
        "lookml_model_name": "retail_demo",
        "lookml_output_dir": Path("/scratch/lookml_retail_demo"),
        "existing_tables": ["fct_orders", "dim_users"],
        "deployed_dashboard_url": "https://acme.looker.com/dashboards/42",
        "deployed_dashboard_id": "42",
        "ca_agent_id": "agent-42",
        "published_to_ge": True,
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
    """Gates are numbered 0..5 contiguously, uniquely named, and gate 2 is the only non-pausing gate."""
    assert len(GATES) == 6
    assert [g.number for g in GATES] == list(range(6))
    assert len(set(_GATE_IDS)) == 6
    for gate in GATES:
        assert gate.id.startswith(f"gate_{gate.number}_")
        assert gate.title.strip()
        assert (gate.human_checkpoint is not None) == gate.requires_human_confirmation

    non_pausing = [g.id for g in GATES if not g.requires_human_confirmation]
    assert non_pausing == ["gate_2_model"]
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
        (0, "gate_0_environment"),
        (1, "gate_1_data"),
        (2, "gate_2_model"),
        (3, "gate_3_deploy"),
        (4, "gate_4_agent"),
        (5, "gate_5_publish"),
        (6, None),
    ],
)
def test_gate_progression_walk(satisfied_count: int, expected_current: str | None) -> None:
    """Completing each gate prefix advances current_gate strictly to the first incomplete gate."""
    state = _state_satisfying(*_GATE_IDS[:satisfied_count])
    current = current_gate(state)
    assert (current.id if current else None) == expected_current
    assert [g.id for g in completed_gates(state)] == _GATE_IDS[:satisfied_count]
    assert is_pipeline_complete(state) is (satisfied_count == 6)

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
    state = _state_satisfying("gate_3_deploy", "gate_1_data")
    assert current_gate(state).id == "gate_0_environment"
    assert [g.id for g in completed_gates(state)] == ["gate_1_data", "gate_3_deploy"]


# ===========================================================================
# Gate command interpolation & branches
# ===========================================================================

_UNSTARTED_COMMANDS: dict[str, str] = {
    "gate_0_environment": "demo-create pre-check --fix --gcp-project <gcp-project>",
    "gate_1_data": "demo-create data generate --domain <domain> --row-count <row-count> --output-dir <output-dir>",
    "gate_2_model": (
        "demo-create lookml model --looker-project <looker-project> --dataset <dataset-id> "
        "--connection <connection-name> --gcp-project <gcp-project>"
    ),
    "gate_3_deploy": "demo-create lookml deploy --looker-project <looker-project> --lookml-dir <lookml-dir>",
    "gate_4_agent": "demo-create agent create --model <model-name> --explore <explore-name>",
    "gate_5_publish": "demo-create agent publish --agent-id <agent-id>",
}

_FINISHED_COMMANDS: dict[str, str] = {
    "gate_0_environment": "demo-create pre-check --fix --gcp-project acme-analytics",
    "gate_1_data": (
        "demo-create data upload --parquet-dir /scratch/retail --gcp-project acme-analytics --dataset retail"
    ),
    "gate_2_model": (
        "demo-create lookml model --looker-project retail_demo --dataset retail "
        "--connection acme_bigquery --gcp-project acme-analytics"
    ),
    "gate_3_deploy": (
        "demo-create lookml deploy --looker-project retail_demo "
        "--lookml-dir /scratch/lookml_retail_demo --looker-account acme-looker"
    ),
    "gate_4_agent": (
        "demo-create agent create --model retail_demo --explore fct_orders "
        "--dashboards-dir /scratch/lookml_retail_demo/dashboards"
    ),
    "gate_5_publish": "demo-create agent publish --agent-id agent-42",
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
    """Verify Gate 1 generate/upload switch, Gate 3 account flag, Gate 4 explore fallback, and blank handling."""
    # Gate 1 switches from generate to upload once Parquet exists
    assert GATES[1].command(_unstarted_state()).startswith("demo-create data generate ")
    with_parquet = _unstarted_state(generated_parquet_dir=Path("/scratch/retail"), bq_dataset_id="retail")
    assert GATES[1].command(with_parquet).startswith("demo-create data upload ")

    # Gate 3 omits --looker-account when None
    assert "--looker-account" not in GATES[3].command(_finished_state(looker_account=None))

    # Gate 4 falls back to existing_tables when generated_tables is empty
    byo_state = _finished_state(generated_tables=[], existing_tables=["fct_shipments"])
    assert "--explore fct_shipments" in GATES[4].command(byo_state)

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
    """`status --json` reports gate 0 on a fresh workspace without creating state, and terminates when complete."""
    fresh = invoke(["status", "--json"])
    assert fresh.exit_code == 0
    assert fresh.stderr == ""
    body = envelope(fresh)
    assert body["command"] == "status"
    assert body["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert body["data"]["current_gate"]["id"] == "gate_0_environment"
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
    state_file(precheck_passed=True, dataset_exists=True, bq_dataset_id="retail", looker_project_name="retail_demo")
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
    assert envelope(ok)["data"]["current_gate"]["id"] == "gate_1_data"
    assert Path(envelope(ok)["data"]["state_file"]) == custom

    broken = tmp_path / "broken.json"
    broken.write_text("{not valid json", encoding="utf-8")
    err = invoke(["status", "--json", "--state-file", str(broken)])
    assert err.exit_code == StateError.exit_code
    assert envelope(err)["errors"][0]["code"] == "STATE_ERROR"
