from __future__ import annotations

import json
import os
import subprocess
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
import requests
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from looker_demo_cli.precheck.looker_auth import get_authenticated_oauth_instances
from looker_demo_cli.utils.console import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
)

if TYPE_CHECKING:
    from looker_demo_cli.workflow.state import FlowState


def get_looker_auth_context(
    instance_url: Optional[str] = None,
    preferred_account: Optional[str] = None,
) -> Tuple[Dict[str, str], str]:
    """Retrieve Looker authentication bearer headers and base URL from active OAuth or env."""
    oauth_instances = get_authenticated_oauth_instances()
    target_oauth = None

    if preferred_account:
        target_oauth = next((i for i in oauth_instances if i.get("instance_name") == preferred_account), None)
    elif instance_url:
        target_oauth = next(
            (i for i in oauth_instances if i.get("base_url", "").rstrip("/") == instance_url.rstrip("/")),
            None,
        )

    if not target_oauth:
        target_oauth = next((i for i in oauth_instances if i.get("is_current")), None) or (
            oauth_instances[0] if oauth_instances else None
        )

    if target_oauth and target_oauth.get("access_token") and target_oauth.get("base_url"):
        base_url = target_oauth["base_url"].rstrip("/")
        headers = {
            "Authorization": f"Bearer {target_oauth['access_token']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        return headers, base_url

    # Fallback to env base URL if present
    base_url = (instance_url or os.getenv("LOOKERSDK_BASE_URL", "")).rstrip("/")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return headers, base_url


def get_looker_ge_config(instance_url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    """Fetch current Gemini enablement and GE configuration from Looker internal API.
    
    Endpoint: GET /api/4.0/gemini_enablement
    """
    clean_url = instance_url.rstrip("/")
    endpoint = f"{clean_url}/api/4.0/gemini_enablement"
    try:
        resp = requests.get(endpoint, headers=headers, timeout=12)
        if resp.status_code == 200:
            return resp.json()
        print_warning(
            f"Could not fetch GE configuration from Looker ({resp.status_code}): {resp.text[:200]}"
        )
    except Exception as e:
        print_warning(f"Error fetching Looker GE configuration from {endpoint}: {e}")
    return {}


def is_ge_configured(config: Dict[str, Any]) -> bool:
    """Check if Gemini Enterprise is already fully configured in Looker."""
    project_id = config.get("ai_ge_project_id") or ""
    instance_id = config.get("ai_ge_instance_id") or ""
    location = config.get("ai_ge_location") or ""
    return bool(project_id.strip() and instance_id.strip() and location.strip())


def render_ge_status_table(config: Dict[str, Any]) -> None:
    """Render a Rich table showing current Gemini enablement and GE parameters."""
    t = Table(title="Looker Gemini Enterprise Configuration", show_header=True, header_style="bold blue")
    t.add_column("Setting", style="bold")
    t.add_column("Value")
    t.add_column("Status", style="dim")

    ge_enabled = bool(config.get("ai_ge_publish_enabled"))
    t.add_row(
        "GE Publish Enabled",
        "[green]true[/green]" if ge_enabled else "[yellow]false[/yellow]",
        "Active" if ge_enabled else "Disabled",
    )
    t.add_row("GE GCP Project ID", config.get("ai_ge_project_id") or "[dim]Not Configured[/dim]", "")
    t.add_row("GE Location / Region", config.get("ai_ge_location") or "[dim]Not Configured[/dim]", "")
    t.add_row("GE Instance / App ID", config.get("ai_ge_instance_id") or "[dim]Not Configured[/dim]", "")
    t.add_row(
        "Looker SA Email",
        config.get("ai_ge_service_account_email") or "[dim]None[/dim]",
        "Discovery Engine Admin target",
    )
    t.add_row(
        "CA Assistant Enabled",
        "[green]true[/green]" if config.get("ai_ca_enabled") else "[yellow]false[/yellow]",
        "",
    )
    console.print(t)


def patch_looker_ge_config(
    instance_url: str,
    headers: Dict[str, str],
    existing_config: Dict[str, Any],
    project_id: str,
    location: str,
    instance_id: str,
    publish_enabled: bool = True,
) -> Tuple[bool, Optional[str]]:
    """Update Looker GE configuration via PATCH /api/internal/core/4.0/gemini_enablement.
    
    Endpoint: PATCH /api/4.0/gemini_enablement
    """
    clean_url = instance_url.rstrip("/")
    endpoint = f"{clean_url}/api/4.0/gemini_enablement"

    # Build full payload starting from existing configuration
    payload = dict(existing_config)
    payload["ai_ge_project_id"] = project_id
    payload["ai_ge_location"] = location
    payload["ai_ge_instance_id"] = instance_id
    payload["ai_ge_publish_enabled"] = publish_enabled

    try:
        resp = requests.patch(endpoint, json=payload, headers=headers, timeout=15)
        if resp.status_code in (200, 201, 204):
            return True, None

        # If Looker rejects read-only 'can' field, retry without 'can'
        if "can" in payload:
            clean_payload = dict(payload)
            clean_payload.pop("can", None)
            retry_resp = requests.patch(endpoint, json=clean_payload, headers=headers, timeout=15)
            if retry_resp.status_code in (200, 201, 204):
                return True, None
            return False, f"HTTP {retry_resp.status_code}: {retry_resp.text[:300]}"

        return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
    except Exception as e:
        return False, str(e)


def list_gcp_ge_instances(
    project_id: str,
    locations: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """Discover available Gemini Enterprise / Discovery Engine apps across standard locations."""
    if locations is None:
        locations = ["global", "us", "eu"]

    token = None
    # 1. Try google.auth
    try:
        import google.auth
        import google.auth.transport.requests

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        req = google.auth.transport.requests.Request()
        creds.refresh(req)
        token = creds.token
    except Exception:
        pass

    # 2. Try gcloud CLI fallback
    if not token:
        try:
            res = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            if res.returncode == 0 and res.stdout.strip():
                token = res.stdout.strip()
        except Exception:
            pass

    if not token:
        return []

    engines: List[Dict[str, str]] = []
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "x-goog-user-project": project_id,
    }

    for loc in locations:
        url = (
            f"https://discoveryengine.googleapis.com/v1/projects/{project_id}/"
            f"locations/{loc}/collections/default_collection/engines"
        )
        try:
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code == 200:
                data = r.json()
                for eng in data.get("engines", []):
                    eng_name = eng.get("name", "")
                    eng_id = eng_name.split("/")[-1] if "/" in eng_name else eng_name
                    display_name = eng.get("displayName") or eng_id
                    engines.append({
                        "id": eng_id,
                        "name": display_name,
                        "location": loc,
                        "display": f"{display_name} ({eng_id}) [location: {loc}]",
                    })
        except Exception:
            continue

    return engines


def grant_looker_sa_iam(project_id: str, sa_email: str) -> Tuple[bool, str]:
    """Grant roles/discoveryengine.admin to Looker Service Account on target GCP project."""
    cmd = [
        "gcloud",
        "projects",
        "add-iam-policy-binding",
        project_id,
        f"--member=serviceAccount:{sa_email}",
        "--role=roles/discoveryengine.admin",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if res.returncode == 0:
            return True, f"Successfully granted roles/discoveryengine.admin to {sa_email} on {project_id}."
        err = res.stderr.strip() or res.stdout.strip()
        return False, err
    except Exception as e:
        return False, str(e)


def ensure_gemini_enterprise_configured(
    state: FlowState,
    headers: Dict[str, str],
    interactive: bool = True,
    allow_reconfigure: bool = True,
) -> FlowState:
    """Ensure Gemini Enterprise is configured in Looker before publishing.
    
    1. Fetches current Looker GE config via GET /api/internal/core/4.0/gemini_enablement.
    2. If already configured:
       - Displays existing config.
       - In interactive mode: asks user to publish to existing or reconfigure.
    3. If not configured (or reconfiguring):
       - Scans GCP for GE apps/instances.
       - Prompts user to choose from discovered apps or enter manually.
       - PATCHes Looker with full payload.
       - Grants roles/discoveryengine.admin to Looker Service Account.
    """
    print_info("Checking Looker Gemini Enterprise (GE) configuration...")
    ge_config = get_looker_ge_config(state.looker_instance_url, headers)

    if not ge_config:
        print_warning("Unable to fetch Gemini enablement configuration from Looker internal API.")
        return state

    render_ge_status_table(ge_config)

    sa_email = ge_config.get("ai_ge_service_account_email") or ""
    state.ge_service_account_email = sa_email

    if is_ge_configured(ge_config):
        state.ge_configured = True
        state.ge_project_id = ge_config.get("ai_ge_project_id")
        state.ge_location = ge_config.get("ai_ge_location")
        state.ge_instance_id = ge_config.get("ai_ge_instance_id")
        print_success(
            f"Gemini Enterprise is already configured in Looker (App: `{state.ge_instance_id}`, Location: `{state.ge_location}`)."
        )

        if interactive and allow_reconfigure:
            import sys
            if not sys.stdin.isatty():
                return state
            try:
                reconfig = Confirm.ask(
                    "Would you like to reconfigure Looker with a different Gemini Enterprise instance?",
                    default=False,
                )
                if not reconfig:
                    return state
            except (EOFError, KeyboardInterrupt):
                return state
        else:
            return state

    # Step: Configure GE in Looker
    print_info(f"Configuring Gemini Enterprise for project `{state.gcp_project_id}`...")
    discovered = list_gcp_ge_instances(state.gcp_project_id)

    selected_id: str = ""
    selected_loc: str = "global"

    is_tty = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()

    if discovered:
        if interactive and is_tty:
            console.print("\n[bold cyan]Discovered Gemini Enterprise Apps in GCP:[/bold cyan]")
            for idx, app in enumerate(discovered, 1):
                console.print(f"  [bold green]{idx}.[/bold green] {app['display']}")
            console.print(f"  [bold yellow]{len(discovered) + 1}.[/bold yellow] Enter custom App ID / Location manually")

            choice = Prompt.ask(
                "Select a Gemini Enterprise App to configure in Looker",
                default="1",
            )
            try:
                c_int = int(choice)
                if 1 <= c_int <= len(discovered):
                    chosen = discovered[c_int - 1]
                    selected_id = chosen["id"]
                    selected_loc = chosen["location"]
            except ValueError:
                selected_id = choice.strip()
        else:
            chosen = discovered[0]
            selected_id = chosen["id"]
            selected_loc = chosen["location"]
            print_info(f"Auto-selected discovered Gemini Enterprise app: `{selected_id}` ({selected_loc})")

    if not selected_id:
        if not discovered:
            print_info(
                f"No existing Gemini Enterprise instances auto-detected in project `{state.gcp_project_id}`."
            )
        if interactive and is_tty:
            selected_id = Prompt.ask("Enter Gemini Enterprise App / Engine ID", default="my-gemini-app")
            selected_loc = Prompt.ask("Enter Gemini Enterprise Location / Region", default="global")
        else:
            selected_id = state.ge_instance_id or "default-ge-app"
            selected_loc = state.ge_location or "global"

    # Patch Looker with full payload
    print_info(
        f"Updating Looker GE settings -> Project: `{state.gcp_project_id}`, App: `{selected_id}`, Location: `{selected_loc}`..."
    )
    patched, patch_err = patch_looker_ge_config(
        instance_url=state.looker_instance_url,
        headers=headers,
        existing_config=ge_config,
        project_id=state.gcp_project_id,
        location=selected_loc,
        instance_id=selected_id,
        publish_enabled=True,
    )

    if patched:
        print_success("Looker Gemini Enterprise configuration updated successfully!")
        state.ge_configured = True
        state.ge_project_id = state.gcp_project_id
        state.ge_location = selected_loc
        state.ge_instance_id = selected_id
    else:
        print_error(f"Failed to update Looker GE configuration: {patch_err}")

    # Grant Looker Service Account IAM
    if sa_email:
        print_info(f"Granting 'roles/discoveryengine.admin' to Looker Service Account: {sa_email}...")
        iam_ok, iam_msg = grant_looker_sa_iam(state.gcp_project_id, sa_email)
        if iam_ok:
            print_success(iam_msg)
        else:
            print_warning(
                f"Automatic IAM grant returned notice: {iam_msg}\n"
                f"Please ensure `{sa_email}` has 'Discovery Engine Admin' on project `{state.gcp_project_id}`.\n"
                f"Manual command:\n"
                f"  $ gcloud projects add-iam-policy-binding {state.gcp_project_id} \\\n"
                f"      --member=\"serviceAccount:{sa_email}\" \\\n"
                f"      --role=\"roles/discoveryengine.admin\"\n"
                f"IAM Console: https://console.cloud.google.com/iam-admin/iam?project={state.gcp_project_id}"
            )
    else:
        print_warning("No Looker Service Account email was returned by the Looker API; skipping IAM grant.")

    return state
