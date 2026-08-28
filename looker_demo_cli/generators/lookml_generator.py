from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class LookMLTableSpec(BaseModel):
    table_name: str
    table_type: str = "fact"  # fact or dimension
    schema_fields: Dict[str, str] = Field(default_factory=dict)
    primary_key: Optional[str] = None
    foreign_keys: Dict[str, str] = Field(default_factory=dict)  # fk_field -> parent_table.pk


class LookMLGenerator:
    """Generates production-grade LookML views, models, explores, and executive dashboards."""

    def __init__(self, project_id: str, dataset_id: str, connection_name: str = "default_bigquery_connection"):
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.connection_name = connection_name

    def _format_label(self, name: str) -> str:
        """Convert snake_case field name to clean Title Case label."""
        acronyms = {"id": "ID", "pk": "PK", "fk": "FK", "url": "URL", "uri": "URI", "usd": "USD", "bq": "BQ", "api": "API", "ip": "IP", "icd": "ICD", "md": "MD", "los": "LOS", "wa": "WA"}
        words = name.split("_")
        formatted = []
        for w in words:
            if w.lower() in acronyms:
                formatted.append(acronyms[w.lower()])
            else:
                formatted.append(w.capitalize())
        return " ".join(formatted)

    def _format_description(self, field_name: str, field_type: str, is_pk: bool = False, table_name: str = "") -> str:
        """Generate human-readable description for dimension/measure."""
        clean_name = self._format_label(field_name)
        if is_pk:
            return f"Unique primary key identifier for {self._format_label(table_name)} records."
        if field_name.endswith("_id"):
            entity = field_name[:-3]
            return f"Foreign key reference linking to {self._format_label(entity)}."
        if any(k in field_name for k in ["amount", "cost", "revenue", "price", "fee"]):
            return f"Monetary amount for {clean_name.lower()} in USD."
        if "rate" in field_name or "pct" in field_name or "percent" in field_name:
            return f"Calculated rate/percentage metric for {clean_name.lower()}."
        if "date" in field_name or "time" in field_name or field_type in ("TIMESTAMP", "DATE"):
            return f"Timestamp/date recording when the {clean_name.lower()} occurred."
        if field_type in ("BOOL", "BOOLEAN") or "is_" in field_name or "has_" in field_name:
            return f"Boolean indicator flag determining whether {clean_name.lower()} is true."
        return f"Attribute representing {clean_name.lower()}."

    def generate_view_lkml(self, spec: LookMLTableSpec) -> str:
        """Generate production-grade LookML view string with mandatory labels and descriptions."""
        lines = [
            f"view: {spec.table_name} {{",
            f"  sql_table_name: `{self.project_id}.{self.dataset_id}.{spec.table_name}` ;;",
            "",
            "  # -------------------------------------------------------------",
            "  # Dimensions",
            "  # -------------------------------------------------------------",
        ]

        # Dimensions
        for field_name, field_type in spec.schema_fields.items():
            is_pk = (field_name == spec.primary_key)
            label = self._format_label(field_name)
            desc = self._format_description(field_name, field_type, is_pk=is_pk, table_name=spec.table_name)

            if "time" in field_name or "date" in field_name or field_type in ("TIMESTAMP", "DATETIME", "DATE"):
                group_name = field_name.replace("_time", "").replace("_date", "")
                group_label = self._format_label(group_name)
                lines.extend([
                    f"  dimension_group: {group_name} {{",
                    f'    label: "{group_label}"',
                    f'    description: "{desc}"',
                    "    type: time",
                    "    timeframes: [raw, time, date, week, month, quarter, year]",
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
            if any(k in field_name for k in ["amount", "cost", "revenue", "price", "val", "loss", "fee", "rate", "distance", "days", "score"]):
                if field_type in ("FLOAT64", "NUMERIC", "INT64", "DOUBLE"):
                    val_format = "usd_0" if any(k in field_name for k in ["usd", "cost", "rev", "price", "loss", "fee", "val", "amount"]) else "decimal_1"
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

    def generate_model_lkml(self, model_name: str, tables: List[LookMLTableSpec]) -> str:
        """Generate LookML model string with joins."""
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

        fact_tables = [t for t in tables if t.table_type == "fact"] or tables[:1]
        for ft in fact_tables:
            lines.extend([
                f"explore: {ft.table_name} {{",
                f'  label: "{ft.table_name.replace("_", " ").title()}"',
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

    def write_lookml_project_files(
        self,
        output_dir: Path,
        model_name: str,
        tables: List[LookMLTableSpec],
        dashboard_content: Optional[str] = None,
    ) -> List[Path]:
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

        # 3. Write Dashboard if provided
        if dashboard_content:
            d_path = dashboards_dir / f"{model_name}_overview.dashboard.lookml"
            d_path.write_text(dashboard_content, encoding="utf-8")
            written_files.append(d_path)

        return written_files
