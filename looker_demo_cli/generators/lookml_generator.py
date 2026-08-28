from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field


class LookMLTableSpec(BaseModel):
    table_name: str
    table_type: str = "dimension"  # fact or dimension
    schema_fields: dict[str, str] = Field(default_factory=dict)
    primary_key: Optional[str] = None
    foreign_keys: dict[str, str] = Field(default_factory=dict)  # fk_col -> TargetTable.TargetCol


class DashboardFilterSpec(BaseModel):
    name: str
    title: str
    type: str = "date_filter"  # date_filter, field_filter, string_filter
    default_value: Optional[str] = "365 days"
    allow_multiple_values: bool = True
    required: bool = False
    ui_config: dict[str, Any] = Field(default_factory=lambda: {"type": "advanced", "display": "popover"})


class DashboardTabSpec(BaseModel):
    name: str
    label: str


class DashboardTileSpec(BaseModel):
    title: str
    name: Optional[str] = None
    model: str
    explore: str
    type: str = "looker_column"  # single_value, looker_area, looker_column, looker_bar, looker_donut_multiples, looker_grid, looker_scatter, looker_line
    fields: list[str] = Field(default_factory=list)
    pivots: list[str] = Field(default_factory=list)
    filters: dict[str, str] = Field(default_factory=dict)
    sorts: list[str] = Field(default_factory=list)
    limit: Optional[int] = None
    listen: dict[str, str] = Field(default_factory=dict)
    tab_name: Optional[str] = None
    row: int = 0
    col: int = 0
    width: int = 12
    height: int = 8


class DashboardSpec(BaseModel):
    dashboard_name: str
    title: str
    layout: str = "newspaper"
    preferred_viewer: str = "dashboards-next"
    crossfilter_enabled: bool = True
    tabs: list[DashboardTabSpec] = Field(default_factory=list)
    filters: list[DashboardFilterSpec] = Field(default_factory=list)
    elements: list[DashboardTileSpec] = Field(default_factory=list)


