"""Tests for the persisted state layer: versioning, atomicity, and loud failure."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from looker_demo_cli.errors import ConfigError, StateError
from looker_demo_cli.state import (
    STATE_FILE_NAME,
    STATE_SCHEMA_VERSION,
    FlowState,
    GateLifecycle,
    GateRecord,
    get_default_state_path,
    load_flow_state,
    save_flow_state,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "field",
    [
        "bq_dataset_id",
        "looker_project_name",
        "lookml_model_name",
        "looker_connection_name",
    ],
)
def test_identity_fields_default_to_none(field: str) -> None:
    """A fresh state must not name a project, dataset, or connection."""
    state = FlowState()
    assert getattr(state, field) is None
    assert state.precheck_passed is False
    assert state.schema_version == STATE_SCHEMA_VERSION


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    """State round-trips cleanly and creates missing parent directories."""
    assert load_flow_state(path=tmp_path / "nope.json") == FlowState()

    target = tmp_path / "nested" / STATE_FILE_NAME
    original = FlowState(
        bq_dataset_id="retail_raw",
        looker_project_name="retail_demo",
        lookml_model_name="retail_demo",
        looker_connection_name="my_bq",
        generated_tables=["fct_orders", "dim_users"],
        generated_parquet_dir=tmp_path / "parquet",
        precheck_passed=True,
        golden_queries_count=7,
    )
    save_flow_state(original, path=target)
    assert load_flow_state(path=target) == original
    assert [p.name for p in target.parent.iterdir()] == [STATE_FILE_NAME]


def test_schema_version_migration_and_rejection(tmp_path: Path) -> None:
    """Unversioned/legacy files upgrade cleanly; newer or invalid versions raise StateError."""
    target = tmp_path / STATE_FILE_NAME

    target.write_text(
        json.dumps({"bq_dataset_id": "legacy_ds", "current_step": 3, "golden_queries_count": 3}),
        encoding="utf-8",
    )
    upgraded = load_flow_state(path=target)
    assert upgraded.bq_dataset_id == "legacy_ds"
    assert upgraded.schema_version == STATE_SCHEMA_VERSION

    target.write_text(json.dumps({"schema_version": STATE_SCHEMA_VERSION + 1}), encoding="utf-8")
    with pytest.raises(StateError, match="newer than this CLI supports"):
        load_flow_state(path=target)

    target.write_text(json.dumps({"schema_version": "one"}), encoding="utf-8")
    with pytest.raises(StateError, match="non-numeric schema_version"):
        load_flow_state(path=target)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("{not json at all", "not valid JSON"),
        ("", "not valid JSON"),
        ("[1, 2, 3]", "must contain a JSON object"),
    ],
)
def test_corrupt_state_file_raises_state_error(tmp_path: Path, content: str, expected: str) -> None:
    """Corrupt state files raise StateError with exit code 7 instead of silently resetting."""
    target = tmp_path / STATE_FILE_NAME
    target.write_text(content, encoding="utf-8")

    with pytest.raises(StateError) as excinfo:
        load_flow_state(path=target)

    assert expected in excinfo.value.message
    assert excinfo.value.exit_code == 7


def test_failed_atomic_write_preserves_previous_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An interrupted os.replace leaves the existing state file intact and cleans up temp files."""
    target = tmp_path / STATE_FILE_NAME
    save_flow_state(FlowState(bq_dataset_id="original"), path=target)

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("looker_demo_cli.state.os.replace", _boom)

    with pytest.raises(StateError, match="Could not write state file"):
        save_flow_state(FlowState(bq_dataset_id="replacement"), path=target)

    assert load_flow_state(path=target).bq_dataset_id == "original"
    assert [p.name for p in tmp_path.iterdir()] == [STATE_FILE_NAME]


def test_state_path_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Prefers CWD, falls back to scratch_dir when present, and defaults to CWD when neither exists."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    assert get_default_state_path(scratch_dir=scratch) == work / STATE_FILE_NAME

    (scratch / STATE_FILE_NAME).write_text("{}", encoding="utf-8")
    assert get_default_state_path(scratch_dir=scratch) == scratch / STATE_FILE_NAME

    (work / STATE_FILE_NAME).write_text("{}", encoding="utf-8")
    assert get_default_state_path(scratch_dir=scratch) == work / STATE_FILE_NAME


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (GateLifecycle.COMPLETED, True),
        (GateLifecycle.SKIPPED, True),
        (GateLifecycle.PENDING, False),
        (GateLifecycle.IN_PROGRESS, False),
    ],
)
def test_gate_lifecycle_and_record_is_satisfied(status: GateLifecycle, expected: bool) -> None:
    """GateRecord.is_satisfied is True for COMPLETED/SKIPPED and False for PENDING/IN_PROGRESS."""
    record = GateRecord(status=status)
    assert record.is_satisfied is expected


