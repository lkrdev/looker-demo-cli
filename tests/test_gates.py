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
    _COMMAND_TO_GATE_ID,
    GATES,
    MODE_ACTIVE_GATES,
    ParamBinding,
    _resolve_primary_explore,
    completed_gates,
    current_gate,
    evaluate_gates,
    gate_by_id,
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

_COMPLETION_SIGNAL: dict[str, dict[str, object]] = {gate.id: gate.sample_completion_state for gate in GATES}


def _unstarted_state(**overrides: Any) -> FlowState:
    overrides.setdefault("gcp_project_id", "")
    return FlowState(**overrides)


def _finished_state(**overrides: Any) -> FlowState:
    fields: dict[str, Any] = {
        "gcp_project_id": "acme-analytics",
        "gcp_account": "analyst@acme.com",
        "looker_account": "acme-looker",
        "looker_connection_name": "acme_bigquery",
        "schema_file_path": Path("/scratch/schema.json"),
        "approved_row_count": 5000,
        "bq_dataset_id": "retail",
        "domain_name": "retail",
        "generated_parquet_dir": Path("/scratch/retail"),
        "generated_tables": ["dim_users", "fct_orders"],
        "looker_project_name": "retail_demo",
        "lookml_model_name": "retail_demo",
        "primary_explore_name": "fct_orders",
        "existing_tables": ["dim_users", "fct_orders"],
        "deployed_dashboard_id": "42",
    }
    for gate in GATES:
        fields.update(gate.sample_completion_state)
    fields["lookml_output_dir"] = Path("/scratch/lookml_retail_demo")
    fields["embed_workspace_dir"] = Path("/scratch/embed_retail_demo")
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
    """Gates are numbered contiguously from 0, uniquely named, with explicit human confirmation checkpoints."""
    assert len(GATES) >= 15
    assert [g.number for g in GATES] == list(range(len(GATES)))
    assert len(set(_GATE_IDS)) == len(GATES)
    for gate in GATES:
        assert gate.id.startswith("gate_")
        assert gate.title.strip()
        assert (gate.human_checkpoint is not None) == gate.requires_human_confirmation
        assert gate.command_specs
        assert gate.sample_completion_state
        assert gate_by_id(gate.id) is gate

    with pytest.raises(KeyError):
        gate_by_id("gate_nonexistent")

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
        "gate_1d_catalog",
        "gate_2b_certify_polish",
        "gate_3a_optimize",
        "gate_3c_critique",
        "gate_4_agent",
        "gate_5_publish",
        "gate_6_embed",
        "gate_7_stream",
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
    [(i, GATES[i].id if i < len(GATES) else None) for i in range(len(GATES) + 1)],
)
def test_gate_progression_walk(satisfied_count: int, expected_current: str | None) -> None:
    """Completing each gate prefix advances current_gate strictly to the first incomplete gate."""
    state = _state_satisfying(*_GATE_IDS[:satisfied_count])
    current = current_gate(state)
    assert (current.id if current else None) == expected_current
    assert [g.id for g in completed_gates(state)] == _GATE_IDS[:satisfied_count]
    assert is_pipeline_complete(state) is (satisfied_count == len(GATES))

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
    """Skipping optional gates marks them complete so pipeline reaches is_pipeline_complete=True."""
    optional_gates = [g for g in GATES if g.sample_skip_state is not None]
    assert [g.id for g in optional_gates] == [
        "gate_1d_catalog",
        "gate_3a_optimize",
        "gate_4_agent",
        "gate_5_publish",
        "gate_6_embed",
        "gate_7_stream",
    ]

    skip_overrides: dict[str, Any] = {}
    for gate in optional_gates:
        assert gate.sample_skip_state is not None
        assert gate.is_complete(_unstarted_state(**gate.sample_skip_state)) is True
        skip_overrides.update(gate.sample_skip_state)

    # Also verify ca_agent_status='skipped' cascades to complete gate_5_publish even when ge_publish_status='pending'
    skip_overrides["ge_publish_status"] = "pending"
    skipped_state = _finished_state(**skip_overrides)
    assert is_pipeline_complete(skipped_state) is True
    assert current_gate(skipped_state) is None


