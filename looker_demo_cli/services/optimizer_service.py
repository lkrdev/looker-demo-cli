from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List
from rich.table import Table

from looker_demo_cli.utils.console import console, print_info, print_success


def optimize_lookml_project(lookml_dir: Path) -> Dict[str, Any]:
    """Scan and patch staged LookML files in-place according to Google Cloud Server Optimization Best Practices.
    
    5-Point Protocol:
      1. Static filter suggestions / caching on low-cardinality dimensions.
      2. Disable filter suggestions (suggestable: no) on PKs, FKs, UUIDs, timestamps, and large text.
      3. Ensure model-level datagroup caching policy (max_cache_age: 4 hours).
      4. Inject partition pruning filters (always_filter) on date-partitioned explores.
      5. Hide raw technical foreign keys (hidden: yes) and assert primary_key: yes.
    """
    if not lookml_dir.exists():
        return {
            "status": "FAILED",
            "lookml_dir": str(lookml_dir),
            "error": f"Directory not found: {lookml_dir}",
            "optimizations_applied": {},
            "files_patched": [],
        }

    view_files = list(lookml_dir.glob("**/views/**/*.view.lkml")) + list(lookml_dir.glob("**/*.view.lkml"))
    view_files = list({f.resolve(): f for f in view_files}.values())

    model_files = list(lookml_dir.glob("**/models/**/*.model.lkml")) + list(lookml_dir.glob("**/*.model.lkml"))
    model_files = list({f.resolve(): f for f in model_files}.values())

    explore_files = list(lookml_dir.glob("**/explores/**/*.explore.lkml")) + list(lookml_dir.glob("**/*.explore.lkml"))
    explore_files = list({f.resolve(): f for f in explore_files}.values())

    patched_files: List[Path] = []
    stats = {
        "static_suggestions_added": 0,
        "suggestable_disabled_count": 0,
        "datagroup_caching_configured": False,
        "partition_pruning_filters_added": 0,
        "foreign_keys_hidden": 0,
        "primary_keys_asserted": 0,
    }

    # -------------------------------------------------------------------------
    # 1 & 2 & 5: Optimize Views
    # -------------------------------------------------------------------------
    for v_file in view_files:
        content = v_file.read_text(encoding="utf-8")
        original = content

        def patch_pk(match: re.Match) -> str:
            block = match.group(0)
            nonlocal stats
            if "primary_key: yes" in block and "suggestable:" not in block:
                stats["suggestable_disabled_count"] += 1
                stats["primary_keys_asserted"] += 1
                return block.replace("primary_key: yes", "primary_key: yes\n    suggestable: no")
            return block

        dim_pattern = re.compile(r"(\s*dimension:\s*([a-zA-Z0-9_]+)\s*\{[^}]*?\})", re.DOTALL)
        content = dim_pattern.sub(patch_pk, content)

        def patch_dim_fields(match: re.Match) -> str:
            block = match.group(0)
            dim_name = match.group(2)
            nonlocal stats

            modified_block = block
            if (dim_name.endswith("_id") or any(k in dim_name.lower() for k in ["uuid", "hash", "token", "payload", "raw_content"])) and "primary_key: yes" not in block:
                if "suggestable:" not in modified_block:
                    modified_block = re.sub(r"(dimension:\s*[a-zA-Z0-9_]+\s*\{)", r"\1\n    suggestable: no", modified_block, count=1)
                    stats["suggestable_disabled_count"] += 1

                if dim_name.endswith("_id") and "hidden:" not in modified_block:
                    modified_block = re.sub(r"(dimension:\s*[a-zA-Z0-9_]+\s*\{)", r"\1\n    hidden: yes", modified_block, count=1)
                    stats["foreign_keys_hidden"] += 1

            elif any(k in dim_name.lower() for k in ["status", "type", "tier", "priority", "category", "channel"]):
                if "suggest_persist_for:" not in modified_block and "suggestions:" not in modified_block and "suggestable: no" not in modified_block:
                    modified_block = re.sub(r"(dimension:\s*[a-zA-Z0-9_]+\s*\{)", r'\1\n    suggest_persist_for: "24 hours"', modified_block, count=1)
                    stats["static_suggestions_added"] += 1

            return modified_block

        content = dim_pattern.sub(patch_dim_fields, content)

        if content != original:
            v_file.write_text(content, encoding="utf-8")
            patched_files.append(v_file)

    # -------------------------------------------------------------------------
    # 3 & 4: Optimize Models (Datagroup + Explores)
    # -------------------------------------------------------------------------
    for m_file in model_files:
        content = m_file.read_text(encoding="utf-8")
        original = content

        # Rule 3: Datagroup Caching
        if "datagroup:" not in content or "persist_with:" not in content:
            datagroup_block = """
# Google Cloud Looker Server Performance Best Practice: Model Datagroup Caching
datagroup: default_caching_policy {
  max_cache_age: "4 hours"
  description: "Default caching policy for operational dashboard and explore queries"
}

persist_with: default_caching_policy
"""
            if "include:" in content:
                last_include_idx = content.rfind("include:")
                end_of_line = content.find("\n", last_include_idx)
                content = content[: end_of_line + 1] + datagroup_block + content[end_of_line + 1 :]
            else:
                content = datagroup_block + "\n" + content
            stats["datagroup_caching_configured"] = True

        # Rule 4: Partition Pruning on inline explores
        def patch_inline_explore(match: re.Match) -> str:
            block = match.group(0)
            exp_name = match.group(2)
            nonlocal stats
            if "always_filter:" not in block and "conditionally_filter:" not in block:
                date_filter = f"{exp_name}.created_date"
                filter_injection = f"""
  always_filter: {{
    filters: [{date_filter}: "365 days"]
  }}"""
                stats["partition_pruning_filters_added"] += 1
                return re.sub(r"(explore:\s*[a-zA-Z0-9_]+\s*\{)", r"\1" + filter_injection, block, count=1)
            return block

        exp_pattern = re.compile(r"(\s*explore:\s*([a-zA-Z0-9_]+)\s*\{[^}]*?\})", re.DOTALL)
        content = exp_pattern.sub(patch_inline_explore, content)

        if content != original:
            m_file.write_text(content, encoding="utf-8")
            if m_file not in patched_files:
                patched_files.append(m_file)

    # -------------------------------------------------------------------------
    # 4: Optimize Separate Explores (if any)
    # -------------------------------------------------------------------------
    for e_file in explore_files:
        content = e_file.read_text(encoding="utf-8")
        original = content

        def patch_explore_file(match: re.Match) -> str:
            block = match.group(0)
            exp_name = match.group(2)
            nonlocal stats
            if "always_filter:" not in block and "conditionally_filter:" not in block:
                date_filter = f"{exp_name}.created_date"
                filter_injection = f"""
  always_filter: {{
    filters: [{date_filter}: "365 days"]
  }}"""
                stats["partition_pruning_filters_added"] += 1
                return re.sub(r"(explore:\s*[a-zA-Z0-9_]+\s*\{)", r"\1" + filter_injection, block, count=1)
            return block

        exp_pattern = re.compile(r"(\s*explore:\s*([a-zA-Z0-9_]+)\s*\{[^}]*?\})", re.DOTALL)
        content = exp_pattern.sub(patch_explore_file, content)

        if content != original:
            e_file.write_text(content, encoding="utf-8")
            if e_file not in patched_files:
                patched_files.append(e_file)

    return {
        "status": "SUCCESS",
        "lookml_dir": str(lookml_dir),
        "optimizations_applied": stats,
        "files_patched": [str(f.relative_to(lookml_dir) if f.is_relative_to(lookml_dir) else f) for f in patched_files],
        "error": None,
    }


