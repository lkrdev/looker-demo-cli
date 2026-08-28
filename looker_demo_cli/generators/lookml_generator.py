# SPDX-FileCopyrightText: Copyright (c) 2026 lkr.dev. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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

    def generate_view_lkml(self, spec: LookMLTableSpec) -> str:
        """Generate LookML view string."""
        lines = [
            f"view: {spec.table_name} {{",
            f"  sql_table_name: `{self.project_id}.{self.dataset_id}.{spec.table_name}` ;;",
            "",
        ]

        # Dimensions
        for field_name, field_type in spec.schema_fields.items():
            is_pk = (field_name == spec.primary_key)
            if "time" in field_name or "date" in field_name or field_type in ("TIMESTAMP", "DATETIME", "DATE"):
                lines.extend([
                    f"  dimension_group: {field_name.replace('_time', '').replace('_date', '')} {{",
                    "    type: time",
                    "    timeframes: [raw, time, date, week, month, quarter, year]",
                    f"    sql: ${{TABLE}}.{field_name} ;;",
                    "  }",
                    "",
                ])
            elif field_type in ("INT64", "FLOAT64", "NUMERIC", "DOUBLE", "INTEGER"):
                lines.extend([
                    f"  dimension: {field_name} {{",
                    f"    primary_key: yes" if is_pk else None,
                    "    type: number",
                    f"    sql: ${{TABLE}}.{field_name} ;;",
                    "  }",
                    "",
                ])
            elif field_type in ("BOOL", "BOOLEAN"):
                lines.extend([
                    f"  dimension: {field_name} {{",
                    "    type: yesno",
                    f"    sql: ${{TABLE}}.{field_name} ;;",
                    "  }",
                    "",
                ])
            else:
                lines.extend([
                    f"  dimension: {field_name} {{",
                    f"    primary_key: yes" if is_pk else None,
                    "    type: string",
                    f"    sql: ${{TABLE}}.{field_name} ;;",
                    "  }",
                    "",
                ])

        # Filter out None lines
        lines = [line for line in lines if line is not None]

        # Standard Measures
        lines.extend([
            "  # -------------------------------------------------------------",
            "  # Measures",
            "  # -------------------------------------------------------------",
            "  measure: count {",
            "    type: count",
            "  }",
        ])

        if spec.primary_key:
            lines.extend([
                f"  measure: count_distinct_{spec.table_name} {{",
                "    type: count_distinct",
                f"    sql: ${{{spec.primary_key}}} ;;",
                "  }",
            ])

        # Sum/Avg measures for numeric columns
        for field_name, field_type in spec.schema_fields.items():
            if any(k in field_name for k in ["amount", "cost", "revenue", "price", "val", "loss", "fee", "rate", "distance"]):
                if field_type in ("FLOAT64", "NUMERIC", "INT64", "DOUBLE"):
                    val_format = "usd_0" if any(k in field_name for k in ["usd", "cost", "rev", "price", "loss", "fee", "val"]) else "decimal_0"
                    lines.extend([
                        f"  measure: total_{field_name} {{",
                        "    type: sum",
                        f"    sql: ${{{field_name}}} ;;",
                        f"    value_format_name: {val_format}",
                        "  }",
                        f"  measure: average_{field_name} {{",
                        "    type: average",
                        f"    sql: ${{{field_name}}} ;;",
                        f"    value_format_name: {val_format}",
                        "  }",
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
