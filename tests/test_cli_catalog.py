"""Tests for ``demo-create catalog`` command group and dataset adoption."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from looker_demo_cli.errors import ConfigError
from looker_demo_cli.state import FlowState, save_flow_state

try:
    from conftest import FakeTable, FakeTableSchemaField, envelope, read_state
except ImportError:  # pragma: no cover
    from tests.conftest import FakeTable, FakeTableSchemaField, envelope, read_state

pytestmark = [pytest.mark.unit]


# ===========================================================================
# catalog inspect
# ===========================================================================


def test_catalog_inspect_happy_path(invoke, fake_bigquery, fake_catalog, isolated_cwd: Path) -> None:
    """`catalog inspect` builds snapshot, computes coverage, writes file, and updates state."""
    fake_bigquery.datasets = {"fintech_ds": ["dim_customers", "fct_transactions"]}
    fake_bigquery.tables = {
        "dim_customers": FakeTable(
            "dim_customers",
            [
                FakeTableSchemaField("customer_id", "STRING", "REQUIRED", description="Unique customer ID"),
                FakeTableSchemaField("first_name", "STRING", "NULLABLE"),
            ],
            num_rows=500,
        ),
        "fct_transactions": FakeTable(
            "fct_transactions",
            [
                FakeTableSchemaField("transaction_id", "STRING", "REQUIRED"),
                FakeTableSchemaField("customer_id", "STRING", "REQUIRED"),
                FakeTableSchemaField("amount", "NUMERIC", "NULLABLE"),
            ],
            num_rows=5000,
        ),
    }

    # Set up some fake catalog entries
    fake_catalog.entries = {
        "fintech_ds.dim_customers": {
            "aspects": {
                "projects/test-p/locations/us/aspectTypes/semantic-curation": {
                    "data": {
                        "business_label": "Customers Dimension",
                        "business_description": "Curated customer accounts",
                        "column_curation": {
                            "first_name": {"business_label": "First Name"},
                        },
                    }
                }
            }
        }
    }

    res = invoke(
        [
            "catalog",
            "inspect",
            "--dataset",
            "fintech_ds",
            "--gcp-project",
            "test-p",
            "--json",
        ]
    )
    assert res.exit_code == 0
    payload = envelope(res)
    assert payload["command"] == "catalog inspect"
    data = payload["data"]
    assert data["dataset_id"] == "fintech_ds"
    assert data["project_id"] == "test-p"
    assert data["table_count"] == 2
    assert "coverage" in data
    assert data["coverage"]["total_columns"] == 5

    # Check snapshot file was created
    snapshot_path = Path(data["snapshot_path"])
    assert snapshot_path.exists()
    snapshot_json = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert "dim_customers" in snapshot_json["tables"]
    assert "fct_transactions" in snapshot_json["tables"]

    # Check state was updated
    state = read_state(isolated_cwd)
    assert state.get("catalog_coverage_pct") is not None
    assert state.get("catalog_profile") is not None

    # Check next_actions transitions to Gate 5 with --catalog
    assert len(payload["next_actions"]) >= 1
    assert payload["next_actions"][0]["gate"] == 5
    assert "--catalog" in payload["next_actions"][0]["command"]


def test_catalog_inspect_human_output(invoke, fake_bigquery, fake_catalog) -> None:
    """`catalog inspect` human mode renders Rich tables and panels."""
    fake_bigquery.datasets = {"analytics": ["dim_users"]}
    fake_bigquery.tables = {
        "dim_users": FakeTable(
            "dim_users",
            [FakeTableSchemaField("user_id", "STRING", "REQUIRED", description="User ID")],
            num_rows=100,
        )
    }

    res = invoke(
        [
            "catalog",
            "inspect",
            "--dataset",
            "analytics",
            "--gcp-project",
            "test-p",
        ]
    )
    assert res.exit_code == 0
    assert "Knowledge Catalog Snapshot: test-p.analytics" in res.output
    assert "dim_users" in res.output
    assert "Metadata Enrichment Coverage" in res.output


def test_catalog_inspect_missing_options_and_errors(invoke, fake_bigquery) -> None:
    """`catalog inspect` raises ConfigError when dataset is missing or does not exist."""
    # Missing --gcp-project and no state
    res_no_proj = invoke(["catalog", "inspect", "--dataset", "my_ds", "--json"])
    assert res_no_proj.exit_code == ConfigError.exit_code

    # Missing --dataset and no state
    res_no_ds = invoke(["catalog", "inspect", "--gcp-project", "my_proj", "--json"])
    assert res_no_ds.exit_code == ConfigError.exit_code

    # Dataset not found in BigQuery
    fake_bigquery.datasets = {}
    res_not_found = invoke(
        [
            "catalog",
            "inspect",
            "--dataset",
            "nonexistent_ds",
            "--gcp-project",
            "test-p",
            "--json",
        ]
    )
    assert res_not_found.exit_code == ConfigError.exit_code
    assert "was not found in project" in envelope(res_not_found)["errors"][0]["message"]


# ===========================================================================
# catalog profiles
# ===========================================================================


def test_catalog_profiles_json_and_human(invoke) -> None:
    """`catalog profiles` returns mapping tiers and metadata attributes."""
    # JSON mode
    res_json = invoke(["catalog", "profiles", "--json"])
    assert res_json.exit_code == 0
    data = envelope(res_json)["data"]
    assert "rich" in data["profiles"]
    assert "hybrid" in data["profiles"]
    assert "minimal" in data["profiles"]
    assert len(data["mappings"]) > 0

    # Human mode
    res_human = invoke(["catalog", "profiles"])
    assert res_human.exit_code == 0
    assert "Knowledge Catalog -> LookML Mapping Profiles" in res_human.output
    assert "RICH" in res_human.output
    assert "HYBRID" in res_human.output
    assert "MINIMAL" in res_human.output


# ===========================================================================
# catalog seed
# ===========================================================================


def test_catalog_seed_plan_and_execute(invoke, fake_bigquery) -> None:
    """`catalog seed` handles both --mode plan and --mode execute."""
    fake_bigquery.datasets = {"seed_ds": ["dim_accounts", "fct_orders"]}
    fake_bigquery.tables = {
        "dim_accounts": FakeTable("dim_accounts", []),
        "fct_orders": FakeTable("fct_orders", []),
    }

    # Plan mode (JSON)
    res_plan = invoke(
        [
            "catalog",
            "seed",
            "--dataset",
            "seed_ds",
            "--gcp-project",
            "test-p",
            "--mode",
            "plan",
            "--json",
        ]
    )
    assert res_plan.exit_code == 0
    plan_data = envelope(res_plan)["data"]
    assert plan_data["status"] == "PLANNED"
    assert plan_data["tables_seeded"] == 2

    # Execute mode (human)
    res_exec = invoke(
        [
            "catalog",
            "seed",
            "--dataset",
            "seed_ds",
            "--gcp-project",
            "test-p",
            "--mode",
            "execute",
        ]
    )
    assert res_exec.exit_code == 0
    assert "metadata verified/seeded" in res_exec.output


def test_catalog_seed_dataset_not_found(invoke, fake_bigquery) -> None:
    """`catalog seed` fails with ConfigError if dataset does not exist or has no tables."""
    fake_bigquery.datasets = {}
    res = invoke(
        [
            "catalog",
            "seed",
            "--dataset",
            "missing_ds",
            "--gcp-project",
            "test-p",
            "--json",
        ]
    )
    assert res.exit_code == ConfigError.exit_code

    fake_bigquery.datasets = {"empty_ds": []}
    res_empty = invoke(
        [
            "catalog",
            "seed",
            "--dataset",
            "empty_ds",
            "--gcp-project",
            "test-p",
            "--json",
        ]
    )
    assert res_empty.exit_code == ConfigError.exit_code
    assert "no tables" in envelope(res_empty)["errors"][0]["message"]


# ===========================================================================
# data adopt
# ===========================================================================


def test_data_adopt_happy_path(invoke, fake_bigquery, isolated_cwd: Path) -> None:
    """`data adopt` marks dataset as existing, advances past Gate 1, and records state."""
    fake_bigquery.datasets = {"production_dw": ["customers", "orders", "line_items"]}
    fake_bigquery.tables = {
        "customers": FakeTable("customers", []),
        "orders": FakeTable("orders", []),
        "line_items": FakeTable("line_items", []),
    }

    res = invoke(
        [
            "data",
            "adopt",
            "--dataset",
            "production_dw",
            "--gcp-project",
            "dw-project",
            "--json",
        ]
    )
    assert res.exit_code == 0
    payload = envelope(res)
    assert payload["command"] == "data adopt"
    assert payload["data"]["dataset"] == "production_dw"
    assert payload["data"]["table_count"] == 3
    assert payload["data"]["data_source_mode"] == "existing"

    state = read_state(isolated_cwd)
    assert state["dataset_exists"] is True
    assert state["bq_dataset_id"] == "production_dw"
    assert state["gcp_project_id"] == "dw-project"
    assert state["data_source_mode"] == "existing"
    assert state["existing_tables"] == ["customers", "orders", "line_items"]

    # Verify next_actions has Gate 5 and optional catalog inspect step
    assert len(payload["next_actions"]) == 2
    assert payload["next_actions"][0]["gate"] == 5
    assert payload["next_actions"][1]["description"] == "Optional: Inspect Knowledge Catalog (Dataplex) metadata"
    assert "catalog inspect --dataset production_dw" in payload["next_actions"][1]["command"]


def test_data_adopt_with_table_filter(invoke, fake_bigquery, isolated_cwd: Path) -> None:
    """`data adopt --tables` restricts adoption to a subset of tables."""
    fake_bigquery.datasets = {"mart": ["dim_a", "dim_b", "fct_c"]}
    fake_bigquery.tables = {
        "dim_a": FakeTable("dim_a", []),
        "dim_b": FakeTable("dim_b", []),
        "fct_c": FakeTable("fct_c", []),
    }

    res = invoke(
        [
            "data",
            "adopt",
            "--dataset",
            "mart",
            "--gcp-project",
            "dw-project",
            "--tables",
            "dim_a,fct_c",
            "--json",
        ]
    )
    assert res.exit_code == 0
    state = read_state(isolated_cwd)
    assert state["existing_tables"] == ["dim_a", "fct_c"]


def test_data_adopt_errors(invoke, fake_bigquery) -> None:
    """`data adopt` fails when dataset is missing or empty."""
    fake_bigquery.datasets = {}
    res = invoke(
        [
            "data",
            "adopt",
            "--dataset",
            "nonexistent",
            "--gcp-project",
            "p1",
            "--json",
        ]
    )
    assert res.exit_code == ConfigError.exit_code

    fake_bigquery.datasets = {"empty_ds": []}
    res_empty = invoke(
        [
            "data",
            "adopt",
            "--dataset",
            "empty_ds",
            "--gcp-project",
            "p1",
            "--json",
        ]
    )
    assert res_empty.exit_code == ConfigError.exit_code


# ===========================================================================
# confirm-targets with --dataset
# ===========================================================================


def test_confirm_targets_with_existing_dataset(invoke, fake_bigquery, isolated_cwd: Path) -> None:
    """`confirm-targets --dataset` validates and adopts existing dataset at Gate 0B."""
    # Pre-seed state so precheck_passed is True
    initial_state = FlowState(precheck_passed=True)
    save_flow_state(initial_state, isolated_cwd / ".demo-state.json")

    fake_bigquery.datasets = {"existing_ds": ["t1", "t2"]}
    fake_bigquery.tables = {"t1": FakeTable("t1", []), "t2": FakeTable("t2", [])}

    res = invoke(
        [
            "confirm-targets",
            "--gcp-account",
            "user@example.com",
            "--gcp-project",
            "my-gcp-proj",
            "--looker-account",
            "dev",
            "--connection",
            "bigquery-conn",
            "--dataset",
            "existing_ds",
            "--json",
        ]
    )
    assert res.exit_code == 0
    state = read_state(isolated_cwd)
    assert state["targets_confirmed"] is True
    assert state["dataset_exists"] is True
    assert state["bq_dataset_id"] == "existing_ds"
    assert state["data_source_mode"] == "existing"
    assert state["existing_tables"] == ["t1", "t2"]


def test_confirm_targets_with_nonexistent_dataset(invoke, fake_bigquery, isolated_cwd: Path) -> None:
    """`confirm-targets --dataset` errors if the dataset does not exist."""
    initial_state = FlowState(precheck_passed=True)
    save_flow_state(initial_state, isolated_cwd / ".demo-state.json")

    fake_bigquery.datasets = {}
    res = invoke(
        [
            "confirm-targets",
            "--gcp-account",
            "user@example.com",
            "--gcp-project",
            "my-gcp-proj",
            "--looker-account",
            "dev",
            "--connection",
            "bigquery-conn",
            "--dataset",
            "ghost_ds",
            "--json",
        ]
    )
    assert res.exit_code == ConfigError.exit_code
