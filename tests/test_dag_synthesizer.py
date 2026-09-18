"""Unit and benchmark tests for ModularDAGSynthesizer."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from looker_demo_cli.generators.dag_synthesizer import ModularDAGSynthesizer
from looker_demo_cli.generators.schema_generator import (
    DomainBlueprint,
    EntityFieldSpec,
    EntitySchemaSpec,
    create_dynamic_blueprint_from_name,
)


def test_topological_sort_orders_dependencies_correctly():
    blueprint = DomainBlueprint(
        domain_name="supply_chain",
        entities=[
            EntitySchemaSpec(
                table_name="fct_order_items",
                table_type="fact",
                primary_key="item_id",
                foreign_keys={"order_id": "fct_orders.order_id", "product_id": "dim_products.product_id"},
            ),
            EntitySchemaSpec(
                table_name="fct_orders",
                table_type="fact",
                primary_key="order_id",
                foreign_keys={"customer_id": "dim_customers.customer_id"},
            ),
            EntitySchemaSpec(
                table_name="dim_customers",
                table_type="dimension",
                primary_key="customer_id",
                foreign_keys={"region_id": "dim_regions.region_id"},
            ),
            EntitySchemaSpec(
                table_name="dim_regions",
                table_type="dimension",
                primary_key="region_id",
            ),
            EntitySchemaSpec(
                table_name="dim_products",
                table_type="dimension",
                primary_key="product_id",
            ),
        ],
    )

    synth = ModularDAGSynthesizer()
    sorted_entities = synth._topological_sort(blueprint.entities)
    names = [e.table_name for e in sorted_entities]

    assert names.index("dim_regions") < names.index("dim_customers")
    assert names.index("dim_customers") < names.index("fct_orders")
    assert names.index("dim_products") < names.index("fct_order_items")
    assert names.index("fct_orders") < names.index("fct_order_items")


def test_modular_dag_synthesizer_15k_benchmark_and_invariants(tmp_path: Path):
    blueprint = create_dynamic_blueprint_from_name("telemetry_analytics")
    synth = ModularDAGSynthesizer(output_dir=tmp_path)

    result = synth.synthesize(blueprint=blueprint, target_fact_rows=15000, validate=True, write_parquet=True)

    # Throughput acceptance criteria: <3.0 seconds for 15,000 fact rows + dimensions
    assert result.duration_seconds < 3.0
    assert result.validation_report.is_valid
    assert result.total_rows == 16000  # 1,000 dim + 15,000 fact

    for table_name, p_path in result.parquet_paths.items():
        assert p_path.exists()
        metrics = result.validation_report.table_metrics[table_name]
        assert metrics["pk_uniqueness"] == 1.0
        assert metrics["orphan_fks"] == 0


def test_synthesize_from_script_executes_and_validates(tmp_path: Path):
    script_file = tmp_path / "custom_gen.py"
    script_file.write_text(
        """
import pandas as pd

def generate_tables(output_dir, row_count=100):
    df_dim = pd.DataFrame({
        "agent_id": [f"AGT-{i:03d}" for i in range(1, 11)],
        "agent_name": [f"Agent {i}" for i in range(1, 11)],
    })
    df_fct = pd.DataFrame({
        "session_id": [f"SES-{i:04d}" for i in range(1, row_count + 1)],
        "agent_id": [f"AGT-{(i % 10) + 1:03d}" for i in range(1, row_count + 1)],
        "session_start_time": pd.date_range("2026-01-01", periods=row_count, freq="min"),
        "session_end_time": pd.date_range("2026-01-01 00:05:00", periods=row_count, freq="min"),
    })
    return {"dim_agents": df_dim, "fct_sessions": df_fct}
"""
    )

    synth = ModularDAGSynthesizer(output_dir=tmp_path / "out")
    result = synth.synthesize_from_script(script_path=script_file, target_fact_rows=200, validate=True)

    assert result.validation_report.is_valid
    assert result.total_rows == 210
    assert "dim_agents" in result.parquet_paths
    assert "fct_sessions" in result.parquet_paths
