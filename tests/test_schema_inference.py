"""Unit tests for `looker_demo_cli.schema_inference`."""

from __future__ import annotations

import pytest

from looker_demo_cli.schema_inference import (
    infer_foreign_keys,
    infer_table_specs,
)

pytestmark = pytest.mark.unit


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
