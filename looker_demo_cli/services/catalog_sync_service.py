"""Knowledge Catalog (Dataplex) to LookML Synchronization Service.

Compares Dataplex metadata snapshots against existing LookML views and refinements,
detects semantic and structural drift, and non-destructively applies updates.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from looker_demo_cli.catalog.models import CatalogSnapshot
from looker_demo_cli.generators.lookml_generator import LookMLGenerator, LookMLTableSpec
from looker_demo_cli.services.catalog_service import snapshot_to_table_specs


class ColumnDiff(BaseModel):
    """Diff detail for an individual column or dimension."""

    column_name: str
    change_type: Literal[
        "added",
        "description_updated",
        "label_updated",
        "tags_updated",
        "format_updated",
        "suggestions_updated",
    ]
    old_value: str | list[str] | None = None
    new_value: str | list[str] | None = None


class TableDiff(BaseModel):
    """Diff detail for a table/view."""

    table_name: str
    change_type: Literal["added", "removed", "modified", "unchanged"]
    table_description_changed: bool = False
    old_description: str | None = None
    new_description: str | None = None
    business_label_changed: bool = False
    old_business_label: str | None = None
    new_business_label: str | None = None
    column_diffs: list[ColumnDiff] = Field(default_factory=list)


class CatalogDiffReport(BaseModel):
    """Comprehensive diff and sync report between Knowledge Catalog and LookML."""

    dataset_id: str
    gcp_project: str
    profile: str
    tables_added: list[str] = Field(default_factory=list)
    tables_removed: list[str] = Field(default_factory=list)
    tables_modified: list[str] = Field(default_factory=list)
    tables_unchanged: list[str] = Field(default_factory=list)
    table_diffs: list[TableDiff] = Field(default_factory=list)
    total_changes: int = 0
    dry_run: bool = False
    files_updated: list[str] = Field(default_factory=list)
    files_created: list[str] = Field(default_factory=list)
    summary: str = ""


class ParsedColumn(BaseModel):
    """Extracted column metadata from an existing LookML file."""

    name: str
    dimension_type: str = "dimension"
    label: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    value_format_name: str | None = None
    suggestions: list[str] = Field(default_factory=list)
    raw_block: str = ""


class ParsedTable(BaseModel):
    """Aggregated metadata for a table extracted across base, refinement, or monolithic view files."""

    table_name: str
    business_label: str | None = None
    description: str | None = None
    columns: dict[str, ParsedColumn] = Field(default_factory=dict)
    custom_blocks: list[str] = Field(default_factory=list)
    base_file: Path | None = None
    refinement_file: Path | None = None
    monolithic_file: Path | None = None
    is_layered: bool = False


def _parse_column_block(d_type: str, d_name: str, body: str) -> ParsedColumn:
    """Parse label, description, tags, format, and suggestions from a dimension block."""
    col = ParsedColumn(name=d_name, dimension_type=d_type, raw_block=f"{d_type}: {d_name} {{{body}}}")

    lbl_m = re.search(r'label:\s*"([^"]+)"', body)
    if lbl_m:
        col.label = lbl_m.group(1)

    desc_m = re.search(r'description:\s*"([^"]+)"', body)
    if desc_m:
        col.description = desc_m.group(1)

    tags_m = re.search(r"tags:\s*\[([^\]]*)\]", body)
    if tags_m:
        inner = tags_m.group(1)
        col.tags = [t.strip().strip("\"'") for t in inner.split(",") if t.strip().strip("\"'")]

    fmt_m = re.search(r"value_format_name:\s*([a-zA-Z0-9_]+)", body)
    if fmt_m:
        col.value_format_name = fmt_m.group(1)

    sugg_m = re.search(r"suggestions:\s*\[([^\]]*)\]", body)
    if sugg_m:
        inner = sugg_m.group(1)
        col.suggestions = [s.strip().strip("\"'") for s in inner.split(",") if s.strip().strip("\"'")]

    return col


def _extract_blocks_from_lookml(content: str) -> tuple[dict[str, ParsedColumn], list[str], str | None, str | None]:
    """Extract columns, custom user-defined blocks, view label, and view description from LookML content."""
    columns: dict[str, ParsedColumn] = {}
    custom_blocks: list[str] = []

    # View-level label and description
    view_label = None
    v_lbl_m = re.search(r"^\s*label:\s*[\"']([^\"']*)[\"']", content, re.MULTILINE)
    if v_lbl_m:
        view_label = v_lbl_m.group(1)

    view_desc = None
    v_desc_m = re.search(r"^\s*(?:#\s*)?description:\s*[\"']([^\"']*)[\"']", content, re.MULTILINE)
    if v_desc_m:
        view_desc = v_desc_m.group(1)

    # 1. Match dimension_group
    dg_pattern = re.compile(r"(\s*dimension_group:\s*([a-zA-Z0-9_]+)\s*\{([^}]*)\})", re.DOTALL)
    for m in dg_pattern.finditer(content):
        _, g_name, body = m.groups()
        columns[g_name] = _parse_column_block("dimension_group", g_name, body)

    # 2. Match dimension
    dim_pattern = re.compile(r"(\s*dimension:\s*([a-zA-Z0-9_]+)\s*\{([^}]*)\})", re.DOTALL)
    for m in dim_pattern.finditer(content):
        _, d_name, body = m.groups()
        columns[d_name] = _parse_column_block("dimension", d_name, body)

    # 3. Match measures and custom blocks
    measure_pattern = re.compile(r"(\s*measure:\s*([a-zA-Z0-9_]+)\s*\{((?:[^{}]|\{[^{}]*\})*)\})", re.DOTALL)
    for m in measure_pattern.finditer(content):
        raw_full, m_name, _ = m.groups()
        # If it's not a standard generated measure, treat as custom user measure
        if not (
            m_name == "count"
            or m_name.startswith("count_distinct_")
            or m_name.startswith("total_")
            or m_name.startswith("average_")
        ):
            custom_blocks.append(raw_full.strip())

    return columns, custom_blocks, view_label, view_desc


def parse_existing_lookml_project(lookml_dir: Path) -> dict[str, ParsedTable]:
    """Inspect and parse all LookML view and refinement files in lookml_dir."""
    tables: dict[str, ParsedTable] = {}
    if not lookml_dir.exists():
        return tables

    # Detect layered directories
    base_dir = lookml_dir / "views" / "base"
    refinements_dir = lookml_dir / "views" / "refinements"
    has_layered = base_dir.exists() or refinements_dir.exists()

    # 1. Parse base views
    if base_dir.exists():
        for b_file in base_dir.glob("*.view.lkml"):
            t_name = b_file.stem.replace(".view", "")
            content = b_file.read_text(encoding="utf-8")
            cols, custom, v_lbl, v_desc = _extract_blocks_from_lookml(content)
            tables[t_name] = ParsedTable(
                table_name=t_name,
                business_label=v_lbl,
                description=v_desc,
                columns=cols,
                custom_blocks=custom,
                base_file=b_file,
                is_layered=True,
            )

    # 2. Parse refinement views
    if refinements_dir.exists():
        for r_file in list(refinements_dir.glob("*.refinement.lkml")) + list(refinements_dir.glob("*.view.lkml")):
            t_name = r_file.stem.replace(".refinement", "").replace(".view", "").replace("+", "")
            content = r_file.read_text(encoding="utf-8")
            cols, custom, v_lbl, v_desc = _extract_blocks_from_lookml(content)
            if t_name in tables:
                tbl = tables[t_name]
                tbl.refinement_file = r_file
                if v_lbl:
                    tbl.business_label = v_lbl
                if v_desc:
                    tbl.description = v_desc
                for c_name, c_meta in cols.items():
                    if c_name in tbl.columns:
                        existing = tbl.columns[c_name]
                        if c_meta.label:
                            existing.label = c_meta.label
                        if c_meta.description:
                            existing.description = c_meta.description
                        if c_meta.tags:
                            existing.tags = c_meta.tags
                        if c_meta.value_format_name:
                            existing.value_format_name = c_meta.value_format_name
                        if c_meta.suggestions:
                            existing.suggestions = c_meta.suggestions
                    else:
                        tbl.columns[c_name] = c_meta
                tbl.custom_blocks.extend(custom)
            else:
                tables[t_name] = ParsedTable(
                    table_name=t_name,
                    business_label=v_lbl,
                    description=v_desc,
                    columns=cols,
                    custom_blocks=custom,
                    refinement_file=r_file,
                    is_layered=True,
                )

    # 3. Parse monolithic views
    views_dir = lookml_dir / "views"
    candidate_views = []
    if views_dir.exists():
        for f in views_dir.glob("*.view.lkml"):
            candidate_views.append(f)
    for f in lookml_dir.glob("*.view.lkml"):
        if f not in candidate_views:
            candidate_views.append(f)

    for v_file in candidate_views:
        # Ignore if already parsed in base or refinements
        t_name = v_file.stem.replace(".view", "")
        if t_name in tables and tables[t_name].is_layered:
            continue
        content = v_file.read_text(encoding="utf-8")
        cols, custom, v_lbl, v_desc = _extract_blocks_from_lookml(content)
        tables[t_name] = ParsedTable(
            table_name=t_name,
            business_label=v_lbl,
            description=v_desc,
            columns=cols,
            custom_blocks=custom,
            monolithic_file=v_file,
            is_layered=has_layered,
        )

    return tables


def diff_catalog_against_lookml(
    lookml_dir: Path,
    specs: list[LookMLTableSpec],
    profile: str = "rich",
    dataset_id: str = "",
    gcp_project: str = "",
) -> CatalogDiffReport:
    """Detect differences between Knowledge Catalog table specifications and existing LookML files."""
    parsed_tables = parse_existing_lookml_project(lookml_dir)
    specs_by_table = {s.table_name: s for s in specs}

    report = CatalogDiffReport(
        dataset_id=dataset_id,
        gcp_project=gcp_project,
        profile=profile,
    )

    # 1. Detect added tables
    for s_name in specs_by_table:
        if s_name not in parsed_tables:
            report.tables_added.append(s_name)
            report.table_diffs.append(TableDiff(table_name=s_name, change_type="added"))
            report.total_changes += 1

    # 2. Detect removed tables
    for p_name in parsed_tables:
        if p_name not in specs_by_table:
            report.tables_removed.append(p_name)
            report.table_diffs.append(TableDiff(table_name=p_name, change_type="removed"))
            report.total_changes += 1

    # 3. Detect modifications for existing tables
    for s_name, spec in specs_by_table.items():
        if s_name not in parsed_tables:
            continue

        parsed = parsed_tables[s_name]
        diff = TableDiff(table_name=s_name, change_type="unchanged")
        has_change = False

        # Compare table label
        if spec.business_label and parsed.business_label != spec.business_label:
            diff.business_label_changed = True
            diff.old_business_label = parsed.business_label
            diff.new_business_label = spec.business_label
            has_change = True

        # Compare table description
        if spec.description and parsed.description != spec.description:
            diff.table_description_changed = True
            diff.old_description = parsed.description
            diff.new_description = spec.description
            has_change = True

        # Compare columns
        for col_name in spec.schema_fields:
            # Match either direct dimension or time group prefix
            matched_col = parsed.columns.get(col_name)
            if not matched_col:
                for g_name, p_col in parsed.columns.items():
                    if p_col.dimension_type == "dimension_group" and col_name.startswith(g_name):
                        matched_col = p_col
                        break

            if not matched_col:
                diff.column_diffs.append(ColumnDiff(column_name=col_name, change_type="added"))
                has_change = True
                continue

            # Compare description
            spec_desc = spec.column_descriptions.get(col_name)
            if spec_desc and matched_col.description != spec_desc:
                diff.column_diffs.append(
                    ColumnDiff(
                        column_name=col_name,
                        change_type="description_updated",
                        old_value=matched_col.description,
                        new_value=spec_desc,
                    )
                )
                has_change = True

            # Compare label
            spec_label = spec.column_labels.get(col_name)
            if spec_label and matched_col.label != spec_label:
                diff.column_diffs.append(
                    ColumnDiff(
                        column_name=col_name,
                        change_type="label_updated",
                        old_value=matched_col.label,
                        new_value=spec_label,
                    )
                )
                has_change = True

            # Compare tags / synonyms
            spec_tags = [s for s in spec.column_synonyms.get(col_name, []) if s]
            if spec_tags and set(matched_col.tags) != set(spec_tags):
                diff.column_diffs.append(
                    ColumnDiff(
                        column_name=col_name,
                        change_type="tags_updated",
                        old_value=matched_col.tags,
                        new_value=spec_tags,
                    )
                )
                has_change = True

            # Compare format
            spec_fmt = spec.column_formats.get(col_name)
            if spec_fmt and matched_col.value_format_name != spec_fmt:
                diff.column_diffs.append(
                    ColumnDiff(
                        column_name=col_name,
                        change_type="format_updated",
                        old_value=matched_col.value_format_name,
                        new_value=spec_fmt,
                    )
                )
                has_change = True

            # Compare suggestions
            spec_sugg = [v for v in spec.column_allowed_values.get(col_name, []) if v]
            if spec_sugg and len(spec_sugg) <= 25 and set(matched_col.suggestions) != set(spec_sugg):
                diff.column_diffs.append(
                    ColumnDiff(
                        column_name=col_name,
                        change_type="suggestions_updated",
                        old_value=matched_col.suggestions,
                        new_value=spec_sugg,
                    )
                )
                has_change = True

        if has_change:
            diff.change_type = "modified"
            report.tables_modified.append(s_name)
            report.table_diffs.append(diff)
            change_count = (
                len(diff.column_diffs)
                + (1 if diff.business_label_changed else 0)
                + (1 if diff.table_description_changed else 0)
            )
            report.total_changes += change_count
        else:
            report.tables_unchanged.append(s_name)
            report.table_diffs.append(diff)

    return report


def _append_custom_blocks_to_lookml(content: str, custom_blocks: list[str]) -> str:
    """Safely append user-defined custom blocks before the closing brace of a LookML view."""
    if not custom_blocks:
        return content

    content = content.rstrip()
    if content.endswith("}"):
        content = content[:-1].rstrip()

    blocks_str = "\n\n".join(f"  {b}" for b in custom_blocks)
    return f"{content}\n\n  # -------------------------------------------------------------\n  # Custom User-Defined Fields (Preserved by catalog sync)\n  # -------------------------------------------------------------\n{blocks_str}\n}}\n"


def sync_catalog_to_lookml(
    lookml_dir: Path,
    snapshot: CatalogSnapshot,
    profile: str = "rich",
    dry_run: bool = False,
    layered: bool | None = None,
    connection_name: str = "default_bigquery_connection",
) -> CatalogDiffReport:
    """Non-destructively synchronize Knowledge Catalog metadata into LookML views and refinements."""
    table_specs = snapshot_to_table_specs(snapshot, profile=profile)
    dataset_id = snapshot.dataset_id
    gcp_project = snapshot.project_id

    report = diff_catalog_against_lookml(
        lookml_dir=lookml_dir,
        specs=table_specs,
        profile=profile,
        dataset_id=dataset_id,
        gcp_project=gcp_project,
    )
    report.dry_run = dry_run

    if dry_run or report.total_changes == 0:
        if report.total_changes == 0:
            report.summary = "Knowledge Catalog metadata and LookML views are in sync. 0 changes needed."
        else:
            report.summary = (
                f"Planned {report.total_changes} change(s) across {len(report.tables_modified)} modified "
                f"and {len(report.tables_added)} added table(s)."
            )
        return report

    # Determine whether to use layered structure
    parsed_tables = parse_existing_lookml_project(lookml_dir)
    if layered is not None:
        use_layered = layered
    else:
        base_dir = lookml_dir / "views" / "base"
        refinements_dir = lookml_dir / "views" / "refinements"
        use_layered = (
            base_dir.exists() or refinements_dir.exists() or any(tbl.is_layered for tbl in parsed_tables.values())
        )

    gen = LookMLGenerator(project_id=gcp_project, dataset_id=dataset_id, connection_name=connection_name)
    specs_by_table = {s.table_name: s for s in table_specs}

    # 1. Create newly added tables
    for tbl_name in report.tables_added:
        spec = specs_by_table[tbl_name]
        if use_layered:
            base_dir = lookml_dir / "views" / "base"
            refinements_dir = lookml_dir / "views" / "refinements"
            base_dir.mkdir(parents=True, exist_ok=True)
            refinements_dir.mkdir(parents=True, exist_ok=True)

            b_path = base_dir / f"{tbl_name}.view.lkml"
            b_path.write_text(gen.generate_base_view_lkml(spec), encoding="utf-8")
            report.files_created.append(str(b_path.relative_to(lookml_dir)))

            r_path = refinements_dir / f"{tbl_name}.refinement.lkml"
            r_path.write_text(gen.generate_refinement_view_lkml(spec), encoding="utf-8")
            report.files_created.append(str(r_path.relative_to(lookml_dir)))
        else:
            views_dir = lookml_dir / "views"
            views_dir.mkdir(parents=True, exist_ok=True)
            v_path = views_dir / f"{tbl_name}.view.lkml"
            v_path.write_text(gen.generate_view_lkml(spec), encoding="utf-8")
            report.files_created.append(str(v_path.relative_to(lookml_dir)))

    # 2. Update modified tables
    for tbl_name in report.tables_modified:
        spec = specs_by_table[tbl_name]
        parsed = parsed_tables.get(tbl_name)
        custom = list(parsed.custom_blocks) if parsed else []
        if parsed:
            for c_name, c_col in parsed.columns.items():
                if c_name not in spec.schema_fields:
                    is_time_group = False
                    for f_name in spec.schema_fields:
                        if f_name.startswith(c_name) and f_name != c_name:
                            is_time_group = True
                            break
                    if not is_time_group and c_col.raw_block:
                        if c_col.raw_block not in custom:
                            custom.append(c_col.raw_block)

        if use_layered:
            base_dir = lookml_dir / "views" / "base"
            refinements_dir = lookml_dir / "views" / "refinements"
            base_dir.mkdir(parents=True, exist_ok=True)
            refinements_dir.mkdir(parents=True, exist_ok=True)

            b_path = base_dir / f"{tbl_name}.view.lkml"
            if not b_path.exists():
                b_path.write_text(gen.generate_base_view_lkml(spec), encoding="utf-8")
                report.files_created.append(str(b_path.relative_to(lookml_dir)))
            else:
                # Regenerate base view to incorporate any newly added columns
                b_content = gen.generate_base_view_lkml(spec)
                b_path.write_text(b_content, encoding="utf-8")
                report.files_updated.append(str(b_path.relative_to(lookml_dir)))

            r_path = refinements_dir / f"{tbl_name}.refinement.lkml"
            new_ref = gen.generate_refinement_view_lkml(spec)
            preserved_ref = _append_custom_blocks_to_lookml(new_ref, custom)
            r_path.write_text(preserved_ref, encoding="utf-8")
            if str(r_path.relative_to(lookml_dir)) not in report.files_updated:
                report.files_updated.append(str(r_path.relative_to(lookml_dir)))
        else:
            views_dir = lookml_dir / "views"
            v_path = views_dir / f"{tbl_name}.view.lkml"
            new_v = gen.generate_view_lkml(spec)
            preserved_v = _append_custom_blocks_to_lookml(new_v, custom)
            v_path.write_text(preserved_v, encoding="utf-8")
            report.files_updated.append(str(v_path.relative_to(lookml_dir)))

    # 3. Check and patch models to include refinements if layered
    if use_layered:
        models_dir = lookml_dir / "models"
        if models_dir.exists():
            for m_file in models_dir.glob("*.model.lkml"):
                m_content = m_file.read_text(encoding="utf-8")
                if 'include: "/views/refinements/**/*.refinement.lkml"' not in m_content:
                    # Update includes
                    if 'include: "/views/**/*.view.lkml"' in m_content:
                        m_content = m_content.replace(
                            'include: "/views/**/*.view.lkml"',
                            'include: "/views/base/**/*.view.lkml"\ninclude: "/views/refinements/**/*.refinement.lkml"',
                        )
                    else:
                        m_content = (
                            'include: "/views/base/**/*.view.lkml"\ninclude: "/views/refinements/**/*.refinement.lkml"\n'
                            + m_content
                        )
                    m_file.write_text(m_content, encoding="utf-8")
                    report.files_updated.append(str(m_file.relative_to(lookml_dir)))

    report.summary = (
        f"Synchronized Knowledge Catalog metadata: {len(report.files_updated)} file(s) updated, "
        f"{len(report.files_created)} file(s) created across {report.total_changes} total change(s)."
    )
    return report
