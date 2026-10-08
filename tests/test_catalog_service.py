"""Unit tests for catalog_service.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from looker_demo_cli.catalog.models import (
    CatalogSnapshot,
    ColumnMeta,
    TableMeta,
)
from looker_demo_cli.services.catalog_service import (
    build_catalog_snapshot,
    snapshot_to_table_specs,
)

pytestmark = pytest.mark.unit


class MockSchemaField:
    def __init__(self, name: str, field_type: str, mode: str = "NULLABLE", description: str | None = None):
        self.name = name
        self.field_type = field_type
        self.mode = mode
        self.description = description


class MockColumnReference:
    def __init__(self, referencing_column: str, referenced_column: str):
        self.referencing_column = referencing_column
        self.referenced_column = referenced_column


class MockForeignKey:
    def __init__(self, target_table: str, col_refs: list[MockColumnReference]):
        self.referenced_table = MagicMock()
        self.referenced_table.table_id = target_table
        self.column_references = col_refs


class MockPrimaryKey:
    def __init__(self, columns: list[str]):
        self.columns = columns


class MockTableConstraints:
    def __init__(self, pk_columns: list[str], fks: list[MockForeignKey]):
        self.primary_key = MockPrimaryKey(pk_columns)
        self.foreign_keys = fks


def test_build_catalog_snapshot_fused_metadata():
    """Verify build_catalog_snapshot aggregates BQ, aspects, entry links, and lookup context."""
    # 1. Mock BQ Client
    mock_bq = MagicMock()
    mock_bq.project_id = "test-proj"
    mock_bq.list_tables.return_value = ["dim_customers", "fct_orders"]

    # dim_customers table
    cust_tbl = MagicMock()
    cust_tbl.num_rows = 500
    cust_tbl.description = "Customers dimension"
    cust_tbl.clustering_fields = ["country"]
    cust_tbl.time_partitioning = None
    cust_tbl.range_partitioning = None
    cust_tbl.schema = [
        MockSchemaField("customer_id", "STRING", "REQUIRED", "Surrogate customer ID"),
        MockSchemaField("customer_tier", "STRING", "NULLABLE"),
        MockSchemaField("country", "STRING", "NULLABLE"),
    ]
    cust_tbl.table_constraints = MockTableConstraints(pk_columns=["customer_id"], fks=[])

    # fct_orders table
    ord_tbl = MagicMock()
    ord_tbl.num_rows = 2500
    ord_tbl.description = "Orders fact table"
    ord_tbl.clustering_fields = []
    ord_tbl.time_partitioning = MagicMock(field="order_date")
    ord_tbl.range_partitioning = None
    ord_tbl.schema = [
        MockSchemaField("order_id", "STRING", "REQUIRED", "PK of order"),
        MockSchemaField("customer_id", "STRING", "NULLABLE"),
        MockSchemaField("order_amount", "FLOAT", "NULLABLE"),
        MockSchemaField("order_date", "DATE", "NULLABLE"),
    ]
    col_ref = MockColumnReference("customer_id", "customer_id")
    ord_tbl.table_constraints = MockTableConstraints(
        pk_columns=["order_id"],
        fks=[MockForeignKey("dim_customers", [col_ref])],
    )

    mock_bq.get_table_metadata.side_effect = lambda ds, tbl: cust_tbl if tbl == "dim_customers" else ord_tbl

    # 2. Mock Catalog Client
    mock_catalog = MagicMock()
    mock_catalog.project_id = "test-proj"
    mock_catalog.location = "us"

    # Dataplex entries and aspects
    def lookup_entry_mock(dataset_id: str, table_id: str, view: str = "ALL"):
        if table_id == "dim_customers":
            return {
                "name": "projects/test-proj/locations/us/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/test-proj/datasets/test_ds/tables/dim_customers",
                "aspects": {
                    "test-proj.us.semantic-curation": {
                        "data": {
                            "business_label": "Customer Profiles",
                            "business_description": "Curated customer accounts and KYC status.",
                            "governance_tags": ["pii", "core_entity"],
                        }
                    },
                    "test-proj.us.semantic-curation@Schema.customer_tier": {
                        "data": {
                            "business_label": "Customer Loyalty Tier",
                            "allowed_values": ["BRONZE", "SILVER", "GOLD"],
                            "synonyms": ["Tier", "Level"],
                        }
                    },
                },
            }
        elif table_id == "fct_orders":
            return {
                "name": "projects/test-proj/locations/us/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/test-proj/datasets/test_ds/tables/fct_orders",
                "aspects": {
                    "test-proj.us.semantic-curation@Schema.order_amount": {
                        "data": {
                            "business_label": "Gross Order Amount (USD)",
                            "format_pattern": "usd_0",
                        }
                    }
                },
            }
        return {}

    mock_catalog.lookup_entry.side_effect = lookup_entry_mock

    # Dataplex entry links
    def lookup_entry_links_mock(entry_name: str, **kwargs):
        if "dim_customers" in entry_name:
            return [
                {
                    "entryLinkType": "definition",
                    "entryReferences": [
                        {"path": "Schema.customer_tier", "type": "SOURCE"},
                        {
                            "entry": "projects/test-proj/locations/us/entryGroups/@dataplex/entries/projects/test-proj/locations/us/glossaries/customer_glossary/terms/loyalty_tier",
                            "type": "TARGET",
                        },
                    ],
                }
            ]
        return []

    mock_catalog.lookup_entry_links.side_effect = lookup_entry_links_mock
    mock_catalog.get_glossary_term.return_value = {
        "displayName": "Loyalty Tier",
        "description": "Customer tier defining perks and discount rates.",
    }

    # lookupContext joins
    mock_catalog.lookup_context.return_value = {
        "joins": {
            "dim_customers fct_orders": [
                {
                    "source": "//bigquery.googleapis.com/projects/test-proj/datasets/test_ds/tables/fct_orders",
                    "target": "//bigquery.googleapis.com/projects/test-proj/datasets/test_ds/tables/dim_customers",
                    "joinKeys": [{"sourceField": "customer_id", "targetField": "customer_id"}],
                }
            ]
        }
    }

    snapshot = build_catalog_snapshot(mock_bq, mock_catalog, "test_ds", location="us")

    # Assertions on snapshot
    assert snapshot.dataset_id == "test_ds"
    assert snapshot.project_id == "test-proj"
    assert len(snapshot.tables) == 2

    # Check dim_customers
    cust_meta = snapshot.tables["dim_customers"]
    assert cust_meta.business_label == "Customer Profiles"
    assert cust_meta.description == "Curated customer accounts and KYC status."
    assert cust_meta.primary_key == ["customer_id"]
    assert cust_meta.columns["customer_tier"].business_label == "Customer Loyalty Tier"
    assert cust_meta.columns["customer_tier"].allowed_values == ["BRONZE", "SILVER", "GOLD"]
    assert cust_meta.columns["customer_tier"].linked_glossary_term == "Loyalty Tier"
    assert cust_meta.columns["customer_tier"].description == "Customer tier defining perks and discount rates."

    # Check fct_orders
    ord_meta = snapshot.tables["fct_orders"]
    assert ord_meta.role == "fact"
    assert ord_meta.partition_field == "order_date"
    assert ord_meta.foreign_keys["customer_id"] == "dim_customers.customer_id"
    assert ord_meta.columns["order_amount"].data_type == "FLOAT64"
    assert ord_meta.columns["order_amount"].business_label == "Gross Order Amount (USD)"
    assert ord_meta.columns["order_amount"].format_pattern == "usd_0"

    # Coverage
    assert snapshot.coverage is not None
    assert snapshot.coverage.coverage_percentage > 50.0

    # Serialization test
    json_str = snapshot.to_json()
    reloaded = CatalogSnapshot.from_json(json_str)
    assert reloaded.dataset_id == "test_ds"
    assert reloaded.tables["dim_customers"].business_label == "Customer Profiles"

    # Convert to LookMLTableSpecs (default profile)
    specs = snapshot_to_table_specs(snapshot)
    assert len(specs) == 2
    ord_spec = next(s for s in specs if s.table_name == "fct_orders")
    assert ord_spec.column_labels["order_amount"] == "Gross Order Amount (USD)"
    assert ord_spec.column_formats["order_amount"] == "usd_0"
    assert ord_spec.partition_field == "order_date"


def test_snapshot_to_table_specs_profiles():
    """Verify rich, hybrid, and minimal mapping profiles filter and format table specs correctly."""
    col1 = ColumnMeta(
        name="customer_id",
        data_type="INT64",
        is_primary_key=True,
        description="Native BQ customer ID",
    )
    col2 = ColumnMeta(
        name="revenue",
        data_type="FLOAT64",
        business_label="Gross Revenue (USD)",
        description="Curated Dataplex aspect description",
        format_pattern="usd_0",
        synonyms=["rev", "sales"],
        allowed_values=[],
    )
    table = TableMeta(
        name="dim_metrics",
        role="dimension",
        primary_key=["customer_id"],
        business_label="Curated Metrics",
        description="Curated table description",
        columns={"customer_id": col1, "revenue": col2},
    )
    snapshot = CatalogSnapshot(
        dataset_id="test_ds",
        project_id="test-proj",
        location="us",
        tables={"dim_metrics": table},
    )

    # 1. Rich profile: preserves all curated labels, formats, synonyms
    specs_rich = snapshot_to_table_specs(snapshot, profile="rich")
    spec_r = specs_rich[0]
    assert spec_r.business_label == "Curated Metrics"
    assert spec_r.column_labels["revenue"] == "Gross Revenue (USD)"
    assert spec_r.column_formats["revenue"] == "usd_0"
    assert spec_r.column_synonyms["revenue"] == ["rev", "sales"]
    assert spec_r.column_descriptions["revenue"] == "Curated Dataplex aspect description"

    # 2. Minimal profile: suppresses Dataplex aspect labels, formats, synonyms, business_label
    specs_minimal = snapshot_to_table_specs(snapshot, profile="minimal")
    spec_m = specs_minimal[0]
    assert spec_m.business_label is None
    assert spec_m.column_labels == {}
    assert spec_m.column_formats == {}
    assert spec_m.column_synonyms == {}
    assert spec_m.primary_keys == ["customer_id"]
    assert "customer_id" in spec_m.column_descriptions
