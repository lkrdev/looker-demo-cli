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
    "lookml-filtered-measures",
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


@pytest.mark.unit
def test_default_dashboard_uses_looker_pie_with_donut(generated_dashboard: str) -> None:
    """Proportional category breakdown uses looker_pie with show_donut: true."""
    assert "type: looker_pie" in generated_dashboard
    assert "show_donut: true" in generated_dashboard
    assert "inner_radius: 50" in generated_dashboard
    assert "type: looker_donut_multiples" not in generated_dashboard


@pytest.mark.unit
def test_validator_catches_donut_multiples() -> None:
    from looker_demo_cli.services.validator_service import lint_dashboard_structure

    bad_dash = {
        "dashboard": "bad_dash",
        "title": "Bad Dashboard",
        "elements": [
            {
                "title": "Bad Donut",
                "type": "looker_donut_multiples",
                "fields": ["orders.category", "orders.count"],
            }
        ],
    }
    diagnostics = lint_dashboard_structure(bad_dash)
    assert len(diagnostics) == 1
    assert "looker_donut_multiples" in diagnostics[0]
    assert "looker_pie" in diagnostics[0]


@pytest.mark.unit
def test_validator_catches_pie_limit_over_50() -> None:
    from looker_demo_cli.services.validator_service import lint_dashboard_structure

    bad_pie = {
        "dashboard": "bad_pie",
        "title": "Bad Pie",
        "elements": [
            {
                "title": "Overloaded Pie",
                "type": "looker_pie",
                "fields": ["orders.sku", "orders.count"],
                "limit": 100,
            }
        ],
    }
    diagnostics = lint_dashboard_structure(bad_pie)
    assert len(diagnostics) == 1
    assert "limit: 100" in diagnostics[0]


@pytest.mark.unit
def test_validator_catches_formatter_key_in_advanced_vis_config() -> None:
    from looker_demo_cli.services.validator_service import lint_dashboard_structure

    bad_config = {
        "dashboard": "bad_adv",
        "title": "Bad Advanced",
        "elements": [
            {
                "title": "Bar Chart",
                "type": "looker_bar",
                "fields": ["orders.category", "orders.total_sales"],
                "advanced_vis_config": '{"yAxis": [{"labels": {"formatter": "function() {}"}}]}',
            }
        ],
    }
    diagnostics = lint_dashboard_structure(bad_config)
    assert len(diagnostics) == 1
    assert "forbidden `formatter` key" in diagnostics[0]


@pytest.mark.unit
def test_validator_catches_advanced_vis_config_on_unsupported_vis() -> None:
    from looker_demo_cli.services.validator_service import lint_dashboard_structure

    bad_kpi = {
        "dashboard": "bad_kpi",
        "title": "Bad KPI",
        "elements": [
            {
                "title": "KPI Card",
                "type": "single_value",
                "fields": ["orders.total_sales"],
                "advanced_vis_config": '{"chart": {"borderRadius": 8}}',
            }
        ],
    }
    diagnostics = lint_dashboard_structure(bad_kpi)
    assert len(diagnostics) == 1
    assert "unsupported visualization type `single_value`" in diagnostics[0]


@pytest.mark.unit
def test_validator_passes_clean_generated_dashboard(generated_dashboard: str) -> None:
    import yaml

    from looker_demo_cli.services.validator_service import lint_dashboard_structure, lint_dashboard_warnings

    parsed = yaml.safe_load(generated_dashboard)
    diagnostics = lint_dashboard_structure(parsed)
    assert diagnostics == []
    warnings = lint_dashboard_warnings(parsed)
    assert warnings == []


@pytest.mark.unit
def test_generated_dashboard_includes_executive_polish_defaults(generated_dashboard: str) -> None:
    """Default dashboard includes theme-inheriting text headers, centered legends, dual-axis, and transparent grids."""
    assert "type: text" in generated_dashboard
    assert "title_text:" in generated_dashboard
    assert "subtitle_text:" in generated_dashboard
    assert "legend_position: center" in generated_dashboard
    assert "table_theme: transparent" in generated_dashboard
    assert "series_cell_visualizations:" in generated_dashboard
    assert "y_axis_combined: false" in generated_dashboard
    assert "y_axis_unpinned: true" in generated_dashboard


@pytest.mark.unit
def test_validator_warns_on_non_centered_legends_and_non_transparent_grids() -> None:
    from looker_demo_cli.services.validator_service import lint_dashboard_warnings

    unpolished_dash = {
        "dashboard": "unpolished",
        "title": "Unpolished Dashboard",
        "elements": [
            {
                "title": "Hero Banner",
                "type": "text",
                "body_text": '<div style="background: linear-gradient(90deg, #000, #333); color: #fff;">Banner</div>',
            },
            {
                "title": "Right Legend Chart",
                "type": "looker_area",
                "legend_position": "right",
            },
            {
                "title": "White Grid",
                "type": "looker_grid",
                "table_theme": "white",
            },
        ],
    }
    warnings = lint_dashboard_warnings(unpolished_dash)
    assert len(warnings) == 3
    assert any("hardcoded HTML background/gradient" in w for w in warnings)
    assert any("legend_position: right" in w for w in warnings)
    assert any("table_theme: white" in w for w in warnings)


@pytest.mark.unit
def test_filtered_measure_auditor_catches_zero_row_literals_and_passes_valid_expressions(tmp_path: Path) -> None:
    import pandas as pd

    from looker_demo_cli.services.validator_service import validate_filtered_measures_in_lookml

    # 1. Create a local Parquet dataset with realistic descriptive literals
    parquet_dir = tmp_path / "scratch"
    parquet_dir.mkdir(parents=True)
    df = pd.DataFrame(
        {
            "request_id": ["r1", "r2", "r3", "r4"],
            "status_class": ["2xx Success", "4xx Client Error", "4xx Client Error", "5xx Server Error"],
            "http_status_code": [200, 404, 429, 503],
        }
    )
    df.to_parquet(parquet_dir / "fct_api_requests.parquet")

    # 2. Write a LookML view containing both broken shorthand filters and valid Looker expressions
    lookml_dir = tmp_path / "lookml"
    views_dir = lookml_dir / "views"
    views_dir.mkdir(parents=True)

    view_lkml = """
    view: fct_api_requests {
      sql_table_name: `proj.dataset.fct_api_requests` ;;

      dimension: status_class {
        type: string
        sql: ${TABLE}.status_class ;;
      }

      dimension: http_status_code {
        type: number
        sql: ${TABLE}.http_status_code ;;
      }

      measure: bad_shorthand_2xx {
        type: count
        filters: [status_class: "2xx"]
      }

      measure: valid_exact_2xx {
        type: count
        filters: [status_class: "2xx Success"]
      }

      measure: valid_wildcard_2xx {
        type: count
        filters: [status_class: "2xx%"]
      }

      measure: valid_negation_4xx_excl_429 {
        type: count
        filters: [status_class: "4xx Client Error", http_status_code: "-429"]
      }

      measure: valid_or_list_5xx {
        type: count
        filters: [http_status_code: "500, 502, 503, 504"]
      }
    }
    """
    (views_dir / "fct_api_requests.view.lkml").write_text(view_lkml, encoding="utf-8")

    errors = validate_filtered_measures_in_lookml(
        lookml_dir=lookml_dir,
        parquet_dirs=[parquet_dir],
    )

    assert len(errors) == 1
    assert "bad_shorthand_2xx" in errors[0]
    assert 'status_class: "2xx"' in errors[0]
    assert "2xx Success" in errors[0]
