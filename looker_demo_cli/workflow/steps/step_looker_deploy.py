import os
import shutil
import subprocess
import sys
from pathlib import Path
import requests

from looker_demo_cli.config import (
    DEFAULT_LOOKER_CLIENT_ID,
    DEFAULT_LOOKER_CLIENT_SECRET,
    DEFAULT_LOOKER_INSTANCE_URL,
)
from looker_demo_cli.precheck.looker_auth import get_authenticated_oauth_instances
from looker_demo_cli.utils.console import print_error, print_info, print_step_header, print_success
from looker_demo_cli.workflow.state import FlowState


def run_looker_deploy_step(state: FlowState) -> FlowState:
    """Step 5: Upload LookML files, validate, commit, and deploy to Looker production using lkr-dev-cli."""
    print_step_header(5, state.total_steps, "Looker Production Deployment via lkr-dev-cli")

    if not state.lookml_output_dir or not state.lookml_output_dir.exists():
        print_error("No LookML output directory found. Cannot deploy.")
        state.status = "failed"
        return state

    oauth_instances = get_authenticated_oauth_instances()
    active_oauth = None
    if state.looker_account:
        active_oauth = next((i for i in oauth_instances if i["instance_name"] == state.looker_account), None)
    if not active_oauth:
        active_oauth = next((i for i in oauth_instances if i["is_current"]), None) or (oauth_instances[0] if oauth_instances else None)

    instance_url = (active_oauth["base_url"] if active_oauth else state.looker_instance_url) or DEFAULT_LOOKER_INSTANCE_URL
    state.looker_instance_url = instance_url.rstrip("/")

    # Ensure Looker SDK environment variables
    env = os.environ.copy()
    env["LOOKERSDK_BASE_URL"] = state.looker_instance_url
    env["LOOKERSDK_CLIENT_ID"] = DEFAULT_LOOKER_CLIENT_ID
    env["LOOKERSDK_CLIENT_SECRET"] = DEFAULT_LOOKER_CLIENT_SECRET
    env["LOOKERSDK_VERIFY_SSL"] = "true"

    headers = {}
    if active_oauth and active_oauth.get("access_token"):
        headers = {"Authorization": f"Bearer {active_oauth['access_token']}"}

    # 1. Provision Looker project & register LookML model if needed
    if headers:
        try:
            # Set dev workspace
            requests.patch(f"{state.looker_instance_url}/api/4.0/session", json={"workspace_id": "dev"}, headers=headers, timeout=10)

            # Check / Create project
            r_proj = requests.get(f"{state.looker_instance_url}/api/4.0/projects/{state.looker_project_name}", headers=headers, timeout=10)
            if r_proj.status_code != 200:
                print_info(f"Creating Looker project `{state.looker_project_name}` via OAuth REST API...")
                requests.post(f"{state.looker_instance_url}/api/4.0/projects", json={"name": state.looker_project_name}, headers=headers, timeout=10)
                requests.patch(f"{state.looker_instance_url}/api/4.0/projects/{state.looker_project_name}", json={"git_remote_url": None, "git_service_name": "bare"}, headers=headers, timeout=10)

            # Check / Create model
            r_mod = requests.get(f"{state.looker_instance_url}/api/4.0/lookml_models/{state.lookml_model_name}", headers=headers, timeout=10)
            if r_mod.status_code != 200:
                print_info(f"Registering LookML model `{state.lookml_model_name}` via OAuth REST API...")
                requests.post(
                    f"{state.looker_instance_url}/api/4.0/lookml_models",
                    json={
                        "name": state.lookml_model_name,
                        "project_name": state.looker_project_name,
                        "allowed_db_connection_names": [state.looker_connection_name],
                        "unlimited_db_connections": False,
                    },
                    headers=headers,
                    timeout=10,
                )
        except Exception as prov_err:
            print_info(f"OAuth project provisioning note: {prov_err}")
    else:
        try:
            import looker_sdk
            from looker_sdk import models40

            sdk = looker_sdk.init40()
            sdk.update_session(models40.WriteApiSession(workspace_id="dev"))

            try:
                sdk.project(state.looker_project_name)
            except Exception:
                print_info(f"Creating Looker project `{state.looker_project_name}`...")
                sdk.create_project(models40.WriteProject(name=state.looker_project_name))
                sdk.update_project(
                    project_id=state.looker_project_name,
                    body=models40.WriteProject(git_remote_url=None, git_service_name="bare"),
                )

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
            print_info(f"SDK project provisioning note: {prov_err}")

    # 2. Synchronize LookML to Dev Branch via lkr CLI
    lkr_bin = shutil.which("lkr") or str(Path(sys.executable).parent / "lkr")
    print_info(f"Using `lkr-dev-cli` binary: `{lkr_bin}`")

    cmd_push = [
        lkr_bin,
        "--dev",
    ]
    if active_oauth:
        cmd_push.extend(["--oauth-account", active_oauth["instance_name"]])
    cmd_push.extend([
        "tools",
        "lookml",
        "push",
        str(state.lookml_output_dir),
        f"--project={state.looker_project_name}",
    ])

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
    if headers:
        try:
            r_val = requests.get(f"{state.looker_instance_url}/api/4.0/projects/{state.looker_project_name}/validate", headers=headers, timeout=20)
            if r_val.status_code == 200:
                val_data = r_val.json()
                errors = val_data.get("errors", [])
                if errors:
                    print_error(f"LookML validation failed with {len(errors)} error(s):")
                    for err in errors:
                        print_error(f" - [{err.get('file_path')}:{err.get('line_number')}] {err.get('message')}")
                    state.status = "failed"
                    state.error_message = f"LookML validation failed: {len(errors)} errors"
                    return state
                print_success("LookML validator passed cleanly with 0 errors.")
        except Exception as val_err:
            print_error(f"Error during LookML validation: {val_err}")
            state.status = "failed"
            state.error_message = str(val_err)
            return state
    else:
        try:
            import looker_sdk
            from looker_sdk import models40

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

    # 4. Exhaustive Dashboard Tile Query Verification Gate
    dash_dir = state.lookml_output_dir / "dashboards"
    if dash_dir.exists() and headers:
        dash_files = list(dash_dir.glob("*.dashboard.lookml"))
        if dash_files:
            import yaml
            print_info(f"Verifying runtime queries across {len(dash_files)} LookML dashboard(s)...")
            for df in dash_files:
                try:
                    content = df.read_text(encoding="utf-8")
                    parsed = yaml.safe_load(content)
                    dash_list = parsed if isinstance(parsed, list) else [parsed]
                    for dash_obj in dash_list:
                        dash_filters = {f.get("name"): f.get("default_value") for f in dash_obj.get("filters", []) if f.get("default_value")}
                        elements = dash_obj.get("elements", [])
                        print_info(f"Testing {len(elements)} visualization tile queries in `{df.name}`...")
                        for el in elements:
                            title = el.get("title") or el.get("name") or "Tile"
                            model = el.get("model")
                            explore = el.get("explore")
                            fields = el.get("fields", [])
                            pivots = el.get("pivots", [])
                            filters = dict(el.get("filters", {}))
                            listen_map = el.get("listen", {})
                            for filter_name, target_field in listen_map.items():
                                if filter_name in dash_filters and target_field not in filters:
                                    filters[target_field] = dash_filters[filter_name]
                            if model and explore and fields:
                                q_body = {
                                    "model": model,
                                    "view": explore,
                                    "fields": fields,
                                    "pivots": pivots,
                                    "filters": filters,
                                    "limit": "5",
                                }
                                resp_q = requests.post(
                                    f"{state.looker_instance_url}/api/4.0/queries/run/json",
                                    json=q_body,
                                    headers=headers,
                                    timeout=15,
                                )
                                if resp_q.status_code != 200:
                                    print_error(f"Dashboard query failed for tile '{title}' ({resp_q.status_code}): {resp_q.text[:200]}")
                                    state.status = "failed"
                                    state.error_message = f"Dashboard tile '{title}' failed query validation: {resp_q.text[:200]}"
                                    return state
                    print_success("All dashboard visualization queries verified successfully (HTTP 200 OK).")
                except Exception as e:
                    print_error(f"Error checking dashboard queries in {df.name}: {e}")

    # 5. Commit and Deploy to Production via lkr CLI
    cmd_deploy = [
        lkr_bin,
        "--dev",
    ]
    if active_oauth:
        cmd_deploy.extend(["--oauth-account", active_oauth["instance_name"]])
    cmd_deploy.extend([
        "tools",
        "lookml",
        "deploy",
        f"--project={state.looker_project_name}",
        "--message=Deploy validated LookML models and dashboards",
    ])

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
