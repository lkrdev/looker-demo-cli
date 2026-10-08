from __future__ import annotations

from typing import Any

import google.auth
from google.cloud import bigquery

from looker_demo_cli.generators.lookml_generator import LookMLTableSpec
from looker_demo_cli.utils.console import print_warning

TYPE_NORMALIZATION = {
    "FLOAT": "FLOAT64",
    "INTEGER": "INT64",
    "BOOLEAN": "BOOL",
    "RECORD": "STRUCT",
}


def introspect_bq_table_specs(
    project_id: str,
    dataset_id: str,
    credentials: Any = None,
    location: str = "US",
    table_filter: list[str] | None = None,
) -> list[LookMLTableSpec]:
    """Introspect BigQuery tables, constraints, partition/clustering, and schema semantics.

    Generates rich LookMLTableSpec objects for LookML modeling.
    """
    try:
        if not credentials:
            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])

        bq_client = bigquery.Client(project=project_id, credentials=credentials, location=location)
        dataset_ref = bigquery.DatasetReference(project_id, dataset_id)
        tables_list = list(bq_client.list_tables(dataset_ref))
    except Exception as e:
        print_warning(f"Could not list tables in BigQuery dataset `{dataset_id}`: {e}")
        return []

    target_tables = [t.table_id for t in tables_list]
    if table_filter:
        target_tables = [t for t in target_tables if t in table_filter]

    specs: list[LookMLTableSpec] = []

    for tbl_id in target_tables:
        try:
            tbl = bq_client.get_table(dataset_ref.table(tbl_id))
            schema_fields: dict[str, str] = {}
            column_descriptions: dict[str, str] = {}
            primary_key: str | None = None
            primary_keys: list[str] = []
            foreign_keys: dict[str, str] = {}

            # 1. Native BigQuery Schema & Normalized Types
            for field in tbl.schema:
                raw_type = (field.field_type or "STRING").upper()
                schema_fields[field.name] = TYPE_NORMALIZATION.get(raw_type, raw_type)
                if field.description:
                    column_descriptions[field.name] = field.description

            # 2. BigQuery Constraints (Primary Key & Foreign Keys)
            constraints = getattr(tbl, "table_constraints", None)
            if constraints:
                # Primary Key
                pk_info = getattr(constraints, "primary_key", None)
                if pk_info and getattr(pk_info, "columns", None):
                    primary_keys = list(pk_info.columns)
                    if primary_keys:
                        primary_key = primary_keys[0]

                # Foreign Keys
                fks = getattr(constraints, "foreign_keys", []) or []
                for fk in fks:
                    ref_tbl = getattr(fk, "referenced_table", None)
                    target_tbl_name = getattr(ref_tbl, "table_id", str(ref_tbl)) if ref_tbl else ""
                    # Check ColumnReference objects in column_references
                    col_refs = getattr(fk, "column_references", []) or []
                    for cr in col_refs:
                        src_col = getattr(cr, "referencing_column", None)
                        tgt_col = getattr(cr, "referenced_column", None)
                        if src_col and tgt_col and target_tbl_name:
                            foreign_keys[src_col] = f"{target_tbl_name}.{tgt_col}"
                    # Fallback for referencing_columns / referenced_columns
                    if not col_refs:
                        referencing = getattr(fk, "referencing_columns", []) or []
                        ref_cols = getattr(fk, "referenced_columns", []) or []
                        if referencing and ref_tbl and ref_cols and target_tbl_name:
                            foreign_keys[referencing[0]] = f"{target_tbl_name}.{ref_cols[0]}"

            # Fallback heuristic for primary key if not explicitly defined in BQ constraints
            if not primary_key:
                candidates = ["id", f"{tbl_id}_id", f"{tbl_id.rstrip('s')}_id"]
                for c in candidates:
                    if c in schema_fields:
                        primary_key = c
                        primary_keys = [c]
                        break

            # 3. Partitioning & Clustering
            partition_field: str | None = None
            time_part = getattr(tbl, "time_partitioning", None)
            if time_part and getattr(time_part, "field", None):
                partition_field = time_part.field
            if not partition_field:
                range_part = getattr(tbl, "range_partitioning", None)
                if range_part and getattr(range_part, "field", None):
                    partition_field = range_part.field

            clustering_fields = list(getattr(tbl, "clustering_fields", []) or [])

            # Table description & labels
            table_desc = getattr(tbl, "description", None)

            # Classify table type (fact vs dimension)
            is_fact = (
                tbl_id.startswith("fct_")
                or "fact" in tbl_id
                or "order" in tbl_id
                or "event" in tbl_id
                or "transaction" in tbl_id
                or "log" in tbl_id
                or "reading" in tbl_id
                or "alert" in tbl_id
            )
            tbl_type = "fact" if is_fact else "dimension"

            specs.append(
                LookMLTableSpec(
                    table_name=tbl_id,
                    table_type=tbl_type,
                    schema_fields=schema_fields,
                    primary_key=primary_key,
                    primary_keys=primary_keys,
                    foreign_keys=foreign_keys,
                    description=table_desc,
                    column_descriptions=column_descriptions,
                    partition_field=partition_field,
                    clustering_fields=clustering_fields,
                )
            )
        except Exception as err:
            print_warning(f"Notice while introspecting table `{tbl_id}`: {err}")

    return specs
