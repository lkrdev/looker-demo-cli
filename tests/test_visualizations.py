"""Tests for the shipped visualization skills and dashboard generation.

Two distinct concerns live here:

* **Skill registration** requires the skill source repositories to be present on
  the host, so it is marked ``integration`` and deselected by default.
* **Skill content hygiene** and **dashboard generation** read only files inside
  this repository and are hermetic unit tests.

Converted from ``unittest.TestCase`` to pytest so the host-dependent test can be
marked independently of the hermetic ones -- previously the whole module was
un-runnable in a clean container.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from looker_demo_cli.generators.lookml_generator import LookMLGenerator, LookMLTableSpec
from looker_demo_cli.precheck.skills_organizer import audit_and_organize_skills

VISUALIZATION_SKILLS = [
    "looker-visualizations",
    "looker-vis-cartesian",
    "looker-vis-tabular-kpi",
    "looker-vis-specialty-maps",
    "looker-vis-advanced-config",
]

#: Strings that must never ship in a published skill.
FORBIDDEN_CONTENT = [
    "helltool",  # internal-only tooling reference
    "/usr/local/google/home",  # a developer's absolute home path
]


@pytest.fixture(scope="module")
def visualizations_skill_dir() -> Path:
    """The in-repo ``skills/looker-visualizations`` directory."""
    return Path(__file__).resolve().parent.parent / "skills" / "looker-visualizations"


@pytest.mark.integration
@pytest.mark.parametrize("skill_name", VISUALIZATION_SKILLS)
def test_visualization_skill_is_registered_and_resolvable(skill_name: str) -> None:
    """Each visualization skill resolves to a real directory containing SKILL.md.

    Host-dependent: ``audit_and_organize_skills`` resolves sources against the
    developer's cloned skill repositories under ``$HOME``.
    """
    statuses = {s.skill_name: s for s in audit_and_organize_skills(fix=False)}

    assert skill_name in statuses, f"{skill_name} is missing from INTENT_SKILL_DEFINITIONS"
    status = statuses[skill_name]
    assert status.is_valid, f"{skill_name} at {status.source_path} did not resolve to a valid skill"

    source = Path(status.source_path)
    assert source.exists(), f"{source} must exist"
    assert (source / "SKILL.md").exists(), f"{source}/SKILL.md must exist"


@pytest.mark.unit
def test_visualization_skills_ship_no_local_paths_or_internal_references(
    visualizations_skill_dir: Path,
) -> None:
    """No skill markdown leaks a developer path or internal tool name.

    These files are published to users' machines, so a leaked absolute path is
    both broken guidance and an information disclosure.
    """
    assert visualizations_skill_dir.exists()

    markdown_files = list(visualizations_skill_dir.rglob("*.md"))
    assert len(markdown_files) > 5, "Expected the full visualization skill suite"

    offenders = [
        f"{md.relative_to(visualizations_skill_dir)}: {forbidden!r}"
        for md in markdown_files
        for forbidden in FORBIDDEN_CONTENT
        if forbidden in md.read_text(encoding="utf-8")
    ]
    assert not offenders, "Forbidden content found in shipped skills:\n" + "\n".join(offenders)


@pytest.fixture
def orders_table_spec() -> LookMLTableSpec:
    """A fact table with the field types the default dashboard generator needs."""
    return LookMLTableSpec(
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


@pytest.fixture
def generated_dashboard(orders_table_spec: LookMLTableSpec) -> str:
    """The LookML text of a default generated dashboard."""
    generator = LookMLGenerator(project_id="test_proj", dataset_id="test_ds")
    return generator.generate_default_dashboard_lkml(model_name="ecommerce", tables=[orders_table_spec])


@pytest.mark.unit
def test_generated_dashboard_applies_rounded_highcharts_geometry(generated_dashboard: str) -> None:
    """Cartesian tiles carry the house-style ``borderRadius`` override."""
    assert "advanced_vis_config:" in generated_dashboard
    assert '"chart": {"borderRadius": 8}' in generated_dashboard


@pytest.mark.unit
def test_single_value_tiles_omit_advanced_vis_config(generated_dashboard: str) -> None:
    """KPI tiles must not receive Highcharts overrides.

    ``single_value`` is not a Highcharts visualization; attaching an
    ``advanced_vis_config`` to one causes Looker to render an empty tile.
    """
    lines = generated_dashboard.splitlines()

    for index, line in enumerate(lines):
        if "type: single_value" in line:
            tile_block = "\n".join(lines[index : index + 15])
            assert "advanced_vis_config" not in tile_block, (
                f"single_value tile at line {index + 1} must not set advanced_vis_config"
            )
