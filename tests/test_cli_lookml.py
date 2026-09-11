"""Tests for the ``demo-create lookml`` command group.

This group carried the most severe defect in the CLI: ``lookml deploy`` exited
``0`` after a failed ``lkr`` push, after LookML validator errors, and after
failing dashboard tile queries. An orchestrator chaining ``demo-create lookml
deploy && demo-create agent create`` proceeded as though production had been
updated. Phase 2 fixed that, along with two ``clean-root`` failures that were
also rendered as success.

What these tests assert
-----------------------

*The Phase 2 output contract*, not the pre-refactor behavior:

1. **stdout carries the JSON envelope and nothing else.** Rich output goes to
   stderr (``utils/console.py`` builds ``Console(stderr=True)``). Every
   ``--json`` assertion therefore parses :func:`envelope`, which reads
   ``result.stdout`` -- a successful ``json.loads`` is itself the assertion
   that no banner leaked onto the machine-readable stream. Human-readable
   assertions use ``result.output``, which mixes both streams in order.
2. **Every deliberate failure has a dedicated exit code** (see
   :mod:`looker_demo_cli.errors`). Tests assert ``SomeError.exit_code`` rather
   than a literal, so the allocation table stays the single source of truth.
3. **``PARTIAL`` and ``FAILED`` both exit non-zero.** A partial result means
   the remote is still in a bad state; it is not a success.
4. **Failure envelopes name the same dotted ``command`` a success would**, so
   a caller can key on it without first branching on ``status``.
5. **``emit()`` is either/or, not a tee.** Under ``--json`` the Rich renderer
   is never invoked, so the summary table is absent from *both* streams;
   without it, stdout stays empty.

Where behavior is still wrong it is pinned as-is and marked
``@pytest.mark.characterization`` with a ``# BUG:`` note. Tests asserting a
deliberate Phase 2 contract carry ``unit`` only.

Commands covered (see ``looker_demo_cli/commands/lookml.py``): ``lookml model``,
``lookml deploy``, ``lookml optimize``, ``lookml restore``,
``lookml clean-root``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import responses

from looker_demo_cli.errors import (
    USAGE_EXIT_CODE,
    AuthError,
    ConfigError,
    RemoteApiError,
    StateError,
    ValidationError,
    no_looker_instance,
)
from looker_demo_cli.output import ENVELOPE_SCHEMA_VERSION

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def envelope(result: Any) -> dict[str, Any]:
    """Parse the JSON envelope a ``--json`` invocation wrote to stdout.

    Deliberately parses ``result.stdout`` and not ``result.output``: the
    Phase 2 contract is that stdout carries the envelope *and nothing else*.
    Before the split, ``json.loads`` failed on most commands because a Rich
    banner was printed ahead of the payload, so a bare parse succeeding is a
    real assertion, not a convenience.
    """
    return json.loads(result.stdout)


def read_state(cwd: Path) -> dict[str, Any]:
    """Read the ``.demo-state.json`` a command persisted into ``cwd``."""
    from looker_demo_cli.state import STATE_FILE_NAME

    path = cwd / STATE_FILE_NAME
    assert path.exists(), f"No {STATE_FILE_NAME} was written to {cwd}"
    return json.loads(path.read_text(encoding="utf-8"))


def lkml_tree(root: Path) -> set[str]:
    """POSIX-relative paths of every generated LookML artifact under ``root``."""
    return {
        str(p.relative_to(root).as_posix())
        for p in root.rglob("*")
        if p.is_file() and (p.name.endswith(".lkml") or p.name.endswith(".lookml"))
    }


@pytest.fixture
def spec_factory():
    """Build ``LookMLTableSpec`` objects without importing at module scope.

    NOTE: candidate for ``conftest.py`` -- ``lookml model`` is not the only
    command that needs a canned table spec to stub BigQuery introspection.
    """
    from looker_demo_cli.generators.lookml_generator import LookMLTableSpec

    def _make(table_name: str, **overrides: Any) -> LookMLTableSpec:
        payload: dict[str, Any] = {
            "table_name": table_name,
            "table_type": "dimension",
            "schema_fields": {f"{table_name}_id": "INT64", "name": "STRING"},
            "primary_key": f"{table_name}_id",
            "foreign_keys": {},
        }
        payload.update(overrides)
        return LookMLTableSpec(**payload)

    return _make


@pytest.fixture
def stub_introspection(patch_cli):
    """Replace ``introspect_bq_table_specs`` and record its keyword args.

    Keeps ``lookml model --dataset`` hermetic: the real function talks to both
    BigQuery and the Knowledge Catalog.

    NOTE: candidate for ``conftest.py``.
    """
    calls: list[dict[str, Any]] = []

    def _install(return_value: list[Any]):
        def _fake(**kwargs: Any) -> list[Any]:
            calls.append(kwargs)
            return list(return_value)

        patch_cli("introspect_bq_table_specs", _fake)
        return calls

    _install.calls = calls  # type: ignore[attr-defined]
    return _install


@pytest.fixture
def stub_deploy_step(patch_cli):
    """Replace ``deploy_lookml_project`` with a recording double.

    The returned ``state.status`` is the single signal ``lookml deploy`` now
    branches on, so shaping it is how a test chooses between the success and
    failure contracts.

    NOTE: candidate for ``conftest.py`` -- ``lookml deploy`` and ``run`` both
    funnel through this single seam.
    """
    seen: dict[str, Any] = {}

    def _install(**mutations: Any) -> dict[str, Any]:
        def _fake(state):
            seen["state_in"] = state.model_copy(deep=True)
            for key, value in mutations.items():
                setattr(state, key, value)
            return state

        patch_cli("deploy_lookml_project", _fake)
        return seen

    return _install


@pytest.fixture
def stub_oauth_instances(monkeypatch: pytest.MonkeyPatch):
    """Point the deploy step's OAuth lookup at a single fake instance.

    Returns the base URL the fake instance reports, which the caller needs in
    order to register matching HTTP mocks.
    """
    base_url = "https://fake.cloud.looker.com"
    monkeypatch.setattr(
        "looker_demo_cli.services.deploy_service.get_authenticated_oauth_instances",
        lambda: [
            {
                "instance_name": "demo-instance",
                "base_url": base_url,
                "is_current": True,
                "access_token": "fake-token",
            }
        ],
    )
    return base_url


# ===========================================================================
# lookml optimize
# ===========================================================================


def test_optimize_happy_path_patches_and_snapshots(invoke, sample_lookml_dir: Path):
    """Optimizing must be reversible, so a snapshot is part of the happy path.

    Without ``.backup_pre_opt`` there is no rollback in a headless, non-git
    environment, which is the only environment this CLI is designed for.
    """
    result = invoke(["lookml", "optimize", "--lookml-dir", str(sample_lookml_dir)])

    assert result.exit_code == 0, result.output
    assert (sample_lookml_dir / ".backup_pre_opt").is_dir()
    assert "Optimization Summary" in result.output
    assert "suggestable: no" in (sample_lookml_dir / "views" / "users.view.lkml").read_text(encoding="utf-8")


def test_optimize_json_emits_the_envelope_with_the_report_merged(invoke, sample_lookml_dir: Path):
    """The optimizer report is nested inside the envelope, not emitted bare.

    Pinning the exact key set matters because callers previously consumed the
    raw service dict; the merge must not drop or rename any of its fields
    while adding the envelope's own ``lookml_dir``/``backup`` context.
    """
    result = invoke(["lookml", "optimize", "--lookml-dir", str(sample_lookml_dir), "--json"])

    assert result.exit_code == 0, result.output
    payload = envelope(result)

    assert payload["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert payload["command"] == "lookml optimize"
    assert payload["status"] == "SUCCESS"
    assert payload["errors"] == []

    data = payload["data"]
    assert set(data) == {
        "lookml_dir",
        "backup",
        "status",
        "optimizations_applied",
        "files_patched",
        "backup_created",
        "backup_dir",
        "error",
    }
    assert data["lookml_dir"] == str(sample_lookml_dir)
    assert data["backup"] is True
    assert data["backup_created"] is True
    assert data["backup_dir"] == str(sample_lookml_dir / ".backup_pre_opt")
    assert data["error"] is None
    assert set(data["optimizations_applied"]) == {
        "static_suggestions_added",
        "suggestable_disabled_count",
        "datagroup_caching_configured",
        "partition_pruning_filters_added",
        "foreign_keys_hidden",
        "primary_keys_asserted",
    }
    # Paths are reported relative to --lookml-dir.
    assert sorted(data["files_patched"]) == ["models/demo.model.lkml", "views/users.view.lkml"]

    # `emit()` is either/or, not a tee: under --json the human renderer is
    # never invoked, so the Rich summary appears on neither stream. Only the
    # progress narration printed before `emit()` reaches stderr.
    assert "Optimization Summary" not in result.stdout
    assert "Optimization Summary" not in result.output
    assert "Auditing and optimizing LookML files" in result.output


def test_optimize_renders_the_summary_table_only_without_json(invoke, sample_lookml_dir: Path):
    """The Rich summary is the human's only view of what changed.

    Paired with the assertions above, this pins ``emit()``'s either/or
    contract from both sides: exactly one of the two renderings happens, so a
    machine caller never has to skip past a table and a human is never left
    with raw JSON.
    """
    result = invoke(["lookml", "optimize", "--lookml-dir", str(sample_lookml_dir)])

    assert result.exit_code == 0, result.output
    assert "Optimization Summary" in result.output
    # Nothing at all is written to the machine-readable stream.
    assert result.stdout == ""


def test_optimize_next_action_tells_the_caller_how_to_roll_back(invoke, sample_lookml_dir: Path):
    """``next_actions`` is how an orchestrator learns rollback is available.

    The suggestion must be the literal command, because the agent runs it
    verbatim rather than reconstructing it from prose.
    """
    result = invoke(["lookml", "optimize", "--lookml-dir", str(sample_lookml_dir), "--json"])

    assert result.exit_code == 0, result.output
    actions = envelope(result)["next_actions"]
    assert len(actions) == 1
    assert actions[0]["command"] == f"demo-create lookml restore --lookml-dir {sample_lookml_dir}"
    assert actions[0]["requires_human_confirmation"] is False


def test_optimize_no_backup_suggests_no_rollback(invoke, sample_lookml_dir: Path):
    """With no snapshot there is nothing to restore, so nothing is suggested.

    Advertising a rollback command that would fail is worse than staying
    silent: the orchestrator would burn a turn discovering it.
    """
    result = invoke(["lookml", "optimize", "--lookml-dir", str(sample_lookml_dir), "--no-backup", "--json"])

    assert result.exit_code == 0, result.output
    payload = envelope(result)
    assert payload["data"]["backup"] is False
    assert payload["data"]["backup_created"] is False
    assert payload["data"]["backup_dir"] is None
    assert payload["next_actions"] == []
    assert not (sample_lookml_dir / ".backup_pre_opt").exists()


def test_optimize_is_idempotent_on_second_run(invoke, sample_lookml_dir: Path):
    """Re-running must be safe: the agent cannot always tell if it already ran."""
    first = invoke(["lookml", "optimize", "--lookml-dir", str(sample_lookml_dir), "--json"])
    assert first.exit_code == 0, first.output

    second = invoke(["lookml", "optimize", "--lookml-dir", str(sample_lookml_dir), "--json"])
    assert second.exit_code == 0, second.output
    data = envelope(second)["data"]
    assert data["files_patched"] == []
    assert data["optimizations_applied"]["datagroup_caching_configured"] is False


def test_optimize_missing_dir_is_a_config_error(invoke, tmp_path: Path):
    """A bad ``--lookml-dir`` is a configuration fault, distinct from exit 1.

    Exit 4 tells the caller the arguments parsed but cannot be acted on, which
    is actionable without reading the message.
    """
    missing = tmp_path / "nope"
    result = invoke(["lookml", "optimize", "--lookml-dir", str(missing)])

    assert result.exit_code == ConfigError.exit_code
    assert "does not exist" in result.output


def test_optimize_missing_dir_honours_the_json_flag(invoke, tmp_path: Path):
    """The optimize error path now respects ``--json``.

    # FIXED: this path used to raise before the ``json_output`` branch was
    # reached, so a caller that passed ``--json`` got unparseable Rich text on
    # failure -- precisely when it most needed structure. ``lookml restore``
    # handled the identical condition correctly, so this was an inconsistency
    # rather than a design. The single error handler in ``ErrorHandlingGroup``
    # now renders any ``DemoCreateError`` in whichever format the command was
    # invoked with.
    """
    missing = tmp_path / "nope"
    result = invoke(["lookml", "optimize", "--lookml-dir", str(missing), "--json"])

    assert result.exit_code == ConfigError.exit_code
    payload = envelope(result)
    # A failure envelope names the same dotted command a success would, so a
    # caller can key on `command` without branching on `status` first.
    assert payload["command"] == "lookml optimize"
    assert payload["status"] == "FAILED"
    assert payload["errors"][0]["code"] == "CONFIG_ERROR"
    assert payload["errors"][0]["message"] == f"LookML directory `{missing}` does not exist."
    assert payload["errors"][0]["remediation"]
    assert payload["errors"][0]["details"] == {"lookml_dir": str(missing)}


def test_optimize_falls_back_to_state_lookml_output_dir(invoke, sample_lookml_dir: Path, state_file):
    """Prior pipeline state supplies the directory so gates can chain flagless."""
    state_file(lookml_output_dir=str(sample_lookml_dir))

    result = invoke(["lookml", "optimize", "--json"])

    assert result.exit_code == 0, result.output
    assert envelope(result)["data"]["lookml_dir"] == str(sample_lookml_dir)


def test_optimize_defaults_to_relative_lookml_dir(invoke, isolated_cwd: Path):
    """With neither flag nor state the target is the relative path ``lookml``.

    Naming the resolved directory in the error is what stops a user from
    debugging the wrong path.
    """
    result = invoke(["lookml", "optimize"])

    assert result.exit_code == ConfigError.exit_code
    assert "`lookml`" in result.output


def test_optimize_does_not_write_state(invoke, sample_lookml_dir: Path, isolated_cwd: Path):
    """``optimize`` reads state but must not advance the pipeline record."""
    from looker_demo_cli.state import STATE_FILE_NAME

    result = invoke(["lookml", "optimize", "--lookml-dir", str(sample_lookml_dir), "--json"])

    assert result.exit_code == 0, result.output
    assert not (isolated_cwd / STATE_FILE_NAME).exists()


# ===========================================================================
# lookml restore  (+ optimize/restore round trip)
# ===========================================================================


def test_optimize_then_restore_round_trip_is_byte_identical(invoke, sample_lookml_dir: Path):
    """The advertised rollback contract: optimize -> restore leaves no trace.

    Anything less than byte identity means a rejected optimization silently
    ships hand-mangled LookML.
    """
    view = sample_lookml_dir / "views" / "users.view.lkml"
    model = sample_lookml_dir / "models" / "demo.model.lkml"
    view_before = view.read_bytes()
    model_before = model.read_bytes()

    opt = invoke(["lookml", "optimize", "--lookml-dir", str(sample_lookml_dir), "--json"])
    assert opt.exit_code == 0, opt.output
    assert (sample_lookml_dir / ".backup_pre_opt").is_dir()
    assert view.read_bytes() != view_before
    assert model.read_bytes() != model_before

    restore = invoke(["lookml", "restore", "--lookml-dir", str(sample_lookml_dir), "--json"])
    assert restore.exit_code == 0, restore.output

    # The snapshot is consumed, so a second restore has nothing to roll back to.
    assert not (sample_lookml_dir / ".backup_pre_opt").exists()
    assert view.read_bytes() == view_before
    assert model.read_bytes() == model_before


def test_restore_json_emits_the_envelope_with_the_report_merged(invoke, sample_lookml_dir: Path):
    """``restore --json`` nests the service report under ``data``, like optimize.

    The two commands are used back to back, so a caller should not have to
    switch parsing strategies between them.
    """
    invoke(["lookml", "optimize", "--lookml-dir", str(sample_lookml_dir), "--json"])

    result = invoke(["lookml", "restore", "--lookml-dir", str(sample_lookml_dir), "--json"])

    assert result.exit_code == 0, result.output
    payload = envelope(result)
    assert payload["command"] == "lookml restore"
    assert payload["status"] == "SUCCESS"
    data = payload["data"]
    assert set(data) == {"status", "lookml_dir", "error", "files_restored"}
    assert data["error"] is None
    assert data["lookml_dir"] == str(sample_lookml_dir)
    assert sorted(data["files_restored"]) == ["models/demo.model.lkml", "views/users.view.lkml"]


def test_restore_text_path_lists_restored_files(invoke, sample_lookml_dir: Path):
    """A human needs the per-file list to confirm the rollback covered everything."""
    invoke(["lookml", "optimize", "--lookml-dir", str(sample_lookml_dir)])

    result = invoke(["lookml", "restore", "--lookml-dir", str(sample_lookml_dir)])

    assert result.exit_code == 0, result.output
    assert "Successfully restored 2 LookML file(s)" in result.output
    assert "views/users.view.lkml" in result.output


def test_restore_without_snapshot_is_a_state_error(invoke, sample_lookml_dir: Path):
    """A missing snapshot is unusable persisted state, not a config mistake.

    Exit 7 distinguishes "you never ran optimize" from "you passed a bad
    path" (exit 4), which are resolved by different follow-up commands.
    """
    result = invoke(["lookml", "restore", "--lookml-dir", str(sample_lookml_dir), "--json"])

    assert result.exit_code == StateError.exit_code
    payload = envelope(result)
    assert payload["command"] == "lookml restore"
    assert payload["status"] == "FAILED"
    assert payload["errors"][0]["code"] == "STATE_ERROR"
    assert "No pre-optimization backup found" in payload["errors"][0]["message"]
    assert ".backup_pre_opt" in payload["errors"][0]["message"]
    assert payload["errors"][0]["details"]["lookml_dir"] == str(sample_lookml_dir)


def test_restore_without_snapshot_text(invoke, sample_lookml_dir: Path):
    """The same failure reads identically for a human, from one message source."""
    result = invoke(["lookml", "restore", "--lookml-dir", str(sample_lookml_dir)])

    assert result.exit_code == StateError.exit_code
    assert "No pre-optimization backup found" in result.output


def test_restore_missing_dir_is_a_config_error(invoke, tmp_path: Path):
    """A non-existent target is caught before the service is consulted.

    This is a config fault, so it must not be conflated with the state error
    raised when the directory exists but holds no snapshot.
    """
    missing = tmp_path / "nope"
    result = invoke(["lookml", "restore", "--lookml-dir", str(missing), "--json"])

    assert result.exit_code == ConfigError.exit_code
    payload = envelope(result)
    # Same message as `optimize`'s guard, but the envelope disambiguates which
    # command produced it.
    assert payload["command"] == "lookml restore"
    assert payload["errors"][0]["code"] == "CONFIG_ERROR"
    assert payload["errors"][0]["message"] == f"LookML directory `{missing}` does not exist."
    assert payload["errors"][0]["details"] == {"lookml_dir": str(missing)}


def test_restore_missing_dir_text(invoke, tmp_path: Path):
    """The human path exits with the same code as the JSON path."""
    result = invoke(["lookml", "restore", "--lookml-dir", str(tmp_path / "nope")])

    assert result.exit_code == ConfigError.exit_code
    assert "does not exist" in result.output


def test_restore_falls_back_to_state_lookml_output_dir(invoke, sample_lookml_dir: Path, state_file):
    """Restore resolves its target the same way optimize does, from state."""
    state_file(lookml_output_dir=str(sample_lookml_dir))
    invoke(["lookml", "optimize", "--json"])

    result = invoke(["lookml", "restore", "--json"])

    assert result.exit_code == 0, result.output
    assert envelope(result)["data"]["lookml_dir"] == str(sample_lookml_dir)


def test_restore_defaults_to_relative_lookml_dir(invoke, isolated_cwd: Path):
    """The relative default is named in the error so the miss is diagnosable."""
    result = invoke(["lookml", "restore", "--json"])

    assert result.exit_code == ConfigError.exit_code
    payload = envelope(result)
    assert payload["command"] == "lookml restore"
    assert payload["errors"][0]["message"] == "LookML directory `lookml` does not exist."


@pytest.mark.characterization
def test_restore_ignores_non_lkml_files_in_snapshot(invoke, sample_lookml_dir: Path):
    """Only ``*.lkml`` round-trips; ``*.dashboard.lookml`` is never snapshotted.

    # BUG: optimizer_service snapshots and restores ``rglob("*.lkml")``, so
    # dashboards -- which the generator writes as ``*.dashboard.lookml`` --
    # sit outside the rollback boundary. ``demo-create lookml restore``
    # therefore reports SUCCESS while silently leaving dashboard edits in
    # place. Not addressed by Phase 2, which changed the error contract
    # rather than the snapshot glob.
    """
    dash_dir = sample_lookml_dir / "dashboards"
    dash_dir.mkdir()
    dash = dash_dir / "demo_overview.dashboard.lookml"
    dash.write_text("- dashboard: demo_overview\n", encoding="utf-8")

    invoke(["lookml", "optimize", "--lookml-dir", str(sample_lookml_dir), "--json"])
    assert not (sample_lookml_dir / ".backup_pre_opt" / "dashboards").exists()

    dash.write_text("- dashboard: EDITED\n", encoding="utf-8")
    result = invoke(["lookml", "restore", "--lookml-dir", str(sample_lookml_dir), "--json"])

    assert result.exit_code == 0, result.output
    assert dash.read_text(encoding="utf-8") == "- dashboard: EDITED\n"


# ===========================================================================
# lookml model
# ===========================================================================


def test_model_from_parquet_writes_expected_tree(invoke, sample_parquet_dir: Path, tmp_path: Path):
    """One view per Parquet file plus a model and dashboard is the whole deliverable."""
    out = tmp_path / "generated"

    result = invoke(
        [
            "lookml",
            "model",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(out),
            "--looker-project",
            "retail_demo",
            "--gcp-project",
            "unit-test-project",
            "--connection",
            "default_bigquery_connection",
        ]
    )

    assert result.exit_code == 0, result.output
    assert lkml_tree(out) == {
        "views/dim_products.view.lkml",
        "views/dim_users.view.lkml",
        "views/fct_orders.view.lkml",
        "models/retail_demo.model.lkml",
        "dashboards/retail_demo_overview.dashboard.lookml",
    }
    assert "Generated 5 LookML files" in result.output


def test_model_json_envelope_shape(invoke, sample_parquet_dir: Path, tmp_path: Path, isolated_cwd: Path):
    """``lookml model`` gained ``--json`` in Phase 2; it previously had none.

    The payload has to name every identifier a downstream gate needs --
    project, model, dataset, connection, output dir -- or the orchestrator is
    forced back to scraping ``.demo-state.json``.
    """
    from looker_demo_cli.state import STATE_FILE_NAME

    out = tmp_path / "generated"

    result = invoke(
        [
            "lookml",
            "model",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(out),
            "--looker-project",
            "retail_demo",
            "--dataset",
            "retail_raw",
            "--gcp-project",
            "unit-test-project",
            "--connection",
            "default_bigquery_connection",
            "--json",
        ]
    )

    assert result.exit_code == 0, result.output
    payload = envelope(result)
    assert payload["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert payload["command"] == "lookml model"
    assert payload["status"] == "SUCCESS"
    assert payload["errors"] == []

    data = payload["data"]
    assert set(data) == {
        "looker_project",
        "model",
        "dataset",
        "gcp_project",
        "connection",
        "source",
        "output_dir",
        "tables",
        "files",
        "state_file",
    }
    assert data["looker_project"] == "retail_demo"
    assert data["model"] == "retail_demo"
    assert data["dataset"] == "retail_raw"
    assert data["gcp_project"] == "unit-test-project"
    assert data["connection"] == "default_bigquery_connection"
    assert data["source"] == "parquet"
    assert data["output_dir"] == str(out)
    assert sorted(data["tables"]) == ["dim_products", "dim_users", "fct_orders"]
    assert set(data["files"]) == lkml_tree(out)
    assert data["state_file"] == str(isolated_cwd / STATE_FILE_NAME)


def test_model_json_next_action_gates_the_deploy(invoke, sample_parquet_dir: Path, tmp_path: Path):
    """Deploying is a Gate 3 decision, so the suggestion must demand a human.

    ``requires_human_confirmation`` is the only thing stopping an autonomous
    orchestrator from pushing freshly generated LookML to production.
    """
    out = tmp_path / "generated"

    result = invoke(
        [
            "lookml",
            "model",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(out),
            "--looker-project",
            "retail_demo",
            "--dataset",
            "retail_raw",
            "--connection",
            "default_bigquery_connection",
            "--json",
        ]
    )

    assert result.exit_code == 0, result.output
    actions = envelope(result)["next_actions"]
    assert len(actions) == 1
    assert actions[0]["command"] == f"demo-create lookml deploy --looker-project retail_demo --lookml-dir {out}"
    assert actions[0]["gate"] == 3
    assert actions[0]["requires_human_confirmation"] is True


def test_model_warns_when_the_dataset_is_derived_rather_than_given(invoke, sample_parquet_dir: Path, tmp_path: Path):
    """Omitting ``--dataset`` must warn, because the fallback corrupts the output.

    ``ds_name`` is baked into every generated ``sql_table_name``, so a silent
    fallback does not merely mis-record state -- it produces views pointing at
    a different warehouse table, and every Explore fails at query time. The
    warning names the exact dataset that was assumed, which is now the Looker
    project name; it used to name the hardcoded ``logistics_analytics``.
    """
    result = invoke(
        [
            "lookml",
            "model",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(tmp_path / "generated"),
            "--looker-project",
            "retail_demo",
            "--gcp-project",
            "unit-test-project",
            "--connection",
            "default_bigquery_connection",
            "--json",
        ]
    )

    assert result.exit_code == 0, result.output
    warnings = envelope(result)["warnings"]
    assert len(warnings) == 1
    assert "unit-test-project.retail_demo" in warnings[0]
    assert "Pass --dataset explicitly" in warnings[0]


def test_model_dataset_warning_is_also_shown_to_humans(invoke, sample_parquet_dir: Path, tmp_path: Path):
    """The warning is worthless if only machine callers ever see it."""
    result = invoke(
        [
            "lookml",
            "model",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(tmp_path / "generated"),
            "--looker-project",
            "retail_demo",
            "--gcp-project",
            "unit-test-project",
            "--connection",
            "default_bigquery_connection",
        ]
    )

    assert result.exit_code == 0, result.output
    assert "No --dataset given" in result.output


def test_model_with_explicit_dataset_emits_no_warning(invoke, sample_parquet_dir: Path, tmp_path: Path):
    """An explicit ``--dataset`` is unambiguous, so warning would be noise.

    Warnings that fire on the correct usage get filtered out by callers, which
    is how the important one stops being read.
    """
    result = invoke(
        [
            "lookml",
            "model",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(tmp_path / "generated"),
            "--looker-project",
            "retail_demo",
            "--dataset",
            "retail_raw",
            "--connection",
            "default_bigquery_connection",
            "--json",
        ]
    )

    assert result.exit_code == 0, result.output
    assert envelope(result)["warnings"] == []


def test_model_dataset_fallback_targets_the_project_name(
    invoke, sample_parquet_dir: Path, tmp_path: Path, isolated_cwd: Path
):
    """With no ``--dataset`` and no prior state, views bind to the project name.

    ``ds_name = dataset or state.bq_dataset_id or proj_name`` always read as if
    it degraded to the project name, but ``FlowState.bq_dataset_id`` used to
    default to a non-empty ``"logistics_analytics"``, so the last arm was dead
    code and every generated ``sql_table_name`` pointed at an unrelated
    dataset -- LookML that validates and deploys but cannot answer a query.
    ``bq_dataset_id`` now defaults to ``None``, which is what makes the final
    arm reachable. This asserts on the view text and not just on state, because
    the dataset is baked into every ``sql_table_name`` and that is where the old
    behaviour actually did its damage.
    """
    out = tmp_path / "generated"

    result = invoke(
        [
            "lookml",
            "model",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(out),
            "--looker-project",
            "retail_demo",
            "--gcp-project",
            "unit-test-project",
            "--connection",
            "default_bigquery_connection",
        ]
    )
    assert result.exit_code == 0, result.output

    state = read_state(isolated_cwd)
    assert state["bq_dataset_id"] == "retail_demo"
    view_text = (out / "views" / "dim_users.view.lkml").read_text(encoding="utf-8")
    assert "retail_demo.dim_users" in view_text
    assert "logistics_analytics" not in view_text


def test_model_from_parquet_persists_state(invoke, sample_parquet_dir: Path, tmp_path: Path, isolated_cwd: Path):
    """Every generated identifier must survive into the next gate's invocation."""
    out = tmp_path / "generated"

    result = invoke(
        [
            "lookml",
            "model",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(out),
            "--looker-project",
            "retail_demo",
            "--dataset",
            "retail_raw",
            "--gcp-project",
            "unit-test-project",
            "--connection",
            "default_bigquery_connection",
        ]
    )
    assert result.exit_code == 0, result.output

    state = read_state(isolated_cwd)
    assert state["looker_project_name"] == "retail_demo"
    assert state["lookml_model_name"] == "retail_demo"
    assert state["bq_dataset_id"] == "retail_raw"
    assert state["lookml_output_dir"] == str(out)
    assert state["gcp_project_id"] == "unit-test-project"
    assert sorted(state["existing_tables"]) == ["dim_products", "dim_users", "fct_orders"]
    assert state["looker_connection_name"] == "default_bigquery_connection"


