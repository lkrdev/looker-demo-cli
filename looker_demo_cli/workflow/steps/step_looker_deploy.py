import os
import shutil
import subprocess
import sys
from pathlib import Path
import looker_sdk
from looker_sdk import models40

from looker_demo_cli.config import (
    DEFAULT_LOOKER_CLIENT_ID,
    DEFAULT_LOOKER_CLIENT_SECRET,
    DEFAULT_LOOKER_INSTANCE_URL,
)
from looker_demo_cli.utils.console import print_error, print_info, print_step_header, print_success
from looker_demo_cli.workflow.state import FlowState


def run_looker_deploy_step(state: FlowState) -> FlowState:
    """Step 5: Upload LookML files, validate, commit, and deploy to Looker production using lkr-dev-cli."""
    print_step_header(5, state.total_steps, "Looker Production Deployment via lkr-dev-cli")

    if not state.lookml_output_dir or not state.lookml_output_dir.exists():
        print_error("No LookML output directory found. Cannot deploy.")
        state.status = "failed"
        return state

    # Ensure Looker SDK environment variables
    env = os.environ.copy()
    env["LOOKERSDK_BASE_URL"] = state.looker_instance_url or DEFAULT_LOOKER_INSTANCE_URL
    env["LOOKERSDK_CLIENT_ID"] = DEFAULT_LOOKER_CLIENT_ID
    env["LOOKERSDK_CLIENT_SECRET"] = DEFAULT_LOOKER_CLIENT_SECRET
    env["LOOKERSDK_VERIFY_SSL"] = "true"

    # 1. Provision Looker project & register LookML model if needed
    try:
        os.environ["LOOKERSDK_BASE_URL"] = env["LOOKERSDK_BASE_URL"]
        os.environ["LOOKERSDK_CLIENT_ID"] = env["LOOKERSDK_CLIENT_ID"]
        os.environ["LOOKERSDK_CLIENT_SECRET"] = env["LOOKERSDK_CLIENT_SECRET"]
        os.environ["LOOKERSDK_VERIFY_SSL"] = "true"

        sdk = looker_sdk.init40()
        sdk.update_session(models40.WriteApiSession(workspace_id="dev"))

        # Project
        try:
            sdk.project(state.looker_project_name)
        except Exception:
            print_info(f"Creating Looker project `{state.looker_project_name}`...")
            sdk.create_project(models40.WriteProject(name=state.looker_project_name))
            sdk.update_project(
                project_id=state.looker_project_name,
                body=models40.WriteProject(git_remote_url=None, git_service_name="bare"),
            )

        # Model
        try:
            sdk.lookml_model(state.lookml_model_name)
        except Exception:
            print_info(f"Registering LookML model `{state.lookml_model_name}`...")
            sdk.create_lookml_model(
                models40.WriteLookmlModel(
                    name=state.lookml_model_name,
                    project_name=state.looker_project_name,
                    allowed_db_connection_names=[state.looker_connection_name],
                    unlimited_db_connections=False,
                )
            )
    except Exception as prov_err:
        print_info(f"Project provisioning note: {prov_err}")

    # 2. Synchronize LookML to Dev Branch via lkr CLI
    lkr_bin = shutil.which("lkr") or str(Path(sys.executable).parent / "lkr")
    print_info(f"Using `lkr-dev-cli` binary: `{lkr_bin}`")

    cmd_push = [
        lkr_bin,
        "--dev",
        "tools",
        "lookml",
        "push",
        str(state.lookml_output_dir),
        f"--project={state.looker_project_name}",
    ]

    print_info(f"Pushing LookML to dev workspace: {' '.join(cmd_push)}")
    try:
        res_push = subprocess.run(cmd_push, capture_output=True, text=True, check=True, env=env)
        print_success("LookML files successfully pushed to dev workspace.")
        if res_push.stdout:
            print_info(res_push.stdout.strip())
    except subprocess.CalledProcessError as e:
        print_error(f"lkr push error (exit code {e.returncode}): {e.stderr or e.stdout}")
        state.status = "failed"
        state.error_message = e.stderr or e.stdout
        return state
    except Exception as e:
        print_error(f"Unexpected error pushing LookML files: {e}")
        state.status = "failed"
        state.error_message = str(e)
        return state

    # 3. LookML Validator Gate
    print_info(f"Running LookML Validator on project `{state.looker_project_name}`...")
    try:
        sdk = looker_sdk.init40()
        sdk.update_session(models40.WriteApiSession(workspace_id="dev"))
        val_results = sdk.validate_project(state.looker_project_name)
        if val_results.errors:
            print_error(f"LookML validation failed with {len(val_results.errors)} error(s):")
            for err in val_results.errors:
                print_error(f" - [{err.file_path}:{err.line_number}] {err.message}")
            state.status = "failed"
            state.error_message = f"LookML validation failed: {len(val_results.errors)} errors"
            return state
        print_success("LookML validator passed cleanly with 0 errors.")
    except Exception as val_err:
        print_error(f"Error during LookML validation: {val_err}")
        state.status = "failed"
        state.error_message = str(val_err)
        return state

    # 4. Commit and Deploy to Production via lkr CLI
    cmd_deploy = [
        lkr_bin,
        "--dev",
        "tools",
        "lookml",
        "deploy",
        f"--project={state.looker_project_name}",
        "--message=Deploy validated LookML models and dashboards",
    ]

    print_info(f"Deploying to production: {' '.join(cmd_deploy)}")
    try:
        res_dep = subprocess.run(cmd_deploy, capture_output=True, text=True, check=True, env=env)
        print_success("LookML project deployed to production successfully via lkr-dev-cli!")
        if res_dep.stdout:
            print_info(res_dep.stdout.strip())
    except subprocess.CalledProcessError as e:
        print_error(f"lkr deploy error (exit code {e.returncode}): {e.stderr or e.stdout}")
        state.status = "failed"
        state.error_message = e.stderr or e.stdout
        return state
    except Exception as e:
        print_error(f"Unexpected error deploying project: {e}")
        state.status = "failed"
        state.error_message = str(e)
        return state

    dash_url = f"{state.looker_instance_url}/dashboards/{state.looker_project_name}::{state.lookml_model_name}_overview"
    state.deployed_dashboard_url = dash_url
    print_success(f"Deployed to production! Dashboard URL: {dash_url}")

    return state
