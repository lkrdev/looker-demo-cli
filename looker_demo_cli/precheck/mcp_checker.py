from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from looker_demo_cli.config import GEMINI_MCP_CONFIG
from looker_demo_cli.utils.console import print_error, print_warning


class MCPStatus(BaseModel):
    server_name: str
    is_configured: bool
    details: dict[str, Any] = {}
    issues: list[str] = []


REQUIRED_MCP_SERVERS: dict[str, dict[str, Any]] = {
    "data-designer": {
        "command": "uvx",
        "args": [
            "--from",
            "git+https://github.com/lkrdev/synthetic-data-generator.git@main",
            "data-designer-mcp",
        ],
    },
    "bigquery": {
        "serverUrl": "https://bigquery.googleapis.com/mcp",
        "authProviderType": "google_credentials",
    },
    "knowledge-catalog": {
        "serverUrl": "https://dataplex.googleapis.com/mcp",
        "authProviderType": "google_credentials",
    },
}


def read_mcp_config() -> dict[str, Any]:
    """Read global MCP configuration file."""
    if not GEMINI_MCP_CONFIG.exists():
        return {"mcpServers": {}}
    try:
        with open(GEMINI_MCP_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print_warning(f"Could not read {GEMINI_MCP_CONFIG}: {e}")
        return {"mcpServers": {}}


def check_mcp_servers() -> list[MCPStatus]:
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


def patch_mcp_config() -> bool:
    """Inject or repair required MCP servers (data-designer, bigquery) in ~/.gemini/config/mcp_config.json."""
    config = read_mcp_config()
    if "mcpServers" not in config:
        config["mcpServers"] = {}

    for s_name, s_def in REQUIRED_MCP_SERVERS.items():
        if s_name not in config["mcpServers"] or not config["mcpServers"][s_name]:
            config["mcpServers"][s_name] = s_def

    GEMINI_MCP_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(GEMINI_MCP_CONFIG, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print_error(f"Failed to write {GEMINI_MCP_CONFIG}: {e}")
        return False
