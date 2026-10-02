"""Unit tests for the vectorized in-memory TableValidator and ValidationReport."""

from __future__ import annotations

import pandas as pd
import pytest

from looker_demo_cli.generators.schema_generator import DomainBlueprint, EntityFieldSpec, EntitySchemaSpec
from looker_demo_cli.generators.validator import TableValidator


def test_validate_table_passes_valid_pk_and_fk():
    parent_spec = EntitySchemaSpec(
        table_name="dim_customers",
        table_type="dimension",
        primary_key="customer_id",
        fields=[EntityFieldSpec(name="customer_id", type="STRING", is_primary_key=True)],
    )
    child_spec = EntitySchemaSpec(
        table_name="fct_orders",
        table_type="fact",
        primary_key="order_id",
        foreign_keys={"customer_id": "dim_customers.customer_id"},
        fields=[
            EntityFieldSpec(name="order_id", type="STRING", is_primary_key=True),
            EntityFieldSpec(name="customer_id", type="STRING", is_foreign_key=True),
        ],
    )

    df_parent = pd.DataFrame({"customer_id": ["C1", "C2", "C3"]})
    pk_pools = {"dim_customers": ["C1", "C2", "C3"]}

    parent_metrics = TableValidator.validate_table(df_parent, parent_spec, pk_pools)
    assert parent_metrics["pk_uniqueness"] == 1.0
    assert parent_metrics["orphan_fks"] == 0

    df_child = pd.DataFrame({"order_id": ["O1", "O2", "O3"], "customer_id": ["C1", "C2", "C1"]})
    child_metrics = TableValidator.validate_table(df_child, child_spec, pk_pools)
    assert child_metrics["pk_uniqueness"] == 1.0
    assert child_metrics["orphan_fks"] == 0


def test_validate_table_fails_on_duplicate_or_null_pk():
    spec = EntitySchemaSpec(
        table_name="dim_users",
        table_type="dimension",
        primary_key="user_id",
        fields=[EntityFieldSpec(name="user_id", type="STRING", is_primary_key=True)],
    )
    df_dup = pd.DataFrame({"user_id": ["U1", "U1", "U2"]})
    with pytest.raises(AssertionError, match="PK collisions detected"):
        TableValidator.validate_table(df_dup, spec, {})

    df_null = pd.DataFrame({"user_id": ["U1", None, "U2"]})
    with pytest.raises(AssertionError, match="NULL values in PK column"):
        TableValidator.validate_table(df_null, spec, {})


def test_validate_table_fails_on_orphan_foreign_keys():
    child_spec = EntitySchemaSpec(
        table_name="fct_orders",
        table_type="fact",
        primary_key="order_id",
        foreign_keys={"customer_id": "dim_customers.customer_id"},
    )
    pk_pools = {"dim_customers": ["C1", "C2"]}
    df_orphan = pd.DataFrame({"order_id": ["O1", "O2"], "customer_id": ["C1", "C999"]})

    with pytest.raises(AssertionError, match="orphan foreign keys"):
        TableValidator.validate_table(df_orphan, child_spec, pk_pools)


def test_audit_relational_dataset_detects_temporal_inversion():
    blueprint = DomainBlueprint(
        domain_name="test_domain",
        entities=[
            EntitySchemaSpec(
                table_name="fct_sessions",
                table_type="fact",
                primary_key="session_id",
            )
        ],
    )
    df_inverted = pd.DataFrame(
        {
            "session_id": ["S1", "S2"],
            "session_start_time": pd.to_datetime(["2026-01-02 10:00:00", "2026-01-02 12:00:00"]),
            "session_end_time": pd.to_datetime(["2026-01-02 11:00:00", "2026-01-02 11:30:00"]),
        }
    )
    report = TableValidator.audit_relational_dataset({"fct_sessions": df_inverted}, blueprint)
    assert not report.is_valid
    assert any("Temporal inversion" in err for err in report.errors)
    assert report.table_metrics["fct_sessions"]["temporal_valid"] is False
