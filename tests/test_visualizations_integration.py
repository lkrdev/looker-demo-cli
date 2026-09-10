from __future__ import annotations

import unittest
from pathlib import Path

from looker_demo_cli.generators.lookml_generator import (
    DashboardSpec,
    DashboardTileSpec,
    LookMLGenerator,
    LookMLTableSpec,
)
from looker_demo_cli.precheck.skills_organizer import audit_and_organize_skills


class TestVisualizationsIntegration(unittest.TestCase):
    def test_visualization_skills_registered_and_valid(self):
        """Test that the looker-visualizations suite is registered and each skill has a valid SKILL.md."""
        statuses = audit_and_organize_skills(fix=False)
        skill_map = {s.skill_name: s for s in statuses}

        expected_skills = [
            "looker-visualizations",
            "looker-vis-cartesian",
            "looker-vis-tabular-kpi",
            "looker-vis-specialty-maps",
            "looker-vis-advanced-config",
        ]

        for sk in expected_skills:
            self.assertIn(sk, skill_map, f"Skill {sk} should be registered in INTENT_SKILL_DEFINITIONS")
            st = skill_map[sk]
            self.assertTrue(
                st.is_valid,
                f"Skill {sk} at {st.source_path} should be valid and contain SKILL.md",
            )
            src_path = Path(st.source_path)
            self.assertTrue(src_path.exists(), f"Path {src_path} must exist")
            self.assertTrue((src_path / "SKILL.md").exists(), f"{src_path}/SKILL.md must exist")

    def test_no_hardcoded_local_paths_in_skills(self):
        """Test that looker-visualizations markdown files contain no local machine paths or helltool references."""
        skills_dir = Path(__file__).resolve().parent.parent / "skills" / "looker-visualizations"
        self.assertTrue(skills_dir.exists())

        md_files = list(skills_dir.rglob("*.md"))
        self.assertGreater(len(md_files), 5, "Should find multiple markdown files in looker-visualizations")

        for md in md_files:
            content = md.read_text(encoding="utf-8")
            self.assertNotIn(
                "helltool",
                content,
                f"Found legacy 'helltool' reference in {md.relative_to(skills_dir)}",
            )
            self.assertNotIn(
                "/usr/local/google/home",
                content,
                f"Found hardcoded local path in {md.relative_to(skills_dir)}",
            )

    def test_dashboard_generator_advanced_vis_config(self):
        """Test that LookMLGenerator serializes advanced_vis_config on supported tiles."""
        gen = LookMLGenerator(project_id="test_proj", dataset_id="test_ds")
        tables = [
            LookMLTableSpec(
                table_name="fct_orders",
                table_type="fact",
                primary_key="order_id",
                schema_fields={
                    "order_id": "STRING",
                    "sale_price": "FLOAT64",
                    "cost": "FLOAT64",
                    "status": "STRING",
                    "order_date": "DATE",
                },
            )
        ]

        dash_lkml = gen.generate_default_dashboard_lkml(model_name="ecommerce", tables=tables)

        # Confirm advanced_vis_config is serialized
        self.assertIn("advanced_vis_config:", dash_lkml)
        # Confirm valid Highcharts JSON configuration with borderRadius
        self.assertIn('"chart": {"borderRadius": 8}', dash_lkml)
        # Confirm single_value tiles do not include advanced_vis_config
        lines = dash_lkml.splitlines()
        for i, line in enumerate(lines):
            if "type: single_value" in line:
                # Next few lines shouldn't have advanced_vis_config
                tile_block = "\n".join(lines[i : min(i + 15, len(lines))])
                self.assertNotIn(
                    "advanced_vis_config",
                    tile_block,
                    "single_value tiles must not have advanced_vis_config",
                )


if __name__ == "__main__":
    unittest.main()
