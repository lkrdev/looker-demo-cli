"""Tests for the MCP-to-CLI skill refactor, Modular DAG synthesis, and ADC ingestion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from looker_demo_cli.config import INTENT_SKILL_DEFINITIONS
from looker_demo_cli.precheck.mcp_checker import (
    DEPRECATED_MCP_SERVERS,
    check_mcp_servers,
    patch_mcp_config,
)

pytestmark = [pytest.mark.unit]


def test_deprecated_mcp_servers_are_pruned_on_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """patch_mcp_config actively removes deprecated MCP servers and preserves unrelated entries."""
    fake_mcp_config = tmp_path / "mcp_config.json"
    fake_mcp_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "data-designer": {"command": "uvx"},
                    "bigquery": {"serverUrl": "https://bigquery.googleapis.com/mcp"},
                    "knowledge-catalog": {"serverUrl": "https://dataplex.googleapis.com/mcp"},
                    "lkr_codemode": {"command": "lkr"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("looker_demo_cli.precheck.mcp_checker.GEMINI_MCP_CONFIG", fake_mcp_config)

    # Before patching: deprecated servers should be flagged as unconfigured/deprecated
    pre_statuses = check_mcp_servers()
    assert len(pre_statuses) == 3
    assert all(not s.is_configured for s in pre_statuses)

    # Patching prunes deprecated servers while keeping lkr_codemode
    assert patch_mcp_config() is True
    updated_data = json.loads(fake_mcp_config.read_text(encoding="utf-8"))
    for dep in DEPRECATED_MCP_SERVERS:
        assert dep not in updated_data["mcpServers"]
    assert "lkr_codemode" in updated_data["mcpServers"]

    # After patching: all deprecated servers report mode=cli_skill and is_configured=True
    post_statuses = check_mcp_servers()
    assert len(post_statuses) == 3
    assert all(s.is_configured and s.details.get("mode") == "cli_skill" for s in post_statuses)


def test_all_cli_skills_are_bundled_and_registered(tmp_path: Path) -> None:
    """All CLI-only skills including synthetic-data-authoring must exist locally in skills/, map to local_cli, and prune deprecated skills."""
    from looker_demo_cli.config import DEPRECATED_SKILLS
    from looker_demo_cli.precheck.skills_organizer import prune_deprecated_skills

    repo_root = Path(__file__).resolve().parent.parent
    expected_skills = [
        "synthetic-data-authoring",
        "bigquery-metadata",
        "knowledge-catalog-metadata",
    ]
    data_design_defs = INTENT_SKILL_DEFINITIONS.get("data-design", {})
    for skill_name in expected_skills:
        skill_file = repo_root / "skills" / skill_name / "SKILL.md"
        assert skill_file.exists(), f"Missing bundled skill file: {skill_file}"
        assert data_design_defs.get(skill_name) == ("local_cli", skill_name)

    for deprecated in DEPRECATED_SKILLS:
        assert not (repo_root / "skills" / deprecated).exists(), f"Deprecated skill still in repo: {deprecated}"
        assert deprecated not in data_design_defs
        fake_skill = tmp_path / deprecated
        fake_skill.mkdir(parents=True, exist_ok=True)
        (fake_skill / "SKILL.md").write_text("# legacy", encoding="utf-8")

    pruned = prune_deprecated_skills(tmp_path)
    assert {Path(p).name for p in pruned} == set(DEPRECATED_SKILLS)
    for deprecated in DEPRECATED_SKILLS:
        assert not (tmp_path / deprecated).exists()


def test_data_engineer_subagent_exists_and_disallows_mcp() -> None:
    """The data-engineer subagent must exist, disallow call_mcp_tool, and use synthetic-data-authoring."""
    repo_root = Path(__file__).resolve().parent.parent
    subagent_path = repo_root / "skills" / "looker-demo-orchestrator" / "subagents" / "data-engineer.md"
    assert subagent_path.exists(), f"Missing subagent file: {subagent_path}"
    content = subagent_path.read_text(encoding="utf-8")
    assert "disallowedTools:" in content
    assert "- call_mcp_tool" in content
    assert "synthetic-data-authoring" in content


def test_data_generate_preview_mode_does_not_write_parquet(invoke, tmp_path: Path, isolated_cwd) -> None:
    """--preview -n 5 renders sample rows and does not write Parquet files to disk."""
    out_dir = tmp_path / "preview_out"
    result = invoke(
        [
            "data",
            "generate",
            "--domain",
            "finops_analytics",
            "--output-dir",
            str(out_dir),
            "--preview",
            "-n",
            "5",
            "--json",
        ]
    )
    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "SUCCESS"
    assert envelope["data"]["preview"] is True
    assert envelope["data"]["preview_rows"] == 5
    assert len(envelope["data"]["samples"]) > 0
    assert not out_dir.exists()


def test_data_generate_validate_only_and_json_scorecard(invoke, tmp_path: Path, isolated_cwd) -> None:
    """--validate-only --json-scorecard runs DAG + TableValidator and returns structured scorecard."""
    out_dir = tmp_path / "validate_out"
    result = invoke(
        [
            "data",
            "generate",
            "--domain",
            "telemetry_analytics",
            "--row-count",
            "200",
            "--output-dir",
            str(out_dir),
            "--validate-only",
            "--json-scorecard",
        ]
    )
    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "SUCCESS"
    assert envelope["data"]["validate_only"] is True
    assert envelope["data"]["uploaded"] is False

    scorecard = envelope["data"]["scorecard"]
    assert scorecard["status"] == "SUCCESS"
    assert scorecard["domain"] == "telemetry_analytics"
    assert scorecard["records_per_second"] > 0
    for tbl_metric in scorecard["tables"].values():
        assert tbl_metric["pk_uniqueness"] == 1.0
        assert tbl_metric["orphan_fks"] == 0


def test_data_upload_verify_only_skips_reupload(invoke, fake_bigquery, sample_parquet_dir, isolated_cwd) -> None:
    """--verify-only records existing BigQuery row counts without calling load_parquet_table."""
    fake_bigquery.get_table_row_count = lambda self, ds, tbl: 999
    load_calls = []
    orig_load = fake_bigquery.load_parquet_table

    def tracked_load(self, ds, tbl, pf, **kwargs):
        load_calls.append(tbl)
        return orig_load(self, ds, tbl, pf, **kwargs)

    fake_bigquery.load_parquet_table = tracked_load

    result = invoke(
        [
            "data",
            "upload",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--dataset",
            "test_verify_ds",
            "--gcp-project",
            "test-proj",
            "--verify-only",
            "--json",
        ]
    )
    assert result.exit_code == 0, f"Command failed: {result.stdout}"
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "SUCCESS"
    assert len(load_calls) == 0
    assert all(rows == 999 for rows in envelope["data"]["loaded_rows"].values())
