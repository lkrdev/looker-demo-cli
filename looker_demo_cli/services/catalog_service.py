"""Catalog service for orchestrating Knowledge Catalog (Dataplex) and BigQuery metadata.

Builds structured, serializable CatalogSnapshots by fusing:
1. BigQuery table schema, types, partition/cluster keys, and native PK/FK constraints.
2. Dataplex Universal Catalog entry-level and column-level curation aspects.
3. Dataplex Business Glossary term definitions linked via EntryLinks.
4. Dataplex lookupContext multi-table joins.
5. Statistical coverage metrics and LookMLTableSpec mapping.
"""

from __future__ import annotations

from typing import Any

from looker_demo_cli.catalog.models import (
    CatalogSnapshot,
    ColumnMeta,
    Relationship,
    SourceStatus,
    TableMeta,
)
from looker_demo_cli.generators.lookml_generator import LookMLTableSpec
from looker_demo_cli.ports import BigQueryPort, CatalogPort
from looker_demo_cli.services.knowledge_service import TYPE_NORMALIZATION
from looker_demo_cli.utils.console import print_warning


def build_catalog_snapshot(
    bq_client: BigQueryPort,
    catalog_client: CatalogPort,
    dataset_id: str,
    location: str = "us",
    table_filter: list[str] | None = None,
) -> CatalogSnapshot:
    """Build a rich CatalogSnapshot fusing BigQuery native and Dataplex metadata."""
    project_id = bq_client.project_id
    sources: dict[str, SourceStatus] = {}
    tables_meta: dict[str, TableMeta] = {}
    relationships: list[Relationship] = []
    raw_aspects: dict[str, Any] = {}

    # 1. BigQuery Native Tables
    try:
        table_names = bq_client.list_tables(dataset_id)
        if table_filter:
            table_names = [t for t in table_names if t in table_filter]
        sources["bigquery"] = SourceStatus(
            source_name="bigquery",
            available=True,
            details=f"Listed {len(table_names)} tables in {dataset_id}",
        )
    except Exception as e:
        sources["bigquery"] = SourceStatus(
            source_name="bigquery",
            available=False,
            details=str(e),
        )
        return CatalogSnapshot(
            dataset_id=dataset_id,
            project_id=project_id,
            location=location,
            sources=sources,
            tables={},
            relationships=[],
        )

    # 2. Introspect Table Metadata & Constraints
    for tbl_id in table_names:
        try:
            tbl = bq_client.get_table_metadata(dataset_id, tbl_id)
        except Exception as e:
            print_warning(f"Could not read metadata for table {tbl_id}: {e}")
            continue

        num_rows = getattr(tbl, "num_rows", 0) or 0
        description = getattr(tbl, "description", None)
        clustering_fields = list(getattr(tbl, "clustering_fields", []) or [])

        # Partitioning
        partition_field: str | None = None
        time_part = getattr(tbl, "time_partitioning", None)
        if time_part and getattr(time_part, "field", None):
            partition_field = time_part.field
        if not partition_field:
            range_part = getattr(tbl, "range_partitioning", None)
            if range_part and getattr(range_part, "field", None):
                partition_field = range_part.field

        # Constraints
        pk_cols: list[str] = []
        fks_dict: dict[str, str] = {}
        constraints = getattr(tbl, "table_constraints", None)
        if constraints:
            pk_info = getattr(constraints, "primary_key", None)
            if pk_info and getattr(pk_info, "columns", None):
                pk_cols = list(pk_info.columns)

            fks = getattr(constraints, "foreign_keys", []) or []
            for fk in fks:
                ref_tbl = getattr(fk, "referenced_table", None)
                target_tbl_name = getattr(ref_tbl, "table_id", str(ref_tbl)) if ref_tbl else ""
                col_refs = getattr(fk, "column_references", []) or []
                for cr in col_refs:
                    src_col = getattr(cr, "referencing_column", None)
                    tgt_col = getattr(cr, "referenced_column", None)
                    if src_col and tgt_col and target_tbl_name:
                        fks_dict[src_col] = f"{target_tbl_name}.{tgt_col}"
                        relationships.append(
                            Relationship(
                                source_table=tbl_id,
                                source_column=src_col,
                                target_table=target_tbl_name,
                                target_column=tgt_col,
                                relationship_type="many_to_one",
                                confidence=1.0,
                                source_type="TABLE_CONSTRAINTS",
                            )
                        )
                if not col_refs:
                    referencing = getattr(fk, "referencing_columns", []) or []
                    ref_cols = getattr(fk, "referenced_columns", []) or []
                    if referencing and ref_tbl and ref_cols and target_tbl_name:
                        fks_dict[referencing[0]] = f"{target_tbl_name}.{ref_cols[0]}"
                        relationships.append(
                            Relationship(
                                source_table=tbl_id,
                                source_column=referencing[0],
                                target_table=target_tbl_name,
                                target_column=ref_cols[0],
                                relationship_type="many_to_one",
                                confidence=1.0,
                                source_type="TABLE_CONSTRAINTS",
                            )
                        )

        # Primary Key fallback heuristic
        if not pk_cols:
            candidates = ["id", f"{tbl_id}_id", f"{tbl_id.rstrip('s')}_id"]
            schema_col_names = [f.name for f in getattr(tbl, "schema", [])]
            for c in candidates:
                if c in schema_col_names:
                    pk_cols = [c]
                    break

        # Columns
        columns: dict[str, ColumnMeta] = {}
        for field in getattr(tbl, "schema", []):
            raw_type = (getattr(field, "field_type", None) or "STRING").upper()
            norm_type = TYPE_NORMALIZATION.get(raw_type, raw_type)
            is_pk = field.name in pk_cols
            is_fk = field.name in fks_dict
            columns[field.name] = ColumnMeta(
                name=field.name,
                data_type=norm_type,
                mode=getattr(field, "mode", "NULLABLE"),
                description=getattr(field, "description", None),
                is_primary_key=is_pk,
                is_foreign_key=is_fk,
                foreign_key_target=fks_dict.get(field.name),
            )

        # Role
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
        role = "fact" if is_fact else "dimension"

        tables_meta[tbl_id] = TableMeta(
            name=tbl_id,
            role=role,
            num_rows=num_rows,
            description=description,
            partition_field=partition_field,
            clustering_fields=clustering_fields,
            primary_key=pk_cols,
            foreign_keys=fks_dict,
            columns=columns,
        )

    # 3. Dataplex Entries & Aspects
    entries_found = 0
    aspects_found = 0
    for tbl_id, table_meta in tables_meta.items():
        try:
            entry = catalog_client.lookup_entry(dataset_id, tbl_id)
            if entry and "name" in entry:
                entries_found += 1
                entry_aspects = entry.get("aspects", {})
                if entry_aspects:
                    aspects_found += len(entry_aspects)
                    raw_aspects[tbl_id] = entry_aspects

                    for aspect_key, aspect_obj in entry_aspects.items():
                        data = aspect_obj.get("data", aspect_obj.get("fields", {}))
                        if not isinstance(data, dict):
                            continue

                        if "@" in aspect_key:
                            schema_path = aspect_key.split("@", 1)[1]
                            col_name = schema_path.replace("Schema.", "").replace("schema.", "")
                            if col_name in table_meta.columns:
                                col = table_meta.columns[col_name]
                                if data.get("business_label"):
                                    col.business_label = str(data["business_label"])
                                if data.get("business_description"):
                                    col.description = str(data["business_description"])
                                if data.get("synonyms") and isinstance(data["synonyms"], list):
                                    col.synonyms = [str(s) for s in data["synonyms"]]
                                if data.get("allowed_values") and isinstance(data["allowed_values"], list):
                                    col.allowed_values = [str(v) for v in data["allowed_values"]]
                                if data.get("format_pattern"):
                                    col.format_pattern = str(data["format_pattern"])
                                if data.get("governance_tags") and isinstance(data["governance_tags"], list):
                                    col.governance_tags = [str(g) for g in data["governance_tags"]]
                        else:
                            # Table-level curation aspect
                            if data.get("business_label"):
                                table_meta.business_label = str(data["business_label"])
                            if data.get("business_description"):
                                table_meta.description = str(data["business_description"])
                            if data.get("governance_tags") and isinstance(data["governance_tags"], list):
                                table_meta.governance_tags = [str(g) for g in data["governance_tags"]]
        except Exception as err:
            print_warning(f"Notice while reading Dataplex entry for {tbl_id}: {err}")

    sources["dataplex_entries"] = SourceStatus(
        source_name="dataplex_entries",
        available=entries_found > 0,
        details=f"Resolved {entries_found}/{len(tables_meta)} table entries and {aspects_found} aspects",
    )

    # 4. Dataplex EntryLinks & Glossaries
    glossary_cache: dict[str, dict[str, Any]] = {}
    definition_links_count = 0
    for tbl_id, table_meta in tables_meta.items():
        entry_name = (
            f"projects/{project_id}/locations/{location}/entryGroups/@bigquery/entries/"
            f"bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{tbl_id}"
        )
        try:
            links = catalog_client.lookup_entry_links(entry_name)
            for link in links:
                link_type = link.get("entryLinkType", "")
                refs = link.get("entryReferences", [])
                if (link_type.endswith("definition") or link_type == "definition") and len(refs) >= 2:
                    src_ref = refs[0]
                    tgt_ref = refs[1]
                    path = src_ref.get("path", "")
                    col_name = path.replace("Schema.", "").replace("schema.", "")
                    target_entry = tgt_ref.get("name") or tgt_ref.get("entry", "")
                    if "/glossaries/" in target_entry and "/terms/" in target_entry:
                        after_g = target_entry.split("/glossaries/")[1]
                        parts = after_g.split("/terms/")
                        glossary_id = parts[0]
                        term_id = parts[1].split("/")[0]
                        cache_key = f"{glossary_id}/{term_id}"

                        if cache_key not in glossary_cache:
                            glossary_cache[cache_key] = catalog_client.get_glossary_term(glossary_id, term_id)
                        term_data = glossary_cache[cache_key]

                        if col_name in table_meta.columns:
                            col = table_meta.columns[col_name]
                            term_label = term_data.get("displayName") or term_id
                            col.linked_glossary_term = term_label
                            if not col.description and term_data.get("description"):
                                col.description = str(term_data["description"])
                            definition_links_count += 1
                elif link_type.endswith("schema-join") or link_type == "schema-join":
                    # Extract joins directly from schema-join links
                    for asp in link.get("aspects", {}).values():
                        if not isinstance(asp, dict):
                            continue
                        joins_list = asp.get("data", {}).get("joins", [])
                        for j in joins_list:
                            src = j.get("source", {})
                            tgt = j.get("target", {})
                            src_tbl = src.get("name", "").split(".")[-1]
                            tgt_tbl = tgt.get("name", "").split(".")[-1]
                            src_fields = src.get("fields", [])
                            tgt_fields = tgt.get("fields", [])
                            if src_fields and tgt_fields and src_tbl in tables_meta:
                                s_col, t_col = src_fields[0], tgt_fields[0]
                                if (
                                    tgt_tbl in tables_meta
                                    and s_col in tables_meta[src_tbl].primary_key
                                    and t_col not in tables_meta[tgt_tbl].primary_key
                                ):
                                    src_tbl, tgt_tbl = tgt_tbl, src_tbl
                                    s_col, t_col = t_col, s_col
                                tables_meta[src_tbl].foreign_keys[s_col] = f"{tgt_tbl}.{t_col}"
                                if s_col in tables_meta[src_tbl].columns:
                                    tables_meta[src_tbl].columns[s_col].is_foreign_key = True
                                    tables_meta[src_tbl].columns[s_col].foreign_key_target = f"{tgt_tbl}.{t_col}"
                                rel_exists = any(
                                    r.source_table == src_tbl and r.source_column == s_col and r.target_table == tgt_tbl
                                    for r in relationships
                                )
                                if not rel_exists:
                                    relationships.append(
                                        Relationship(
                                            source_table=src_tbl,
                                            source_column=s_col,
                                            target_table=tgt_tbl,
                                            target_column=t_col,
                                            relationship_type="many_to_one",
                                            confidence=1.0,
                                            source_type=j.get("inferenceSource", "TABLE_CONSTRAINTS"),
                                        )
                                    )
        except Exception as err:
            print_warning(f"Notice while reading entry links for {tbl_id}: {err}")

    sources["dataplex_links"] = SourceStatus(
        source_name="dataplex_links",
        available=definition_links_count > 0,
        details=f"Linked {definition_links_count} column definition links across {len(glossary_cache)} glossary terms",
    )

    # 5. Dataplex lookupContext Joins
    try:
        import json

        resources = [
            f"projects/{project_id}/locations/{location}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{tbl_id}"
            for tbl_id in tables_meta.keys()
        ]
        context_data = catalog_client.lookup_context(resources=resources)
        context_raw = context_data.get("context", "")
        if isinstance(context_raw, str) and context_raw:
            try:
                parsed_ctx = json.loads(context_raw)
                joins_dict = parsed_ctx.get("joins", {})
            except Exception:
                joins_dict = context_data.get("joins", {})
        else:
            joins_dict = context_data.get("joins", {})

        joins_found = 0
        for _pair, join_list in joins_dict.items():
            if not isinstance(join_list, list):
                continue
            for j in join_list:
                src_tbl = j.get("source", "").split("/tables/")[-1]
                tgt_tbl = j.get("target", "").split("/tables/")[-1]
                join_keys = j.get("joinKeys", [])
                for jk in join_keys:
                    src_col = jk.get("sourceField")
                    tgt_col = jk.get("targetField")
                    if src_col and tgt_col and src_tbl in tables_meta:
                        s_tbl, t_tbl, s_col, t_col = src_tbl, tgt_tbl, src_col, tgt_col
                        if (
                            t_tbl in tables_meta
                            and s_col in tables_meta[s_tbl].primary_key
                            and t_col not in tables_meta[t_tbl].primary_key
                        ):
                            s_tbl, t_tbl = t_tbl, s_tbl
                            s_col, t_col = t_col, s_col
                        tables_meta[s_tbl].foreign_keys[s_col] = f"{t_tbl}.{t_col}"
                        if s_col in tables_meta[s_tbl].columns:
                            tables_meta[s_tbl].columns[s_col].is_foreign_key = True
                            tables_meta[s_tbl].columns[s_col].foreign_key_target = f"{t_tbl}.{t_col}"

                        already_rel = any(
                            r.source_table == s_tbl and r.source_column == s_col and r.target_table == t_tbl
                            for r in relationships
                        )
                        if not already_rel:
                            relationships.append(
                                Relationship(
                                    source_table=s_tbl,
                                    source_column=s_col,
                                    target_table=t_tbl,
                                    target_column=t_col,
                                    relationship_type="many_to_one",
                                    confidence=1.0,
                                    source_type="LOOKUP_CONTEXT",
                                )
                            )
                        joins_found += 1

        sources["lookup_context"] = SourceStatus(
            source_name="lookup_context",
            available=joins_found > 0,
            details=f"Resolved {joins_found} joins via Dataplex lookupContext",
        )
    except Exception as err:
        sources["lookup_context"] = SourceStatus(
            source_name="lookup_context",
            available=False,
            details=str(err),
        )

    snapshot = CatalogSnapshot(
        dataset_id=dataset_id,
        project_id=project_id,
        location=location,
        sources=sources,
        tables=tables_meta,
        relationships=relationships,
        raw_aspects=raw_aspects,
    )
    snapshot.compute_coverage()
    return snapshot


