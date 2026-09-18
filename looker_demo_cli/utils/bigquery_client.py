from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import google.auth
import pandas as pd
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from looker_demo_cli.config import DEFAULT_GCP_PROJECT
from looker_demo_cli.utils.console import print_warning

os.environ["CLOUDSDK_CONTEXT_AWARE_USE_CLIENT_CERTIFICATE"] = "false"
os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"


class BigQueryOptimizationAdvisor:
    """Dataset-agnostic heuristic engine that recommends BigQuery Day Partitioning and Clustering."""

    @staticmethod
    def _extract_columns_and_types(source: pd.DataFrame | Path | None) -> tuple[list[str], set[str]]:
        """Extract column list and set of temporal (date/timestamp) column names from DataFrame or Parquet."""
        if source is None:
            return [], set()

        if isinstance(source, pd.DataFrame):
            cols = [str(c) for c in source.columns]
            temporal_cols: set[str] = set()
            for col in source.columns:
                col_str = str(col)
                dtype_str = str(source[col].dtype).lower()
                if (
                    pd.api.types.is_datetime64_any_dtype(source[col])
                    or "date" in dtype_str
                    or "timestamp" in dtype_str
                    or col_str.lower().endswith(("_date", "_time", "_at", "_timestamp"))
                ):
                    temporal_cols.add(col_str)
            return cols, temporal_cols

        if isinstance(source, Path) and source.exists():
            try:
                import pyarrow.parquet as pq

                schema = pq.read_schema(source)
                cols = list(schema.names)
                temporal_cols = set()
                for name, pa_type in zip(schema.names, schema.types):
                    t_str = str(pa_type).lower()
                    if (
                        "timestamp" in t_str
                        or "date" in t_str
                        or name.lower().endswith(("_date", "_time", "_at", "_timestamp"))
                    ):
                        temporal_cols.add(name)
                return cols, temporal_cols
            except Exception:
                return [], set()

        return [], set()

    @staticmethod
    def infer_partition_field(table_name: str, source: pd.DataFrame | Path | None) -> str | None:
        """Heuristic: Identifies primary event timestamp or date column for fact tables."""
        t_lower = table_name.lower()
        is_fact = (
            t_lower.startswith(("fct_", "fact_"))
            or any(marker in t_lower for marker in ("event", "session", "interaction", "transaction", "order", "log"))
        )
        if not is_fact:
            return None

        cols, temporal_cols = BigQueryOptimizationAdvisor._extract_columns_and_types(source)
        if not temporal_cols:
            return None

        preferred_order = [
            "timestamp",
            "event_time",
            "created_at",
            "session_start_time",
            "order_date",
            "event_date",
            "date",
            "created_date",
        ]
        for pref in preferred_order:
            if pref in temporal_cols:
                return pref

        # Preserve column order when picking the first temporal candidate
        for col in cols:
            if col in temporal_cols:
                return col
        return None

    @staticmethod
    def infer_cluster_fields(
        table_name: str,
        source: pd.DataFrame | Path | None,
        partition_field: str | None = None,
    ) -> list[str]:
        """Heuristic: Selects high-cardinality foreign keys and categorical filter dimensions (up to 4 cols)."""
        cols, _ = BigQueryOptimizationAdvisor._extract_columns_and_types(source)
        candidates: list[str] = []
        for col in cols:
            if col == partition_field:
                continue
            col_lower = col.lower()
            if (
                col_lower.endswith("_id")
                or col_lower.endswith("_type")
                or col_lower.endswith("_status")
                or col_lower in ("category", "tier", "region", "environment", "status", "channel", "segment")
            ):
                candidates.append(col)
        return candidates[:4]


