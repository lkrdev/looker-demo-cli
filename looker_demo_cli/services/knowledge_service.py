from __future__ import annotations

from typing import Any

import google.auth
import google.auth.transport.requests
import requests
from google.cloud import bigquery

from looker_demo_cli.generators.lookml_generator import LookMLTableSpec
from looker_demo_cli.utils.console import print_warning


def introspect_bq_table_specs(
    project_id: str,
    dataset_id: str,
    credentials: Any = None,
    location: str = "US",
    table_filter: list[str] | None = None,
) -> list[LookMLTableSpec]:
    """Introspect BigQuery tables, constraints, and Knowledge Catalog/Dataplex semantics.

    Generates rich LookMLTableSpec objects for LookML modeling.
    """
    if not credentials:
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])

    bq_client = bigquery.Client(project=project_id, credentials=credentials, location=location)
    dataset_ref = bq_client.dataset(dataset_id)

    try:
        tables_list = list(bq_client.list_tables(dataset_ref))
    except Exception as e:
        print_warning(f"Could not list tables in BigQuery dataset `{dataset_id}`: {e}")
        return []

    target_tables = [t.table_id for t in tables_list]
    if table_filter:
        target_tables = [t for t in target_tables if t in table_filter]

    # Prepare token for optional Data Catalog / Dataplex enrichment
    auth_token = None
    try:
        req = google.auth.transport.requests.Request()
        credentials.refresh(req)
        auth_token = credentials.token
    except Exception:
        pass

    specs: list[LookMLTableSpec] = []

    for tbl_id in target_tables:
        try:
            tbl = bq_client.get_table(dataset_ref.table(tbl_id))
            schema_fields: dict[str, str] = {}
            column_descriptions: dict[str, str] = {}
            primary_key: str | None = None
            foreign_keys: dict[str, str] = {}

            # 1. Native BigQuery Schema & Types
            for field in tbl.schema:
                schema_fields[field.name] = field.field_type
                if field.description:
                    column_descriptions[field.name] = field.description

            # 2. BigQuery Constraints (Primary Key & Foreign Keys)
            constraints = getattr(tbl, "table_constraints", None)
            if constraints:
                # Primary Key
                pk_info = getattr(constraints, "primary_key", None)
                if pk_info and getattr(pk_info, "columns", None):
                    primary_key = pk_info.columns[0]

                # Foreign Keys
                fks = getattr(constraints, "foreign_keys", []) or []
                for fk in fks:
                    referencing = getattr(fk, "referencing_columns", [])
                    ref_tbl = getattr(fk, "referenced_table", None)
                    ref_cols = getattr(fk, "referenced_columns", [])
                    if referencing and ref_tbl and ref_cols:
                        target_tbl_name = getattr(ref_tbl, "table_id", str(ref_tbl))
                        foreign_keys[referencing[0]] = f"{target_tbl_name}.{ref_cols[0]}"

            # Fallback heuristic for primary key if not explicitly defined in BQ constraints
            if not primary_key:
                candidates = ["id", f"{tbl_id}_id", f"{tbl_id.rstrip('s')}_id"]
                for c in candidates:
                    if c in schema_fields:
                        primary_key = c
                        break

            # 3. Knowledge Catalog / Dataplex Semantics Enrichment
            if auth_token:
                try:
                    entry_id = f"projects.{project_id}.datasets.{dataset_id}.tables.{tbl_id}"
                    cat_url = (
                        f"https://datacatalog.googleapis.com/v1/projects/{project_id}/"
                        f"locations/{location.lower()}/entryGroups/@bigquery/entries/{entry_id}"
                    )
                    r_cat = requests.get(
                        cat_url,
                        headers={"Authorization": f"Bearer {auth_token}"},
                        timeout=5,
                    )
                    if r_cat.status_code == 200:
                        cat_data = r_cat.json()
                        cat_schema = cat_data.get("schema", {}).get("columns", [])
                        for col in cat_schema:
                            c_name = col.get("column")
                            c_desc = col.get("description")
                            if c_name and c_desc and c_name not in column_descriptions:
                                column_descriptions[c_name] = c_desc
                except Exception:
                    pass

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
                    foreign_keys=foreign_keys,
                )
            )
        except Exception as err:
            print_warning(f"Notice while introspecting table `{tbl_id}`: {err}")

    return specs
