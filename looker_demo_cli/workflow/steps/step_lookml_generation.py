# SPDX-FileCopyrightText: Copyright (c) 2026 lkr.dev. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path
from typing import List
from looker_demo_cli.generators.lookml_generator import LookMLGenerator, LookMLTableSpec
from looker_demo_cli.utils.console import print_info, print_step_header, print_success
from looker_demo_cli.workflow.state import FlowState


def run_lookml_generation_step(state: FlowState, scratch_dir: Path, tables: List[LookMLTableSpec], dashboard_content: str = "") -> FlowState:
    """Step 4: Generate LookML views, explores, models, and dashboard."""
    print_step_header(4, state.total_steps, "LookML Code Generation")

    lookml_dir = scratch_dir / f"lookml_{state.looker_project_name}"
    lookml_dir.mkdir(parents=True, exist_ok=True)
    state.lookml_output_dir = lookml_dir

    gen = LookMLGenerator(
        project_id=state.gcp_project_id,
        dataset_id=state.bq_dataset_id,
        connection_name=state.looker_connection_name,
    )

    written_files = gen.write_lookml_project_files(
        output_dir=lookml_dir,
        model_name=state.lookml_model_name,
        tables=tables,
        dashboard_content=dashboard_content,
    )

    print_success(f"Generated {len(written_files)} LookML files in `{lookml_dir}`")
    return state
