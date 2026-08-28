from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from looker_demo_cli.config import (
    DEFAULT_LOOKER_CLIENT_ID,
    DEFAULT_LOOKER_CLIENT_SECRET,
    DEFAULT_LOOKER_INSTANCE_URL,
)


class LookerAuthStatus(BaseModel):
    is_authenticated: bool
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    instance_url: str = DEFAULT_LOOKER_INSTANCE_URL
    available_connections: List[str] = []
    has_default_bigquery_conn: bool = False
    error_message: Optional[str] = None


def check_looker_auth(
    instance_url: str = DEFAULT_LOOKER_INSTANCE_URL,
    client_id: str = DEFAULT_LOOKER_CLIENT_ID,
    client_secret: str = DEFAULT_LOOKER_CLIENT_SECRET,
) -> LookerAuthStatus:
    """Verify Looker API authentication and database connections."""
    os.environ["LOOKERSDK_BASE_URL"] = instance_url
    os.environ["LOOKERSDK_CLIENT_ID"] = client_id
    os.environ["LOOKERSDK_CLIENT_SECRET"] = client_secret
    os.environ["LOOKERSDK_VERIFY_SSL"] = "true"

    try:
        import looker_sdk
        from looker_sdk import models40

        sdk = looker_sdk.init40()
        me = sdk.me()
        user_name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "User"
        user_email = me.email or ""

        conns = [c.name for c in (sdk.all_connections() or []) if c.name]
        has_default_bq = "default_bigquery_connection" in conns or "sample_bigquery_connection" in conns

        return LookerAuthStatus(
            is_authenticated=True,
            user_name=user_name,
            user_email=user_email,
            instance_url=instance_url,
            available_connections=conns,
            has_default_bigquery_conn=has_default_bq,
        )
    except Exception as e:
        return LookerAuthStatus(
            is_authenticated=False,
            instance_url=instance_url,
            error_message=str(e).split("\n")[0],
        )
