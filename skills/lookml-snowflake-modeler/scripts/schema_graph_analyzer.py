#!/usr/bin/env python3
"""
schema_graph_analyzer.py

Staff Data Engineer & DBA graph analysis engine for modeling Snowflake and 3NF database schemas in Looker.
Constructs a Directed Multigraph of schema relationships (FK -> PK), classifies tables into Base Views vs.
Joined Dimensions, eliminates fan/chasm traps via NDT rollups, resolves diamond joins with role-playing aliases,
and generates production-ready LookML models.
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import json
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field


class TableSchemaSpec(BaseModel):
    table_name: str
    table_type: str = "dimension"  # fact, dimension, bridge, event
    primary_key: Optional[str] = None
    foreign_keys: dict[str, str] = Field(default_factory=dict)  # fk_col -> TargetTable.TargetCol
    schema_fields: dict[str, str] = Field(default_factory=dict)  # col_name -> TYPE
    description: Optional[str] = None


class JoinEdge(BaseModel):
    join_name: str
    from_view: str
    target_view: str
    fk_column: str
    target_pk: str
    relationship: str = "many_to_one"  # many_to_one, one_to_one
    view_label: str
    sql_on: str
    is_role_playing: bool = False
    is_self_referential: bool = False
    is_ndt: bool = False


class ExploreSpec(BaseModel):
    explore_name: str
    base_view: str
    label: str
    description: str
    joins: list[JoinEdge] = Field(default_factory=list)


class NDTRollupSpec(BaseModel):
    view_name: str
    child_table: str
    parent_table: str
    parent_pk: str
    child_fk: str
    measures: list[dict[str, str]] = Field(default_factory=list)


class SchemaGraphAnalyzer:
    """Graph Analysis Engine for Snowflake & 3NF Schema LookML Generation."""

    def __init__(
        self,
        tables: list[TableSchemaSpec],
        project_id: str = "your_project",
        dataset_id: str = "your_dataset",
        connection_name: str = "default_bigquery_connection",
    ):
        self.tables_map: dict[str, TableSchemaSpec] = {t.table_name: t for t in tables}
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.connection_name = connection_name

        # Directed Graph adjacency: u (child/many) -> list of (v (parent/one), fk_col, target_pk)
        self.adj_out: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        # Reverse adjacency: v (parent/one) -> list of (u (child/many), fk_col, target_pk)
        self.adj_in: dict[str, list[tuple[str, str, str]]] = defaultdict(list)

        self._build_graph()

    def _build_graph(self) -> None:
        """Populate directed graph edges based on foreign keys (convention: child FK -> parent PK)."""
        for t_name, t_spec in self.tables_map.items():
            for fk_col, target_ref in t_spec.foreign_keys.items():
                if "." in target_ref:
                    target_table, target_pk = target_ref.split(".", 1)
                else:
                    target_table = target_ref
                    target_pk = self.tables_map.get(target_table, TableSchemaSpec(table_name=target_table)).primary_key or "id"

                self.adj_out[t_name].append((target_table, fk_col, target_pk))
                self.adj_in[target_table].append((t_name, fk_col, target_pk))

    def _format_label(self, raw_name: str) -> str:
        """Convert snake_case or raw identifier to human-friendly Title Case."""
        clean = raw_name.replace("dim_", "").replace("fct_", "").replace("_usd", " (USD)").replace("_pct", " (%)")
        words = clean.split("_")
        acronyms = {"id": "ID", "usd": "USD", "url": "URL", "api": "API", "kpi": "KPI", "nps": "NPS", "ltv": "LTV", "fk": "FK", "pk": "PK", "qa": "QA", "pr": "PR"}
        formatted = [acronyms.get(w.lower(), w.capitalize()) for w in words if w]
        return " ".join(formatted)

    def classify_tables(self) -> dict[str, dict[str, Any]]:
        """Compute topological degrees and classify each table's architectural role."""
        classifications = {}
        for t_name, t_spec in self.tables_map.items():
            d_out = len(self.adj_out[t_name])
            d_in = len(self.adj_in[t_name])

            # Determine Role
            is_declared_fact = t_spec.table_type in ("fact", "event") or t_name.startswith("fct_")
            is_bridge = (
                t_spec.table_type == "bridge"
                or "mapping" in t_name
                or "rel_" in t_name
                or (d_out >= 2 and d_in == 0 and ("_id" in (t_spec.primary_key or "") and "," in (t_spec.primary_key or "")))
            )

            if is_bridge:
                role = "Bridge / Junction Table"
                explore_role = "Array Rollup Dimension / Bridge Explore"
                is_base_candidate = False
            elif is_declared_fact and d_in == 0:
                role = "Atomic Event Leaf Fact"
                explore_role = "Primary Base View (Event Stream Explore)"
                is_base_candidate = True
            elif is_declared_fact or (d_out >= 1 and d_in >= 1):
                role = "Accumulating Stateful Fact / Core Entity"
                explore_role = "Primary Base View (Entity Lifecycle Explore)"
                is_base_candidate = True
            elif d_out == 0 and d_in >= 1:
                role = "Sink / Hierarchy Dimension"
                explore_role = "Joined View Only (Never Base View)"
                is_base_candidate = False
            else:
                role = "Intermediate Dimension"
                explore_role = "Joined View Only"
                is_base_candidate = False

            classifications[t_name] = {
                "role": role,
                "explore_role": explore_role,
                "is_base_candidate": is_base_candidate,
                "out_degree": d_out,
                "in_degree": d_in,
            }
        return classifications

    def detect_chasm_traps(self, base_view: str) -> list[tuple[str, str, str]]:
        """Detect child tables pointing to this base view that would cause fan-out Cartesian products if joined directly."""
        child_refs = self.adj_in.get(base_view, [])
        # Filter out self-loops
        external_children = [c for c in child_refs if c[0] != base_view]
        return external_children

    def generate_ndt_rollups_for_base(self, base_view: str) -> list[NDTRollupSpec]:
        """Generate NDT summary specifications for all 1:N children of a base view to avoid chasm traps."""
        children = self.detect_chasm_traps(base_view)
        rollups = []
        base_pk = self.tables_map.get(base_view, TableSchemaSpec(table_name=base_view)).primary_key or "id"

        for child_table, child_fk, parent_pk in children:
            child_spec = self.tables_map.get(child_table)
            child_clean = child_table.replace("dim_", "").replace("fct_", "")
            view_name = f"{child_clean}_rollup_for_{base_view.replace('dim_', '').replace('fct_', '')}"

            measures = [
                {"name": f"total_{child_clean}_count", "field": f"{child_table}.count", "type": "number", "label": f"Total {self._format_label(child_clean)} Count"}
            ]

            # Check if child has timestamp for last activity
            if child_spec:
                for f_name, f_type in child_spec.schema_fields.items():
                    if f_type in ("TIMESTAMP", "DATETIME", "DATE") or f_name.endswith(("_at", "_time", "_date")):
                        measures.append({
                            "name": f"last_{child_clean}_time",
                            "field": f"{child_table}.max_{f_name.replace('_at', '').replace('_time', '').replace('_date', '')}",
                            "type": "time",
                            "label": f"Last {self._format_label(child_clean)} Date/Time",
                        })
                        break

            rollups.append(
                NDTRollupSpec(
                    view_name=view_name,
                    child_table=child_table,
                    parent_table=base_view,
                    parent_pk=parent_pk,
                    child_fk=child_fk,
                    measures=measures,
                )
            )
        return rollups

    def build_explore_spec(self, base_view: str) -> ExploreSpec:
        """Construct safe Explore with BFS forward reachability, diamond resolution, and topological join ordering."""
        base_label = self._format_label(base_view)
        explore_spec = ExploreSpec(
            explore_name=base_view,
            base_view=base_view,
            label=base_label,
            description=f"Curated Explore for analyzing {base_label.lower()} with multi-hop snowflake dimensions.",
        )

        visited_paths: set[str] = set()
        queue: deque[tuple[str, str, int]] = deque([(base_view, base_view, 0)])  # (current_node, alias_name, depth)
        target_counts: dict[str, int] = defaultdict(int)

        joins: list[JoinEdge] = []

        while queue:
            curr_table, curr_alias, depth = queue.popleft()

            for target_table, fk_col, target_pk in self.adj_out.get(curr_table, []):
                # 1. Self-referential check
                if target_table == curr_table:
                    join_name = f"parent_{target_table.replace('dim_', '').replace('fct_', '')}"
                    joins.append(
                        JoinEdge(
                            join_name=join_name,
                            from_view=target_table,
                            target_view=target_table,
                            fk_column=fk_col,
                            target_pk=target_pk,
                            relationship="many_to_one",
                            view_label=f"Parent {self._format_label(target_table)}",
                            sql_on=f"${{{curr_alias}.{fk_col}}} = ${{{join_name}.{target_pk}}}",
                            is_self_referential=True,
                        )
                    )
                    continue

                target_counts[target_table] += 1
                target_count = target_counts[target_table]

                # 2. Canonical FK vs. Role-Playing Diamond Resolution
                base_clean = target_table.replace("dim_", "").replace("fct_", "")
                singular_base = base_clean[:-1] if base_clean.endswith("s") else base_clean
                canonical_fk_names = {
                    f"{target_table}_id",
                    f"{base_clean}_id",
                    f"{singular_base}_id",
                    "id",
                    f"{target_pk}",
                }
                # Also handle compound suffixes like workflow_state -> state_id
                for part in base_clean.split("_"):
                    canonical_fk_names.add(f"{part}_id")
                    if part.endswith("s"):
                        canonical_fk_names.add(f"{part[:-1]}_id")

                is_role_playing = (target_count > 1) or (fk_col.lower() not in canonical_fk_names)

                if is_role_playing:
                    role_alias = fk_col.replace("_user_id", "").replace("_id", "").replace("id_", "")
                    join_name = role_alias if role_alias not in (base_clean, singular_base) else f"{role_alias}_{target_table}"
                    view_label = self._format_label(role_alias)
                else:
                    join_name = target_table
                    view_label = self._format_label(target_table)

                path_sig = f"{curr_alias}->{join_name}"
                if path_sig in visited_paths:
                    continue
                visited_paths.add(path_sig)

                # Format SQL ON
                sql_on = f"${{{curr_alias}.{fk_col}}} = ${{{join_name}.{target_pk}}}"

                joins.append(
                    JoinEdge(
                        join_name=join_name,
                        from_view=target_table,
                        target_view=target_table,
                        fk_column=fk_col,
                        target_pk=target_pk,
                        relationship="many_to_one",
                        view_label=view_label,
                        sql_on=sql_on,
                        is_role_playing=is_role_playing,
                    )
                )

                # Continue forward BFS search up the snowflake chain
                queue.append((target_table, join_name, depth + 1))

        # 3. Add NDT Rollup Joins for Chasm Trap Elimination
        ndt_rollups = self.generate_ndt_rollups_for_base(base_view)
        for ndt in ndt_rollups:
            joins.append(
                JoinEdge(
                    join_name=ndt.view_name,
                    from_view=ndt.view_name,
                    target_view=ndt.view_name,
                    fk_column=base_view,
                    target_pk=ndt.parent_pk,
                    relationship="one_to_one",
                    view_label=f"{base_label} Metrics ({self._format_label(ndt.child_table)})",
                    sql_on=f"${{{base_view}.{ndt.parent_pk}}} = ${{{ndt.view_name}.{ndt.child_fk}}}",
                    is_ndt=True,
                )
            )

        explore_spec.joins = joins
        return explore_spec

    def generate_lookml_model(self) -> str:
        """Generate complete, valid LookML Model with all curated Explores."""
        classifications = self.classify_tables()
        base_candidates = [t for t, c in classifications.items() if c["is_base_candidate"]]

        if not base_candidates:
            base_candidates = list(self.tables_map.keys())[:1]

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
            "# " + "=" * 70,
            "# Curated Explores (Constructed via Graph Forward BFS Reachability)",
            "# " + "=" * 70,
            "",
        ]

        for base_table in base_candidates:
            spec = self.build_explore_spec(base_table)
            lines.extend([
                f"explore: {spec.explore_name} {{",
                f'  label: "{spec.label}"',
                f'  description: "{spec.description}"',
                f'  view_label: "{spec.label} (Base)"',
                "",
            ])

            for j in spec.joins:
                lines.extend([
                    f"  join: {j.join_name} {{",
                ])
                if j.from_view != j.join_name:
                    lines.append(f"    from: {j.from_view}")
                lines.extend([
                    f'    view_label: "{j.view_label}"',
                    "    type: left_outer",
                    f"    relationship: {j.relationship}",
                    f"    sql_on: {j.sql_on} ;;",
                    "  }",
                    "",
                ])

            lines.extend(["}", ""])

        return "\n".join(lines)

    def generate_view_lkml(self, spec: TableSchemaSpec) -> str:
        """Generate self-documenting LookML view with mandatory labels, descriptions, and primary keys."""
        lines = [
            f"view: {spec.table_name} {{",
            f"  sql_table_name: `{self.project_id}.{self.dataset_id}.{spec.table_name}` ;;",
            "",
            "  # -------------------------------------------------------------",
            "  # Dimensions",
            "  # -------------------------------------------------------------",
        ]

        pk_col = spec.primary_key or "id"

        for field_name, field_type in spec.schema_fields.items():
            is_pk = (field_name == pk_col)
            label = self._format_label(field_name)
            desc = f"Primary key for {self._format_label(spec.table_name)}." if is_pk else f"Attribute representing {label.lower()}."

            is_time = field_type in ("TIMESTAMP", "DATETIME", "DATE") or field_name.endswith(("_at", "_time", "_date", "_day"))
            if is_time and field_type not in ("INT64", "FLOAT64", "NUMERIC"):
                group_name = field_name
                for sfx in ["_at", "_time", "_date", "_day"]:
                    if group_name.endswith(sfx):
                        group_name = group_name[:-len(sfx)]
                        break
                is_date = (field_type == "DATE") or field_name.endswith(("_date", "_day"))
                lines.extend([
                    f"  dimension_group: {group_name} {{",
                    f'    label: "{self._format_label(group_name)}"',
                    f'    description: "{desc}"',
                    "    type: time",
                    f"    datatype: {'date' if is_date else 'timestamp'}",
                    f"    timeframes: [{'raw, date, week, month, quarter, year' if is_date else 'raw, time, date, week, month, quarter, year'}]",
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

        # Measures
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
            f"  measure: count_distinct_{spec.table_name} {{",
            f'    label: "Distinct {self._format_label(spec.table_name)} Count"',
            f'    description: "Distinct count of {self._format_label(pk_col)}."',
            "    type: count_distinct",
            f"    sql: ${{{pk_col}}} ;;",
            "  }",
            "",
        ])

        for f_name, f_type in spec.schema_fields.items():
            if f_name != pk_col and not f_name.endswith("_id") and f_type in ("FLOAT64", "NUMERIC", "INT64", "DOUBLE"):
                f_label = self._format_label(f_name)
                fmt = "usd_0" if any(k in f_name.lower() for k in ["usd", "cost", "price", "amount", "rev"]) else "decimal_1"
                lines.extend([
                    f"  measure: total_{f_name} {{",
                    f'    label: "Total {f_label}"',
                    f'    description: "Sum of {f_label.lower()}."',
                    "    type: sum",
                    f"    sql: ${{{f_name}}} ;;",
                    f"    value_format_name: {fmt}",
                    "  }",
                    "",
                    f"  measure: average_{f_name} {{",
                    f'    label: "Average {f_label}"',
                    f'    description: "Average {f_label.lower()} per record."',
                    "    type: average",
                    f"    sql: ${{{f_name}}} ;;",
                    f"    value_format_name: {fmt}",
                    "  }",
                    "",
                ])

        lines.append("}")
        return "\n".join(lines)