def test_bq_only_mode_completes_at_gate_5_and_supports_upgrade(invoke, state_file) -> None:
    """In `bq_only` mode, satisfying Gates 0-5 auto-completes Gates 6-13 and advances to Gate 14 (`gate_7_stream`); upgrading to `full` resumes at Gate 6 (`gate_2a_lookml_model`)."""
    from looker_demo_cli.gates import attach_next_gate_action
    from looker_demo_cli.output import CommandResult

    state = FlowState(
        deployment_mode="bq_only",
        precheck_passed=True,
        targets_confirmed=True,
        gcp_project_id="p",
        schema_proposed=True,
        schema_approved=True,
        bq_dataset_id="ds",
        dataset_exists=True,
        catalog_status="seeded",
    )
    stream_gate = gate_by_id("gate_7_stream")
    lookml_gate = gate_by_id("gate_2a_lookml_model")

    # With stream_status="pending", Gates 0..stream_gate.number-1 are complete and gate_7_stream is current
    statuses_pending_stream = evaluate_gates(state)
    assert [s.gate.number for s in statuses_pending_stream if s.complete] == list(range(stream_gate.number))
    curr_stream = current_gate(state)
    assert curr_stream is not None
    assert curr_stream.number == stream_gate.number
    assert curr_stream.id == "gate_7_stream"
    next_after_catalog = attach_next_gate_action(CommandResult.success("catalog seed"), state).next_actions
    assert len(next_after_catalog) == 1
    assert next_after_catalog[0].gate == stream_gate.number
    assert next_after_catalog[0].command == "demo-create stream plan --gcp-project p --dataset ds"

    # Once Gate 14 is skipped (or deployed), all gates are complete
    state.stream_status = "skipped"
    statuses = evaluate_gates(state)
    assert all(s.complete for s in statuses)
    assert not any(s.is_current for s in statuses)
    assert is_pipeline_complete(state) is True
    assert current_gate(state) is None
    assert len(completed_gates(state)) == len(GATES)
    assert attach_next_gate_action(CommandResult.success("stream plan"), state).next_actions == []

    state_file(**state.model_dump())
    res_bq = envelope(invoke(["status", "--json"]))
    assert res_bq["data"]["is_complete"] is True
    assert res_bq["data"]["current_gate"] is None
    assert res_bq["next_actions"] == []
    assert len(res_bq["data"]["completed_gates"]) == len(GATES)

    # Upgrading the same state to "full" resumes at Gate 6 (gate_2a_lookml_model)
    state.deployment_mode = "full"
    statuses_full = evaluate_gates(state)
    assert is_pipeline_complete(state) is False
    curr = current_gate(state)
    assert curr is not None
    assert curr.number == lookml_gate.number
    assert curr.id == "gate_2a_lookml_model"
    assert [s.gate.number for s in statuses_full if s.is_current] == [lookml_gate.number]

    state_file(**state.model_dump())
    res_full = envelope(invoke(["status", "--json"]))
    assert res_full["data"]["is_complete"] is False
    assert res_full["data"]["current_gate"]["number"] == lookml_gate.number
    assert res_full["data"]["current_gate"]["id"] == "gate_2a_lookml_model"
    assert res_full["next_actions"][0]["gate"] == lookml_gate.number


