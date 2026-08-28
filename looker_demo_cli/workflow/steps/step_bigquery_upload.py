from __future__ import annotations

from pathlib import Path
from looker_demo_cli.utils.bigquery_client import BigQueryHelper
from looker_demo_cli.utils.console import print_error, print_info, print_step_header, print_success
from looker_demo_cli.workflow.state import FlowState


def run_bigquery_upload_step(state: FlowState, bq_helper: BigQueryHelper) -> FlowState:
    """Step 3: Upload Parquet files to BigQuery dataset."""
    print_step_header(3, state.total_steps, "BigQuery Dataset Upload")

    if not state.generated_parquet_dir or not state.generated_parquet_dir.exists():
        print_info("No new Parquet files to upload. Using existing BigQuery tables.")
        return state

    parquet_files = list(state.generated_parquet_dir.glob("*.parquet"))
    if not parquet_files:
        print_info("No parquet files found in generation folder.")
        return state

    print_info(f"Uploading {len(parquet_files)} tables to `{state.gcp_project_id}.{state.bq_dataset_id}`...")
    for p_file in parquet_files:
        table_name = p_file.stem
        try:
            num_rows = bq_helper.load_parquet_table(
                dataset_id=state.bq_dataset_id,
                table_name=table_name,
                parquet_file=p_file,
            )
            print_success(f"Loaded `{table_name}` ({num_rows:,} rows)")
            if table_name not in state.generated_tables:
                state.generated_tables.append(table_name)
        except Exception as e:
            print_error(f"Failed to load `{table_name}`: {e}")

    return state