class LookMLGenerator:
    """Generates LookML Views, Model, and Custom/Flexible Executive Dashboards with documentation standards."""

    def __init__(self, project_id: str, dataset_id: str, connection_name: str = "default_bigquery_connection"):
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.connection_name = connection_name

    def _format_label(self, raw_name: str) -> str:
        """Convert snake_case or raw identifier to human-friendly Title Case."""
        clean = raw_name.replace("dim_", "").replace("fct_", "").replace("_usd", " (USD)").replace("_pct", " (%)")
        words = clean.split("_")
        acronyms = {"id": "ID", "usd": "USD", "url": "URL", "api": "API", "kpi": "KPI", "nps": "NPS", "ltv": "LTV", "fk": "FK", "pk": "PK"}
        formatted = [acronyms.get(w.lower(), w.capitalize()) for w in words if w]
        return " ".join(formatted)

    def _format_description(self, field_name: str, field_type: str, is_pk: bool = False, is_fk: bool = False, table_name: str = "") -> str:
        """Generate descriptive documentation for LookML fields."""
        table_label = self._format_label(table_name)
        if is_pk:
            return f"Unique primary key identifier for {table_label} records."
        if is_fk or field_name.endswith("_id"):
            ref_name = self._format_label(field_name.replace("_id", ""))
            return f"Foreign key reference linking to {ref_name}."
        if field_type in ("DATE", "TIMESTAMP", "DATETIME") or field_name.endswith(("_date", "_time", "_at")):
            action = field_name.replace("_date", "").replace("_time", "").replace("_at", "").replace("_", " ")
            return f"Date/timestamp recording when the {action} occurred."
        if "usd" in field_name.lower() or "amount" in field_name.lower() or "price" in field_name.lower() or "revenue" in field_name.lower() or "cost" in field_name.lower():
            return f"Monetary amount for {field_name.replace('_', ' ')} in USD."
        if "pct" in field_name.lower() or "rate" in field_name.lower() or "score" in field_name.lower():
            return f"Calculated metric or score for {field_name.replace('_', ' ')}."
        if field_type in ("BOOL", "BOOLEAN"):
            return f"Boolean indicator flag determining whether {field_name.replace('_', ' ')} is true."
        return f"Attribute representing {field_name.replace('_', ' ')}."

    def generate_view_lkml(self, spec: LookMLTableSpec) -> str:
        """Generate a self-documenting LookML view file."""
        lines = [
            f"view: {spec.table_name} {{",
            f"  sql_table_name: `{self.project_id}.{self.dataset_id}.{spec.table_name}` ;;",
            "",
            "  # -------------------------------------------------------------",
            "  # Dimensions",
            "  # -------------------------------------------------------------",
        ]

        for field_name, field_type in spec.schema_fields.items():
            is_pk = (field_name == spec.primary_key)
            is_fk = (field_name in spec.foreign_keys or (field_name.endswith("_id") and not is_pk))
            label = self._format_label(field_name)
            desc = self._format_description(field_name, field_type, is_pk=is_pk, is_fk=is_fk, table_name=spec.table_name)

            is_time_col = field_type in ("TIMESTAMP", "DATETIME", "DATE") or field_name.endswith(("_time", "_date", "_at", "_day"))
            if is_time_col and field_type not in ("INT64", "FLOAT64", "NUMERIC", "DOUBLE", "INTEGER"):
                group_name = field_name
                for sfx in ["_time", "_date", "_at", "_day"]:
                    if group_name.endswith(sfx):
                        group_name = group_name[:-len(sfx)]
                        break
                group_label = self._format_label(group_name)
                is_date_only = (field_type == "DATE") or field_name.endswith(("_date", "_day")) or field_name.startswith("date_")
                dt_param = "date" if is_date_only else "timestamp"
                t_frames = "[raw, date, week, month, quarter, year]" if is_date_only else "[raw, time, date, week, month, quarter, year]"
                lines.extend([
                    f"  dimension_group: {group_name} {{",
                    f'    label: "{group_label}"',
                    f'    description: "{desc}"',
                    "    type: time",
                    f"    datatype: {dt_param}",
                    f"    timeframes: {t_frames}",
                    f"    sql: ${{TABLE}}.{field_name} ;;",
                    "  }",
                    "",
                ])
            elif field_type in ("INT64", "FLOAT64", "NUMERIC", "DOUBLE", "INTEGER"):
                dim_lines = [
                    f"  dimension: {field_name} {{",
                    f'    label: "{label}"',
                    f'    description: "{desc}"',
                ]
                if is_pk:
                    dim_lines.append("    primary_key: yes")
                dim_lines.extend([
                    "    type: number",
                    f"    sql: ${{TABLE}}.{field_name} ;;",
                    "  }",
                    "",
                ])
                lines.extend(dim_lines)
            elif field_type in ("BOOL", "BOOLEAN"):
                lines.extend([
                    f"  dimension: {field_name} {{",
                    f'    label: "{label}"',
                    f'    description: "{desc}"',
                    "    type: yesno",
                    f"    sql: ${{TABLE}}.{field_name} ;;",
                    "  }",
                    "",
                ])
            else:
                dim_lines = [
                    f"  dimension: {field_name} {{",
                    f'    label: "{label}"',
                    f'    description: "{desc}"',
                ]
                if is_pk:
                    dim_lines.append("    primary_key: yes")
                dim_lines.extend([
                    "    type: string",
                    f"    sql: ${{TABLE}}.{field_name} ;;",
                    "  }",
                    "",
                ])
                lines.extend(dim_lines)

        # Standard Measures
        lines.extend([
            "  # -------------------------------------------------------------",
            "  # Measures",
            "  # -------------------------------------------------------------",
            "  measure: count {",
            f'    label: "Total {self._format_label(spec.table_name)} Count"',
            f'    description: "Total record count of {self._format_label(spec.table_name)}."',
            "    type: count",
            "  }",
            "",
        ])

        if spec.primary_key:
            pk_label = self._format_label(spec.primary_key)
            lines.extend([
                f"  measure: count_distinct_{spec.table_name} {{",
                f'    label: "Distinct {self._format_label(spec.table_name)} Count"',
                f'    description: "Distinct unique count of {pk_label}."',
                "    type: count_distinct",
                f"    sql: ${{{spec.primary_key}}} ;;",
                "  }",
                "",
            ])

        # Sum/Avg measures for numeric columns
        for field_name, field_type in spec.schema_fields.items():
            if field_name != spec.primary_key and not field_name.endswith("_id"):
                if field_type in ("FLOAT64", "NUMERIC", "INT64", "DOUBLE"):
                    val_format = "usd_0" if any(k in field_name.lower() for k in ["usd", "cost", "rev", "price", "loss", "fee", "val", "amount", "payout", "premium"]) else "decimal_1"
                    field_label = self._format_label(field_name)
                    lines.extend([
                        f"  measure: total_{field_name} {{",
                        f'    label: "Total {field_label}"',
                        f'    description: "Sum of {field_label.lower()} across all matching records."',
                        "    type: sum",
                        f"    sql: ${{{field_name}}} ;;",
                        f"    value_format_name: {val_format}",
                        "  }",
                        "",
                        f"  measure: average_{field_name} {{",
                        f'    label: "Average {field_label}"',
                        f'    description: "Average {field_label.lower()} per record."',
                        "    type: average",
                        f"    sql: ${{{field_name}}} ;;",
                        f"    value_format_name: {val_format}",
                        "  }",
                        "",
                    ])

        lines.append("}")
        return "\n".join(lines)

    def generate_model_lkml(self, model_name: str, tables: list[LookMLTableSpec]) -> str:
        """Generate LookML model string with explores and joins."""
        lines = [
            f'connection: "{self.connection_name}"',
            "",
            'include: "/views/**/*.view.lkml"',
            'include: "/dashboards/**/*.dashboard.lookml"',
            "",
            "datagroup: default_datagroup {",
            '  max_cache_age: "4 hours"',
            "}",
            "",
            "persist_with: default_datagroup",
            "",
        ]

        explored_tables = [t for t in tables if t.table_type == "fact" or len(t.foreign_keys) > 0] or tables[:1]
        for ft in explored_tables:
            label = self._format_label(ft.table_name)
            lines.extend([
                f"explore: {ft.table_name} {{",
                f'  label: "{label}"',
                f'  description: "Explore for analyzing {label.lower()} data with relational dimensions."',
            ])
            # Add joins
            for fk_col, parent_ref in ft.foreign_keys.items():
                parent_table = parent_ref.split(".")[0]
                parent_pk = parent_ref.split(".")[1] if "." in parent_ref else "id"
                lines.extend([
                    f"  join: {parent_table} {{",
                    "    type: left_outer",
                    "    relationship: many_to_one",
                    f"    sql_on: ${{{ft.table_name}.{fk_col}}} = ${{{parent_table}.{parent_pk}}} ;;",
                    "  }",
                ])
            lines.extend(["}", ""])

        return "\n".join(lines)

    def generate_dashboard_from_spec(self, spec: DashboardSpec) -> str:
        """Generate LookML dashboard YAML string from a flexible DashboardSpec."""
        lines = [
            f"- dashboard: {spec.dashboard_name}",
            f'  title: "{spec.title}"',
            f"  layout: {spec.layout}",
            f"  preferred_viewer: {spec.preferred_viewer}",
            f"  crossfilter_enabled: {'true' if spec.crossfilter_enabled else 'false'}",
        ]

        if spec.tabs:
            lines.append("  tabs:")
            for t in spec.tabs:
                lines.extend([
                    f"  - name: {t.name}",
                    f"    label: {t.label}",
                ])

        if spec.filters:
            lines.append("  filters:")
            for f in spec.filters:
                lines.extend([
                    f"  - name: {f.name}",
                    f'    title: "{f.title}"',
                    f"    type: {f.type}",
                    f"    default_value: {f.default_value}",
                    f"    allow_multiple_values: {'true' if f.allow_multiple_values else 'false'}",
                    f"    required: {'true' if f.required else 'false'}",
                    "    ui_config:",
                    f"      type: {f.ui_config.get('type', 'advanced')}",
                    f"      display: {f.ui_config.get('display', 'popover')}",
                ])

        lines.append("  elements:")
        for el in spec.elements:
            tile_name = el.name or el.title
            lines.extend([
                f"  - title: {el.title}",
                f"    name: {tile_name}",
                f"    model: {el.model}",
                f"    explore: {el.explore}",
                f"    type: {el.type}",
                f"    fields: [{', '.join(el.fields)}]",
            ])
            if el.pivots:
                lines.append(f"    pivots: [{', '.join(el.pivots)}]")
            if el.sorts:
                lines.append(f"    sorts: [{', '.join(el.sorts)}]")
            if el.limit:
                lines.append(f"    limit: {el.limit}")
            if el.tab_name:
                lines.append(f"    tab_name: {el.tab_name}")
            lines.extend([
                f"    row: {el.row}",
                f"    col: {el.col}",
                f"    width: {el.width}",
                f"    height: {el.height}",
            ])
            if el.listen:
                lines.append("    listen:")
                for filter_k, target_col in el.listen.items():
                    lines.append(f"      {filter_k}: {target_col}")
            lines.append("")

        return "\n".join(lines)

    def generate_default_dashboard_lkml(self, model_name: str, tables: list[LookMLTableSpec]) -> str:
        """Generate a flexible dynamic LookML dashboard adapted to the available tables."""
        fact_tables = [t for t in tables if t.table_type == "fact"] or tables
        primary_fact = fact_tables[0]
        dim_tables = [t for t in tables if t.table_type == "dimension" and t.table_name != primary_fact.table_name]

        numeric_fields = [
            f for f, t in primary_fact.schema_fields.items()
            if t in ("FLOAT64", "NUMERIC", "INT64", "DOUBLE") and f != primary_fact.primary_key and not f.endswith("_id")
        ]
        kpi_1 = numeric_fields[0] if numeric_fields else "count"
        kpi_2 = numeric_fields[1] if len(numeric_fields) > 1 else (numeric_fields[0] if numeric_fields else "count")

        date_field = None
        for f, t in primary_fact.schema_fields.items():
            if t == "DATE" or f.endswith(("_date", "_day")):
                date_field = f
                break
        if not date_field:
            for f, t in primary_fact.schema_fields.items():
                if t == "TIMESTAMP" or f.endswith(("_time", "_at")):
                    date_field = f
                    break

        date_group = date_field.replace("_date", "").replace("_time", "").replace("_at", "").replace("_day", "") if date_field else None
        date_filter_target = f"{primary_fact.table_name}.{date_group}_date" if date_group else None
        month_timeline = f"{primary_fact.table_name}.{date_group}_month" if date_group else None

        cat_dims = [
            f for f, t in primary_fact.schema_fields.items()
            if t == "STRING" and f != primary_fact.primary_key and not f.endswith("_id")
        ]
        cat_1 = cat_dims[0] if cat_dims else (dim_tables[0].primary_key if dim_tables and dim_tables[0].primary_key else primary_fact.primary_key)
        cat_2 = cat_dims[1] if len(cat_dims) > 1 else cat_1

        title_display = self._format_label(model_name)

        tabs = [
            DashboardTabSpec(name="Executive Pulse", label="Executive Pulse"),
            DashboardTabSpec(name="Entity Breakdown", label="Entity Breakdown"),
            DashboardTabSpec(name="Operational Health", label="Operational Health"),
        ]

        filters = []
        listen_map = {}
        if date_filter_target:
            filters.append(DashboardFilterSpec(name="Date Range", title="Date Range", default_value="365 days"))
            listen_map = {"Date Range": date_filter_target}

        elements = [
            DashboardTileSpec(
                title=f"Total {self._format_label(kpi_1)}",
                model=model_name,
                explore=primary_fact.table_name,
                type="single_value",
                fields=[f"{primary_fact.table_name}.total_{kpi_1 if kpi_1 != 'count' else 'count'}"],
                tab_name="Executive Pulse",
                row=0,
                col=0,
                width=6,
                height=4,
                listen=listen_map,
            ),
            DashboardTileSpec(
                title=f"Average {self._format_label(kpi_2)}",
                model=model_name,
                explore=primary_fact.table_name,
                type="single_value",
                fields=[f"{primary_fact.table_name}.average_{kpi_2 if kpi_2 != 'count' else 'count'}"],
                tab_name="Executive Pulse",
                row=0,
                col=6,
                width=6,
                height=4,
                listen=listen_map,
            ),
            DashboardTileSpec(
                title=f"Total {self._format_label(primary_fact.table_name)} Volume",
                model=model_name,
                explore=primary_fact.table_name,
                type="single_value",
                fields=[f"{primary_fact.table_name}.count"],
                tab_name="Executive Pulse",
                row=0,
                col=12,
                width=6,
                height=4,
                listen=listen_map,
            ),
        ]

        if month_timeline:
            elements.append(
                DashboardTileSpec(
                    title=f"Monthly {self._format_label(kpi_1)} Trajectory",
                    model=model_name,
                    explore=primary_fact.table_name,
                    type="looker_area",
                    fields=[month_timeline, f"{primary_fact.table_name}.total_{kpi_1 if kpi_1 != 'count' else 'count'}"],
                    sorts=[f"{month_timeline} asc"],
                    limit=500,
                    tab_name="Executive Pulse",
                    row=4,
                    col=0,
                    width=14,
                    height=8,
                    listen=listen_map,
                )
            )

        elements.extend([
            DashboardTileSpec(
                title=f"Distribution by {self._format_label(cat_1)}",
                model=model_name,
                explore=primary_fact.table_name,
                type="looker_donut_multiples",
                fields=[f"{primary_fact.table_name}.{cat_1}", f"{primary_fact.table_name}.count"],
                sorts=[f"{primary_fact.table_name}.count desc"],
                limit=10,
                tab_name="Executive Pulse",
                row=4,
                col=14,
                width=10,
                height=8,
                listen=listen_map,
            ),
            DashboardTileSpec(
                title=f"Performance by {self._format_label(cat_2)}",
                model=model_name,
                explore=primary_fact.table_name,
                type="looker_bar",
                fields=[f"{primary_fact.table_name}.{cat_2}", f"{primary_fact.table_name}.total_{kpi_1 if kpi_1 != 'count' else 'count'}", f"{primary_fact.table_name}.count"],
                sorts=[f"{primary_fact.table_name}.total_{kpi_1 if kpi_1 != 'count' else 'count'} desc"],
                limit=15,
                tab_name="Entity Breakdown",
                row=0,
                col=0,
                width=12,
                height=8,
                listen=listen_map,
            ),
            DashboardTileSpec(
                title=f"Detailed {title_display} Records Overview",
                model=model_name,
                explore=primary_fact.table_name,
                type="looker_grid",
                fields=[f"{primary_fact.table_name}.{cat_1}", f"{primary_fact.table_name}.count", f"{primary_fact.table_name}.total_{kpi_1 if kpi_1 != 'count' else 'count'}", f"{primary_fact.table_name}.average_{kpi_2 if kpi_2 != 'count' else 'count'}"],
                sorts=[f"{primary_fact.table_name}.count desc"],
                limit=50,
                tab_name="Entity Breakdown",
                row=0,
                col=12,
                width=12,
                height=8,
                listen=listen_map,
            ),
            DashboardTileSpec(
                title=f"Volume Concentration by {self._format_label(cat_1)}",
                model=model_name,
                explore=primary_fact.table_name,
                type="looker_column",
                fields=[f"{primary_fact.table_name}.{cat_1}", f"{primary_fact.table_name}.count"],
                sorts=[f"{primary_fact.table_name}.count desc"],
                limit=20,
                tab_name="Operational Health",
                row=0,
                col=0,
                width=24,
                height=8,
                listen=listen_map,
            ),
        ])

        spec = DashboardSpec(
            dashboard_name=f"{model_name}_overview",
            title=f"{title_display} Executive Command Center",
            tabs=tabs,
            filters=filters,
            elements=elements,
        )
        return self.generate_dashboard_from_spec(spec)

    def write_lookml_project_files(
        self,
        output_dir: Path,
        model_name: str,
        tables: list[LookMLTableSpec],
        dashboard_content: Optional[str] = None,
    ) -> list[Path]:
        """Write all LookML files to local output folder."""
        views_dir = output_dir / "views"
        models_dir = output_dir / "models"
        dashboards_dir = output_dir / "dashboards"

        views_dir.mkdir(parents=True, exist_ok=True)
        models_dir.mkdir(parents=True, exist_ok=True)
        dashboards_dir.mkdir(parents=True, exist_ok=True)

        written_files = []

        # 1. Write Views
        for t in tables:
            v_content = self.generate_view_lkml(t)
            v_path = views_dir / f"{t.table_name}.view.lkml"
            v_path.write_text(v_content, encoding="utf-8")
            written_files.append(v_path)

        # 2. Write Model
        m_content = self.generate_model_lkml(model_name, tables)
        m_path = models_dir / f"{model_name}.model.lkml"
        m_path.write_text(m_content, encoding="utf-8")
        written_files.append(m_path)

        # 3. Write Dashboard
        dash_str = dashboard_content or self.generate_default_dashboard_lkml(model_name, tables)
        d_path = dashboards_dir / f"{model_name}_overview.dashboard.lookml"
        d_path.write_text(dash_str, encoding="utf-8")
        written_files.append(d_path)

        return written_files
