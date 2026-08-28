# SPDX-FileCopyrightText: Copyright (c) 2026 lkr.dev. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Tuple
from pydantic import BaseModel

from looker_demo_cli.config import GEMINI_SKILLS_DIR, INTENT_SKILL_MAPPINGS
from looker_demo_cli.utils.console import print_error, print_info, print_success, print_warning


class SkillInstallStatus(BaseModel):
    category: str
    skill_name: str
    source_path: str
    target_path: str
    is_installed: bool
    is_valid: bool


def audit_and_organize_skills(fix: bool = False) -> List[SkillInstallStatus]:
    """Audit and organize skills by intent category into ~/.gemini/config/skills/."""
    GEMINI_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    results: List[SkillInstallStatus] = []

    for category, skills in INTENT_SKILL_MAPPINGS.items():
        category_dir = GEMINI_SKILLS_DIR / category
        if fix:
            category_dir.mkdir(parents=True, exist_ok=True)

        for skill_name, src_path in skills.items():
            target_link = category_dir / skill_name
            flat_target_link = GEMINI_SKILLS_DIR / skill_name

            source_exists = src_path.exists() and (src_path / "SKILL.md").exists()
            link_exists = target_link.exists() or flat_target_link.exists()

            if fix and source_exists:
                # Remove legacy flat symlink or broken symlink if needed
                if target_link.is_symlink() or target_link.exists():
                    try:
                        target_link.unlink()
                    except Exception:
                        pass

                # Also provide root level symlink if needed by Jetski flat discovery
                try:
                    target_link.symlink_to(src_path, target_is_directory=True)
                    link_exists = True
                except Exception as e:
                    print_warning(f"Could not symlink {skill_name} in {category}: {e}")

                # Also symlink at root for fallback
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
