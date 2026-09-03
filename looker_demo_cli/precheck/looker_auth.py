from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import requests
from pydantic import BaseModel

from looker_demo_cli.config import (
    DEFAULT_LOOKER_CLIENT_ID,
    DEFAULT_LOOKER_CLIENT_SECRET,
    DEFAULT_LOOKER_INSTANCE_URL,
    HOME_DIR,
)

LKR_AUTH_DB = HOME_DIR / ".lkr" / "auth.db"


class LookerAuthStatus(BaseModel):
    is_authenticated: bool
    auth_method: str = "none"  # "oauth", "api_key", "none"
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    instance_url: str = ""
    oauth_account: Optional[str] = None
    available_oauth_instances: List[Dict[str, Any]] = []
    available_connections: List[str] = []
    has_default_bigquery_conn: bool = False
    error_message: Optional[str] = None


def get_authenticated_oauth_instances() -> List[Dict[str, Any]]:
    """Retrieve all authenticated OAuth accounts from ~/.lkr/auth.db."""
    instances = []
    if not LKR_AUTH_DB.exists():
        return instances

    try:
        conn = sqlite3.connect(LKR_AUTH_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='auth'")
        if not cursor.fetchone():
            return instances

        cursor.execute(
            "SELECT id, instance_name, access_token, refresh_token, token_type, expires_at, current_instance, base_url, use_production FROM auth"
        )
        for row in cursor.fetchall():
            instances.append({
                "id": row[0],
                "instance_name": row[1],
                "access_token": row[2],
                "refresh_token": row[3],
                "token_type": row[4],
                "expires_at": row[5],
                "is_current": bool(row[6]),
                "base_url": row[7],
                "use_production": bool(row[8]),
            })
    except Exception:
        pass
    return instances


LKR_OAUTH_CLIENT_ID = "lkr-cli"
LKR_OAUTH_REDIRECT_URI = "http://localhost:8000/callback"
LKR_OAUTH_CLIENT_PAYLOAD = {
    "redirect_uri": "http://localhost:8000/callback",
    "display_name": "LKR",
    "description": "lkr.dev language server, MCP and CLI",
    "enabled": True,
}


def validate_looker_oauth_preflight(instance_url: str) -> Tuple[bool, str]:
    """Run a pre-flight GET check against the Looker instance auth endpoint for the lkr-cli OAuth client."""
    clean_url = instance_url.rstrip("/")
    preflight_url = (
        f"{clean_url}/auth?client_id={LKR_OAUTH_CLIENT_ID}&redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fcallback"
        f"&response_type=code&scope=api&state=preflight_state&code_challenge=preflight_challenge&code_challenge_method=S256"
    )
    try:
        resp = requests.get(preflight_url, timeout=6)
        if "The OAuth client was not found" in resp.text:
            return (
                False,
                f"OAuth client 'lkr-cli' is not configured on {clean_url}. "
                "See configuration guide: https://www.lkr.dev/docs/tools/cli/#oauth2-prerequisites",
            )
        if "redirect_uri mismatch" in resp.text or resp.status_code in (200, 302):
            return (True, f"OAuth client 'lkr-cli' is verified on {clean_url}.")
        return (False, f"Pre-flight check returned HTTP {resp.status_code}.")
    except Exception as e:
        return (False, f"Could not connect to Looker instance at {clean_url}: {e}")


def check_looker_auth(
    instance_url: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    preferred_oauth_account: Optional[str] = None,
) -> LookerAuthStatus:
    """Verify Looker authentication via OAuth session or API key credentials."""
    oauth_instances = get_authenticated_oauth_instances()

    # 1. Try OAuth Authentication via ~/.lkr/auth.db
    target_oauth = None
    if preferred_oauth_account:
        for inst in oauth_instances:
            if inst["instance_name"] == preferred_oauth_account:
                target_oauth = inst
                break
    elif instance_url:
        for inst in oauth_instances:
            if inst["base_url"].rstrip("/") == instance_url.rstrip("/"):
                target_oauth = inst
                break
    else:
        # Pick current active instance
        for inst in oauth_instances:
            if inst["is_current"]:
                target_oauth = inst
                break
        if not target_oauth and oauth_instances:
            target_oauth = oauth_instances[0]

    if target_oauth and target_oauth.get("access_token") and target_oauth.get("base_url"):
        base_url = target_oauth["base_url"]
        token = target_oauth["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp_me = requests.get(f"{base_url.rstrip('/')}/api/4.0/user", headers=headers, timeout=6)
            if resp_me.status_code == 200:
                user_data = resp_me.json()
                user_name = user_data.get("display_name") or f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip() or "Looker User"
                user_email = user_data.get("email") or ""

                # Query database connections
                resp_conns = requests.get(f"{base_url.rstrip('/')}/api/4.0/connections", headers=headers, timeout=6)
                conns = []
                if resp_conns.status_code == 200:
                    conns = [c.get("name") for c in resp_conns.json() if c.get("name")]
                has_default_bq = "default_bigquery_connection" in conns or "sample_bigquery_connection" in conns

                return LookerAuthStatus(
                    is_authenticated=True,
                    auth_method="oauth",
                    user_name=user_name,
                    user_email=user_email,
                    instance_url=base_url,
                    oauth_account=target_oauth["instance_name"],
                    available_oauth_instances=oauth_instances,
                    available_connections=conns,
                    has_default_bigquery_conn=has_default_bq,
                )
        except Exception:
            pass

    # 2. Try API Key Authentication via Environment / SDK
    url = instance_url or os.getenv("LOOKERSDK_BASE_URL") or DEFAULT_LOOKER_INSTANCE_URL
    cid = client_id or os.getenv("LOOKERSDK_CLIENT_ID") or DEFAULT_LOOKER_CLIENT_ID
    sec = client_secret or os.getenv("LOOKERSDK_CLIENT_SECRET") or DEFAULT_LOOKER_CLIENT_SECRET

    if url and cid and sec:
        os.environ["LOOKERSDK_BASE_URL"] = url
        os.environ["LOOKERSDK_CLIENT_ID"] = cid
        os.environ["LOOKERSDK_CLIENT_SECRET"] = sec
        os.environ["LOOKERSDK_VERIFY_SSL"] = "true"

        try:
            import looker_sdk

            sdk = looker_sdk.init40()
            me = sdk.me()
            user_name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "User"
            user_email = me.email or ""

            conns = [c.name for c in (sdk.all_connections() or []) if c.name]
            has_default_bq = "default_bigquery_connection" in conns or "sample_bigquery_connection" in conns

            return LookerAuthStatus(
                is_authenticated=True,
                auth_method="api_key",
                user_name=user_name,
                user_email=user_email,
                instance_url=url,
                available_oauth_instances=oauth_instances,
                available_connections=conns,
                has_default_bigquery_conn=has_default_bq,
            )
        except Exception as e:
            return LookerAuthStatus(
                is_authenticated=False,
                auth_method="api_key",
                instance_url=url,
                available_oauth_instances=oauth_instances,
                error_message=f"API key auth failed: {str(e).splitlines()[0]}",
            )

    # 3. Neither OAuth nor API Key is configured
    return LookerAuthStatus(
        is_authenticated=False,
        auth_method="none",
        instance_url=url or "Not configured",
        available_oauth_instances=oauth_instances,
        error_message="No active Looker OAuth session or API key credentials found.",
    )
