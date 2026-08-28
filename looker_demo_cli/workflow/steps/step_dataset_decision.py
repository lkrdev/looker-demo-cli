from __future__ import annotations

from typing import Tuple
from looker_demo_cli.utils.bigquery_client import BigQueryHelper
from looker_demo_cli.utils.console import print_info, print_step_header, print_success, print_warning
from looker_demo_cli.workflow.state import FlowState


def run_dataset_decision_step(state: FlowState, bq_helper: BigQueryHelper) -> FlowState:
    """Step 1: Inspect BigQuery and determine dataset intent."""
    print_step_header(1, state.total_steps, "Dataset & Intent Evaluation")

    exists = bq_helper.dataset_exists(state.bq_dataset_id)
    state.dataset_exists = exists

    if exists:
        tables = bq_helper.list_tables(state.bq_dataset_id)
        state.existing_tables = tables
        print_success(f"Found existing BigQuery dataset `{state.gcp_project_id}.{state.bq_dataset_id}` with {len(tables)} tables: {tables}")
    else:
        print_info(f"Dataset `{state.gcp_project_id}.{state.bq_dataset_id}` does not exist yet. Will create via synthetic generation.")
        state.action_intent = "create_new_dataset"

    return state
