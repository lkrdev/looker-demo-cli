from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from looker_demo_cli.utils.console import print_info, print_step_header, print_success
from looker_demo_cli.workflow.state import FlowState


from looker_demo_cli.generators.schema_generator import generate_domain_dataset


def run_schema_synthesis_step(state: FlowState, scratch_dir: Path) -> FlowState:
    """Step 2: Synthesize schema and generate micro-sample or scaled dataset."""
    print_step_header(2, state.total_steps, "Schema Synthesis & Data Generation")

    output_dir = scratch_dir / state.bq_dataset_id
    output_dir.mkdir(parents=True, exist_ok=True)
    state.generated_parquet_dir = output_dir

    print_info(f"Target data generation output directory: `{output_dir}`")
    if not state.dataset_exists or not state.existing_tables:
        print_info(f"Synthesizing high-fidelity domain dataset for `{state.looker_project_name}`...")
        specs = generate_domain_dataset(state.looker_project_name, output_dir)
        state.generated_tables = [s.table_name for s in specs]
        print_success(f"Generated {len(specs)} synthetic tables: {state.generated_tables}")

    return state
