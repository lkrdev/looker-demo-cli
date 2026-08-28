# SPDX-FileCopyrightText: Copyright (c) 2026 lkr.dev. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List
from pydantic import BaseModel, Field

# Global Directory Paths
HOME_DIR = Path.home()
GEMINI_CONFIG_DIR = HOME_DIR / ".gemini" / "config"
GEMINI_SKILLS_DIR = GEMINI_CONFIG_DIR / "skills"
GEMINI_MCP_CONFIG = GEMINI_CONFIG_DIR / "mcp_config.json"
GCLOUD_CONFIG_DIR = HOME_DIR / ".config" / "gcloud"
GCLOUD_CREDS_DB = GCLOUD_CONFIG_DIR / "credentials.db"
GCLOUD_CONFIGS_DIR = GCLOUD_CONFIG_DIR / "configurations"

# Source Repositories
SYNTHETIC_DATA_GEN_REPO = HOME_DIR / "synthetic-data-generator"
LOOKER_EMBED_DEMO_REPO = HOME_DIR / "looker-embed-demo"

# Intent-Based Skill Mappings
INTENT_SKILL_MAPPINGS: Dict[str, Dict[str, Path]] = {
    "data-design": {
        "data-designer": SYNTHETIC_DATA_GEN_REPO / "skills" / "data-designer",
        "data-designer-architect": SYNTHETIC_DATA_GEN_REPO / "skills" / "data-designer-architect",
        "data-designer-engineer": SYNTHETIC_DATA_GEN_REPO / "skills" / "data-designer-engineer",
        "data-designer-evaluator": SYNTHETIC_DATA_GEN_REPO / "skills" / "data-designer-evaluator",
        "vertex-ai": SYNTHETIC_DATA_GEN_REPO / "skills" / "vertex-ai",
    },
    "lookml": {
        "repo-lookml": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "repo-lookml",
        "lookml-model": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "lookml-model",
        "lookml-explore": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "lookml-explore",
        "lookml-view": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "lookml-view",
        "lookml-dashboard": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "lookml-dashboard",
        "lookml-dashboard-to-query": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "lookml-dashboard-to-query",
        "lookml-fields": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "lookml-fields",
        "lookml-liquid": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "lookml-liquid",
        "lookml-access-grants": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "lookml-access-grants",
        "lookml-refinements": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "lookml-refinements",
        "lookml-sets": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "lookml-sets",
        "lookml-tests": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "lookml-tests",
        "embed-themes": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "embed-themes",
    },
    "embed-portal": {
        "looker-demo-orchestrator": HOME_DIR / "looker-demo-cli" / "skills" / "looker-demo-orchestrator",
        "setup-embed-demo": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "setup-embed-demo",
        "customize-frontend": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "customize-frontend",
        "customize-frontend-branding": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "customize-frontend-branding",
        "customize-frontend-theme": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "customize-frontend-theme",
        "customize-frontend-looker-config": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "customize-frontend-looker-config",
        "demo-apis": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "demo-apis",
        "sso-embed": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "sso-embed",
        "looker-sdk-browser": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "looker-sdk-browser",
        "embed-javascript-events-api": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "embed-javascript-events-api",
        "visualization-components": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "visualization-components",
        "update-user-attribute": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "update-user-attribute",
        "localize-frontend": LOOKER_EMBED_DEMO_REPO / ".agents" / "skills" / "localize-frontend",
    },
}

# Default Environment Variables
DEFAULT_LOOKER_INSTANCE_URL = os.getenv("LOOKERSDK_BASE_URL", "https://looker.lukapuka.co")
DEFAULT_LOOKER_CLIENT_ID = os.getenv("LOOKERSDK_CLIENT_ID", "Tv8SQv8tp8ZRVz7mq8Bx")
DEFAULT_LOOKER_CLIENT_SECRET = os.getenv("LOOKERSDK_CLIENT_SECRET", "YwNJYrBDDWHCWTk8vxR84xfd")
DEFAULT_GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "looker-demo-392616")
DEFAULT_GCP_LOCATION = os.getenv("BIGQUERY_LOCATION", "US")


class AppConfig(BaseModel):
    """Global configuration settings for demo-create."""
    looker_base_url: str = DEFAULT_LOOKER_INSTANCE_URL
    looker_client_id: str = DEFAULT_LOOKER_CLIENT_ID
    looker_client_secret: str = DEFAULT_LOOKER_CLIENT_SECRET
    gcp_project_id: str = DEFAULT_GCP_PROJECT
    gcp_location: str = DEFAULT_GCP_LOCATION
    gcp_account: str | None = None
    default_connection_name: str = "default_bigquery_connection"
