"""Regression tests for Phase 1 CLI hardening and documentation/skill drift fixes."""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from typer.testing import CliRunner

try:
    import conftest as _conftest
    from conftest import FakeBigQueryHelper, FakeTable, FakeTableSchemaField, envelope
except ImportError:  # pragma: no cover
    import tests.conftest as _conftest
    from tests.conftest import FakeBigQueryHelper, FakeTable, FakeTableSchemaField, envelope

from looker_demo_cli.cli import app
from looker_demo_cli.error_boundary import ErrorHandlingGroup, make_typer
from looker_demo_cli.state import FlowState

pytestmark = [pytest.mark.unit]


def test_conftest_command_modules_discovers_stream() -> None:
    """`tests.conftest._COMMAND_MODULES` dynamically discovers `looker_demo_cli.commands.stream`."""
    assert "looker_demo_cli.commands.stream" in _conftest._COMMAND_MODULES


def test_error_boundary_make_typer_and_subgroup_guard() -> None:
    """`make_typer` binds `cls=ErrorHandlingGroup` and `ErrorHandlingGroup.add_command` rejects plain `click.Group`."""
    sub_typer = make_typer("sub", help="Subcommand group")
    assert sub_typer.info.cls is ErrorHandlingGroup

    group = ErrorHandlingGroup(name="root")
    with pytest.raises(TypeError, match="ErrorHandlingGroup"):
        group.add_command(click.Group("bad"))


def test_lookml_optimize_no_backup_attaches_next_gate_action(
    runner: CliRunner,
    sample_lookml_dir: Path,
    tmp_path: Path,
) -> None:
    """`lookml optimize --no-backup` still attaches the next gate action (`lookml deploy`)."""
    state_path = tmp_path / ".demo-state.json"
    state = FlowState(
        precheck_passed=True,
        targets_confirmed=True,
        gcp_account="user@example.com",
        gcp_project_id="my-gcp-project",
        looker_account="demo-looker",
        looker_connection_name="bq_conn",
        schema_proposed=True,
        schema_approved=True,
        approved_row_count=1000,
        bq_dataset_id="retail_demo",
        dataset_exists=True,
        catalog_status="skipped",
        looker_project_name="retail_demo_proj",
        lookml_output_dir=sample_lookml_dir,
        polish_certified=True,
    )
    state_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "lookml",
            "optimize",
            "--lookml-dir",
            str(sample_lookml_dir),
            "--no-backup",
            "--state-file",
            str(state_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = envelope(result)
    assert payload["status"] == "SUCCESS"
    assert payload["data"]["backup"] is False
    assert payload["next_actions"], "Expected non-empty next_actions when --no-backup is passed"
    assert "lookml deploy" in payload["next_actions"][0]["command"]


@pytest.mark.parametrize("subcommand", ["inspect", "seed", "sync"])
def test_catalog_skip_envelopes_include_skipped_true_and_state_file(
    runner: CliRunner,
    tmp_path: Path,
    subcommand: str,
) -> None:
    """`catalog {inspect,seed,sync} --skip --json` returns `skipped: True` and `state_file` in `data`."""
    state_path = tmp_path / f".demo-state-{subcommand}.json"
    state = FlowState(
        precheck_passed=True,
        targets_confirmed=True,
        gcp_project_id="my-gcp-project",
        bq_dataset_id="retail_demo",
        dataset_exists=True,
    )
    state_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "catalog",
            subcommand,
            "--skip",
            "--state-file",
            str(state_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = envelope(result)
    assert payload["status"] == "SUCCESS"
    assert payload["data"]["skipped"] is True
    assert "state_file" in payload["data"]
    assert Path(payload["data"]["state_file"]).resolve() == state_path.resolve()


def test_catalog_profiles_accepts_state_file(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    """`catalog profiles --state-file <state> --json` accepts `--state-file` and exits 0."""
    state_path = tmp_path / ".demo-state.json"
    state_path.write_text(FlowState().model_dump_json(indent=2), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "catalog",
            "profiles",
            "--state-file",
            str(state_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = envelope(result)
    assert payload["status"] == "SUCCESS"
    assert "profiles" in payload["data"]


def test_confirmed_state_gcp_project_not_clobbered_by_default_gcp_project(
    runner: CliRunner,
    fake_bigquery: type[FakeBigQueryHelper],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Confirmed `state.gcp_project_id` takes precedence over `DEFAULT_GCP_PROJECT` when `--gcp-project` is omitted."""
    import looker_demo_cli.commands.data as data_mod

    monkeypatch.setattr(data_mod, "DEFAULT_GCP_PROJECT", "ambient-env-proj")

    fake_bigquery.datasets["my_ds"] = ["orders"]
    fake_bigquery.tables["orders"] = FakeTable(
        "orders",
        columns=[FakeTableSchemaField("order_id", "STRING")],
        num_rows=10,
    )

    state_path = tmp_path / ".demo-state.json"
    state = FlowState(
        gcp_project_id="confirmed-state-proj",
        bq_dataset_id="my_ds",
        targets_confirmed=True,
    )
    state_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "data",
            "inspect",
            "--dataset",
            "my_ds",
            "--state-file",
            str(state_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = envelope(result)
    assert payload["data"]["project_id"] == "confirmed-state-proj"
    assert payload["data"]["project_id"] != "ambient-env-proj"
