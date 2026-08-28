# SPDX-FileCopyrightText: Copyright (c) 2026 lkr.dev. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from looker_demo_cli.utils.console import print_error, print_info, print_step_header, print_success
from looker_demo_cli.workflow.state import FlowState


def run_looker_deploy_step(state: FlowState) -> FlowState:
    """Step 5: Upload LookML files, validate, commit, and deploy to Looker production using lkr-dev-cli."""
    print_step_header(5, state.total_steps, "Looker Production Deployment via lkr-dev-cli")

    if not state.lookml_output_dir or not state.lookml_output_dir.exists():
        print_error("No LookML output directory found. Cannot deploy.")
        state.status = "failed"
        return state

    lkr_bin = shutil.which("lkr") or str(Path(sys.executable).parent / "lkr")
    print_info(f"Using `lkr-dev-cli` binary: `{lkr_bin}`")

    cmd = [
        lkr_bin,
        "tools",
        "lookml",
        "push",
        str(state.lookml_output_dir),
        f"--project={state.looker_project_name}",
        "--deploy",
    ]

    print_info(f"Executing: {' '.join(cmd)}")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print_success("LookML files successfully pushed, validated, and deployed to production!")
        if res.stdout:
            print_info(res.stdout.strip())
    except subprocess.CalledProcessError as e:
        print_error(f"lkr push error (exit code {e.returncode}): {e.stderr or e.stdout}")
        state.status = "failed"
        state.error_message = e.stderr or e.stdout
        return state
    except Exception as e:
        print_error(f"Unexpected error running lkr CLI: {e}")
        state.status = "failed"
        state.error_message = str(e)
        return state

    dash_url = f"{state.looker_instance_url}/dashboards/{state.looker_project_name}::{state.lookml_model_name}_overview"
    state.deployed_dashboard_url = dash_url
    print_success(f"Deployed to production! Dashboard URL: {dash_url}")

    return state
