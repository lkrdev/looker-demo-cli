"""Tests for the persisted state layer: versioning, atomicity, and loud failure."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from looker_demo_cli.errors import StateError
from looker_demo_cli.state import (
    STATE_FILE_NAME,
    STATE_SCHEMA_VERSION,
    FlowState,
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
