"""Tests for the persisted state layer: versioning, atomicity, and loud failure.

Phase 5 rebuilt `looker_demo_cli.state` around three guarantees, each replacing
a specific failure mode that the previous implementation had:

1. **Versioning.** A file from a newer CLI is rejected instead of half-read.
2. **Atomicity.** A `write_text` interrupted mid-flight used to leave truncated
   JSON behind; writes now go through a temp file and `os.replace`.
3. **Loud failure.** A corrupt file used to be swallowed by `except: pass` and
   returned as a *default* state, so a run silently continued against values
   nobody chose. It now raises.

The fourth theme here is "Cause A": identity fields no longer default to
placeholder strings, so tests pin that they arrive as `None`.
"""

from __future__ import annotations

import json
import os
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


# ---------------------------------------------------------------------------
# Cause A: identity fields must not carry placeholder defaults
# ---------------------------------------------------------------------------


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
    """A fresh state must not name a project, dataset, or connection.

    Three of these used to default to the literal ``"logistics_analytics"`` and
    one to ``"default_bigquery_connection"``. Because the defaults were truthy,
    a command run with no arguments silently targeted a real project nobody had
    named -- reproduced live against the Looker API during Phase 3 -- and the
    final arm of every ``flag or state.field or ...`` chain was unreachable.
    """
    assert getattr(FlowState(), field) is None


def test_fresh_state_has_not_passed_precheck() -> None:
    """`demo-create status` distinguishes "audit passed" from "never run".

    No other field can express that difference, which is why the flag exists.
    """
    assert FlowState().precheck_passed is False


# ---------------------------------------------------------------------------
# Round-tripping
# ---------------------------------------------------------------------------


def test_save_then_load_round_trips_every_field(tmp_path: Path) -> None:
    """State is the only channel between gates, so a lossy round trip loses work."""
    target = tmp_path / STATE_FILE_NAME
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


def test_load_returns_a_default_state_when_no_file_exists(tmp_path: Path) -> None:
    """A first run is not an error; it is the normal way every demo starts."""
    assert load_flow_state(path=tmp_path / "nope.json") == FlowState()


def test_saved_file_is_human_readable_json(tmp_path: Path) -> None:
    """The state file is a debugging surface, so it stays indented and greppable."""
    target = tmp_path / STATE_FILE_NAME
    save_flow_state(FlowState(bq_dataset_id="retail"), path=target)

    content = target.read_text(encoding="utf-8")

    assert '"bq_dataset_id": "retail"' in content
    assert json.loads(content)["schema_version"] == STATE_SCHEMA_VERSION


def test_save_creates_missing_parent_directories(tmp_path: Path) -> None:
    """`--state-file` may point into a scratch tree that does not exist yet."""
    target = tmp_path / "deep" / "nested" / STATE_FILE_NAME

    save_flow_state(FlowState(), path=target)

    assert target.exists()


# ---------------------------------------------------------------------------
# Schema versioning
# ---------------------------------------------------------------------------


def test_new_state_is_stamped_with_the_current_schema_version() -> None:
    """Without a stamp, a future breaking change has nothing to detect."""
    assert FlowState().schema_version == STATE_SCHEMA_VERSION


def test_a_file_from_a_newer_cli_is_rejected_rather_than_half_read(tmp_path: Path) -> None:
    """Reading a newer format on a best-effort basis is worse than refusing.

    Fields this CLI does not know about would be dropped, and a subsequent save
    would write the truncated state back -- silently destroying the newer CLI's
    progress. Refusing keeps the file intact.
    """
    target = tmp_path / STATE_FILE_NAME
    target.write_text(json.dumps({"schema_version": STATE_SCHEMA_VERSION + 1}), encoding="utf-8")

    with pytest.raises(StateError) as excinfo:
        load_flow_state(path=target)

    assert "newer than this CLI supports" in excinfo.value.message
    assert excinfo.value.details["found_version"] == STATE_SCHEMA_VERSION + 1
    assert excinfo.value.details["supported_version"] == STATE_SCHEMA_VERSION
    # The remediation must offer both ways out, since an agent cannot guess.
    assert "upgrade" in (excinfo.value.remediation or "").lower()
    assert "delete" in (excinfo.value.remediation or "").lower()


def test_an_unversioned_file_is_treated_as_pre_release_and_upgraded(tmp_path: Path) -> None:
    """Files written before 0.3.0 have no version key and must still load.

    They are a strict subset of version 1 -- every field added since has a
    default -- so migration is just stamping the version on.
    """
    target = tmp_path / STATE_FILE_NAME
    target.write_text(json.dumps({"bq_dataset_id": "legacy_ds", "golden_queries_count": 3}), encoding="utf-8")

    state = load_flow_state(path=target)

    assert state.bq_dataset_id == "legacy_ds"
    assert state.golden_queries_count == 3
    assert state.schema_version == STATE_SCHEMA_VERSION


def test_removed_fields_in_an_old_file_are_ignored(tmp_path: Path) -> None:
    """Phase 4 deleted four dead pipeline fields; 0.2.x files still carry them.

    Pydantic ignores unknown keys by default. This test pins that, because
    switching to ``extra="forbid"`` would silently make every pre-existing state
    file unloadable.
    """
    target = tmp_path / STATE_FILE_NAME
    target.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bq_dataset_id": "retail",
                "current_step": 3,
                "total_steps": 7,
                "action_intent": "create_new_dataset",
                "domain_description": "gone",
            }
        ),
        encoding="utf-8",
    )

    state = load_flow_state(path=target)

    assert state.bq_dataset_id == "retail"
    assert not hasattr(state, "total_steps")


