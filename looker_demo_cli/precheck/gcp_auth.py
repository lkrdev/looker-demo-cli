from __future__ import annotations

import configparser
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import google.auth
import google.auth.credentials
import google.oauth2.credentials
from google.cloud import bigquery
from pydantic import BaseModel

from looker_demo_cli.config import GCLOUD_CONFIGS_DIR, GCLOUD_CONFIG_DIR, GCLOUD_CREDS_DB
from looker_demo_cli.utils.console import console, print_error, print_info, print_success, print_warning


class GCPAccountInfo(BaseModel):
    account_id: str
    is_active: bool = False
    project_id: Optional[str] = None
    has_bigquery_access: bool = False
    error_message: Optional[str] = None


def get_available_gcloud_configs() -> Dict[str, Dict[str, str]]:
    """Parse all configurations in ~/.config/gcloud/configurations/."""
    configs = {}
    if not GCLOUD_CONFIGS_DIR.exists():
        return configs

    for cfg_file in GCLOUD_CONFIGS_DIR.glob("config_*"):
        cfg_name = cfg_file.name.replace("config_", "")
        parser = configparser.ConfigParser()
        try:
            parser.read(cfg_file)
            section_data = {}
            for section in parser.sections():
                for k, v in parser.items(section):
                    section_data[f"{section}.{k}"] = v
            configs[cfg_name] = section_data
        except Exception as e:
            configs[cfg_name] = {"error": str(e)}
    return configs


def get_active_gcloud_config_name() -> Optional[str]:
    """Read the active config name from ~/.config/gcloud/active_config."""
    active_file = GCLOUD_CONFIG_DIR / "active_config"
    if active_file.exists():
        return active_file.read_text(encoding="utf-8").strip()
    return None


def get_authenticated_accounts() -> List[str]:
    """Query all accounts stored in ~/.config/gcloud/credentials.db."""
    accounts = []
    if not GCLOUD_CREDS_DB.exists():
        return accounts

    try:
        conn = sqlite3.connect(GCLOUD_CREDS_DB)
        cursor = conn.cursor()
        for row in cursor.execute("SELECT account_id FROM credentials"):
            accounts.append(row[0])
    except Exception as e:
        print_warning(f"Could not read credentials.db: {e}")
    return accounts


def get_oauth_credentials_for_account(account_id: str) -> Optional[google.oauth2.credentials.Credentials]:
    """Build google.oauth2.credentials.Credentials for a given account from credentials.db."""
    if not GCLOUD_CREDS_DB.exists():
        return None

    try:
        conn = sqlite3.connect(GCLOUD_CREDS_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM credentials WHERE account_id = ?", (account_id,))
        row = cursor.fetchone()
        if not row:
            return None

        data = json.loads(row[0])
        return google.oauth2.credentials.Credentials(
            token=None,
            refresh_token=data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
        )
    except Exception as e:
        print_warning(f"Failed to load OAuth credentials for {account_id}: {e}")
        return None


from looker_demo_cli.config import DEFAULT_GCP_PROJECT, GCLOUD_CONFIGS_DIR, GCLOUD_CONFIG_DIR, GCLOUD_CREDS_DB


def inspect_gcp_accounts(target_project: str = DEFAULT_GCP_PROJECT) -> List[GCPAccountInfo]:
    """Inspect all authenticated GCP accounts and test BigQuery dataset access."""
    # Ensure client certificates don't cause failures on linux/cloudtop
    os.environ["CLOUDSDK_CONTEXT_AWARE_USE_CLIENT_CERTIFICATE"] = "false"
    os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"

    active_cfg = get_active_gcloud_config_name()
    configs = get_available_gcloud_configs()
    accounts = get_authenticated_accounts()

    results: List[GCPAccountInfo] = []

    for acc in accounts:
        info = GCPAccountInfo(account_id=acc)
        # Check if active in gcloud
        if active_cfg and active_cfg in configs:
            cfg_acc = configs[active_cfg].get("core.account")
            if cfg_acc == acc:
                info.is_active = True
                info.project_id = configs[active_cfg].get("core.project", target_project or "")

        if not info.project_id:
            info.project_id = target_project or ""

        creds = get_oauth_credentials_for_account(acc)
        if creds:
            try:
                # If project_id is set, verify BigQuery dataset access
                if info.project_id:
                    client = bigquery.Client(project=info.project_id, credentials=creds)
                    _ = list(client.list_datasets(max_results=2))
                info.has_bigquery_access = True
            except Exception as e:
                info.has_bigquery_access = False
                info.error_message = str(e).split("\n")[0]
        else:
            info.error_message = "No OAuth tokens in credentials.db"

        results.append(info)

    return results


def select_gcp_credentials(
    preferred_account: Optional[str] = None,
    preferred_project: Optional[str] = None,
    interactive: bool = True,
) -> Tuple[google.auth.credentials.Credentials, str]:
    """Select or prompt for the active GCP account and project ID."""
    os.environ["CLOUDSDK_CONTEXT_AWARE_USE_CLIENT_CERTIFICATE"] = "false"
    os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"

    accounts = inspect_gcp_accounts(target_project=preferred_project or DEFAULT_GCP_PROJECT)
    valid_accounts = [a for a in accounts if a.has_bigquery_access]

    # If preferred_account matches
    if preferred_account:
        for acc in accounts:
            if acc.account_id == preferred_account and acc.has_bigquery_access:
                creds = get_oauth_credentials_for_account(acc.account_id)
                if creds:
                    return creds, preferred_project or acc.project_id or DEFAULT_GCP_PROJECT

    # Default to first valid account
    if valid_accounts:
        chosen = valid_accounts[0]
        creds = get_oauth_credentials_for_account(chosen.account_id)
        if creds:
            return creds, preferred_project or chosen.project_id or DEFAULT_GCP_PROJECT

    # Fallback to default ADC
    adc_creds, project = google.auth.default()
    return adc_creds, preferred_project or project or DEFAULT_GCP_PROJECT


def list_available_gcp_projects() -> List[Dict[str, str]]:
    """List accessible Google Cloud projects via gcloud."""
    import subprocess
    try:
        res = subprocess.run(
            ["gcloud", "projects", "list", "--format=json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            return [
                {
                    "project_id": p.get("projectId", ""),
                    "name": p.get("name", ""),
                    "project_number": str(p.get("projectNumber", "")),
                }
                for p in data
                if p.get("projectId")
            ]
    except Exception as e:
        print_warning(f"Could not query GCP projects via gcloud: {e}")
    return []

