from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel

from looker_demo_cli.config import LOOKER_EMBED_DEMO_REPO, SKILLS_CACHE_DIR
from looker_demo_cli.precheck.env_checker import ensure_gitignore
from looker_demo_cli.utils.console import print_info, print_success


class EmbedConfigOptions(BaseModel):
    demo_name: str
    target_dir: Path
    brand_name: str
    brand_title: str
    looker_instance_url: str
    looker_project_name: str
    lookml_model_name: str
    dashboard_id: str
    agent_id: str = ""
    explore_path: str = ""
    primary_color: str = "#1A73E8"
    accent_color: str = "#4285F4"


class EmbedScaffolder:
    """Clones and customizes a fresh standalone workspace for external embedded demos."""

    @staticmethod
    def _resolve_template_repo() -> Path:
        """Resolve template repo path from local checkout, cache, or clone."""
        if LOOKER_EMBED_DEMO_REPO.exists():
            return LOOKER_EMBED_DEMO_REPO
        cache_path = SKILLS_CACHE_DIR / "looker-embed-demo"
        if cache_path.exists():
            return cache_path

        # Clone on demand if needed
        SKILLS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        urls = [
            "https://github.com/lkrdev/looker-embed-demo.git",
            "https://github.com/LukaFontanilla/looker-embed-demo.git",
        ]
        for url in urls:
            try:
                res = subprocess.run(
                    ["git", "clone", "--depth", "1", url, str(cache_path)], capture_output=True, text=True, env=env
                )
                if res.returncode == 0:
                    return cache_path
            except Exception:
                continue
        raise FileNotFoundError("Could not find or clone looker-embed-demo template repository.")

    @classmethod
    def scaffold_demo_workspace(cls, opts: EmbedConfigOptions) -> Path:
        """Scaffold a new standalone workspace from looker-embed-demo template."""
        source_repo = cls._resolve_template_repo()

        target_dir = opts.target_dir
        if target_dir.exists():
            print_info(f"Target directory `{target_dir}` already exists. Reusing existing folder.")
        else:
            print_info(f"Scaffolding fresh embed portal into `{target_dir}`...")
            shutil.copytree(
                source_repo,
                target_dir,
                ignore=shutil.ignore_patterns(
                    ".git", "node_modules", ".venv", "dist", "build", ".pytest_cache", ".ruff_cache", "scratch"
                ),
            )

        explore_path = opts.explore_path or f"{opts.lookml_model_name}/{opts.lookml_model_name}"
        env_content = f"""# Looker Embed Demo Environment
LOOKER_INSTANCE_URL={opts.looker_instance_url}
VITE_LOOKER_INSTANCE_URL={opts.looker_instance_url}
LOOKERSDK_BASE_URL={opts.looker_instance_url}
LOOKER_PROJECT_NAME={opts.looker_project_name}
LOOKER_CONNECTION_NAME=default_bigquery_connection
VITE_DASHBOARD_ID={opts.dashboard_id}
VITE_CHAT_AGENT_ID={opts.agent_id}
VITE_EXPLORE_PATH={explore_path}
VITE_APP_TITLE={opts.brand_title}
VITE_BRAND_NAME={opts.brand_name}
VITE_PRIMARY_COLOR={opts.primary_color}
VITE_ACCENT_COLOR={opts.accent_color}
"""
        ensure_gitignore(target_dir)
        (target_dir / ".env").write_text(env_content, encoding="utf-8")
        if (target_dir / "frontend").exists():
            (target_dir / "frontend" / ".env").write_text(env_content, encoding="utf-8")

        for rel in ("frontend/src/config/constants.ts", "frontend/src/constants.ts", "src/constants.ts"):
            constants_file = target_dir / rel
            if constants_file.exists():
                content = constants_file.read_text(encoding="utf-8")
                content = content.replace("embed_demo::brand_overview", opts.dashboard_id)
                content = content.replace("embed_demo/order_items", explore_path)
                content = content.replace("embed_demo", opts.lookml_model_name)
                content = content.replace("ea1262d262ab43b1a9bb23152f25c236", opts.agent_id)
                constants_file.write_text(content, encoding="utf-8")

        print_success(f"Embed demo workspace scaffolded at `{target_dir}`.")
        return target_dir
