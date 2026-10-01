"""Unit tests for `looker_demo_cli.schema_inference`."""

from __future__ import annotations

import pytest

from looker_demo_cli.schema_inference import (
    classify_table,
    infer_foreign_keys,
    infer_primary_key,
    infer_table_specs,
    normalize_column_type,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("arrow_type", "expected"),
    [
        ("int8", "INT64"),
        ("int64", "INT64"),
        ("uint64", "INT64"),
        ("INT64", "INT64"),
        ("float", "FLOAT64"),
        ("float64", "FLOAT64"),
        ("double", "FLOAT64"),
        ("bool", "BOOL"),
        ("timestamp[us]", "TIMESTAMP"),
        ("timestamp[us, tz=UTC]", "TIMESTAMP"),
        ("time64[ns]", "TIMESTAMP"),
        ("date32[day]", "TIMESTAMP"),
        ("datetime64[ns]", "TIMESTAMP"),
        ("string", "STRING"),
        ("large_string", "STRING"),
        ("binary", "STRING"),
    ],
)
def test_normalize_column_type(arrow_type: str, expected: str) -> None:
    """Arrow and BigQuery type strings map to canonical LookML/BigQuery types."""
    assert normalize_column_type(arrow_type) == expected


@pytest.mark.parametrize(
    ("table_name", "expected"),
    [
        ("fct_orders", "fact"),
        ("transactions", "fact"),
        ("security_alerts", "fact"),
        ("orders", "fact"),
        ("dim_users", "dimension"),
        ("users", "dimension"),
        ("products", "dimension"),
    ],
)
def test_classify_table(table_name: str, expected: str) -> None:
    """Fact vs. dimension table classification based on prefix and naming markers."""
    assert classify_table(table_name) == expected


@pytest.mark.parametrize(
    ("table_name", "columns", "expected"),
    [
        ("dim_users", ["user_id", "user_name"], "user_id"),
        ("fct_orders", ["order_id", "amount"], "order_id"),
        ("dim_users", ["id", "user_name"], "id"),
        ("dim_inventory", ["inventory_id", "qty"], "inventory_id"),
        ("dim_users", ["created_at", "user_key_id"], "user_key_id"),
        ("fct_alerts", ["created_at", "device_id", "user_id"], "device_id"),
        ("dim_regions", ["region_name", "country", "identifier"], None),
    ],
)
def test_infer_primary_key(table_name: str, columns: list[str], expected: str | None) -> None:
    """Primary key inference across exact entity match, stem prefix match, fallback _id, and None."""
    assert infer_primary_key(table_name, columns) == expected


def test_infer_foreign_keys_matches_plural_forms_and_excludes_pk() -> None:
    """Foreign key inference resolves singular/plural parent tables and excludes the table's own PK."""
    tables = ["fct_orders", "dim_orders", "dim_users", "dim_boxes", "dim_companies"]
    cols = ["order_id", "user_id", "box_id", "company_id"]

    fks = infer_foreign_keys("fct_orders", cols, "order_id", tables)
    assert fks == {
        "user_id": "dim_users.user_id",
        "box_id": "dim_boxes.box_id",
        "company_id": "dim_companies.company_id",
    }
    assert infer_foreign_keys("dim_users", ["id", "name"], "id", ["dim_users", "dim_ids"]) == {}


def test_infer_table_specs_end_to_end() -> None:
    """infer_table_specs normalizes types, classifies tables, and builds the star-schema join graph."""
    raw_schemas = {
        "dim_users": {"user_id": "int64", "user_name": "string", "signed_up_at": "timestamp[us]"},
        "dim_products": {"product_id": "int64", "product_name": "string", "list_price": "double"},
        "fct_orders": {
            "order_id": "int64",
            "user_id": "int64",
            "product_id": "int64",
            "amount": "double",
            "is_gift": "bool",
            "ordered_at": "timestamp[us]",
        },
    }

    users, products, orders = infer_table_specs(raw_schemas)

    assert users.table_type == "dimension"
    assert users.primary_key == "user_id"
    assert users.foreign_keys == {}
    assert users.schema_fields == {"user_id": "INT64", "user_name": "STRING", "signed_up_at": "TIMESTAMP"}

    assert products.table_type == "dimension"
    assert products.primary_key == "product_id"
    assert products.schema_fields["list_price"] == "FLOAT64"

    assert orders.table_type == "fact"
    assert orders.primary_key == "order_id"
    assert orders.foreign_keys == {"user_id": "dim_users.user_id", "product_id": "dim_products.product_id"}
    assert orders.schema_fields["is_gift"] == "BOOL"

    assert infer_table_specs({}) == []
