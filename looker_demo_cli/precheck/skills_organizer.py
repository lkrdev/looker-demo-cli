from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel

from looker_demo_cli.config import (
    GEMINI_SKILLS_DIR,
    INTENT_SKILL_DEFINITIONS,
    SKILL_GIT_REPOSITORIES,
    SKILLS_CACHE_DIR,
)
from looker_demo_cli.utils.console import print_error, print_info, print_success, print_warning


class SkillInstallStatus(BaseModel):
    category: str
    skill_name: str
    source_path: str
    target_path: str
    is_installed: bool
    is_valid: bool


def sync_remote_skill_repos(fix: bool = False) -> Dict[str, Path]:
    """Ensure remote skill repositories are cloned or updated to the latest revision in cache."""
    resolved_repo_paths: Dict[str, Path] = {}
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    for repo_key, repo_info in SKILL_GIT_REPOSITORIES.items():
        env_var = repo_info.get("env_var")
        local_override = os.getenv(env_var) if env_var else None

        # 1. Check user-defined local override
        if local_override and Path(local_override).exists():
            resolved_repo_paths[repo_key] = Path(local_override)
            continue

        # 2. Check local default checkout (e.g. ~/synthetic-data-generator)
        local_default = repo_info.get("local_default")
        if local_default and Path(local_default).exists():
            resolved_repo_paths[repo_key] = Path(local_default)
            continue

        # 3. Use/Populate Git Cache directory
        cache_path = SKILLS_CACHE_DIR / repo_key
        resolved_repo_paths[repo_key] = cache_path

        if not fix:
            continue

        SKILLS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if cache_path.exists() and (cache_path / ".git").exists():
            try:
                res = subprocess.run(
                    ["git", "-C", str(cache_path), "pull", "--ff-only"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=15,
                )
                if res.returncode == 0:
                    print_info(f"Updated skill repository `{repo_key}` to latest commit.")
            except Exception as e:
                print_warning(f"Notice while updating `{repo_key}` from git: {e}")
        else:
            # Clone from available remote URLs
            urls = repo_info.get("urls", [])
            cloned = False
            for url in urls:
                try:
                    res = subprocess.run(
                        ["git", "clone", "--depth", "1", url, str(cache_path)],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=20,
                    )
                    if res.returncode == 0:
                        print_success(f"Cloned latest skills from `{url}` into cache.")
                        cloned = True
                        break
                except Exception:
                    continue
            if not cloned and not cache_path.exists():
                print_warning(f"Could not clone `{repo_key}` from remote URLs. Skills may need local checkouts.")

    return resolved_repo_paths


def audit_and_organize_skills(fix: bool = False) -> List[SkillInstallStatus]:
    """Audit and organize skills by intent category into ~/.gemini/config/skills/."""
    GEMINI_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    repo_paths = sync_remote_skill_repos(fix=fix)
    local_cli_root = Path(__file__).resolve().parent.parent.parent

    results: List[SkillInstallStatus] = []

    for category, skills in INTENT_SKILL_DEFINITIONS.items():
        category_dir = GEMINI_SKILLS_DIR / category
        if fix:
            category_dir.mkdir(parents=True, exist_ok=True)

        for skill_name, (repo_key, skill_rel_folder) in skills.items():
            if repo_key == "local_cli":
                src_path = local_cli_root / "skills" / skill_name
            else:
                base_repo = repo_paths.get(repo_key, SKILLS_CACHE_DIR / repo_key)
                subpath = SKILL_GIT_REPOSITORIES.get(repo_key, {}).get("skills_subpath", "skills")
                src_path = base_repo / subpath / skill_rel_folder

            target_link = category_dir / skill_name
            flat_target_link = GEMINI_SKILLS_DIR / skill_name

            source_exists = src_path.exists() and (src_path / "SKILL.md").exists()
            link_exists = (target_link.exists() and (target_link / "SKILL.md").exists()) or (
                flat_target_link.exists() and (flat_target_link / "SKILL.md").exists()
            )

            if fix and source_exists:
                # Remove legacy flat symlink or broken symlink if needed
                if target_link.is_symlink() or target_link.exists():
                    try:
                        if target_link.is_symlink():
                            target_link.unlink()
                    except Exception:
                        pass

                try:
                    target_link.symlink_to(src_path, target_is_directory=True)
                    link_exists = True
                except Exception as e:
                    print_warning(f"Could not symlink {skill_name} in {category}: {e}")

                # Also symlink at root for flat skill discovery compatibility
                if not flat_target_link.exists():
                    try:
                        flat_target_link.symlink_to(src_path, target_is_directory=True)
                    except Exception:
                        pass

            results.append(
                SkillInstallStatus(
                    category=category,
                    skill_name=skill_name,
                    source_path=str(src_path),
                    target_path=str(target_link),
                    is_installed=link_exists,
                    is_valid=source_exists,
                )
            )

    return results
