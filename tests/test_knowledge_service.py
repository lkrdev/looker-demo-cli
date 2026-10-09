"""Unit tests for BigQuery native introspection and enhanced LookML generator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from looker_demo_cli.generators.lookml_generator import LookMLGenerator, LookMLTableSpec
from looker_demo_cli.services.knowledge_service import introspect_bq_table_specs

pytestmark = pytest.mark.unit


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


class MockSchemaField:
    def __init__(self, name: str, field_type: str, description: str | None = None):
        self.name = name
        self.field_type = field_type
        self.description = description


class MockTimePartitioning:
    def __init__(self, field: str):
        self.field = field


def test_introspect_bq_table_specs_with_constraints_and_metadata():
    """Verify BQ introspection parses ColumnReferences, PKs, partition, and descriptions."""
    mock_client = MagicMock()

    # Table 1: dim_merchants
    merchants_tbl = MagicMock()
    merchants_tbl.table_id = "dim_merchants"
    merchants_tbl.description = "Merchant directory table"
    merchants_tbl.schema = [
        MockSchemaField("merchant_id", "STRING", "Unique merchant identifier"),
        MockSchemaField("name", "STRING", "Legal merchant entity name"),
    ]
    merchants_tbl.table_constraints = MockTableConstraints(
        pk_columns=["merchant_id"],
        fks=[],
    )
    merchants_tbl.time_partitioning = None
    merchants_tbl.range_partitioning = None
    merchants_tbl.clustering_fields = ["name"]

    # Table 2: fct_transactions
    tx_tbl = MagicMock()
    tx_tbl.table_id = "fct_transactions"
    tx_tbl.description = "Ledger of financial transactions"
    tx_tbl.schema = [
        MockSchemaField("transaction_id", "STRING", "PK of transaction"),
        MockSchemaField("merchant_id", "STRING", "FK to merchant"),
        MockSchemaField("amount", "FLOAT", "Monetary amount in USD"),
        MockSchemaField("item_count", "INTEGER", "Number of line items"),
        MockSchemaField("is_flagged", "BOOLEAN", "Fraud flag"),
        MockSchemaField("created_at", "TIMESTAMP", "Transaction timestamp"),
    ]
    col_ref = MockColumnReference("merchant_id", "merchant_id")
    tx_tbl.table_constraints = MockTableConstraints(
        pk_columns=["transaction_id"],
        fks=[MockForeignKey("dim_merchants", [col_ref])],
    )
    tx_tbl.time_partitioning = MockTimePartitioning("created_at")
    tx_tbl.range_partitioning = None
    tx_tbl.clustering_fields = ["merchant_id"]

    mock_client.list_tables.return_value = [
        MagicMock(table_id="dim_merchants"),
        MagicMock(table_id="fct_transactions"),
    ]
    mock_client.get_table.side_effect = lambda ref: merchants_tbl if "dim_merchants" in str(ref) else tx_tbl

    with (
        patch(
            "looker_demo_cli.services.knowledge_service.google.auth.default", return_value=(MagicMock(), "test-proj")
        ),
        patch("google.cloud.bigquery.Client", return_value=mock_client),
    ):
        specs = introspect_bq_table_specs("test-proj", "test-ds")

    assert len(specs) == 2
    merchants_spec = next(s for s in specs if s.table_name == "dim_merchants")
    tx_spec = next(s for s in specs if s.table_name == "fct_transactions")

    # Merchants checks
    assert merchants_spec.table_type == "dimension"
    assert merchants_spec.primary_key == "merchant_id"
    assert merchants_spec.description == "Merchant directory table"
    assert merchants_spec.clustering_fields == ["name"]
    assert merchants_spec.column_descriptions["merchant_id"] == "Unique merchant identifier"

    # Transactions checks
    assert tx_spec.table_type == "fact"
    assert tx_spec.primary_key == "transaction_id"
    assert tx_spec.description == "Ledger of financial transactions"
    assert tx_spec.partition_field == "created_at"
    assert tx_spec.clustering_fields == ["merchant_id"]

    # Foreign key was correctly resolved via column_references
    assert tx_spec.foreign_keys == {"merchant_id": "dim_merchants.merchant_id"}

    # Type normalization checks: FLOAT -> FLOAT64, INTEGER -> INT64, BOOLEAN -> BOOL
    assert tx_spec.schema_fields["amount"] == "FLOAT64"
    assert tx_spec.schema_fields["item_count"] == "INT64"
    assert tx_spec.schema_fields["is_flagged"] == "BOOL"


def test_lookml_generator_with_rich_metadata():
    """Verify LookMLGenerator generates views, models, and dashboards honoring rich metadata."""
    tx_spec = LookMLTableSpec(
        table_name="fct_transactions",
        table_type="fact",
        schema_fields={
            "transaction_id": "STRING",
            "merchant_id": "STRING",
            "status": "STRING",
            "amount": "FLOAT64",
            "item_count": "INT64",
            "created_at": "TIMESTAMP",
        },
        primary_key="transaction_id",
        foreign_keys={"merchant_id": "dim_merchants.merchant_id"},
        description="Core financial ledger table",
        business_label="Financial Transactions",
        column_descriptions={
            "amount": "Transaction amount in USD after currency conversion.",
            "merchant_id": "Reference to merchant profile.",
        },
        column_labels={
            "amount": "Gross Transaction Volume (USD)",
        },
        column_formats={
            "amount": "usd_0",
        },
        column_synonyms={
            "amount": ["GTV", "Transaction Amount"],
            "status": ["Payment State"],
        },
        column_allowed_values={
            "status": ["COMPLETED", "PENDING", "DECLINED"],
        },
        partition_field="created_at",
    )

    merchants_spec = LookMLTableSpec(
        table_name="dim_merchants",
        table_type="dimension",
        schema_fields={
            "merchant_id": "STRING",
            "merchant_name": "STRING",
        },
        primary_key="merchant_id",
        description="Merchant registry",
    )

    gen = LookMLGenerator("my-gcp-project", "fintech_ds", "bq_conn")

    # 1. View Generation
    view_lkml = gen.generate_view_lkml(tx_spec)
    assert 'label: "Financial Transactions"' in view_lkml
    assert 'description: "Core financial ledger table"' in view_lkml
    assert 'label: "Gross Transaction Volume (USD)"' in view_lkml
    assert 'description: "Transaction amount in USD after currency conversion."' in view_lkml
    assert 'tags: ["GTV", "Transaction Amount"]' in view_lkml
    assert 'tags: ["Payment State"]' in view_lkml
    assert 'suggestions: ["COMPLETED", "PENDING", "DECLINED"]' in view_lkml
    assert "measure: total_amount {" in view_lkml
    assert "value_format_name: usd_0" in view_lkml

    # 2. Model Generation
    model_lkml = gen.generate_model_lkml("fintech_model", [tx_spec, merchants_spec])
    assert "explore: fct_transactions {" in model_lkml
    assert 'filters: [fct_transactions.created_at_date: "365 days"]' in model_lkml
    assert "join: dim_merchants {" in model_lkml
    assert "${fct_transactions.merchant_id} = ${dim_merchants.merchant_id}" in model_lkml

    # 3. Dashboard Generation
    dash_lkml = gen.generate_default_dashboard_lkml("fintech_model", [tx_spec, merchants_spec])
    assert "fct_transactions.total_amount" in dash_lkml
    assert "fct_transactions.average_item_count" in dash_lkml
