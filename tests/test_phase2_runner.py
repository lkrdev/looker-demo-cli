"""Unit tests for the shared command runner, skip handler, and ``@gated_command`` decorator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import pytest
import typer
from typer.testing import CliRunner

from looker_demo_cli.commands.options import StateFileOption
from looker_demo_cli.commands.runner import (
    GateOutcome,
    SkipSpec,
    check_required_gates,
    gated_command,
    handle_gate_skip,
    init_command_context,
)
from looker_demo_cli.context import AppContext
from looker_demo_cli.error_boundary import make_typer
from looker_demo_cli.errors import StateError
from looker_demo_cli.output import CommandResult, is_json_mode
from looker_demo_cli.state import FlowState, GateLifecycle, load_flow_state, save_flow_state
from looker_demo_cli.utils.console import print_success

pytestmark = pytest.mark.unit


def test_init_command_context_sets_state_file_and_json_mode(tmp_path: Path) -> None:
    """init_command_context binds the explicit state_file and configures json_mode."""
    custom_state = tmp_path / "custom-state.json"
    save_flow_state(FlowState(bq_dataset_id="ctx_dataset"), path=custom_state)

    app_ctx, state = init_command_context(None, state_file=custom_state, output_json=True)
    assert isinstance(app_ctx, AppContext)
    assert app_ctx.state_path == custom_state
    assert app_ctx.json_mode is True
    assert is_json_mode() is True
    assert state.bq_dataset_id == "ctx_dataset"


def test_handle_gate_skip_with_string_and_callable_message(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """handle_gate_skip applies state updates, marks gate SKIPPED, attaches next_actions, and renders string/callable messages."""
    state_path = tmp_path / "skip-str.json"
    save_flow_state(
        FlowState(
            precheck_passed=True,
            targets_confirmed=True,
            dataset_exists=True,
            bq_dataset_id="retail_ds",
            gcp_project_id="my-gcp-proj",
            looker_project_name="retail_lkr",
            looker_connection_name="bq_conn",
        ),
        path=state_path,
    )
    app_ctx = AppContext(state_path=state_path)

    spec_str = SkipSpec(
        state_updates={"catalog_status": "skipped"},
        extra_data={"reason": "user_declined"},
        warnings=["Catalog enrichment skipped"],
        gate_id="gate_1d_catalog",
        message="Skipped Knowledge Catalog seeding.",
    )
    result = handle_gate_skip(app_ctx, "catalog seed", spec_str, output_json=False)

    assert result.ok is True
    assert result.data["skipped"] is True
    assert result.data["catalog_status"] == "skipped"
    assert result.data["reason"] == "user_declined"
    assert result.data["state_file"] == str(state_path)
    assert result.warnings == ["Catalog enrichment skipped"]
    assert app_ctx.state.gates["gate_1d_catalog"].status == GateLifecycle.SKIPPED
    assert len(result.next_actions) == 1
    assert result.next_actions[0].gate == 6
    assert "demo-create lookml model" in (result.next_actions[0].command or "")

    captured = capsys.readouterr()
    assert "Skipped Knowledge Catalog seeding." in captured.err
    assert str(state_path) in captured.err

    # Test callable message rendering
    state_path_fn = tmp_path / "skip-fn.json"
    app_ctx_fn = AppContext(state_path=state_path_fn)
    app_ctx_fn.state.looker_project_name = "custom_proj"

    spec_fn = SkipSpec(
        state_updates={"optimizer_status": "skipped"},
        gate_id="gate_3a_optimize",
        message=lambda st, sp: f"Custom skip for {st.looker_project_name} at {sp.name}",
    )
    res_fn = handle_gate_skip(app_ctx_fn, "lookml optimize", spec_fn, output_json=False)
    assert res_fn.data["skipped"] is True
    assert res_fn.data["state_file"] == str(state_path_fn)
    assert app_ctx_fn.state.gates["gate_3a_optimize"].status == GateLifecycle.SKIPPED

    captured_fn = capsys.readouterr()
    assert f"Custom skip for custom_proj at {state_path_fn.name}" in captured_fn.err


def test_check_required_gates_enforces_when_tracked_and_allows_adhoc() -> None:
    """check_required_gates raises StateError with canonical remediation when precheck_passed=True, and allows ad-hoc runs when precheck_passed=False."""
    # Ad-hoc invocation (precheck_passed=False) is not blocked even when required gate is incomplete
    adhoc_state = FlowState(precheck_passed=False, polish_certified=False)
    check_required_gates(adhoc_state, ("gate_2b_certify_polish",))

    # Tracked pipeline (precheck_passed=True) raises StateError when prerequisite gate is incomplete
    tracked_state = FlowState(
        precheck_passed=True,
        lookml_output_dir=Path("/tmp/my_lookml"),
        polish_certified=False,
    )
    with pytest.raises(StateError) as excinfo:
        check_required_gates(tracked_state, ("gate_2b_certify_polish",))

    assert "gate_2b_certify_polish" in excinfo.value.message
    assert excinfo.value.remediation == "Run `demo-create lookml certify-polish --lookml-dir /tmp/my_lookml` first."

    # Satisfied prerequisite gate passes cleanly
    tracked_state.polish_certified = True
    check_required_gates(tracked_state, ("gate_2b_certify_polish",))


def test_gated_command_end_to_end_with_cli_runner(runner: CliRunner, tmp_path: Path) -> None:
    """@gated_command handles --skip, missing prerequisite StateError, and GateOutcome in both --json and human modes."""
    sub_app = make_typer("demo-sub", help="Test sub-application for @gated_command.")

    @gated_command(
        sub_app,
        "optimize",
        command_label="lookml optimize",
        gate_id="gate_3a_optimize",
        requires_gates=("gate_2b_certify_polish",),
        skip_spec=SkipSpec(
            state_updates={"optimizer_status": "skipped"},
            message="Skipped LookML optimization.",
        ),
    )
    def _optimize_cmd(
        ctx: typer.Context,
        skip: Annotated[bool, typer.Option("--skip")] = False,
        output_json: Annotated[bool, typer.Option("--json")] = False,
        state_file: StateFileOption = None,
    ) -> GateOutcome:
        def _render(res: CommandResult) -> None:
            print_success(f"Optimized LookML in {res.data['optimized_files']} files.")

        return GateOutcome(
            data={"optimized_files": 3},
            state_updates={"optimizer_status": "applied"},
            gate_substage="applied",
            invalidate_downstream=("gate_3b_deploy", "gate_3c_critique"),
            warnings=["Minor optimization warning"],
            render=_render,
        )

    @sub_app.command("noop")
    def _noop_cmd() -> None:
        """Keep sub_app as a multi-command group."""

    state_path = tmp_path / ".demo-state.json"

    # 1. Missing prerequisite gate when precheck_passed=True -> raises StateError (exit code 7)
    save_flow_state(
        FlowState(
            precheck_passed=True,
            lookml_output_dir=tmp_path / "lookml",
            polish_certified=False,
        ),
        path=state_path,
    )
    fail_res = runner.invoke(sub_app, ["optimize", "--state-file", str(state_path), "--json"])
    assert fail_res.exit_code == 7
    fail_env = json.loads(fail_res.stdout)
    assert fail_env["status"] == "FAILED"
    assert fail_env["errors"][0]["code"] == "STATE_ERROR"
    assert "gate_2b_certify_polish" in fail_env["errors"][0]["message"]

    # 2. Satisfy prerequisite and test --skip in --json mode
    save_flow_state(
        FlowState(
            precheck_passed=True,
            targets_confirmed=True,
            dataset_exists=True,
            catalog_status="skipped",
            lookml_output_dir=tmp_path / "lookml",
            looker_project_name="retail_demo",
            polish_certified=True,
        ),
        path=state_path,
    )
    skip_res = runner.invoke(sub_app, ["optimize", "--skip", "--state-file", str(state_path), "--json"])
    assert skip_res.exit_code == 0
    skip_env = json.loads(skip_res.stdout)
    assert skip_env["command"] == "lookml optimize"
    assert skip_env["status"] == "SUCCESS"
    assert skip_env["data"]["skipped"] is True
    assert skip_env["data"]["optimizer_status"] == "skipped"
    assert skip_env["data"]["state_file"] == str(state_path)
    assert skip_env["next_actions"][0]["gate"] == 9

    persisted_after_skip = load_flow_state(path=state_path)
    assert persisted_after_skip.optimizer_status == "skipped"
    assert persisted_after_skip.gates["gate_3a_optimize"].status == GateLifecycle.SKIPPED

    # 3. Normal GateOutcome execution in --json mode (also verifying downstream invalidation)
    save_flow_state(
        FlowState(
            precheck_passed=True,
            targets_confirmed=True,
            dataset_exists=True,
            catalog_status="skipped",
            lookml_output_dir=tmp_path / "lookml",
            looker_project_name="retail_demo",
            polish_certified=True,
            deployed_dashboard_url="https://looker.example.com/dashboards/1",
            critique_approved=True,
        ),
        path=state_path,
    )
    json_res = runner.invoke(sub_app, ["optimize", "--state-file", str(state_path), "--json"])
    assert json_res.exit_code == 0
    json_env = json.loads(json_res.stdout)
    assert json_env["command"] == "lookml optimize"
    assert json_env["status"] == "SUCCESS"
    assert json_env["data"]["optimized_files"] == 3
    assert json_env["data"]["state_file"] == str(state_path)
    assert json_env["warnings"] == ["Minor optimization warning"]
    assert json_env["next_actions"][0]["gate"] == 9

    persisted_after_json = load_flow_state(path=state_path)
    assert persisted_after_json.optimizer_status == "applied"
    assert persisted_after_json.gates["gate_3a_optimize"].status == GateLifecycle.COMPLETED
    assert persisted_after_json.gates["gate_3a_optimize"].substage == "applied"
    assert persisted_after_json.gates["gate_3b_deploy"].status == GateLifecycle.PENDING
    assert persisted_after_json.gates["gate_3c_critique"].status == GateLifecycle.PENDING
    assert persisted_after_json.critique_approved is False
    assert persisted_after_json.deployed_dashboard_url == "https://looker.example.com/dashboards/1"

    # 4. Normal GateOutcome execution in human mode
    human_res = runner.invoke(sub_app, ["optimize", "--state-file", str(state_path)])
    assert human_res.exit_code == 0
    combined_output = human_res.stdout + human_res.stderr
    assert "Optimized LookML in 3 files." in combined_output
    assert f"Updated state saved to `{state_path}`" in combined_output