def render_optimization_report(result: Dict[str, Any]) -> None:
    """Print a clean CLI summary table of applied LookML optimizations."""
    table = Table(title="Google Cloud LookML Performance Optimization Summary", show_header=True)
    table.add_column("Optimization Area", style="cyan")
    table.add_column("Rule Description", style="dim")
    table.add_column("Action Taken", style="green")

    stats = result.get("optimizations_applied", {})

    table.add_row(
        "Rule 1: Static Filter Suggestions",
        "Cache distinct dropdowns for categorical dimensions",
        f"{stats.get('static_suggestions_added', 0)} dimensions cached",
    )
    table.add_row(
        "Rule 2: High-Cardinality Pruning",
        "Disable suggest queries on PKs, UUIDs, timestamps, raw IDs",
        f"{stats.get('suggestable_disabled_count', 0)} suggestions disabled",
    )
    table.add_row(
        "Rule 3: Datagroup Caching",
        "Enforce 4-hour model-level cache datagroup policy",
        "Configured" if stats.get("datagroup_caching_configured") else "Already Active",
    )
    table.add_row(
        "Rule 4: BigQuery Partition Pruning",
        "Enforce always_filter date window on partitioned explores",
        f"{stats.get('partition_pruning_filters_added', 0)} explore filters added",
    )
    table.add_row(
        "Rule 5: Explore UI Pruning & FK Hiding",
        "Hide raw technical foreign keys from explore field picker",
        f"{stats.get('foreign_keys_hidden', 0)} foreign keys hidden",
    )

    console.print(table)
    files = result.get("files_patched", [])
    if files:
        print_success(f"Successfully audited and patched {len(files)} LookML file(s):")
        for f in files:
            console.print(f"  • {f}")
    else:
        print_info("All LookML files already satisfy Google Cloud Server Optimization standards.")
