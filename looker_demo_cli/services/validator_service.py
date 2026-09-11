"""Static LookML Dashboard and Visualization Contract Linter.

Audits dashboard LookML files against Looker visualization standards and
Highcharts advanced_vis_config constraints before pushing to Looker.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

SUPPORTED_ADVANCED_VIS_TYPES = {
    "looker_column",
    "looker_bar",
    "looker_line",
    "looker_area",
    "looker_scatter",
    "looker_pie",
    "looker_funnel",
    "looker_timeline",
    "looker_waterfall",
    "looker_boxplot",
    "looker_wordcloud",
    "looker_histogram",
    "looker_bullet",
    "bullet",
    "looker_sankey",
    "sankey",
}

UNSUPPORTED_ADVANCED_VIS_TYPES = {
    "single_value",
    "looker_grid",
    "table",
    "looker_single_record",
    "looker_google_map",
    "looker_geo_choropleth",
    "looker_geo_coordinates",
}


def _check_forbidden_formatter_keys(obj: Any) -> bool:
    """Recursively search for forbidden 'formatter' keys in parsed Highcharts config."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() == "formatter":
                return True
            if _check_forbidden_formatter_keys(v):
                return True
    elif isinstance(obj, list):
        for item in obj:
            if _check_forbidden_formatter_keys(item):
                return True
    return False


def lint_dashboard_structure(
    parsed: Any,
    file_name: str = "dashboard.lookml",
) -> list[str]:
    """Audit parsed dashboard dictionary/list against Looker visualization standards.

    Args:
        parsed: YAML-parsed dictionary or list of dashboards.
        file_name: Originating file name for error reporting.

    Returns:
        List of error and warning diagnostic strings (empty if clean).
    """
    diagnostics: list[str] = []
    dash_list = parsed if isinstance(parsed, list) else [parsed]

    for dash in dash_list:
        if not isinstance(dash, dict):
            continue

        dash_title = dash.get("title") or dash.get("dashboard") or "Untitled Dashboard"

        # 1. Deprecated root crossfilter check
        if "crossfilter" in dash and "crossfilter_enabled" not in dash:
            diagnostics.append(
                f"Dashboard '{dash_title}' in `{file_name}` uses deprecated root `crossfilter: true`. "
                f"Use `crossfilter_enabled: true` instead."
            )

        elements = dash.get("elements", [])
        for el in elements:
            if not isinstance(el, dict):
                continue

            title = el.get("title") or el.get("name") or "Unnamed Tile"
            vis_type = el.get("type", "looker_column")
            limit = el.get("limit")
            adv_config = el.get("advanced_vis_config")

            # 2. looker_donut_multiples check
            if vis_type == "looker_donut_multiples":
                diagnostics.append(
                    f"Tile '{title}' in `{file_name}` uses `type: looker_donut_multiples`. "
                    f"For proportional breakdowns, use `type: looker_pie` with `show_donut: true` "
                    f"and `inner_radius: 50`."
                )

            # 3. looker_pie slice limit check
            if vis_type == "looker_pie" and limit is not None:
                try:
                    limit_val = int(limit)
                    if limit_val > 50:
                        diagnostics.append(
                            f"Tile '{title}' in `{file_name}` has `limit: {limit_val}` (> 50). "
                            f"Looker strictly rejects pie charts with > 50 slices (recommended <= 7)."
                        )
                except (ValueError, TypeError):
                    pass

            # 4. advanced_vis_config validation
            if adv_config is not None:
                # Check for unsupported vis types
                if vis_type in UNSUPPORTED_ADVANCED_VIS_TYPES:
                    diagnostics.append(
                        f"Tile '{title}' in `{file_name}` sets `advanced_vis_config` on "
                        f"unsupported visualization type `{vis_type}`."
                    )

                # Parse JSON
                config_str = str(adv_config).strip()
                try:
                    parsed_json = json.loads(config_str)
                    if _check_forbidden_formatter_keys(parsed_json):
                        diagnostics.append(
                            f"Tile '{title}' in `{file_name}` uses forbidden `formatter` key in `advanced_vis_config`. "
                            f"Use Highcharts `format` strings or Looker's declarative `formatters` array."
                        )
                except json.JSONDecodeError as json_err:
                    diagnostics.append(
                        f"Tile '{title}' in `{file_name}` contains malformed JSON in `advanced_vis_config`: {json_err}"
                    )

    return diagnostics


def lint_dashboard_file(path: Path) -> list[str]:
    """Parse a dashboard LookML file and return visualization contract violations.

    Args:
        path: Path to the ``*.dashboard.lookml`` file.

    Returns:
        List of error strings (empty if clean).
    """
    if not path.exists():
        return [f"Dashboard file `{path.name}` does not exist."]

    try:
        content = path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
        if not parsed:
            return []
        return lint_dashboard_structure(parsed, file_name=path.name)
    except yaml.YAMLError as y_err:
        return [f"Dashboard YAML syntax error in `{path.name}`: {y_err}"]