def test_model_tables_filter_limits_generated_views(invoke, sample_parquet_dir: Path, tmp_path: Path):
    """``--tables`` is split tolerantly because agents assemble it by concatenation."""
    out = tmp_path / "generated"

    result = invoke(
        [
            "lookml",
            "model",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(out),
            "--looker-project",
            "retail_demo",
            "--connection",
            "default_bigquery_connection",
            "--tables",
            "dim_users, fct_orders ,",
        ]
    )

    assert result.exit_code == 0, result.output
    views = {p for p in lkml_tree(out) if p.startswith("views/")}
    assert views == {"views/dim_users.view.lkml", "views/fct_orders.view.lkml"}


def test_model_without_any_source_is_a_config_error(invoke, tmp_path: Path, isolated_cwd: Path):
    """No discoverable tables is a config fault: exit 4, and no state written.

    Recording state for a model that was never generated would let the next
    gate proceed against a directory containing nothing. ``--connection`` is
    passed so the *source* guard is the one that fires -- omitting it now trips
    the required-option guard instead, which would leave this test green while
    silently no longer covering its subject.
    """
    from looker_demo_cli.state import STATE_FILE_NAME

    out = tmp_path / "generated"
    result = invoke(
        [
            "lookml",
            "model",
            "--output-dir",
            str(out),
            "--looker-project",
            "empty_demo",
            "--connection",
            "default_bigquery_connection",
        ]
    )

    assert result.exit_code == ConfigError.exit_code
    assert "No tables found to model" in result.output
    assert not (isolated_cwd / STATE_FILE_NAME).exists()