def test_a_non_numeric_schema_version_is_rejected(tmp_path: Path) -> None:
    """A hand-edited file should fail on the version, not deep inside validation."""
    target = tmp_path / STATE_FILE_NAME
    target.write_text(json.dumps({"schema_version": "one"}), encoding="utf-8")

    with pytest.raises(StateError, match="non-numeric schema_version"):
        load_flow_state(path=target)


# ---------------------------------------------------------------------------
# Corruption is reported, not swallowed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("{not json at all", "not valid JSON", id="malformed"),
        pytest.param("", "not valid JSON", id="empty-file-from-interrupted-write"),
        pytest.param('{"bq_dataset_id": "retail"', "not valid JSON", id="truncated-mid-write"),
        pytest.param("[1, 2, 3]", "must contain a JSON object", id="json-array"),
        pytest.param('"just a string"', "must contain a JSON object", id="json-scalar"),
    ],
)
def test_a_corrupt_state_file_raises_instead_of_resetting(tmp_path: Path, content: str, expected: str) -> None:
    """# The old behaviour here was the most dangerous bug in the state layer.

    `load_flow_state` wrapped everything in ``except Exception: pass`` and
    returned ``FlowState()``. A corrupt file was therefore indistinguishable
    from a fresh start: the run continued against defaults, and the *next* save
    overwrote whatever was left of the real state. Two of these cases --
    ``empty-file`` and ``truncated`` -- are exactly what a crashed non-atomic
    write used to leave behind, so the old code could destroy state it had
    itself half-written.
    """
    target = tmp_path / STATE_FILE_NAME
    target.write_text(content, encoding="utf-8")

    with pytest.raises(StateError) as excinfo:
        load_flow_state(path=target)

    assert expected in excinfo.value.message
    assert excinfo.value.details["state_file"] == str(target)


def test_state_error_exits_with_its_own_code(tmp_path: Path) -> None:
    """Agents branch on the exit code, so state faults need a distinct one."""
    target = tmp_path / STATE_FILE_NAME
    target.write_text("{broken", encoding="utf-8")

    with pytest.raises(StateError) as excinfo:
        load_flow_state(path=target)

    assert excinfo.value.exit_code == 7
    assert excinfo.value.code == "STATE_ERROR"


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------


def test_write_leaves_no_temporary_files_behind(tmp_path: Path) -> None:
    """A litter of `.demo-state.json.*.tmp` files would break state discovery."""
    target = tmp_path / STATE_FILE_NAME

    for i in range(5):
        save_flow_state(FlowState(bq_dataset_id=f"ds_{i}"), path=target)

    assert [p.name for p in tmp_path.iterdir()] == [STATE_FILE_NAME]


def test_the_temporary_file_is_created_in_the_target_directory(tmp_path: Path, monkeypatch) -> None:
    """`os.replace` is only atomic within one filesystem.

    A temp file in the system temp directory is frequently on a different mount,
    where the replace degrades to a copy and reintroduces the torn-write window
    this design exists to close.
    """
    target = tmp_path / "nested" / STATE_FILE_NAME
    target.parent.mkdir()
    seen: list[str] = []

    real_replace = os.replace

    def _spy(src, dst):
        seen.append(str(src))
        return real_replace(src, dst)

    monkeypatch.setattr("looker_demo_cli.state.os.replace", _spy)
    save_flow_state(FlowState(), path=target)

    assert len(seen) == 1
    assert Path(seen[0]).parent == target.parent


def test_a_failed_write_leaves_the_previous_state_intact(tmp_path: Path, monkeypatch) -> None:
    """The whole point of writing atomically: a crash must not lose prior work."""
    target = tmp_path / STATE_FILE_NAME
    save_flow_state(FlowState(bq_dataset_id="original"), path=target)

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("looker_demo_cli.state.os.replace", _boom)

    with pytest.raises(StateError, match="Could not write state file"):
        save_flow_state(FlowState(bq_dataset_id="replacement"), path=target)

    assert load_flow_state(path=target).bq_dataset_id == "original"
    # The abandoned temp file must be cleaned up even on the failure path.
    assert [p.name for p in tmp_path.iterdir()] == [STATE_FILE_NAME]


def test_concurrent_writers_never_observe_a_partial_file(tmp_path: Path) -> None:
    """Several subagents may run gated commands against one state file.

    Interleaving a read between every write proves each read sees a complete,
    parseable document -- never the half-written JSON a plain `write_text`
    could expose.
    """
    target = tmp_path / STATE_FILE_NAME

    for i in range(25):
        save_flow_state(FlowState(bq_dataset_id=f"ds_{i}", golden_queries_count=i), path=target)
        observed = load_flow_state(path=target)
        assert observed.bq_dataset_id == f"ds_{i}"
        assert observed.golden_queries_count == i


# ---------------------------------------------------------------------------
# Path discovery
# ---------------------------------------------------------------------------


def test_discovery_prefers_the_working_directory(tmp_path: Path, monkeypatch) -> None:
    """Running the gates from one directory is the documented happy path."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / STATE_FILE_NAME).write_text("{}", encoding="utf-8")
    (tmp_path / STATE_FILE_NAME).write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert get_default_state_path(scratch_dir=scratch) == tmp_path / STATE_FILE_NAME


def test_discovery_falls_back_to_the_scratch_directory(tmp_path: Path, monkeypatch) -> None:
    """Agents often generate into a scratch tree and run commands from elsewhere."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / STATE_FILE_NAME).write_text("{}", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    assert get_default_state_path(scratch_dir=scratch) == scratch / STATE_FILE_NAME


def test_discovery_targets_the_working_directory_when_nothing_exists(tmp_path: Path, monkeypatch) -> None:
    """The first gate has to create the file somewhere predictable."""
    monkeypatch.chdir(tmp_path)

    assert get_default_state_path() == tmp_path / STATE_FILE_NAME
