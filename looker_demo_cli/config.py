from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Tuple
from pydantic import BaseModel, Field

# Global Directory Paths
HOME_DIR = Path.home()
GEMINI_CONFIG_DIR = HOME_DIR / ".gemini" / "config"
GEMINI_SKILLS_DIR = GEMINI_CONFIG_DIR / "skills"
GEMINI_MCP_CONFIG = GEMINI_CONFIG_DIR / "mcp_config.json"
GCLOUD_CONFIG_DIR = HOME_DIR / ".config" / "gcloud"
GCLOUD_CREDS_DB = GCLOUD_CONFIG_DIR / "credentials.db"
GCLOUD_CONFIGS_DIR = GCLOUD_CONFIG_DIR / "configurations"

SKILLS_CACHE_DIR = HOME_DIR / ".cache" / "looker-demo-cli" / "skills-repos"
SYNTHETIC_DATA_GEN_REPO = Path(os.getenv("SYNTHETIC_DATA_GEN_PATH", str(HOME_DIR / "synthetic-data-generator")))
LOOKER_EMBED_DEMO_REPO = Path(os.getenv("LOOKER_EMBED_DEMO_PATH", str(HOME_DIR / "looker-embed-demo")))

# Remote Repositories for Automatic Skill Syncing
SKILL_GIT_REPOSITORIES: Dict[str, Dict[str, Any]] = {
    "synthetic-data-generator": {
        "urls": [
            "https://github.com/lkrdev/synthetic-data-generator.git",
            "https://github.com/LukaFontanilla/synthetic-data-generator.git",
        ],
        "skills_subpath": "skills",
        "env_var": "SYNTHETIC_DATA_GEN_PATH",
        "local_default": HOME_DIR / "synthetic-data-generator",
    },
    "looker-embed-demo": {
        "urls": [
            "https://github.com/lkrdev/looker-embed-demo.git",
            "https://github.com/LukaFontanilla/looker-embed-demo.git",
        ],
        "skills_subpath": ".agents/skills",
        "env_var": "LOOKER_EMBED_DEMO_PATH",
        "local_default": HOME_DIR / "looker-embed-demo",
    },
    "lkr-cli": {
        "urls": [
            "https://github.com/lkrdev/cli.git",
        ],
        "skills_subpath": "skills",
        "env_var": "LOOKER_CLI_PATH",
        "local_default": HOME_DIR / "lkr-cli",
    },
}

# Intent-Based Skill Mappings (category -> skill_name -> (repo_key, relative_skill_subfolder))
INTENT_SKILL_DEFINITIONS: Dict[str, Dict[str, Tuple[str, str]]] = {
    "data-design": {
        "data-designer": ("synthetic-data-generator", "data-designer"),
        "data-designer-architect": ("synthetic-data-generator", "data-designer-architect"),
        "data-designer-engineer": ("synthetic-data-generator", "data-designer-engineer"),
        "data-designer-evaluator": ("synthetic-data-generator", "data-designer-evaluator"),
        "vertex-ai": ("synthetic-data-generator", "vertex-ai"),
    },
    "lookml": {
        "lkr-code-mode": ("lkr-cli", "lkr-code-mode"),
        "repo-lookml": ("looker-embed-demo", "repo-lookml"),
        "lookml-model": ("looker-embed-demo", "lookml-model"),
        "lookml-explore": ("looker-embed-demo", "lookml-explore"),
        "lookml-view": ("looker-embed-demo", "lookml-view"),
        "lookml-dashboard": ("looker-embed-demo", "lookml-dashboard"),
        "lookml-dashboard-to-query": ("looker-embed-demo", "lookml-dashboard-to-query"),
        "lookml-fields": ("looker-embed-demo", "lookml-fields"),
        "lookml-liquid": ("looker-embed-demo", "lookml-liquid"),
        "lookml-access-grants": ("looker-embed-demo", "lookml-access-grants"),
        "lookml-refinements": ("looker-embed-demo", "lookml-refinements"),
        "lookml-sets": ("looker-embed-demo", "lookml-sets"),
        "lookml-tests": ("looker-embed-demo", "lookml-tests"),
        "embed-themes": ("looker-embed-demo", "embed-themes"),
    },
    "embed-portal": {
        "looker-demo-orchestrator": ("local_cli", "looker-demo-orchestrator"),
        "setup-embed-demo": ("looker-embed-demo", "setup-embed-demo"),
        "customize-frontend": ("looker-embed-demo", "customize-frontend"),
        "customize-frontend-branding": ("looker-embed-demo", "customize-frontend-branding"),
        "customize-frontend-theme": ("looker-embed-demo", "customize-frontend-theme"),
        "customize-frontend-looker-config": ("looker-embed-demo", "customize-frontend-looker-config"),
        "demo-apis": ("looker-embed-demo", "demo-apis"),
        "sso-embed": ("looker-embed-demo", "sso-embed"),
        "looker-sdk-browser": ("looker-embed-demo", "looker-sdk-browser"),
        "embed-javascript-events-api": ("looker-embed-demo", "embed-javascript-events-api"),
        "visualization-components": ("looker-embed-demo", "visualization-components"),
        "update-user-attribute": ("looker-embed-demo", "update-user-attribute"),
        "localize-frontend": ("looker-embed-demo", "localize-frontend"),
    },
}

def get_looker_credentials_from_mcp() -> Dict[str, str]:
    """Extract Looker credentials from ~/.gemini/config/mcp_config.json if present."""
    import json
    if not GEMINI_MCP_CONFIG.exists():
        return {}
    try:
        with open(GEMINI_MCP_CONFIG, "r", encoding="utf-8") as f:
            data = json.load(f)
            lkr_env = data.get("mcpServers", {}).get("lkr_codemode", {}).get("env", {})
            return {
                "base_url": lkr_env.get("LOOKERSDK_BASE_URL", ""),
                "client_id": lkr_env.get("LOOKERSDK_CLIENT_ID", ""),
                "client_secret": lkr_env.get("LOOKERSDK_CLIENT_SECRET", ""),
            }
    except Exception:
        return {}


_mcp_looker = get_looker_credentials_from_mcp()

# Default Environment Variables (loaded from env, MCP config, or set to generic placeholders)
DEFAULT_LOOKER_INSTANCE_URL = os.getenv("LOOKERSDK_BASE_URL") or _mcp_looker.get("base_url", "")
DEFAULT_LOOKER_CLIENT_ID = os.getenv("LOOKERSDK_CLIENT_ID") or _mcp_looker.get("client_id", "")
DEFAULT_LOOKER_CLIENT_SECRET = os.getenv("LOOKERSDK_CLIENT_SECRET") or _mcp_looker.get("client_secret", "")
DEFAULT_GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
DEFAULT_GCP_LOCATION = os.getenv("BIGQUERY_LOCATION", "US")


class AppConfig(BaseModel):
    """Global configuration settings for demo-create."""
    looker_base_url: str = Field(default_factory=lambda: DEFAULT_LOOKER_INSTANCE_URL)
    looker_client_id: str = Field(default_factory=lambda: DEFAULT_LOOKER_CLIENT_ID)
    looker_client_secret: str = Field(default_factory=lambda: DEFAULT_LOOKER_CLIENT_SECRET)
    gcp_project_id: str = Field(default_factory=lambda: DEFAULT_GCP_PROJECT)
    gcp_location: str = DEFAULT_GCP_LOCATION
    gcp_account: str | None = None
    default_connection_name: str = "default_bigquery_connection"
