# SPDX-FileCopyrightText: Copyright (c) 2026 lkr.dev. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import BaseModel

from looker_demo_cli.config import LOOKER_EMBED_DEMO_REPO
from looker_demo_cli.utils.console import print_error, print_info, print_success, print_warning


class EmbedConfigOptions(BaseModel):
    demo_name: str
    target_dir: Path
    brand_name: str
    brand_title: str
    looker_instance_url: str
    looker_project_name: str
    lookml_model_name: str
    dashboard_id: str
    primary_color: str = "#1A73E8"
    accent_color: str = "#4285F4"


class EmbedScaffolder:
    """Clones and customizes a fresh standalone workspace for external embedded demos."""

    @staticmethod
    def scaffold_demo_workspace(opts: EmbedConfigOptions) -> Path:
        """Scaffold a new standalone workspace from looker-embed-demo template."""
        if not LOOKER_EMBED_DEMO_REPO.exists():
            raise FileNotFoundError(f"Source template `{LOOKER_EMBED_DEMO_REPO}` not found.")

        target_dir = opts.target_dir
        if target_dir.exists():
            print_info(f"Target directory `{target_dir}` already exists. Reusing existing folder.")
        else:
            print_info(f"Scaffolding fresh embed portal into `{target_dir}`...")
            # Copy template directory excluding build artifacts and git
            shutil.copytree(
                LOOKER_EMBED_DEMO_REPO,
                target_dir,
                ignore=shutil.ignore_patterns(".git", "node_modules", ".venv", "dist", "build", ".pytest_cache", ".ruff_cache", "scratch"),
            )

        # 1. Update .env
        env_file = target_dir / ".env"
        env_content = f"""# Looker Embed Demo Environment
VITE_LOOKER_INSTANCE_URL={opts.looker_instance_url}
LOOKERSDK_BASE_URL={opts.looker_instance_url}
LOOKER_PROJECT_NAME={opts.looker_project_name}
LOOKER_CONNECTION_NAME=default_bigquery_connection
VITE_APP_TITLE={opts.brand_title}
VITE_BRAND_NAME={opts.brand_name}
"""
        env_file.write_text(env_content, encoding="utf-8")

        # 2. Update src/constants.ts if exists
        constants_file = target_dir / "src" / "constants.ts"
        if constants_file.exists():
            content = constants_file.read_text(encoding="utf-8")
            # Replace dashboard ID and model name
            content = content.replace("embed_demo", opts.lookml_model_name)
            constants_file.write_text(content, encoding="utf-8")

        print_success(f"Embed demo workspace scaffolded at `{target_dir}`.")
        return target_dir
