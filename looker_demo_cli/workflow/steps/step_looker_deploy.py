# SPDX-FileCopyrightText: Copyright (c) 2026 lkr.dev. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path
from looker_demo_cli.utils.console import print_error, print_info, print_step_header, print_success
from looker_demo_cli.utils.looker_client import LookerDeployHelper
from looker_demo_cli.workflow.state import FlowState


def run_looker_deploy_step(state: FlowState, looker_helper: LookerDeployHelper) -> FlowState:
    """Step 5: Provision project, upload files, validate, and deploy to Looker production."""
    print_step_header(5, state.total_steps, "Looker Project Provisioning & Production Deployment")

    if not state.lookml_output_dir or not state.lookml_output_dir.exists():
        print_error("No LookML output directory found. Cannot deploy.")
        state.status = "failed"
        return state

    print_info(f"Provisioning project `{state.looker_project_name}` on Looker...")
    looker_helper.ensure_project(state.looker_project_name)
    looker_helper.ensure_model_configuration(
        model_name=state.lookml_model_name,
        project_id=state.looker_project_name,
        connection_name=state.looker_connection_name,
    )

    print_info(f"Uploading LookML files to `{state.looker_project_name}`...")
    uploaded = looker_helper.upload_lookml_directory(state.looker_project_name, state.lookml_output_dir)
    print_success(f"Uploaded {len(uploaded)} LookML files to dev workspace.")

    print_info("Validating LookML and deploying to production...")
    res = looker_helper.validate_and_deploy(
        project_id=state.looker_project_name,
        commit_message=f"Deploy {state.looker_project_name} from demo-create CLI",
    )

    val_errors = res.get("validation_errors", [])
    if val_errors:
        print_error(f"Validation warnings/errors: {val_errors}")
    else:
        print_success("LookML validation clean (0 errors).")

    dash_url = f"{state.looker_instance_url}/dashboards/{state.looker_project_name}::{state.lookml_model_name}_overview"
    state.deployed_dashboard_url = dash_url
    print_success(f"Deployed to production! Dashboard URL: {dash_url}")

    return state