def test_model_no_tables_json_names_what_it_looked_at(invoke, tmp_path: Path):
    """The failure carries the dataset and project it searched, plus a fix.

    Without those details the caller cannot tell whether it mistyped a dataset
    or is pointed at the wrong Google Cloud project. With no ``--dataset`` and
    no prior state, the searched dataset is now derived from the project name;
    it used to be reported as the hardcoded ``logistics_analytics``, which named
    a dataset the caller had never mentioned and sent them looking in the wrong
    place.
    """
    result = invoke(
        [
            "lookml",
            "model",
            "--output-dir",
            str(tmp_path / "generated"),
            "--looker-project",
            "empty_demo",
            "--gcp-project",
            "unit-test-project",
            "--connection",
            "default_bigquery_connection",
            "--json",
        ]
    )

    assert result.exit_code == ConfigError.exit_code
    payload = envelope(result)
    assert payload["command"] == "lookml model"
    assert payload["status"] == "FAILED"
    error = payload["errors"][0]
    assert error["code"] == "CONFIG_ERROR"
    assert error["message"] == "No tables found to model."
    assert "demo-create data generate" in error["remediation"]
    assert error["details"] == {"dataset": "empty_demo", "gcp_project": "unit-test-project"}


@pytest.mark.characterization
def test_model_creates_output_dir_even_when_it_fails(invoke, tmp_path: Path):
    """The output directory is created before the source is validated.

    # BUG: ``out_dir.mkdir(parents=True, exist_ok=True)`` runs ahead of the
    # "No tables found to model" guard, so a failed invocation litters the
    # filesystem with an empty directory -- under the user's real
    # ``$HOME/scratch/demo_create/`` when ``--output-dir`` is omitted. Phase 2
    # changed the exit code of this path but not the ordering.
    """
    out = tmp_path / "generated"
    assert not out.exists()

    # ``--connection`` is supplied only so the run gets past the Phase 5
    # required-option guard and reaches the ordering this test pins.
    result = invoke(
        [
            "lookml",
            "model",
            "--output-dir",
            str(out),
            "--looker-project",
            "empty_demo",
            "--connection",
            "default_bigquery_connection",
        ]
    )

    assert result.exit_code == ConfigError.exit_code
    assert out.is_dir()
    assert list(out.iterdir()) == []


