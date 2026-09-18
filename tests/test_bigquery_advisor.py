"""Unit tests for BigQueryOptimizationAdvisor and BigQueryHelper.load_parquet_table_optimized."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from looker_demo_cli.utils.bigquery_client import BigQueryHelper, BigQueryOptimizationAdvisor


def test_infer_partition_field_for_fact_vs_dimension():
    df_fact = pd.DataFrame(
        {
            "interaction_id": ["I1", "I2"],
            "agent_id": ["A1", "A2"],
            "event_time": pd.to_datetime(["2026-01-01 10:00:00", "2026-01-01 11:00:00"]),
            "status_code": ["200", "500"],
        }
    )
    assert BigQueryOptimizationAdvisor.infer_partition_field("fct_interactions", df_fact) == "event_time"
    assert BigQueryOptimizationAdvisor.infer_partition_field("dim_agents", df_fact) is None


def test_infer_cluster_fields_caps_at_four_and_excludes_partition_field():
    df = pd.DataFrame(
        {
            "event_id": ["E1"],
            "timestamp": pd.to_datetime(["2026-01-01"]),
            "customer_id": ["C1"],
            "agent_id": ["A1"],
            "interaction_type": ["chat"],
            "session_status": ["completed"],
            "region": ["NA"],
            "tier": ["Enterprise"],
            "amount_usd": [100.0],
        }
    )
    clusters = BigQueryOptimizationAdvisor.infer_cluster_fields("fct_events", df, partition_field="timestamp")
    assert len(clusters) == 4
    assert "timestamp" not in clusters
    assert clusters == ["event_id", "customer_id", "agent_id", "interaction_type"]


def test_infer_from_parquet_file_on_disk(tmp_path: Path):
    df = pd.DataFrame(
        {
            "session_id": ["S1", "S2"],
            "user_id": ["U1", "U2"],
            "session_start_time": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "category": ["Support", "Sales"],
        }
    )
    parquet_path = tmp_path / "fct_sessions.parquet"
    df.to_parquet(parquet_path, index=False)

    part_col = BigQueryOptimizationAdvisor.infer_partition_field("fct_sessions", parquet_path)
    assert part_col == "session_start_time"

    cluster_cols = BigQueryOptimizationAdvisor.infer_cluster_fields("fct_sessions", parquet_path, part_col)
    assert cluster_cols == ["session_id", "user_id", "category"]


@patch("looker_demo_cli.utils.bigquery_client.google.auth.default")
@patch("looker_demo_cli.utils.bigquery_client.bigquery.Client")
def test_load_parquet_table_optimized_applies_partition_and_cluster(
    mock_bq_client_cls, mock_auth_default, tmp_path: Path
):
    mock_auth_default.return_value = (MagicMock(), "test-project")
    mock_client = MagicMock()
    mock_bq_client_cls.return_value = mock_client

    dest_table_mock = MagicMock()
    dest_table_mock.num_rows = 42
    mock_client.get_table.return_value = dest_table_mock

    df = pd.DataFrame(
        {
            "order_id": ["O1", "O2"],
            "customer_id": ["C1", "C2"],
            "order_date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "order_status": ["Shipped", "Pending"],
        }
    )
    parquet_file = tmp_path / "fct_orders.parquet"
    df.to_parquet(parquet_file, index=False)

    helper = BigQueryHelper(project_id="test-project")
    result = helper.load_parquet_table_optimized("demo_ds", "fct_orders", parquet_file, df_sample=df)

    assert result["rows"] == 42
    assert result["partition_field"] == "order_date"
    assert result["clustering_fields"] == ["order_id", "customer_id", "order_status"]

    # Verify LoadJobConfig received time_partitioning and clustering_fields
    _, kwargs = mock_client.load_table_from_file.call_args
    job_config = kwargs["job_config"]
    assert job_config.time_partitioning.field == "order_date"
    assert job_config.clustering_fields == ["order_id", "customer_id", "order_status"]
