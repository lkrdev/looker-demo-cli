"""Static LookML Dashboard, Visualization Contract, and Filtered Measure Auditor.

Audits dashboard LookML files against Looker visualization standards, Highcharts
advanced_vis_config constraints, Executive UI Polish guidelines, and verifies
LookML view filtered measure literals against actual Parquet/BigQuery distinct values.
"""

from __future__ import annotations

import json
import math
import re
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

CHART_TYPES_WITH_LEGENDS = {
    "looker_area",
    "looker_column",
    "looker_bar",
    "looker_line",
    "looker_pie",
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
    """Audit parsed dashboard dictionary/list for hard Looker contract violations.

    Args:
        parsed: YAML-parsed dictionary or list of dashboards.
        file_name: Originating file name for error reporting.

    Returns:
        List of hard error diagnostic strings (empty if clean).
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


def lint_dashboard_warnings(
    parsed: Any,
    file_name: str = "dashboard.lookml",
) -> list[str]:
    """Audit parsed dashboard dictionary/list for non-blocking Executive Polish warnings.

    Checks:
    1. Legend centering on cartesian/pie charts (`legend_position: center`).
    2. Transparent table theme on data grids (`table_theme: transparent`).
    3. Absence of hardcoded HTML color banners/gradients in `type: text` tiles.

    Args:
        parsed: YAML-parsed dictionary or list of dashboards.
        file_name: Originating file name for warning reporting.

    Returns:
        List of non-blocking warning strings (empty if fully polished).
    """
    warnings: list[str] = []
    dash_list = parsed if isinstance(parsed, list) else [parsed]

    for dash in dash_list:
        if not isinstance(dash, dict):
            continue

        elements = dash.get("elements", [])
        for el in elements:
            if not isinstance(el, dict):
                continue

            title = el.get("title") or el.get("name") or el.get("title_text") or "Unnamed Tile"
            vis_type = el.get("type", "looker_column")

            # 1. Centered legend check
            if vis_type in CHART_TYPES_WITH_LEGENDS:
                legend_pos = el.get("legend_position")
                if legend_pos and str(legend_pos).lower() in ("left", "right"):
                    warnings.append(
                        f"Tile '{title}' in `{file_name}` sets `legend_position: {legend_pos}`. "
                        f"Executive polish standard recommends `legend_position: center`."
                    )

            # 2. Transparent table theme check
            if vis_type == "looker_grid":
                theme = el.get("table_theme")
                if theme and str(theme).lower() != "transparent":
                    warnings.append(
                        f"Grid tile '{title}' in `{file_name}` sets `table_theme: {theme}`. "
                        f"Executive polish standard recommends `table_theme: transparent`."
                    )

            # 3. Theme-inheriting text headers (no hardcoded HTML background gradients/colors)
            if vis_type == "text" or "body_text" in el:
                body = str(el.get("body_text") or "")
                title_txt = str(el.get("title_text") or "")
                combined_html = f"{title_txt} {body}".lower()
                if (
                    "linear-gradient" in combined_html
                    or "background-color:" in combined_html
                    or "background:" in combined_html
                ):
                    warnings.append(
                        f"Text tile '{title}' in `{file_name}` contains hardcoded HTML background/gradient styles. "
                        f"Use theme-inheriting native `title_text` and `subtitle_text` instead."
                    )

    return warnings


def lint_dashboard_file(path: Path) -> list[str]:
    """Parse a dashboard LookML file and return hard visualization contract violations.

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


def lint_dashboard_file_warnings(path: Path) -> list[str]:
    """Parse a dashboard LookML file and return non-blocking Executive Polish warnings."""
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
        if not parsed:
            return []
        return lint_dashboard_warnings(parsed, file_name=path.name)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# LookML Filtered Measure Distinct-Value Auditor
# ---------------------------------------------------------------------------


def _is_null_or_nan(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    return False


def _match_single_positive_term(val: Any, term: str) -> bool:
    """Evaluate a single positive Looker filter term against a scalar value."""
    upper_term = term.strip().upper()
    if upper_term == "NULL":
        return _is_null_or_nan(val)
    if upper_term == "NOT NULL":
        return not _is_null_or_nan(val)
    if upper_term == "EMPTY":
        return not _is_null_or_nan(val) and str(val) == ""

    if _is_null_or_nan(val):
        return False

    # Check numeric range / comparison operators (e.g. "> 100", ">= 0 AND < 50")
    if " AND " in upper_term and any(op in upper_term for op in (">", "<", "=")):
        sub_clauses = [c.strip() for c in re.split(r"\bAND\b", term, flags=re.IGNORECASE)]
        return all(_match_single_positive_term(val, sc) for sc in sub_clauses)

    num_match = re.match(r"^(>=|<=|>|<|=)\s*([+-]?\d+(?:\.\d+)?)$", term.strip())
    if num_match:
        op, target_str = num_match.groups()
        try:
            num_val = float(val)
            target_num = float(target_str)
            if op == ">=":
                return num_val >= target_num
            if op == "<=":
                return num_val <= target_num
            if op == ">":
                return num_val > target_num
            if op == "<":
                return num_val < target_num
            if op == "=":
                return num_val == target_num
        except (ValueError, TypeError):
            return False

    # Boolean yesno checks
    if isinstance(val, bool):
        if term.strip().lower() in ("yes", "true", "1"):
            return val is True
        if term.strip().lower() in ("no", "false", "0"):
            return val is False

    str_val = str(val)

    # Wildcard pattern matching (%)
    if "%" in term:
        # Convert Looker % wildcard to regex
        parts = term.split("%")
        regex_pattern = "^" + ".*".join(re.escape(p) for p in parts) + "$"
        return bool(re.match(regex_pattern, str_val))

    # Exact string equality
    if str_val == term:
        return True

    # Numeric equality fallback (e.g. val=429 or 429.0 vs term="429")
    try:
        if float(val) == float(term):
            return True
    except (ValueError, TypeError):
        pass

    return False


def match_looker_filter_expression(val: Any, expr: str) -> bool:
    """Evaluate a Looker filter expression string against a column value.

    Supports:
    - Exact string literals ("2xx Success")
    - Wildcards ("2xx%", "%Error%")
    - Comma-separated OR lists ("500, 502, 503, 504")
    - Negations ("-429", "-NULL", "-EMPTY", "-2xx Success")
    - Numeric comparisons ("> 0", ">= 100")
    - Skips dynamic Liquid / parameter expressions ("{% parameter ... %}")
    """
    clean_expr = expr.strip()
    if not clean_expr:
        return True

    # Skip dynamic Liquid or parameter expressions
    if "{%" in clean_expr or "{{" in clean_expr or "_filters[" in clean_expr or "_user_attributes[" in clean_expr:
        return True

    raw_terms = [t.strip() for t in clean_expr.split(",") if t.strip()]
    if not raw_terms:
        return True

    positive_terms: list[str] = []
    negative_terms: list[str] = []

    for term in raw_terms:
        # A leading '-' denotes negation UNLESS it is a negative number in a numeric comparison
        if term.startswith("-"):
            inner = term[1:].strip()
            negative_terms.append(inner)
        else:
            positive_terms.append(term)

    # All negative terms must hold (AND not match)
    for neg in negative_terms:
        if _match_single_positive_term(val, neg):
            return False

    # If there are positive terms, at least one must match (OR)
    if positive_terms:
        return any(_match_single_positive_term(val, pos) for pos in positive_terms)

    # If only negative terms were present and none matched, evaluate to True
    return True


def parse_lookml_view_filtered_measures(view_content: str) -> dict[str, Any]:
    """Parse a LookML view string to extract table name, dimension column map, and filtered measures.

    Returns:
        Dict with keys:
        - view_name: str
        - sql_table_name: str | None
        - table_basename: str | None
        - dim_to_col: dict[str, str]
        - filtered_measures: list[dict] with 'name' and 'filters' (dict[dim, expr])
    """
    view_match = re.search(r"view:\s+([a-zA-Z0-9_]+)\s*\{", view_content)
    view_name = view_match.group(1) if view_match else "unknown_view"

    sql_table_match = re.search(r"sql_table_name:\s*([^;]+);;", view_content)
    sql_table_raw = sql_table_match.group(1).strip().strip("`\"'") if sql_table_match else None
    table_basename = sql_table_raw.split(".")[-1].strip("`\"'") if sql_table_raw else view_name

    # Extract dimension -> underlying column mappings
    dim_to_col: dict[str, str] = {}
    dim_pattern = re.compile(r"dimension:\s+([a-zA-Z0-9_]+)\s*\{([^}]*)\}", re.DOTALL)
    for dm in dim_pattern.finditer(view_content):
        d_name, d_body = dm.groups()
        col_match = re.search(r"sql:\s*\$\{TABLE\}\.([a-zA-Z0-9_]+)\s*;;", d_body)
        dim_to_col[d_name] = col_match.group(1) if col_match else d_name

    # Extract measures containing filters:
    filtered_measures: list[dict[str, Any]] = []
    # Match measure blocks (handling nested braces one level deep for filters/drill_fields)
    measure_pattern = re.compile(
        r"measure:\s+([a-zA-Z0-9_]+)\s*\{((?:[^{}]|\{[^{}]*\})*)\}",
        re.DOTALL,
    )
    for mm in measure_pattern.finditer(view_content):
        m_name, m_body = mm.groups()
        filters_dict: dict[str, str] = {}

        # 1. Bracketed list syntax: filters: [dim1: "val1", dim2: "val2"]
        bracket_match = re.search(r"filters:\s*\[([^\]]+)\]", m_body, re.DOTALL)
        if bracket_match:
            inner_filters = bracket_match.group(1)
            for pair in re.finditer(r"([a-zA-Z0-9_.]+)\s*:\s*\"([^\"]*)\"", inner_filters):
                f_dim, f_expr = pair.groups()
                # Strip view prefix if present
                f_dim_clean = f_dim.split(".")[-1]
                filters_dict[f_dim_clean] = f_expr

        # 2. Legacy block syntax: filters: { field: dim value: "val" }
        for legacy_match in re.finditer(r"filters:\s*\{([^}]+)\}", m_body, re.DOTALL):
            l_body = legacy_match.group(1)
            f_field = re.search(r"field:\s*([a-zA-Z0-9_.]+)", l_body)
            f_val = re.search(r"value:\s*\"([^\"]*)\"", l_body)
            if f_field and f_val:
                f_dim_clean = f_field.group(1).split(".")[-1]
                filters_dict[f_dim_clean] = f_val.group(1)

        if filters_dict:
            filtered_measures.append({"name": m_name, "filters": filters_dict})

    return {
        "view_name": view_name,
        "sql_table_name": sql_table_raw,
        "table_basename": table_basename,
        "dim_to_col": dim_to_col,
        "filtered_measures": filtered_measures,
    }


def _find_local_parquet_file(
    table_basename: str,
    lookml_dir: Path,
    extra_dirs: list[Path] | None = None,
) -> Path | None:
    """Search candidate local directories for `<table_basename>.parquet`."""
    search_roots: list[Path] = []
    if extra_dirs:
        search_roots.extend(extra_dirs)

    # Standard project locations relative to lookml_dir and cwd
    parent = lookml_dir.resolve().parent
    search_roots.extend(
        [
            parent / "scratch",
            parent / "parquet",
            parent,
            Path.cwd() / "scratch",
            Path.cwd() / "parquet",
        ]
    )

    seen: set[Path] = set()
    for root in search_roots:
        if not root.exists() or root in seen:
            continue
        seen.add(root)
        direct = root / f"{table_basename}.parquet"
        if direct.exists():
            return direct
        # Recursive search up to depth 3 inside scratch/parquet dirs
        matches = list(root.glob(f"**/{table_basename}.parquet"))
        if matches:
            return matches[0]

    return None


def _fetch_bigquery_column_values(
    sql_table_name: str,
    columns: list[str],
    gcp_project: str | None = None,
) -> dict[str, list[Any]] | None:
    """Query BigQuery for distinct values of specified columns if credentials/table exist."""
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=gcp_project) if gcp_project else bigquery.Client()
        distinct_map: dict[str, list[Any]] = {}
        for col in columns:
            query = f"SELECT DISTINCT `{col}` AS val FROM `{sql_table_name}` LIMIT 500"
            job = client.query(query)
            rows = list(job.result(timeout=15))
            distinct_map[col] = [r.val for r in rows]
        return distinct_map
    except Exception:
        return None


def validate_filtered_measures_in_lookml(
    lookml_dir: Path,
    gcp_project: str | None = None,
    parquet_dirs: list[Path] | None = None,
) -> list[str]:
    """Audit all LookML views in `lookml_dir` to verify filtered measures match > 0 rows.

    Checks local Parquet files first for instant zero-latency verification, falling back
    to BigQuery `SELECT DISTINCT` queries against `sql_table_name`.

    Args:
        lookml_dir: Directory containing `views/*.view.lkml` (or `*.view.lkml`).
        gcp_project: Optional GCP project ID for BigQuery fallback queries.
        parquet_dirs: Optional list of directories to search for `.parquet` files.

    Returns:
        List of diagnostic error strings for any filtered measure matching 0 rows.
    """
    if not lookml_dir.exists():
        return []

    view_files = list(lookml_dir.glob("**/*.view.lkml"))
    diagnostics: list[str] = []

    for vf in view_files:
        try:
            content = vf.read_text(encoding="utf-8")
        except Exception:
            continue

        parsed_view = parse_lookml_view_filtered_measures(content)
        filtered_measures = parsed_view["filtered_measures"]
        if not filtered_measures:
            continue

        view_name = parsed_view["view_name"]
        table_basename = parsed_view["table_basename"]
        sql_table_name = parsed_view["sql_table_name"]
        dim_to_col = parsed_view["dim_to_col"]

        # Gather all underlying columns needed across filtered measures in this view
        needed_cols: set[str] = set()
        for fm in filtered_measures:
            for dim_name in fm["filters"]:
                needed_cols.add(dim_to_col.get(dim_name, dim_name))

        # 1. Try local Parquet file first
        parquet_path = _find_local_parquet_file(table_basename, lookml_dir, parquet_dirs)
        df = None
        if parquet_path is not None:
            try:
                import pandas as pd

                df = pd.read_parquet(parquet_path)
            except Exception:
                df = None

        if parquet_path is not None and df is not None and not df.empty:
            for fm in filtered_measures:
                m_name = fm["name"]
                filters_map: dict[str, str] = fm["filters"]

                # Check each individual filter and build combined mask
                combined_mask = [True] * len(df)
                individual_failed = False

                for dim_name, expr in filters_map.items():
                    col_name = dim_to_col.get(dim_name, dim_name)
                    if col_name not in df.columns:
                        continue

                    col_series = df[col_name]
                    col_matches = [match_looker_filter_expression(v, expr) for v in col_series]
                    if not any(col_matches):
                        distinct_vals = [str(x) for x in col_series.dropna().unique()[:10]]
                        diagnostics.append(
                            f"Filtered measure `{m_name}` in view `{view_name}` (`{vf.name}`) "
                            f'has filter `{dim_name}: "{expr}"` which matched 0 rows in `{parquet_path.name}`. '
                            f"Actual distinct values in column `{col_name}`: {distinct_vals}"
                        )
                        individual_failed = True
                    else:
                        combined_mask = [a and b for a, b in zip(combined_mask, col_matches, strict=False)]

                if not individual_failed and not any(combined_mask):
                    filter_summary = ", ".join(f'{k}: "{v}"' for k, v in filters_map.items())
                    diagnostics.append(
                        f"Filtered measure `{m_name}` in view `{view_name}` (`{vf.name}`) "
                        f"with combined filters `[{filter_summary}]` matched 0 rows in `{parquet_path.name}`."
                    )
            continue

        # 2. Fallback to BigQuery SELECT DISTINCT if sql_table_name is available
        if sql_table_name and "." in sql_table_name:
            bq_distinct = _fetch_bigquery_column_values(
                sql_table_name=sql_table_name,
                columns=list(needed_cols),
                gcp_project=gcp_project,
            )
            if bq_distinct is not None:
                for fm in filtered_measures:
                    m_name = fm["name"]
                    filters_map = fm["filters"]
                    for dim_name, expr in filters_map.items():
                        col_name = dim_to_col.get(dim_name, dim_name)
                        if col_name not in bq_distinct:
                            continue
                        col_vals = bq_distinct[col_name]
                        if not col_vals:
                            continue
                        if not any(match_looker_filter_expression(v, expr) for v in col_vals):
                            sample_vals = [str(x) for x in col_vals if x is not None][:10]
                            diagnostics.append(
                                f"Filtered measure `{m_name}` in view `{view_name}` (`{vf.name}`) "
                                f'has filter `{dim_name}: "{expr}"` which matched 0 distinct values in BigQuery table `{sql_table_name}`. '
                                f"Actual distinct values in column `{col_name}`: {sample_vals}"
                            )

    return diagnostics