def test_model_with_dataset_uses_bigquery_introspection(
    invoke, stub_introspection, spec_factory, tmp_path: Path, isolated_cwd: Path
):
    """``--dataset`` routes through Knowledge Catalog introspection, not Parquet.

    The envelope reports ``source`` so a caller can tell which of the two
    generators produced the views it is about to deploy.
    """
    calls = stub_introspection([spec_factory("orders"), spec_factory("customers")])
    out = tmp_path / "generated"

    result = invoke(
        [
            "lookml",
            "model",
            "--dataset",
            "retail_raw",
            "--output-dir",
            str(out),
            "--looker-project",
            "retail_demo",
            "--gcp-project",
            "unit-test-project",
            "--connection",
            "default_bigquery_connection",
            "--json",
        ]
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0] == {
        "project_id": "unit-test-project",
        "dataset_id": "retail_raw",
        "location": "US",
        "table_filter": None,
    }
    assert envelope(result)["data"]["source"] == "bigquery"
    assert "Discovered and enriched 2 table(s)" in result.output
    assert lkml_tree(out) == {
        "views/orders.view.lkml",
        "views/customers.view.lkml",
        "models/retail_demo.model.lkml",
        "dashboards/retail_demo_overview.dashboard.lookml",
    }
    assert read_state(isolated_cwd)["bq_dataset_id"] == "retail_raw"