def test_declarative_mode_applicability_and_command_registry(invoke, state_file) -> None:
    """Verify MODE_ACTIVE_GATES, GateStatus.status='not_applicable', _COMMAND_TO_GATE_ID derivation, and ParamBinding shell quoting."""
    assert MODE_ACTIVE_GATES["full"] == frozenset(_GATE_IDS)
    assert MODE_ACTIVE_GATES["bq_only"] == frozenset(
        {
            "gate_0a_precheck",
            "gate_0b_confirm_targets",
            "gate_1a_propose_schema",
            "gate_1b_approve_schema",
            "gate_1c_generate_data",
            "gate_1d_catalog",
            "gate_7_stream",
        }
    )

    bq_early = _unstarted_state(deployment_mode="bq_only", precheck_passed=True)
    statuses = evaluate_gates(bq_early)
    by_id = {s.gate.id: s for s in statuses}
    assert by_id["gate_0a_precheck"].status == "complete"
    assert by_id["gate_0b_confirm_targets"].status == "current"
    assert by_id["gate_1a_propose_schema"].status == "pending"
    assert by_id["gate_2a_lookml_model"].applicable is False
    assert by_id["gate_2a_lookml_model"].status == "not_applicable"
    assert by_id["gate_6_embed"].status == "not_applicable"
    assert by_id["gate_7_stream"].applicable is True
    assert by_id["gate_7_stream"].status == "pending"

    state_file(**bq_early.model_dump())
    human_res = invoke(["status"])
    assert human_res.exit_code == 0
    assert "not_applicable" in human_res.stderr

    # Verify _COMMAND_TO_GATE_ID includes both primary and register_only subcommands
    assert _COMMAND_TO_GATE_ID["data adopt"] == "gate_1c_generate_data"
    assert _COMMAND_TO_GATE_ID["catalog sync"] == "gate_1d_catalog"
    assert _COMMAND_TO_GATE_ID["ge publish"] == "gate_5_publish"
    assert set(_COMMAND_TO_GATE_ID) == {spec.subcommand for g in GATES for spec in g.command_specs}

    # Verify ParamBinding shell-quotes values containing spaces while keeping placeholders unquoted
    binding = ParamBinding("--schema-file", "schema_file_path", "<schema-file>")
    assert binding.render(_unstarted_state()) == "--schema-file <schema-file>"
    spaced_state = _unstarted_state(schema_file_path=Path("/scratch/my dir/schema.json"))
    rendered = binding.render(spaced_state)
    assert rendered == "--schema-file '/scratch/my dir/schema.json'"
    assert shlex.split(rendered) == ["--schema-file", "/scratch/my dir/schema.json"]


# ===========================================================================
# Gate command interpolation & branches
# ===========================================================================


def test_gate_command_branch_behaviors() -> None:
    """Verify Gate 1C generate/upload switch, Gate 1D seed/inspect switch, Gate 3B account flag, Gate 4 fct_ explore preference, Gate 7 plan/deploy switch, and blank handling."""
    # Gate 1C switches from generate to upload once Parquet exists
    gate_1c = gate_by_id("gate_1c_generate_data")
    assert gate_1c.command(_unstarted_state()).startswith("demo-create data generate ")
    with_parquet = _unstarted_state(generated_parquet_dir=Path("/scratch/retail"), bq_dataset_id="retail")
    assert gate_1c.command(with_parquet).startswith("demo-create data upload ")

    # Gate 1D switches from seed (synthetic) to inspect (existing)
    gate_1d = gate_by_id("gate_1d_catalog")
    assert gate_1d.command(_unstarted_state(data_source_mode="synthetic")).startswith("demo-create catalog seed ")
    assert gate_1d.command(_unstarted_state(data_source_mode="existing")).startswith("demo-create catalog inspect ")

    # Gate 3B omits --looker-account when None
    gate_3b = gate_by_id("gate_3b_deploy")
    assert "--looker-account" not in gate_3b.command(_finished_state(looker_account=None))

    # Gate 4 prefers primary_explore_name, then fct_ tables over dim_ tables
    gate_4 = gate_by_id("gate_4_agent")
    byo_state = _finished_state(
        primary_explore_name=None,
        generated_tables=[],
        existing_tables=["dim_carriers", "fct_shipments"],
    )
    assert _resolve_primary_explore(byo_state) == "fct_shipments"
    assert "--explore fct_shipments" in gate_4.command(byo_state)

    # Gate 7 switches from stream plan to stream deploy once stream_status == "planned"
    gate_7 = gate_by_id("gate_7_stream")
    assert gate_7.command(_unstarted_state(stream_status="pending")).startswith("demo-create stream plan ")
    assert gate_7.command(_unstarted_state(stream_status="planned")).startswith("demo-create stream deploy ")

    # Whitespace-only values render as placeholders
    gate_0a = gate_by_id("gate_0a_precheck")
    assert "--gcp-project <gcp-project>" in gate_0a.command(_unstarted_state(gcp_project_id="   "))


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
