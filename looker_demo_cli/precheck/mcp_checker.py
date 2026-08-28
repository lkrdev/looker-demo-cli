# SPDX-FileCopyrightText: Copyright (c) 2026 lkr.dev. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple
from pydantic import BaseModel

from looker_demo_cli.config import (
    DEFAULT_LOOKER_CLIENT_ID,
    DEFAULT_LOOKER_CLIENT_SECRET,
    DEFAULT_LOOKER_INSTANCE_URL,
    GEMINI_MCP_CONFIG,
)
from looker_demo_cli.utils.console import print_error, print_info, print_success, print_warning


class MCPStatus(BaseModel):
    server_name: str
    is_configured: bool
    details: Dict[str, Any] = {}
    issues: List[str] = []


REQUIRED_MCP_SERVERS = {
    "data-designer": {
        "command": "uvx",
        "args": [
            "--from",
            "git+https://github.com/LukaFontanilla/synthetic-data-generator.git@main",
            "data-designer-mcp",
        ],
    },
    "bigquery": {
        "serverUrl": "https://bigquery.googleapis.com/mcp",
        "authProviderType": "google_credentials",
    },
    "lkr_codemode": {
        "command": "gpkg",
        "args": [
            "uvx",
            "--quiet",
            "--from",
            "lkr-dev-cli[codemode]",
            "lkr",
            "code-mode",
            "run",
        ],
        "env": {
            "PYTHONUNBUFFERED": "1",
            "LOOKERSDK_BASE_URL": DEFAULT_LOOKER_INSTANCE_URL,
            "LOOKERSDK_CLIENT_ID": DEFAULT_LOOKER_CLIENT_ID,
            "LOOKERSDK_CLIENT_SECRET": DEFAULT_LOOKER_CLIENT_SECRET,
        },
    },
}


def read_mcp_config() -> Dict[str, Any]:
    """Read global MCP configuration file."""
    if not GEMINI_MCP_CONFIG.exists():
        return {"mcpServers": {}}
    try:
        with open(GEMINI_MCP_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print_warning(f"Could not read {GEMINI_MCP_CONFIG}: {e}")
        return {"mcpServers": {}}


def check_mcp_servers() -> List[MCPStatus]:
    """Check if all required MCP servers are present and properly configured."""
    config = read_mcp_config()
    servers = config.get("mcpServers", {})

    results = []
    for s_name, expected in REQUIRED_MCP_SERVERS.items():
        if s_name not in servers:
            results.append(
                MCPStatus(
                    server_name=s_name,
                    is_configured=False,
                    issues=[f"Missing MCP server definition for '{s_name}'"],
                )
            )
        else:
            curr = servers[s_name]
            issues = []
            # Check basic structure
            if "serverUrl" in expected and "serverUrl" not in curr:
                issues.append("Missing 'serverUrl'")
            if "command" in expected and "command" not in curr:
                issues.append("Missing 'command'")

            results.append(
                MCPStatus(
                    server_name=s_name,
                    is_configured=(len(issues) == 0),
                    details=curr,
                    issues=issues,
                )
            )
    return results


def patch_mcp_config(
    looker_url: str = DEFAULT_LOOKER_INSTANCE_URL,
    client_id: str = DEFAULT_LOOKER_CLIENT_ID,
    client_secret: str = DEFAULT_LOOKER_CLIENT_SECRET,
) -> bool:
    """Inject or repair required MCP servers in ~/.gemini/config/mcp_config.json."""
    config = read_mcp_config()
    if "mcpServers" not in config:
        config["mcpServers"] = {}

    # Copy defaults and update Looker credentials
    servers_to_patch = json.loads(json.dumps(REQUIRED_MCP_SERVERS))
    servers_to_patch["lkr_codemode"]["env"]["LOOKERSDK_BASE_URL"] = looker_url
    servers_to_patch["lkr_codemode"]["env"]["LOOKERSDK_CLIENT_ID"] = client_id
    servers_to_patch["lkr_codemode"]["env"]["LOOKERSDK_CLIENT_SECRET"] = client_secret

    for s_name, s_def in servers_to_patch.items():
        config["mcpServers"][s_name] = s_def

    GEMINI_MCP_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(GEMINI_MCP_CONFIG, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print_error(f"Failed to write {GEMINI_MCP_CONFIG}: {e}")
        return False