def snapshot_to_table_specs(
    snapshot: CatalogSnapshot,
    profile: str | None = None,
) -> list[LookMLTableSpec]:
    """Convert a CatalogSnapshot into LookMLTableSpecs for LookML generation according to a mapping profile.

    Profiles:
      - 'rich': Strict adherence to Dataplex labels, descriptions, and formats.
      - 'hybrid': Uses Dataplex curation where available, falling back to LookML heuristic formatting for unannotated fields.
      - 'minimal': Uses native BigQuery constraints, types, and schema descriptions, ignoring Dataplex aspect metadata.
    """
    selected_profile = (
        profile or (snapshot.coverage.recommended_profile if snapshot.coverage else None) or "hybrid"
    ).lower()
    if selected_profile in ("auto", "default"):
        selected_profile = "hybrid"

    specs: list[LookMLTableSpec] = []
    for tbl in snapshot.tables.values():
        schema_fields = {col.name: col.data_type for col in tbl.columns.values()}

        if selected_profile == "minimal":
            # Minimal profile: native BigQuery only. Suppress Dataplex annotations.
            column_descriptions = {
                col.name: str(col.description)
                for col in tbl.columns.values()
                if col.description and not col.linked_glossary_term
            }
            column_labels = {}
            column_formats = {}
            column_synonyms = {}
            column_allowed_values = {}
            business_label = None
            description = tbl.description
        else:
            # Rich & Hybrid profiles: Dataplex curation where available
            column_descriptions = {col.name: str(col.description) for col in tbl.columns.values() if col.description}
            column_labels = {
                col.name: str(col.business_label or col.linked_glossary_term)
                for col in tbl.columns.values()
                if (col.business_label or col.linked_glossary_term)
            }
            column_formats = {col.name: str(col.format_pattern) for col in tbl.columns.values() if col.format_pattern}
            column_synonyms = {col.name: col.synonyms for col in tbl.columns.values() if col.synonyms}
            column_allowed_values = {col.name: col.allowed_values for col in tbl.columns.values() if col.allowed_values}
            business_label = tbl.business_label
            description = tbl.description

        specs.append(
            LookMLTableSpec(
                table_name=tbl.name,
                table_type=tbl.role,
                schema_fields=schema_fields,
                primary_key=tbl.primary_key[0] if tbl.primary_key else None,
                primary_keys=tbl.primary_key,
                foreign_keys=dict(tbl.foreign_keys),
                description=description,
                business_label=business_label,
                column_descriptions=column_descriptions,
                column_labels=column_labels,
                column_formats=column_formats,
                column_synonyms=column_synonyms,
                column_allowed_values=column_allowed_values,
                partition_field=tbl.partition_field,
                clustering_fields=tbl.clustering_fields,
            )
        )
    return specs
