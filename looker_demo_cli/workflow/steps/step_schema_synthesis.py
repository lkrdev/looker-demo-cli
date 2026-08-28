# SPDX-FileCopyrightText: Copyright (c) 2026 lkr.dev. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from looker_demo_cli.utils.console import print_info, print_step_header, print_success
from looker_demo_cli.workflow.state import FlowState


def run_schema_synthesis_step(state: FlowState, scratch_dir: Path) -> FlowState:
    """Step 2: Synthesize schema and generate micro-sample or scaled dataset."""
    print_step_header(2, state.total_steps, "Schema Synthesis & Data Generation")

    output_dir = scratch_dir / state.bq_dataset_id
    output_dir.mkdir(parents=True, exist_ok=True)
    state.generated_parquet_dir = output_dir

    print_info(f"Target data generation output directory: `{output_dir}`")
    return state