class BigQueryHelper:
    """Manages BigQuery datasets, resilient table loading via ADC, and automatic query optimization."""

    def __init__(self, project_id: str = DEFAULT_GCP_PROJECT, credentials: Any = None, location: str = "US"):
        self.project_id = project_id
        self.location = location
        self.credentials = credentials
        if not self.credentials:
            self.credentials, default_project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            if not self.project_id and default_project:
                self.project_id = default_project
        self.client = bigquery.Client(project=self.project_id, credentials=self.credentials, location=self.location)

    def dataset_exists(self, dataset_id: str) -> bool:
        """Check if a dataset exists in BigQuery."""
        dataset_ref = self.client.dataset(dataset_id)
        try:
            self.client.get_dataset(dataset_ref)
            return True
        except NotFound:
            return False
        except Exception as e:
            print_warning(f"Notice while checking dataset `{dataset_id}`: {e}")
            return False

    def list_tables(self, dataset_id: str) -> list[str]:
        """List all table IDs in a dataset."""
        dataset_ref = self.client.dataset(dataset_id)
        try:
            tables = list(self.client.list_tables(dataset_ref))
            return [t.table_id for t in tables]
        except Exception as e:
            print_warning(f"Could not list tables in `{dataset_id}`: {e}")
            return []

    def get_table_row_count(self, dataset_id: str, table_name: str) -> int | None:
        """Return existing table row count if table exists in BigQuery, else None."""
        try:
            table_ref = self.client.dataset(dataset_id).table(table_name)
            tbl = self.client.get_table(table_ref)
            return int(tbl.num_rows or 0)
        except Exception:
            return None

    def ensure_dataset(
        self, dataset_id: str, description: str = "Demo Dataset created by demo-create"
    ) -> bigquery.Dataset:
        """Ensure dataset exists; create if missing."""
        dataset_ref = self.client.dataset(dataset_id)
        try:
            return self.client.get_dataset(dataset_ref)
        except NotFound:
            dataset = bigquery.Dataset(dataset_ref)
            dataset.location = self.location
            dataset.description = description
            return self.client.create_dataset(dataset)

    def load_parquet_table_optimized(
        self,
        dataset_id: str,
        table_name: str,
        parquet_file: Path,
        df_sample: pd.DataFrame | None = None,
        partition_field: str | None = None,
        clustering_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Load a Parquet table with automated heuristic Day Partitioning and Clustering."""
        if not parquet_file.exists():
            raise FileNotFoundError(f"Parquet file {parquet_file} does not exist.")

        self.ensure_dataset(dataset_id)
        table_ref = self.client.dataset(dataset_id).table(table_name)

        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            autodetect=True,
        )

        source_for_advisor = df_sample if df_sample is not None else parquet_file
        part_col = partition_field or BigQueryOptimizationAdvisor.infer_partition_field(
            table_name, source_for_advisor
        )
        cluster_cols = clustering_fields or BigQueryOptimizationAdvisor.infer_cluster_fields(
            table_name, source_for_advisor, part_col
        )

        if part_col:
            job_config.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field=part_col,
            )
        if cluster_cols:
            job_config.clustering_fields = cluster_cols

        with open(parquet_file, "rb") as source_file:
            job = self.client.load_table_from_file(source_file, table_ref, job_config=job_config)

        job.result()  # Wait for load job to complete
        dest_table = self.client.get_table(table_ref)

        return {
            "table_name": table_name,
            "rows": int(dest_table.num_rows or 0),
            "partition_field": part_col,
            "clustering_fields": cluster_cols or [],
        }

    def load_parquet_table(
        self,
        dataset_id: str,
        table_name: str,
        parquet_file: Path,
        clustering_fields: list[str] | None = None,
        partition_field: str | None = None,
    ) -> int:
        """Load a single Parquet file into a BigQuery table with automatic partitioning and clustering."""
        res = self.load_parquet_table_optimized(
            dataset_id=dataset_id,
            table_name=table_name,
            parquet_file=parquet_file,
            partition_field=partition_field,
            clustering_fields=clustering_fields,
        )
        return res["rows"]