def test_model_dataset_flag_forwards_table_filter(invoke, stub_introspection, spec_factory, tmp_path: Path):
    """``--tables`` is pushed down so BigQuery is not introspected wholesale."""
    calls = stub_introspection([spec_factory("orders")])

    result = invoke(
        [
            "lookml",
            "model",
            "--dataset",
            "retail_raw",
            "--tables",
            "orders",
            "--output-dir",
            str(tmp_path / "generated"),
            "--looker-project",
            "retail_demo",
            "--connection",
            "default_bigquery_connection",
        ]
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["table_filter"] == ["orders"]


def test_model_falls_back_to_parquet_when_introspection_is_empty(
    invoke, stub_introspection, sample_parquet_dir: Path, tmp_path: Path
):
    """An empty BigQuery result degrades to Parquet rather than failing.

    ``source`` in the envelope is what makes the degradation visible instead
    of silent.
    """
    calls = stub_introspection([])
    out = tmp_path / "generated"

    result = invoke(
        [
            "lookml",
            "model",
            "--dataset",
            "retail_raw",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(out),
            "--looker-project",
            "retail_demo",
            "--connection",
            "default_bigquery_connection",
            "--json",
        ]
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert envelope(result)["data"]["source"] == "parquet"
    assert "Extracted 3 table spec(s) from Parquet files" in result.output
    assert "views/dim_users.view.lkml" in lkml_tree(out)


def test_model_state_dataset_exists_triggers_introspection(
    invoke, stub_introspection, spec_factory, state_file, tmp_path: Path
):
    """``state.dataset_exists`` alone is enough to reach BigQuery, with its location."""
    calls = stub_introspection([spec_factory("orders")])
    state_file(dataset_exists=True, bq_dataset_id="prior_dataset", gcp_location="EU")

    result = invoke(
        [
            "lookml",
            "model",
            "--output-dir",
            str(tmp_path / "generated"),
            "--looker-project",
            "retail_demo",
            "--gcp-project",
            "unit-test-project",
            "--connection",
            "default_bigquery_connection",
        ]
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["dataset_id"] == "prior_dataset"
    assert calls[0]["location"] == "EU"


def test_model_parquet_dir_flag_suppresses_state_dataset_introspection(
    invoke, stub_introspection, sample_parquet_dir: Path, state_file, tmp_path: Path
):
    """An explicit ``--parquet-dir`` beats stale state, avoiding a pointless API call."""
    calls = stub_introspection([])
    state_file(dataset_exists=True, bq_dataset_id="prior_dataset")

    result = invoke(
        [
            "lookml",
            "model",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(tmp_path / "generated"),
            "--looker-project",
            "retail_demo",
            "--connection",
            "default_bigquery_connection",
        ]
    )

    assert result.exit_code == 0, result.output
    assert calls == []


def test_model_reads_parquet_dir_from_state(invoke, sample_parquet_dir: Path, state_file, tmp_path: Path):
    """``data generate`` records where it wrote, so ``model`` needs no flag."""
    state_file(generated_parquet_dir=str(sample_parquet_dir))
    out = tmp_path / "generated"

    result = invoke(
        [
            "lookml",
            "model",
            "--output-dir",
            str(out),
            "--looker-project",
            "retail_demo",
            "--connection",
            "default_bigquery_connection",
        ]
    )

    assert result.exit_code == 0, result.output
    assert "views/fct_orders.view.lkml" in lkml_tree(out)


def test_model_reads_project_name_from_state(invoke, sample_parquet_dir: Path, state_file, tmp_path: Path):
    """The model file is named from prior state when ``--looker-project`` is omitted."""
    state_file(looker_project_name="from_state", generated_parquet_dir=str(sample_parquet_dir))
    out = tmp_path / "generated"

    result = invoke(["lookml", "model", "--output-dir", str(out), "--connection", "default_bigquery_connection"])

    assert result.exit_code == 0, result.output
    assert "models/from_state.model.lkml" in lkml_tree(out)


def test_model_connection_is_reused_from_state_when_the_flag_is_omitted(
    invoke, sample_parquet_dir: Path, state_file, tmp_path: Path, isolated_cwd: Path
):
    """A connection recorded by a previous run is reused, not overwritten.

    ``--connection`` used to default to the literal
    ``"default_bigquery_connection"``. Because that default was truthy, the
    right-hand side of ``connection or state.looker_connection_name`` was dead
    code, and a demo built against a differently named Looker connection had its
    LookML silently regenerated against the wrong one -- a model that validates
    and deploys but cannot run a single query. The flag now defaults to ``None``,
    which is what makes the state fallback reachable at all.
    """
    state_file(
        looker_connection_name="my_custom_connection",
        generated_parquet_dir=str(sample_parquet_dir),
    )
    out = tmp_path / "generated"

    result = invoke(["lookml", "model", "--output-dir", str(out), "--looker-project", "retail_demo"])

    assert result.exit_code == 0, result.output
    model_text = (out / "models" / "retail_demo.model.lkml").read_text(encoding="utf-8")
    assert "my_custom_connection" in model_text
    assert "default_bigquery_connection" not in model_text
    assert read_state(isolated_cwd)["looker_connection_name"] == "my_custom_connection"


def test_model_explicit_connection_overrides_state(
    invoke, sample_parquet_dir: Path, state_file, tmp_path: Path, isolated_cwd: Path
):
    """The flag still wins over state, which is the other half of the same contract.

    Removing the literal default made the state fallback reachable; this pins
    that it did not invert the precedence in the process.
    """
    state_file(
        looker_connection_name="my_custom_connection",
        generated_parquet_dir=str(sample_parquet_dir),
    )
    out = tmp_path / "generated"

    result = invoke(
        [
            "lookml",
            "model",
            "--output-dir",
            str(out),
            "--looker-project",
            "retail_demo",
            "--connection",
            "explicit_connection",
        ]
    )

    assert result.exit_code == 0, result.output
    model_text = (out / "models" / "retail_demo.model.lkml").read_text(encoding="utf-8")
    assert "explicit_connection" in model_text
    assert read_state(isolated_cwd)["looker_connection_name"] == "explicit_connection"


def test_model_without_any_connection_is_a_config_error(invoke, sample_parquet_dir: Path, tmp_path: Path):
    """No flag and no recorded connection is a reported failure, not a guess.

    The old literal default meant this condition could not arise; a caller who
    had never named a connection got ``default_bigquery_connection`` and
    discovered the mistake only when an Explore failed at query time.
    """
    result = invoke(
        [
            "lookml",
            "model",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--output-dir",
            str(tmp_path / "generated"),
            "--looker-project",
            "retail_demo",
            "--json",
        ]
    )

    assert result.exit_code == ConfigError.exit_code
    error = envelope(result)["errors"][0]
    assert error["code"] == "CONFIG_ERROR"
    assert error["details"] == {"option": "--connection"}


@pytest.mark.characterization
def test_model_gcp_project_state_fallback_depends_on_import_time_default(
    invoke, sample_parquet_dir: Path, state_file, tmp_path: Path, isolated_cwd: Path
):
    """``state.gcp_project_id`` is only consulted when the CLI default is empty.

    # BUG: ``gcp_project or state.gcp_project_id`` is governed by
    # ``DEFAULT_GCP_PROJECT``, read from ``GOOGLE_CLOUD_PROJECT`` at *import*
    # time. Whether the state fallback works therefore depends on the
    # developer's shell at process start rather than on pipeline state. Phase 3
    # owns lazy config resolution; Phase 5 left this alone -- it supplies
    # ``--connection`` only so the run reaches the code this pins.
    """
    from looker_demo_cli.config import DEFAULT_GCP_PROJECT

    state_file(gcp_project_id="project-from-state", generated_parquet_dir=str(sample_parquet_dir))

    result = invoke(
        [
            "lookml",
            "model",
            "--output-dir",
            str(tmp_path / "generated"),
            "--looker-project",
            "retail_demo",
            "--connection",
            "default_bigquery_connection",
        ]
    )

    assert result.exit_code == 0, result.output
    expected = DEFAULT_GCP_PROJECT or "project-from-state"
    assert read_state(isolated_cwd)["gcp_project_id"] == expected


# ===========================================================================
# lookml deploy
# ===========================================================================


def test_deploy_without_lookml_dir_is_a_config_error(invoke):
    """Refusing to deploy nothing is the point; exit 4 says the input is at fault."""
    result = invoke(["lookml", "deploy"])

    assert result.exit_code == ConfigError.exit_code
    assert "No LookML directory found to deploy" in result.output


def test_deploy_nonexistent_lookml_dir_is_a_config_error(invoke, tmp_path: Path):
    """A path that does not exist is treated the same as no path at all."""
    result = invoke(["lookml", "deploy", "--lookml-dir", str(tmp_path / "nope")])

    assert result.exit_code == ConfigError.exit_code
    assert "No LookML directory found to deploy" in result.output


def test_deploy_missing_dir_json_envelope(invoke, tmp_path: Path):
    """``deploy`` gained ``--json``, including on its pre-flight guard."""
    missing = tmp_path / "nope"
    result = invoke(["lookml", "deploy", "--lookml-dir", str(missing), "--json"])

    assert result.exit_code == ConfigError.exit_code
    payload = envelope(result)
    assert payload["command"] == "lookml deploy"
    assert payload["status"] == "FAILED"
    assert payload["errors"][0]["code"] == "CONFIG_ERROR"
    assert payload["errors"][0]["details"] == {"lookml_dir": str(missing)}


def test_deploy_applies_options_to_state_before_running(invoke, sample_lookml_dir: Path, stub_deploy_step):
    """``--project`` sets both the project and model name, which must stay in sync."""
    seen = stub_deploy_step(status="completed")

    result = invoke(
        [
            "lookml",
            "deploy",
            "--lookml-dir",
            str(sample_lookml_dir),
            "--looker-project",
            "retail_demo",
            "--looker-account",
            "demo-instance",
        ]
    )

    assert result.exit_code == 0, result.output
    state_in = seen["state_in"]
    assert state_in.lookml_output_dir == sample_lookml_dir
    assert state_in.looker_project_name == "retail_demo"
    assert state_in.lookml_model_name == "retail_demo"
    assert state_in.looker_account == "demo-instance"


def test_deploy_persists_returned_state(invoke, sample_lookml_dir: Path, stub_deploy_step, isolated_cwd: Path):
    """Whatever the step learned is written back, so later gates can read it."""
    stub_deploy_step(status="completed", deployed_dashboard_url="https://fake.looker.com/dashboards/1")

    result = invoke(["lookml", "deploy", "--lookml-dir", str(sample_lookml_dir), "--looker-project", "retail_demo"])

    assert result.exit_code == 0, result.output
    state = read_state(isolated_cwd)
    assert state["status"] == "completed"
    assert state["deployed_dashboard_url"] == "https://fake.looker.com/dashboards/1"
    assert "Updated state saved to" in result.output


def test_deploy_json_emits_the_envelope(invoke, sample_lookml_dir: Path, stub_deploy_step, isolated_cwd: Path):
    """``deploy --json`` is new in Phase 2 and must hand over the dashboard URL.

    The URL is the artifact a human is asked to review at Gate 3; recovering
    it by re-reading the state file was the only prior option.
    """
    from looker_demo_cli.state import STATE_FILE_NAME

    stub_deploy_step(status="completed", deployed_dashboard_url="https://fake.looker.com/dashboards/1")

    result = invoke(
        ["lookml", "deploy", "--lookml-dir", str(sample_lookml_dir), "--looker-project", "retail_demo", "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = envelope(result)
    assert payload["command"] == "lookml deploy"
    assert payload["status"] == "SUCCESS"
    data = payload["data"]
    assert set(data) == {
        "looker_project",
        "model",
        "lookml_dir",
        "instance_url",
        "dashboard_url",
        "state_file",
    }
    assert data["looker_project"] == "retail_demo"
    assert data["model"] == "retail_demo"
    assert data["lookml_dir"] == str(sample_lookml_dir)
    assert data["dashboard_url"] == "https://fake.looker.com/dashboards/1"
    assert data["state_file"] == str(isolated_cwd / STATE_FILE_NAME)


def test_deploy_json_next_action_gates_the_agent(invoke, sample_lookml_dir: Path, stub_deploy_step):
    """A successful deploy points at Gate 4, still behind human confirmation."""
    stub_deploy_step(status="completed")

    result = invoke(
        ["lookml", "deploy", "--lookml-dir", str(sample_lookml_dir), "--looker-project", "retail_demo", "--json"]
    )

    assert result.exit_code == 0, result.output
    actions = envelope(result)["next_actions"]
    assert actions[0]["command"] == "demo-create agent create --model retail_demo"
    assert actions[0]["gate"] == 4
    assert actions[0]["requires_human_confirmation"] is True


def test_deploy_failed_step_exits_non_zero(invoke, sample_lookml_dir: Path, stub_deploy_step, isolated_cwd: Path):
    """A failed deployment must be visible in ``$?``, not only in the state file.

    # FIXED: this was the worst defect in the codebase. Every failure mode of
    # ``deploy_lookml_project`` -- a failed ``lkr`` push, LookML validator
    # errors, failing dashboard tile queries -- previously exited 0, so an
    # agent or CI job chaining on ``&&`` continued as though production had
    # been updated. The command now raises ``ValidationError`` whenever the
    # returned state says ``failed``, while still persisting that state so the
    # failure is diagnosable.
    """
    stub_deploy_step(status="failed", error_message="LookML validation failed: 3 errors")

    result = invoke(["lookml", "deploy", "--lookml-dir", str(sample_lookml_dir), "--looker-project", "retail_demo"])

    assert result.exit_code == ValidationError.exit_code
    assert "LookML validation failed: 3 errors" in result.output
    state = read_state(isolated_cwd)
    assert state["status"] == "failed"
    assert state["error_message"] == "LookML validation failed: 3 errors"


def test_deploy_failed_step_json_envelope(invoke, sample_lookml_dir: Path, stub_deploy_step, isolated_cwd: Path):
    """The failure envelope carries the step's own message and where to look.

    An orchestrator branches on ``VALIDATION_ERROR`` to hand off to the QA
    self-healing subagent, so the code -- not the prose -- is load bearing.
    """
    from looker_demo_cli.state import STATE_FILE_NAME

    stub_deploy_step(status="failed", error_message="LookML validation failed: 3 errors")

    result = invoke(
        ["lookml", "deploy", "--lookml-dir", str(sample_lookml_dir), "--looker-project", "retail_demo", "--json"]
    )

    assert result.exit_code == ValidationError.exit_code
    payload = envelope(result)
    assert payload["command"] == "lookml deploy"
    assert payload["status"] == "FAILED"
    error = payload["errors"][0]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"] == "LookML validation failed: 3 errors"
    assert "re-run `demo-create lookml deploy`" in error["remediation"]
    assert error["details"] == {
        "looker_project": "retail_demo",
        "lookml_dir": str(sample_lookml_dir),
        "state_file": str(isolated_cwd / STATE_FILE_NAME),
    }


def test_deploy_failure_with_no_message_still_exits_non_zero(invoke, sample_lookml_dir: Path, stub_deploy_step):
    """A failure the step forgot to describe is still a failure.

    Falling back to a generic message keeps the exit code correct even when
    an upstream branch neglects to set ``error_message``.
    """
    stub_deploy_step(status="failed")

    result = invoke(["lookml", "deploy", "--lookml-dir", str(sample_lookml_dir), "--looker-project", "retail_demo"])

    assert result.exit_code == ValidationError.exit_code
    assert "LookML deployment failed." in result.output


def test_deploy_reads_lookml_dir_from_state(invoke, sample_lookml_dir: Path, state_file, stub_deploy_step):
    """Gate 3 can be invoked flagless because Gate 2 recorded the output directory."""
    seen = stub_deploy_step(status="completed")
    state_file(lookml_output_dir=str(sample_lookml_dir), looker_project_name="from_state")

    result = invoke(["lookml", "deploy"])

    assert result.exit_code == 0, result.output
    assert seen["state_in"].lookml_output_dir == sample_lookml_dir
    assert seen["state_in"].looker_project_name == "from_state"


def test_deploy_end_to_end_with_stubbed_oauth_http_and_shell(
    invoke,
    sample_lookml_dir: Path,
    fake_shell,
    isolated_cwd: Path,
    stub_oauth_instances: str,
    state_file,
):
    """A clean run drives push, validate, and production deploy, and records success.

    ``status == "completed"`` is asserted because only the failure branches
    used to write ``status``; a fully successful deploy left the state at its
    initial ``"pending"``, which made the state file unusable as a completion
    signal.
    """
    # The connection is a real precondition of deploying, not scaffolding: the
    # deploy step refuses without one. It used to be supplied invisibly by the
    # FlowState placeholder default; `demo-create lookml model` is what records
    # it in the real flow, so the test states it rather than inheriting it.
    state_file(looker_connection_name="default_bigquery_connection")
    base_url = stub_oauth_instances

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.PATCH, f"{base_url}/api/4.0/session", json={}, status=200)
        rsps.add(responses.GET, f"{base_url}/api/4.0/projects/retail_demo", json={}, status=200)
        rsps.add(responses.GET, f"{base_url}/api/4.0/lookml_models/retail_demo", json={}, status=200)
        rsps.add(responses.GET, f"{base_url}/api/4.0/projects/retail_demo/files", json=[], status=200)
        rsps.add(
            responses.GET,
            f"{base_url}/api/4.0/projects/retail_demo/validate",
            json={"errors": []},
            status=200,
        )

        result = invoke(
            [
                "lookml",
                "deploy",
                "--lookml-dir",
                str(sample_lookml_dir),
                "--looker-project",
                "retail_demo",
                "--looker-account",
                "demo-instance",
            ]
        )

    assert result.exit_code == 0, result.output
    assert "LookML validator passed cleanly with 0 errors." in result.output
    fake_shell.assert_called_with_substring("tools lookml push")
    fake_shell.assert_called_with_substring("tools lookml deploy")
    fake_shell.assert_called_with_substring("--oauth-account demo-instance")

    state = read_state(isolated_cwd)
    assert state["looker_instance_url"] == base_url
    assert state["deployed_dashboard_url"] == f"{base_url}/dashboards/retail_demo::retail_demo_overview"
    assert state["status"] == "completed"


def test_deploy_validator_errors_exit_non_zero(
    invoke,
    sample_lookml_dir: Path,
    fake_shell,
    isolated_cwd: Path,
    stub_oauth_instances: str,
    state_file,
):
    """LookML validator errors abort the process, not merely the step.

    # FIXED: this is the end-to-end proof of the deploy fix. The validator
    # reports an error, the production ``lkr ... tools lookml deploy``
    # shell-out is skipped -- and the process now exits 6 instead of 0, so a
    # caller chaining ``&&`` stops instead of provisioning an agent against
    # LookML that was never released.
    """
    state_file(looker_connection_name="default_bigquery_connection")
    base_url = stub_oauth_instances

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.PATCH, f"{base_url}/api/4.0/session", json={}, status=200)
        rsps.add(responses.GET, f"{base_url}/api/4.0/projects/retail_demo", json={}, status=200)
        rsps.add(responses.GET, f"{base_url}/api/4.0/lookml_models/retail_demo", json={}, status=200)
        rsps.add(responses.GET, f"{base_url}/api/4.0/projects/retail_demo/files", json=[], status=200)
        rsps.add(
            responses.GET,
            f"{base_url}/api/4.0/projects/retail_demo/validate",
            json={"errors": [{"file_path": "views/users.view.lkml", "line_number": 4, "message": "Unknown field"}]},
            status=200,
        )

        result = invoke(["lookml", "deploy", "--lookml-dir", str(sample_lookml_dir), "--looker-project", "retail_demo"])

    assert result.exit_code == ValidationError.exit_code
    assert "LookML validation failed with 1 error(s)" in result.output
    state = read_state(isolated_cwd)
    assert state["status"] == "failed"
    assert state["error_message"] == "LookML validation failed: 1 errors"
    # The production deploy shell-out is never reached.
    joined = [" ".join(c) if isinstance(c, (list, tuple)) else str(c) for c in fake_shell.calls]
    assert not any("tools lookml deploy" in c for c in joined)


def test_deploy_validator_errors_json_envelope(
    invoke,
    sample_lookml_dir: Path,
    fake_shell,
    stub_oauth_instances: str,
    state_file,
):
    """The same real validator failure is machine-readable on stdout.

    Proves the envelope survives a path where the step itself printed a large
    amount of Rich diagnostic output first.
    """
    state_file(looker_connection_name="default_bigquery_connection")
    base_url = stub_oauth_instances

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.PATCH, f"{base_url}/api/4.0/session", json={}, status=200)
        rsps.add(responses.GET, f"{base_url}/api/4.0/projects/retail_demo", json={}, status=200)
        rsps.add(responses.GET, f"{base_url}/api/4.0/lookml_models/retail_demo", json={}, status=200)
        rsps.add(responses.GET, f"{base_url}/api/4.0/projects/retail_demo/files", json=[], status=200)
        rsps.add(
            responses.GET,
            f"{base_url}/api/4.0/projects/retail_demo/validate",
            json={"errors": [{"file_path": "views/users.view.lkml", "line_number": 4, "message": "Unknown field"}]},
            status=200,
        )

        result = invoke(
            ["lookml", "deploy", "--lookml-dir", str(sample_lookml_dir), "--looker-project", "retail_demo", "--json"]
        )

    assert result.exit_code == ValidationError.exit_code
    payload = envelope(result)
    assert payload["command"] == "lookml deploy"
    assert payload["status"] == "FAILED"
    assert payload["errors"][0]["code"] == "VALIDATION_ERROR"
    assert payload["errors"][0]["message"] == "LookML validation failed: 1 errors"


# ===========================================================================
# lookml clean-root
# ===========================================================================


PROJECT_FILES_URL = "https://fake.cloud.looker.com/api/4.0/projects/retail_demo/files"


def test_clean_root_unauthenticated_is_an_auth_error(invoke, unauthenticated_looker, install_looker_auth):
    """Exit 3 tells the caller to re-authenticate rather than retry.

    The fixture models "no instance configured", so the expected error is
    :func:`no_looker_instance`, not the generic "authentication required".
    ``clean-root`` used to emit the latter for both conditions -- its inline
    check was ``if not base_url or not headers`` behind a single message -- and
    sent anyone missing only an instance URL to ``lkr auth login``, which does
    not fix it. Resolution now happens in ``AppContext``, which distinguishes
    the two.
    """
    install_looker_auth(unauthenticated_looker)

    result = invoke(["lookml", "clean-root", "--looker-project", "retail_demo", "--json"])

    assert result.exit_code == AuthError.exit_code
    payload = envelope(result)
    assert payload["command"] == "lookml clean-root"
    assert payload["status"] == "FAILED"
    assert payload["errors"] == [
        {
            "code": "AUTH_ERROR",
            "message": no_looker_instance().message,
            "remediation": no_looker_instance().remediation,
            "details": {},
        }
    ]


def test_clean_root_unauthenticated_text(invoke, unauthenticated_looker, install_looker_auth):
    """The human path shares one message with the JSON path, from one factory."""
    install_looker_auth(unauthenticated_looker)

    result = invoke(["lookml", "clean-root", "--looker-project", "retail_demo"])

    assert result.exit_code == AuthError.exit_code
    assert no_looker_instance().message in result.output


def test_clean_root_missing_project_is_a_config_error(invoke, state_file, fake_looker, install_looker_auth):
    """A resolvable-option miss names the flag, so the fix needs no guesswork."""
    install_looker_auth(fake_looker)
    state_file(looker_project_name="")

    result = invoke(["lookml", "clean-root", "--json"])

    assert result.exit_code == ConfigError.exit_code
    payload = envelope(result)
    assert payload["command"] == "lookml clean-root"
    assert payload["status"] == "FAILED"
    error = payload["errors"][0]
    assert error["code"] == "CONFIG_ERROR"
    assert "--looker-project" in error["message"]
    assert error["details"] == {"option": "--looker-project"}


def test_clean_root_missing_project_text(invoke, state_file, fake_looker, install_looker_auth):
    """Same guard, human rendering, same exit code."""
    install_looker_auth(fake_looker)
    state_file(looker_project_name="")

    result = invoke(["lookml", "clean-root"])

    assert result.exit_code == ConfigError.exit_code
    assert "--looker-project" in result.output


def test_clean_root_with_no_project_refuses_before_making_any_request(invoke, fake_looker, install_looker_auth):
    """Omitting ``--looker-project`` errors *before* the Looker API is touched.

    This is the headline defect of the refactor. ``FlowState.looker_project_name``
    defaulted to the non-empty ``"logistics_analytics"``, which made the
    ``if not proj_name`` guard unreachable: a bare ``demo-create lookml clean-root``
    issued a live ``GET`` -- and then live ``DELETE``s -- against whatever real
    project happened to bear that name on the caller's instance. It was
    reproduced against a production instance before the state default was
    removed.

    The guarantee under test is therefore not "it errors" but "it errors
    without reaching the network", so the assertion is on the request log. An
    exit code alone would still pass if the guard ran *after* the GET.

    Complements ``test_clean_root_missing_project_is_a_config_error``, which
    covers an explicitly blank project recorded in state; here there is no state
    file at all, which is the case that used to be silently defaulted.
    """
    install_looker_auth(fake_looker)

    # No registered mocks: any outbound request raises ConnectionError instead
    # of being served, so a regression fails loudly rather than by assertion.
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        result = invoke(["lookml", "clean-root"])

        assert len(rsps.calls) == 0, f"clean-root reached the network: {[c.request.url for c in rsps.calls]}"

    assert result.exit_code == ConfigError.exit_code
    assert "--looker-project" in result.output
    assert "logistics_analytics" not in result.output


def test_clean_root_no_duplicates_success_json(invoke, fake_looker, install_looker_auth):
    """A genuinely clean project is SUCCESS -- and must be distinguishable from an error.

    This is the control case for the API-failure test below: both used to
    produce this identical payload.
    """
    install_looker_auth(fake_looker)

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(
            responses.GET,
            PROJECT_FILES_URL,
            json=[{"path": "views/users.view.lkml"}, {"path": "models/retail_demo.model.lkml"}],
            status=200,
        )
        result = invoke(["lookml", "clean-root", "--looker-project", "retail_demo", "--json"])

    assert result.exit_code == 0, result.output
    payload = envelope(result)
    assert payload["command"] == "lookml clean-root"
    assert payload["status"] == "SUCCESS"
    assert payload["errors"] == []
    assert payload["data"] == {
        "looker_project": "retail_demo",
        "dry_run": False,
        "status": "SUCCESS",
        "cleaned_files": [],
        "message": "No duplicate root files found in project `retail_demo`.",
    }


def test_clean_root_no_duplicates_success_text(invoke, fake_looker, install_looker_auth):
    """The clean-project affirmation is what closes out the Gate 3 audit."""
    install_looker_auth(fake_looker)

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(responses.GET, PROJECT_FILES_URL, json=[{"path": "views/users.view.lkml"}], status=200)
        result = invoke(["lookml", "clean-root", "--looker-project", "retail_demo"])

    assert result.exit_code == 0, result.output
    assert "has a clean root directory structure" in result.output


def test_clean_root_dry_run_is_a_success_status(invoke, fake_looker, install_looker_auth):
    """``DRY_RUN`` exits 0: reporting duplicates is the requested outcome.

    It is a distinct status rather than SUCCESS so a caller cannot mistake a
    preview for an actual cleanup.
    """
    install_looker_auth(fake_looker)

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(
            responses.GET,
            PROJECT_FILES_URL,
            json=[
                {"path": "users.view.lkml"},
                {"path": "views/users.view.lkml"},
                {"path": "retail_demo.model.lkml"},
                {"path": "models/retail_demo.model.lkml"},
            ],
            status=200,
        )
        result = invoke(["lookml", "clean-root", "--looker-project", "retail_demo", "--dry-run", "--json"])

    assert result.exit_code == 0, result.output
    payload = envelope(result)
    assert payload["status"] == "DRY_RUN"
    assert payload["errors"] == []
    assert payload["data"]["dry_run"] is True
    assert sorted(payload["data"]["cleaned_files"]) == ["retail_demo.model.lkml", "users.view.lkml"]
    assert payload["data"]["message"] == "Found 2 duplicate root file(s) (Dry Run)."


def test_clean_root_deletes_duplicates_json(invoke, fake_looker, install_looker_auth):
    """Root orphans are deleted via ``DELETE /files?file_path=<path>``.

    The file is passed as a query parameter against the collection endpoint --
    the same URL used to list files -- rather than as a path segment, so this
    pins the wire shape a Looker upgrade could break.
    """
    install_looker_auth(fake_looker)

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(
            responses.GET,
            PROJECT_FILES_URL,
            json=[{"path": "users.view.lkml"}, {"path": "views/users.view.lkml"}],
            status=200,
        )
        # NOTE: 200, not 204. A `responses` DELETE mock registered with
        # status=204 *and* a json body raises inside the urllib3 adapter.
        rsps.add(responses.DELETE, PROJECT_FILES_URL, json={}, status=200)
        result = invoke(["lookml", "clean-root", "--looker-project", "retail_demo", "--json"])

    assert result.exit_code == 0, result.output
    payload = envelope(result)
    assert payload["status"] == "SUCCESS"
    assert payload["data"]["cleaned_files"] == ["users.view.lkml"]
    assert payload["data"]["total_detected"] == 1
    assert payload["data"]["total_deleted"] == 1


def test_clean_root_delete_falls_back_to_path_in_url(invoke, fake_looker, install_looker_auth):
    """A rejected query-param DELETE is retried with the path inlined in the URL.

    Looker versions disagree on the accepted form, and a partial cleanup is
    now a hard failure, so the fallback is what keeps older instances green.
    """
    install_looker_auth(fake_looker)

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(
            responses.GET,
            PROJECT_FILES_URL,
            json=[{"path": "users.view.lkml"}, {"path": "views/users.view.lkml"}],
            status=200,
        )
        rsps.add(responses.DELETE, PROJECT_FILES_URL, json={}, status=422)
        rsps.add(responses.DELETE, f"{PROJECT_FILES_URL}/users.view.lkml", json={}, status=200)
        result = invoke(["lookml", "clean-root", "--looker-project", "retail_demo", "--json"])

    assert result.exit_code == 0, result.output
    assert envelope(result)["data"]["total_deleted"] == 1


def test_clean_root_partial_deletion_exits_non_zero(invoke, fake_looker, install_looker_auth):
    """A ``PARTIAL`` cleanup is a failure: duplicates survived on the remote.

    # FIXED: the command previously exited 0 on ``PARTIAL`` and the human path
    # even printed a green success line, so a caller could not distinguish
    # "root is clean" from "I failed to delete anything". Surviving root
    # orphans keep the remote master branch desynced and get resurrected by
    # the next deploy, so this now exits with the remote-API code and carries
    # a structured error.
    """
    install_looker_auth(fake_looker)

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(
            responses.GET,
            PROJECT_FILES_URL,
            json=[{"path": "users.view.lkml"}, {"path": "views/users.view.lkml"}],
            status=200,
        )
        rsps.add(responses.DELETE, PROJECT_FILES_URL, json={}, status=500)
        rsps.add(responses.DELETE, f"{PROJECT_FILES_URL}/users.view.lkml", json={}, status=500)
        result = invoke(["lookml", "clean-root", "--looker-project", "retail_demo", "--json"])

    assert result.exit_code == RemoteApiError.exit_code
    payload = envelope(result)
    # Built by the command itself rather than the error handler, so this also
    # checks the two paths agree on the command path.
    assert payload["command"] == "lookml clean-root"
    assert payload["status"] == "PARTIAL"
    error = payload["errors"][0]
    assert error["code"] == "REMOTE_API_ERROR"
    assert error["message"] == "Deleted 0 of 1 duplicate root file(s)."
    assert error["remediation"]
    # The partial data is still reported, so the caller can see what survived.
    assert payload["data"]["cleaned_files"] == []
    assert payload["data"]["total_detected"] == 1
    assert payload["data"]["total_deleted"] == 0


def test_clean_root_partial_deletion_prints_no_success_line(invoke, fake_looker, install_looker_auth):
    """A human must not be told the project is clean when it is not.

    # FIXED: the non-JSON branch previously printed the green
    # "clean root directory structure" line on a PARTIAL result.
    """
    install_looker_auth(fake_looker)

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(
            responses.GET,
            PROJECT_FILES_URL,
            json=[{"path": "users.view.lkml"}, {"path": "views/users.view.lkml"}],
            status=200,
        )
        rsps.add(responses.DELETE, PROJECT_FILES_URL, json={}, status=500)
        rsps.add(responses.DELETE, f"{PROJECT_FILES_URL}/users.view.lkml", json={}, status=500)
        result = invoke(["lookml", "clean-root", "--looker-project", "retail_demo"])

    assert result.exit_code == RemoteApiError.exit_code
    assert "clean root directory structure" not in result.output
    assert "Deleted 0 of 1 duplicate root file(s)." in result.output


def test_clean_root_files_api_error_is_surfaced(invoke, fake_looker, install_looker_auth):
    """An unreachable Looker API must not be reported as a clean project.

    # FIXED: ``find_root_duplicate_files`` swallowed non-200 responses and
    # returned ``[]``, so a 500 rendered as ``status: SUCCESS`` /
    # ``cleaned_files: []`` -- byte-identical to a genuinely clean project
    # (see ``test_clean_root_no_duplicates_success_json``). The Gate 3 audit
    # therefore "passed" without ever auditing anything. It now raises
    # ``RemoteApiError`` carrying the HTTP status.
    """
    install_looker_auth(fake_looker)

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(responses.GET, PROJECT_FILES_URL, body="boom", status=500)
        result = invoke(["lookml", "clean-root", "--looker-project", "retail_demo", "--json"])

    assert result.exit_code == RemoteApiError.exit_code
    payload = envelope(result)
    assert payload["command"] == "lookml clean-root"
    assert payload["status"] == "FAILED"
    error = payload["errors"][0]
    assert error["code"] == "REMOTE_API_ERROR"
    assert "HTTP 500" in error["message"]
    assert error["details"]["status_code"] == 500
    assert error["details"]["project_id"] == "retail_demo"
    # No misleading "cleaned nothing" payload accompanies the failure.
    assert payload["data"] == {}


def test_clean_root_files_api_error_text(invoke, fake_looker, install_looker_auth):
    """The human sees the failure too, instead of a green audit-passed line."""
    install_looker_auth(fake_looker)

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(responses.GET, PROJECT_FILES_URL, body="boom", status=500)
        result = invoke(["lookml", "clean-root", "--looker-project", "retail_demo"])

    assert result.exit_code == RemoteApiError.exit_code
    assert "clean root directory structure" not in result.output
    assert "Could not fetch project files" in result.output


def test_clean_root_network_failure_is_a_remote_api_error(
    invoke, fake_looker, install_looker_auth, monkeypatch: pytest.MonkeyPatch
):
    """A transport-level failure is classified like an HTTP failure.

    Both mean "the audit did not happen"; collapsing them to one exit code
    lets a caller retry on 5 without inspecting the message.
    """
    install_looker_auth(fake_looker)

    def _boom(*_args: Any, **_kwargs: Any):
        raise ConnectionError("connection refused")

    monkeypatch.setattr("looker_demo_cli.services.lookml_cleaner.requests.get", _boom)

    result = invoke(["lookml", "clean-root", "--looker-project", "retail_demo", "--json"])

    assert result.exit_code == RemoteApiError.exit_code
    payload = envelope(result)
    assert payload["command"] == "lookml clean-root"
    error = payload["errors"][0]
    assert error["code"] == "REMOTE_API_ERROR"
    assert "connection refused" in error["message"]


def test_clean_root_uses_project_name_from_state(invoke, fake_looker, install_looker_auth, state_file):
    """Prior state supplies the project so Gate 3 can run the audit flagless."""
    install_looker_auth(fake_looker)
    state_file(looker_project_name="retail_demo")

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(responses.GET, PROJECT_FILES_URL, json=[], status=200)
        result = invoke(["lookml", "clean-root", "--json"])

    assert result.exit_code == 0, result.output
    assert envelope(result)["status"] == "SUCCESS"


def test_clean_root_does_not_write_state(invoke, fake_looker, install_looker_auth, isolated_cwd: Path):
    """An audit is not a pipeline step, so it must not advance the state record."""
    from looker_demo_cli.state import STATE_FILE_NAME

    install_looker_auth(fake_looker)

    with responses.RequestsMock(assert_all_requests_are_fired=True) as rsps:
        rsps.add(responses.GET, PROJECT_FILES_URL, json=[], status=200)
        result = invoke(["lookml", "clean-root", "--looker-project", "retail_demo", "--json"])

    assert result.exit_code == 0, result.output
    assert not (isolated_cwd / STATE_FILE_NAME).exists()


# ===========================================================================
# Group-level behavior
# ===========================================================================


@pytest.mark.parametrize("subcommand", ["model", "deploy", "optimize", "restore", "clean-root"])
def test_every_lookml_subcommand_exposes_json(invoke, subcommand: str):
    """``--json`` is now universal across the group.

    An orchestrator should never have to remember which subcommands can be
    parsed; ``deploy`` was the last holdout and gained the flag in Phase 2.
    """
    result = invoke(["lookml", subcommand, "--help"])

    assert result.exit_code == 0
    assert "--json" in result.output


def test_lookml_group_with_no_args_shows_help(invoke):
    """``no_args_is_help=True`` keeps a bare group invocation discoverable."""
    result = invoke(["lookml"])

    assert result.exit_code == USAGE_EXIT_CODE
    for name in ("model", "deploy", "optimize", "restore", "clean-root"):
        assert name in result.output


def test_lookml_unknown_subcommand_exits_2(invoke):
    """Exit 2 stays reserved for Click usage errors, never reused by our codes."""
    result = invoke(["lookml", "frobnicate"])

    assert result.exit_code == USAGE_EXIT_CODE
    assert "frobnicate" in result.output
