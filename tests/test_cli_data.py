"""Tests for the `demo-create data` command group (`generate`, `upload`, `inspect`)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from looker_demo_cli.errors import ConfigError, RemoteApiError, StateError

try:
    from conftest import FakeTable, FakeTableSchemaField, envelope, read_state
except ImportError:  # pragma: no cover
    from tests.conftest import FakeTable, FakeTableSchemaField, envelope, read_state

pytestmark = [pytest.mark.unit]


class FakeSynthesizer:
    """In-memory recorder for `create_dynamic_blueprint_from_name` and `generate_domain_dataset`."""

    def __init__(self) -> None:
        self.table_names: list[str] = ["dim_things", "fct_events"]
        self.write_files: bool = True
        self.blueprint_calls: list[str] = []
        self.generate_calls: list[tuple[Any, Path]] = []

    def create_blueprint(self, domain_name: str) -> Any:
        self.blueprint_calls.append(domain_name)
        return SimpleNamespace(
            domain_name=domain_name,
            description=f"fake blueprint for {domain_name}",
            entities=[
                SimpleNamespace(
                    table_name="dim_things",
                    table_type="dimension",
                    row_count=1000,
                    primary_key="thing_id",
                    foreign_keys={},
                    fields=[SimpleNamespace(name="thing_id", type="STRING", is_primary_key=True, is_foreign_key=False)],
                ),
                SimpleNamespace(
                    table_name="fct_events",
                    table_type="fact",
                    row_count=5000,
                    primary_key="event_id",
                    foreign_keys={"thing_id": "dim_things.thing_id"},
                    fields=[
                        SimpleNamespace(name="event_id", type="STRING", is_primary_key=True, is_foreign_key=False),
                        SimpleNamespace(
                            name="thing_id",
                            type="STRING",
                            is_primary_key=False,
                            is_foreign_key=True,
                            foreign_reference="dim_things.thing_id",
                        ),
                    ],
                ),
            ],
        )

    def generate(
        self,
        target: Any,
        output_dir: Path,
        micro_sample_only: bool = False,
        builder_script: Path | None = None,
        engine: str = "auto",
    ) -> list[Any]:
        self.generate_calls.append((target, Path(output_dir)))
        if not micro_sample_only:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            if self.write_files:
                for name in self.table_names:
                    (Path(output_dir) / f"{name}.parquet").write_bytes(b"PAR1-fake")
        return [SimpleNamespace(table_name=n) for n in self.table_names]


@pytest.fixture
def fake_synth(patch_cli) -> FakeSynthesizer:
    fake = FakeSynthesizer()
    patch_cli("create_dynamic_blueprint_from_name", fake.create_blueprint)
    patch_cli("generate_domain_dataset", fake.generate)
    return fake


# ===========================================================================
# data propose-schema & data approve-schema
# ===========================================================================


def test_data_propose_and_approve_schema_and_generate_guards(
    invoke, fake_synth, state_file, isolated_cwd: Path, tmp_path: Path
) -> None:
    """Covers Gate 1A `propose-schema`, Gate 1B `approve-schema`, and `StateError` precondition guards."""
    # Gate 1A guard: precheck_passed=True without targets_confirmed=True raises StateError
    state_file(precheck_passed=True, targets_confirmed=False)
    unconfirmed = invoke(["data", "propose-schema", "--domain", "retail_ops", "--json"])
    assert unconfirmed.exit_code == StateError.exit_code
    assert "Gate 0B" in envelope(unconfirmed)["errors"][0]["message"]

    # Gate 1B guard: approve-schema before propose-schema raises StateError
    state_file(precheck_passed=True, targets_confirmed=True)
    unproposed = invoke(["data", "approve-schema", "--row-count", "5000", "--json"])
    assert unproposed.exit_code == StateError.exit_code
    assert "Gate 1A" in envelope(unproposed)["errors"][0]["message"]

    # Gate 1A happy path: propose-schema generates Mermaid ERD & table schemas and advances to Gate 3
    (isolated_cwd / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
    prop = invoke(["data", "propose-schema", "--domain", "retail_ops", "--json"])
    assert prop.exit_code == 0, prop.output
    prop_payload = envelope(prop)
    assert "erDiagram" in prop_payload["data"]["mermaid_erd"]
    assert len(prop_payload["data"]["table_schemas"]) == 2
    assert prop_payload["next_actions"][0]["gate"] == 3
    assert "erDiagram" in (isolated_cwd / "SPEC.md").read_text(encoding="utf-8")

    # Gate 1C guard: data generate before approve-schema raises StateError
    unapproved = invoke(["data", "generate", "--domain", "retail_ops", "--output-dir", str(tmp_path / "out"), "--json"])
    assert unapproved.exit_code == StateError.exit_code
    assert "Gate 1B" in envelope(unapproved)["errors"][0]["message"]

    # Gate 1B happy path: approve-schema records approval and advances to Gate 4
    app_res = invoke(["data", "approve-schema", "--row-count", "7500", "--dataset", "retail_ds", "--json"])
    assert app_res.exit_code == 0, app_res.output
    app_payload = envelope(app_res)
    assert app_payload["data"]["schema_approved"] is True
    assert app_payload["data"]["approved_row_count"] == 7500
    assert app_payload["next_actions"][0]["gate"] == 4
    assert read_state(isolated_cwd)["schema_approved"] is True


# ===========================================================================
# data generate
# ===========================================================================


def test_data_generate_json_and_human_happy_path(invoke, fake_synth, isolated_cwd: Path, tmp_path: Path) -> None:
    """`data generate` writes Parquet files, persists state, and emits structured JSON or stderr narration."""
    out = tmp_path / "parquet_out"
    res_json = invoke(
        ["data", "generate", "--domain", "retail_ops", "--row-count", "25", "--output-dir", str(out), "--json"]
    )
    assert res_json.exit_code == 0
    payload = envelope(res_json)
    assert payload["command"] == "data generate"
    assert payload["status"] == "SUCCESS"
    assert payload["data"]["domain"] == "retail_ops"
    assert payload["data"]["tables"] == ["dim_things", "fct_events"]
    assert payload["data"]["uploaded"] is False
    assert payload["next_actions"][0]["gate"] == 4

    state = read_state(isolated_cwd)
    assert state["domain_name"] == "retail_ops"
    assert state["bq_dataset_id"] == "retail_ops"
    assert state["generated_tables"] == ["dim_things", "fct_events"]
    assert Path(state["generated_parquet_dir"]) == out

    # Human mode check
    res_human = invoke(["data", "generate", "--domain", "retail_ops", "--output-dir", str(tmp_path / "h")])
    assert res_human.exit_code == 0
    assert res_human.stdout == ""
    assert "Generated 2 tables" in res_human.output


def test_data_generate_with_upload(invoke, fake_synth, fake_bigquery, isolated_cwd: Path, tmp_path: Path) -> None:
    """`data generate --upload` synthesizes tables and loads them into BigQuery in one step."""
    result = invoke(
        [
            "data",
            "generate",
            "--domain",
            "freight",
            "--output-dir",
            str(tmp_path / "o"),
            "--upload",
            "--gcp-project",
            "proj-x",
            "--dataset",
            "freight_ds",
            "--json",
        ]
    )
    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["data"]["uploaded"] is True
    assert payload["data"]["loaded_rows"] == {"dim_things": 42, "fct_events": 42}
    assert payload["next_actions"][0]["gate"] == 5
    assert fake_bigquery.datasets == {"freight_ds": ["dim_things", "fct_events"]}
    assert read_state(isolated_cwd)["dataset_exists"] is True


def test_data_generate_preview_and_validate_only_modes(invoke, tmp_path: Path, isolated_cwd: Path) -> None:
    """`--preview` samples rows without writing Parquet; `--validate-only --json-scorecard` runs DAG + TableValidator."""
    preview_dir = tmp_path / "preview_out"
    prev = invoke(
        [
            "data",
            "generate",
            "--domain",
            "finops_analytics",
            "--output-dir",
            str(preview_dir),
            "--preview",
            "-n",
            "5",
            "--json",
        ]
    )
    assert prev.exit_code == 0
    prev_data = envelope(prev)["data"]
    assert prev_data["preview"] is True
    assert prev_data["preview_rows"] == 5
    assert len(prev_data["samples"]) > 0
    assert not preview_dir.exists()

    val_dir = tmp_path / "validate_out"
    val = invoke(
        [
            "data",
            "generate",
            "--domain",
            "telemetry_analytics",
            "--row-count",
            "100",
            "--output-dir",
            str(val_dir),
            "--validate-only",
            "--json-scorecard",
        ]
    )
    assert val.exit_code == 0
    val_data = envelope(val)["data"]
    assert val_data["validate_only"] is True
    assert val_data["scorecard"]["status"] == "SUCCESS"


# ===========================================================================
# data upload
# ===========================================================================


def test_data_upload_happy_path_and_verify_only(
    invoke, fake_bigquery, sample_parquet_dir: Path, isolated_cwd: Path
) -> None:
    """`data upload` loads Parquet files into BigQuery and updates state; `--verify-only` checks existing row counts."""
    res = invoke(
        [
            "data",
            "upload",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--dataset",
            "retail",
            "--gcp-project",
            "proj-1",
            "--json",
        ]
    )
    assert res.exit_code == 0
    payload = envelope(res)
    assert payload["command"] == "data upload"
    assert payload["data"]["loaded_rows"] == {"dim_products": 42, "dim_users": 42, "fct_orders": 42}
    assert payload["data"]["total_rows"] == 126
    assert payload["next_actions"][0]["gate"] == 5
    assert read_state(isolated_cwd)["dataset_exists"] is True

    # Human mode check
    res_human = invoke(["data", "upload", "--parquet-dir", str(sample_parquet_dir), "--dataset", "retail"])
    assert res_human.exit_code == 0
    assert res_human.stdout == ""

    # --verify-only mode skips load_parquet_table
    fake_bigquery.get_table_row_count = lambda self, ds, tbl: 999
    verify = invoke(
        [
            "data",
            "upload",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--dataset",
            "retail",
            "--gcp-project",
            "proj-1",
            "--verify-only",
            "--json",
        ]
    )
    assert verify.exit_code == 0
    assert all(r == 999 for r in envelope(verify)["data"]["loaded_rows"].values())


def test_data_upload_config_errors(invoke, fake_bigquery, sample_parquet_dir: Path, tmp_path: Path) -> None:
    """Missing/empty `--parquet-dir` or missing `--dataset` exits with `ConfigError.exit_code` (4)."""
    assert invoke(["data", "upload", "--dataset", "retail"]).exit_code == ConfigError.exit_code

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert (
        invoke(["data", "upload", "--parquet-dir", str(empty_dir), "--dataset", "retail"]).exit_code
        == ConfigError.exit_code
    )
    assert (
        invoke(["data", "upload", "--parquet-dir", str(sample_parquet_dir), "--json"]).exit_code
        == ConfigError.exit_code
    )
    assert fake_bigquery.datasets == {}


# ===========================================================================
# data inspect
# ===========================================================================


def test_data_inspect_happy_partial_and_error_paths(invoke, fake_bigquery) -> None:
    """`data inspect` reports table schemas on success, `PARTIAL` (exit 5) on unreadable tables, and `FAILED` on missing dataset."""
    # Missing dataset -> RemoteApiError (exit 5)
    missing = invoke(["data", "inspect", "--dataset", "ghost_ds", "--gcp-project", "proj-1", "--json"])
    assert missing.exit_code == RemoteApiError.exit_code
    assert envelope(missing)["errors"][0]["code"] == "REMOTE_API_ERROR"

    # Missing --dataset flag and no state -> ConfigError (exit 4)
    assert invoke(["data", "inspect", "--json"]).exit_code == ConfigError.exit_code

    # Happy path (JSON + human)
    fake_bigquery.datasets = {"retail": ["dim_users"]}
    fake_bigquery.tables = {
        "dim_users": FakeTable(
            "dim_users",
            [FakeTableSchemaField("user_id", "STRING", "REQUIRED"), FakeTableSchemaField("age", "INT64")],
            num_rows=1500,
        )
    }
    ok_json = invoke(["data", "inspect", "--dataset", "retail", "--gcp-project", "proj-1", "--json"])
    assert ok_json.exit_code == 0
    data = envelope(ok_json)["data"]
    assert data["table_count"] == 1
    assert data["tables"][0]["num_rows"] == 1500

    ok_human = invoke(["data", "inspect", "--dataset", "retail", "--gcp-project", "proj-1"])
    assert ok_human.exit_code == 0
    assert "dim_users" in ok_human.output
    assert "1,500" in ok_human.output

    # Partial failure when one table fails metadata lookup
    fake_bigquery.datasets = {"retail": ["dim_users", "ghost"]}
    partial = invoke(["data", "inspect", "--dataset", "retail", "--json"])
    assert partial.exit_code == RemoteApiError.exit_code
    assert envelope(partial)["status"] == "PARTIAL"