def run_cli():
    parser = argparse.ArgumentParser(description="Analyze Schema Graph and Generate LookML Explores for Snowflake Schemas.")
    parser.add_argument("--schema-file", type=str, help="Path to JSON file containing list of TableSchemaSpecs.")
    parser.add_argument("--output-dir", type=str, default="./lookml_output", help="Directory to write LookML view and model files.")
    parser.add_argument("--project-id", type=str, default="looker-demo-392616", help="Google Cloud Project ID.")
    parser.add_argument("--dataset-id", type=str, default="linear_demo", help="BigQuery Dataset ID.")
    parser.add_argument("--connection-name", type=str, default="default_bigquery_connection", help="Looker Database Connection Name.")
    args = parser.parse_args()

    if not args.schema_file:
        print("Error: --schema-file is required.")
        return

    schema_path = Path(args.schema_file)
    if not schema_path.exists():
        print(f"Error: File not found: {schema_path}")
        return

    with open(schema_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    tables = [TableSchemaSpec(**t) for t in data]
    analyzer = SchemaGraphAnalyzer(
        tables=tables,
        project_id=args.project_id,
        dataset_id=args.dataset_id,
        connection_name=args.connection_name,
    )

    out_dir = Path(args.output_dir)
    views_dir = out_dir / "views"
    models_dir = out_dir / "models"
    views_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)

    # Write views
    for t in tables:
        v_code = analyzer.generate_view_lkml(t)
        v_path = views_dir / f"{t.table_name}.view.lkml"
        v_path.write_text(v_code, encoding="utf-8")

    # Write model
    m_code = analyzer.generate_lookml_model()
    m_path = models_dir / f"{args.dataset_id}.model.lkml"
    m_path.write_text(m_code, encoding="utf-8")

    print(f"Successfully generated LookML model and {len(tables)} views in {out_dir}")


if __name__ == "__main__":
    run_cli()
