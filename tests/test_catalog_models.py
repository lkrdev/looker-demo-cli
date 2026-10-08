"""Unit tests for Knowledge Catalog data models."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from looker_demo_cli.catalog.models import (
    CatalogSnapshot,
    ColumnMeta,
    CoverageReport,
    Relationship,
    SourceStatus,
    TableMeta,
)


@pytest.mark.unit
def test_source_status_defaults_and_custom() -> None:
    status_default = SourceStatus(source_name="dataplex", available=True)
    assert status_default.source_name == "dataplex"
    assert status_default.available is True
    assert status_default.details == ""

    status_custom = SourceStatus(
        source_name="bigquery",
        available=False,
        details="Permission denied 403",
    )
    assert status_custom.details == "Permission denied 403"


@pytest.mark.unit
def test_column_meta_defaults_and_custom() -> None:
    col = ColumnMeta(name="user_id", data_type="INT64")
    assert col.name == "user_id"
    assert col.data_type == "INT64"
    assert col.mode == "NULLABLE"
    assert col.description is None
    assert col.business_label is None
    assert col.synonyms == []
    assert col.allowed_values == []
    assert col.format_pattern is None
    assert col.governance_tags == []
    assert col.policy_tags == []
    assert col.is_primary_key is False
    assert col.is_foreign_key is False
    assert col.foreign_key_target is None
    assert col.linked_glossary_term is None

    col_custom = ColumnMeta(
        name="email",
        data_type="STRING",
        mode="REQUIRED",
        description="User email address",
        business_label="Contact Email",
        synonyms=["mail", "user_email"],
        allowed_values=[],
        format_pattern=r"^[^@]+@[^@]+\.[^@]+$",
        governance_tags=["PII", "CONFIDENTIAL"],
        policy_tags=["projects/p/locations/us/taxonomies/t/policyTags/pt"],
        is_primary_key=False,
        is_foreign_key=False,
        linked_glossary_term="projects/p/locations/us/glossaries/g/terms/email",
    )
    assert col_custom.mode == "REQUIRED"
    assert col_custom.description == "User email address"
    assert col_custom.governance_tags == ["PII", "CONFIDENTIAL"]
    assert col_custom.linked_glossary_term is not None


@pytest.mark.unit
def test_table_meta_defaults_and_relationships() -> None:
    col_pk = ColumnMeta(name="order_id", data_type="INT64", is_primary_key=True)
    col_fk = ColumnMeta(
        name="customer_id",
        data_type="INT64",
        is_foreign_key=True,
        foreign_key_target="customers.id",
    )
    table = TableMeta(
        name="orders",
        role="fact",
        num_rows=10000,
        description="Order transaction records",
        business_label="Customer Orders",
        partition_field="order_date",
        clustering_fields=["customer_id", "status"],
        primary_key=["order_id"],
        foreign_keys={"customer_id": "customers.id"},
        columns={"order_id": col_pk, "customer_id": col_fk},
    )
    assert table.role == "fact"
    assert table.num_rows == 10000
    assert table.primary_key == ["order_id"]
    assert table.foreign_keys == {"customer_id": "customers.id"}
    assert len(table.columns) == 2


@pytest.mark.unit
def test_relationship_model() -> None:
    rel = Relationship(
        source_table="orders",
        source_column="customer_id",
        target_table="customers",
        target_column="id",
    )
    assert rel.relationship_type == "many_to_one"
    assert rel.confidence == 1.0
    assert rel.source_type == "TABLE_CONSTRAINTS"


@pytest.mark.unit
def test_coverage_report_computation() -> None:
    # 1. Empty tables -> 0 coverage, auto profile
    report_empty = CoverageReport.compute({})
    assert report_empty.total_columns == 0
    assert report_empty.coverage_percentage == 0.0
    assert report_empty.recommended_profile == "auto"

    # 2. Fully enriched tables -> rich profile (>= 70%)
    col1 = ColumnMeta(
        name="id",
        data_type="INT64",
        description="Primary identifier",
        business_label="ID",
        linked_glossary_term="glossaries/g/terms/id",
        format_pattern="#,##0",
    )
    col2 = ColumnMeta(
        name="amount",
        data_type="FLOAT64",
        description="Transaction amount in USD",
        business_label="Gross Amount",
        format_pattern="$#,##0.00",
    )
    table_rich = TableMeta(
        name="transactions",
        columns={"id": col1, "amount": col2},
    )
    report_rich = CoverageReport.compute({"transactions": table_rich})
    assert report_rich.total_columns == 2
    assert report_rich.columns_with_descriptions == 2
    assert report_rich.columns_with_labels == 2
    assert report_rich.columns_with_glossary == 1
    assert report_rich.columns_with_formats == 2
    assert report_rich.coverage_percentage == 100.0
    assert report_rich.recommended_profile == "rich"

    # 3. Partially enriched tables -> hybrid profile (>= 30% and < 70%)
    # 2 columns out of 4 have enrichment -> 50%
    col3 = ColumnMeta(name="raw_code", data_type="STRING")
    col4 = ColumnMeta(name="flag", data_type="BOOL")
    table_hybrid = TableMeta(
        name="data",
        columns={"id": col1, "amount": col2, "raw_code": col3, "flag": col4},
    )
    report_hybrid = CoverageReport.compute({"data": table_hybrid})
    assert report_hybrid.total_columns == 4
    assert report_hybrid.coverage_percentage == 50.0
    assert report_hybrid.recommended_profile == "hybrid"

    # 4. Low enrichment tables -> auto profile (< 30%)
    # 1 column out of 5 has enrichment -> 20%
    col5 = ColumnMeta(name="extra", data_type="STRING")
    table_low = TableMeta(
        name="low",
        columns={"id": col1, "c2": col3, "c3": col4, "c4": col5, "c5": ColumnMeta(name="c5", data_type="INT64")},
    )
    report_low = CoverageReport.compute({"low": table_low})
    assert report_low.total_columns == 5
    assert report_low.coverage_percentage == 20.0
    assert report_low.recommended_profile == "auto"


@pytest.mark.unit
def test_snapshot_serialization_and_deserialization() -> None:
    col = ColumnMeta(name="id", data_type="INT64", is_primary_key=True, description="Identifier")
    table = TableMeta(name="users", primary_key=["id"], columns={"id": col})
    rel = Relationship(
        source_table="orders",
        source_column="user_id",
        target_table="users",
        target_column="id",
    )
    source = SourceStatus(source_name="dataplex", available=True)

    snapshot = CatalogSnapshot(
        dataset_id="ecommerce_demo",
        project_id="test-project",
        location="us",
        created_at="2026-10-07T12:00:00Z",
        sources={"dataplex": source},
        tables={"users": table},
        relationships=[rel],
        raw_aspects={"users": {"schema": {"fields": []}}},
    )
    snapshot.compute_coverage()

    json_str = snapshot.to_json()
    assert isinstance(json_str, str)
    data = json.loads(json_str)
    assert data["dataset_id"] == "ecommerce_demo"
    assert data["project_id"] == "test-project"
    assert data["coverage"]["total_columns"] == 1
    assert data["coverage"]["columns_with_descriptions"] == 1

    restored = CatalogSnapshot.from_json(json_str)
    assert restored.dataset_id == snapshot.dataset_id
    assert restored.project_id == snapshot.project_id
    assert restored.location == snapshot.location
    assert restored.created_at == snapshot.created_at
    assert len(restored.tables) == 1
    assert restored.tables["users"].columns["id"].description == "Identifier"
    assert len(restored.relationships) == 1
    assert restored.coverage is not None
    assert restored.coverage.total_columns == 1


@pytest.mark.unit
def test_snapshot_save_and_load(tmp_path: Path) -> None:
    col = ColumnMeta(name="status", data_type="STRING", description="Order state")
    table = TableMeta(name="orders", columns={"status": col})
    snapshot = CatalogSnapshot(
        dataset_id="analytics",
        project_id="my-gcp-project",
        location="eu",
        tables={"orders": table},
    )
    snapshot.compute_coverage()

    save_path = tmp_path / "nested" / "catalog_snapshot.json"
    snapshot.save(save_path)
    assert save_path.exists()

    loaded = CatalogSnapshot.load(save_path)
    assert loaded.dataset_id == "analytics"
    assert loaded.location == "eu"
    assert "orders" in loaded.tables
    assert loaded.tables["orders"].columns["status"].description == "Order state"
    assert loaded.coverage is not None
    assert loaded.coverage.columns_with_descriptions == 1
