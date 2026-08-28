# SPDX-FileCopyrightText: Copyright (c) 2026 lkr.dev. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import google.auth
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from looker_demo_cli.utils.console import print_error, print_info, print_success, print_warning

os.environ["CLOUDSDK_CONTEXT_AWARE_USE_CLIENT_CERTIFICATE"] = "false"
os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"


class BigQueryHelper:
    def __init__(self, project_id: str = "looker-demo-392616", credentials: Any = None, location: str = "US"):
        self.project_id = project_id
        self.location = location
        self.credentials = credentials
        if not self.credentials:
            self.credentials, _ = google.auth.default()
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

    def list_tables(self, dataset_id: str) -> List[str]:
        """List all table IDs in a dataset."""
        dataset_ref = self.client.dataset(dataset_id)
        try:
            tables = list(self.client.list_tables(dataset_ref))
            return [t.table_id for t in tables]
        except Exception as e:
            print_warning(f"Could not list tables in `{dataset_id}`: {e}")
            return []

    def ensure_dataset(self, dataset_id: str, description: str = "Demo Dataset created by demo-create") -> bigquery.Dataset:
        """Ensure dataset exists; create if missing."""
        dataset_ref = self.client.dataset(dataset_id)
        try:
            return self.client.get_dataset(dataset_ref)
        except NotFound:
            dataset = bigquery.Dataset(dataset_ref)
            dataset.location = self.location
            dataset.description = description
            return self.client.create_dataset(dataset)

    def load_parquet_table(
        self,
        dataset_id: str,
        table_name: str,
        parquet_file: Path,
        clustering_fields: Optional[List[str]] = None,
        partition_field: Optional[str] = None,
    ) -> int:
        """Load a single Parquet file into a BigQuery table with optional partitioning and clustering."""
        if not parquet_file.exists():
            raise FileNotFoundError(f"Parquet file {parquet_file} does not exist.")

        self.ensure_dataset(dataset_id)
        table_ref = self.client.dataset(dataset_id).table(table_name)

        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            autodetect=True,
        )

        if clustering_fields:
            job_config.clustering_fields = clustering_fields

        if partition_field:
            job_config.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field=partition_field,
            )

        with open(parquet_file, "rb") as f:
            load_job = self.client.load_table_from_file(f, table_ref, job_config=job_config)

        load_job.result()  # Wait for completion
        dest_table = self.client.get_table(table_ref)
        return dest_table.num_rows