def test_sync_gates_on_construction_and_round_trip(tmp_path: Path) -> None:
    """Flat fields automatically populate state.gates on construction and across save/load."""
    state = FlowState(
        precheck_passed=True,
        catalog_status="seeded",
        lookml_output_dir=Path("/tmp/l"),
        stream_status="planned",
    )

    assert state.gates["gate_0a_precheck"].status == GateLifecycle.COMPLETED
    assert state.gates["gate_1d_catalog"].status == GateLifecycle.COMPLETED
    assert state.gates["gate_1d_catalog"].substage == "seeded"
    assert state.gates["gate_2a_lookml_model"].status == GateLifecycle.COMPLETED
    assert state.gates["gate_7_stream"].status == GateLifecycle.IN_PROGRESS
    assert state.gates["gate_7_stream"].substage == "planned"
    assert state.is_gate_satisfied("gate_0a_precheck") is True
    assert state.is_gate_satisfied("gate_7_stream") is False
    assert state.gate_status("gate_7_stream") == GateLifecycle.IN_PROGRESS

    # Mutate flat field after construction and verify save_flow_state / load_flow_state syncs
    state.optimizer_status = "skipped"
    target = tmp_path / STATE_FILE_NAME
    save_flow_state(state, path=target)

    loaded = load_flow_state(path=target)
    assert loaded.gates["gate_3a_optimize"].status == GateLifecycle.SKIPPED
    assert loaded.is_gate_satisfied("gate_3a_optimize") is True
    assert loaded.gates["gate_1d_catalog"].substage == "seeded"
    assert loaded.gates["gate_2a_lookml_model"].status == GateLifecycle.COMPLETED
    assert loaded.gates["gate_7_stream"].status == GateLifecycle.IN_PROGRESS


def test_mark_gate_invalidates_downstream_without_deleting_deployed_dashboard_url(tmp_path: Path) -> None:
    """mark_gate sets downstream gates to PENDING and resets critique_approved without deleting deployed_dashboard_url."""
    from looker_demo_cli.gates import current_gate

    state = FlowState(
        precheck_passed=True,
        targets_confirmed=True,
        dataset_exists=True,
        catalog_status="skipped",
        lookml_output_dir=Path("/tmp/l"),
        polish_certified=True,
        optimizer_status="skipped",
        deployed_dashboard_url="https://looker.example.com/dashboards/42",
        critique_approved=True,
    )
    assert state.gate_status("gate_3b_deploy") == GateLifecycle.COMPLETED
    assert state.gate_status("gate_3c_critique") == GateLifecycle.COMPLETED

    state.mark_gate(
        "gate_2a_lookml_model",
        GateLifecycle.COMPLETED,
        substage="regenerated",
        invalidate_downstream=("gate_3b_deploy", "gate_3c_critique"),
    )

    assert state.gates["gate_2a_lookml_model"].status == GateLifecycle.COMPLETED
    assert state.gates["gate_2a_lookml_model"].substage == "regenerated"
    assert state.gates["gate_2a_lookml_model"].explicit is True

    assert state.gate_status("gate_3b_deploy") == GateLifecycle.PENDING
    assert state.gate_status("gate_3c_critique") == GateLifecycle.PENDING
    assert state.is_gate_satisfied("gate_3b_deploy") is False
    assert state.is_gate_satisfied("gate_3c_critique") is False
    assert state.critique_approved is False
    assert state.deployed_dashboard_url == "https://looker.example.com/dashboards/42"

    target = tmp_path / STATE_FILE_NAME
    save_flow_state(state, path=target)
    reloaded = load_flow_state(path=target)
    assert current_gate(reloaded).id == "gate_3b_deploy"  # type: ignore[union-attr]


def test_resolve_and_require() -> None:
    """state.resolve and state.require strip whitespace, respect fallback order, mutate write_to, and raise ConfigError."""
    state = FlowState(bq_dataset_id=None, domain_name="  logistics_domain  ")

    # Explicit value wins and strips whitespace
    assert state.resolve("  explicit_ds  ", "bq_dataset_id", "domain_name") == "explicit_ds"

    # Blank explicit falls back across state fields ("bq_dataset_id" -> "domain_name") and strips whitespace
    assert state.resolve("   ", "bq_dataset_id", "domain_name") == "logistics_domain"

    # First state field wins when populated
    state.bq_dataset_id = "  primary_ds  "
    assert state.resolve(None, "bq_dataset_id", "domain_name") == "primary_ds"

    # Default returned when all candidates are None or blank
    empty_state = FlowState(bq_dataset_id="   ", domain_name=None)
    assert empty_state.resolve("  ", "bq_dataset_id", "domain_name", default="fallback_ds") == "fallback_ds"

    # require() mutates write_to attribute with resolved value
    resolved = state.require(
        "  new_dataset  ",
        "bq_dataset_id",
        "domain_name",
        option="--dataset",
        purpose="target BigQuery dataset",
        write_to="bq_dataset_id",
    )
    assert resolved == "new_dataset"
    assert state.bq_dataset_id == "new_dataset"

    # require() raises ConfigError (missing_option) when all candidates are None or blank
    with pytest.raises(ConfigError) as excinfo:
        empty_state.require(
            "   ",
            "bq_dataset_id",
            "domain_name",
            option="--dataset",
            purpose="target BigQuery dataset",
            hint="Run data approve-schema first.",
        )
    assert excinfo.value.exit_code == 4
    assert "--dataset" in excinfo.value.message
    assert excinfo.value.details == {"option": "--dataset"}
    assert "Run data approve-schema first." in (excinfo.value.remediation or "")
