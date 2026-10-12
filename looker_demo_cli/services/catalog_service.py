"""Catalog service for orchestrating Knowledge Catalog (Dataplex) and BigQuery metadata.

Builds structured, serializable CatalogSnapshots by fusing:
1. BigQuery table schema, types, partition/cluster keys, and native PK/FK constraints.
2. Dataplex Universal Catalog entry-level and column-level curation aspects.
3. Dataplex Business Glossary term definitions linked via EntryLinks.
4. Dataplex lookupContext multi-table joins.
5. Statistical coverage metrics and LookMLTableSpec mapping.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from looker_demo_cli.catalog.models import (
    CatalogSeedPlan,
    CatalogSnapshot,
    ColumnMeta,
    ColumnSeedSpec,
    GlossaryTermSeed,
    Relationship,
    SourceStatus,
    TableMeta,
    TableSeedSpec,
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


_ACRONYMS = {
    "id": "ID",
    "usd": "(USD)",
    "fps": "(FPS)",
    "ms": "(ms)",
    "sla": "SLA",
    "kpi": "KPI",
    "api": "API",
    "url": "URL",
    "os": "OS",
    "ip": "IP",
    "cpu": "CPU",
    "gpu": "GPU",
    "ram": "RAM",
    "ttl": "TTL",
    "dau": "DAU",
    "mau": "MAU",
    "ltv": "LTV",
    "arpu": "ARPU",
    "arppu": "ARPPU",
    "iap": "IAP",
    "pii": "PII",
    "d1": "Day-1",
    "d7": "Day-7",
    "d14": "Day-14",
    "d30": "Day-30",
}


def _humanize_identifier(raw_name: str, strip_prefix: bool = False) -> str:
    """Convert a snake_case table or column identifier into a polished business label."""
    cleaned = raw_name
    if strip_prefix:
        for pfx in ("dim_", "fct_", "fact_", "stg_", "mart_"):
            if cleaned.startswith(pfx):
                cleaned = cleaned[len(pfx) :]
                break
    tokens = [t for t in cleaned.split("_") if t]
    out = [_ACRONYMS.get(t.lower(), t.capitalize()) for t in tokens]
    return " ".join(out) if out else raw_name


def _infer_format_pattern(col_name: str, norm_type: str) -> str | None:
    """Infer a LookML/Dataplex format_pattern from column name and BigQuery data type."""
    lower = col_name.lower()
    if norm_type in ("FLOAT64", "NUMERIC", "BIGNUMERIC", "INT64"):
        if any(
            k in lower for k in ("_usd", "amount", "price", "cost", "revenue", "spend", "discount", "fee", "mrr", "arr")
        ):
            return "usd"
        if any(k in lower for k in ("_rate", "_pct", "_ratio", "percentage", "share")):
            return "percent_1"
        if norm_type == "INT64" and not lower.endswith(("_id", "_time", "_timestamp", "_at")):
            return "decimal_0"
        if norm_type in ("FLOAT64", "NUMERIC", "BIGNUMERIC"):
            return "decimal_1"
    return None


def _should_auto_create_glossary_term(
    col_name: str,
    norm_type: str,
    is_pk: bool,
    is_fk: bool,
    allowed_values: list[str],
    format_pattern: str | None,
) -> bool:
    """Determine whether a column represents a domain concept or KPI worthy of a Business Glossary Term."""
    lower = col_name.lower()
    if is_pk or is_fk or lower.endswith("_id") or lower == "id":
        return False
    if norm_type in ("DATE", "TIMESTAMP", "DATETIME") or lower.endswith(("_date", "_time", "_timestamp", "_at")):
        return False
    if norm_type == "STRING" and allowed_values:
        return True
    if norm_type == "STRING" and any(
        k in lower for k in ("status", "segment", "tier", "mode", "type", "category", "channel", "platform", "stage")
    ):
        return True
    if norm_type == "BOOL" and any(k in lower for k in ("retained", "churn", "fraud", "active", "verified", "sla")):
        return True
    if format_pattern in ("usd", "percent_1") or any(
        k in lower for k in ("duration", "latency", "ping", "fps", "score", "revenue", "spend", "margin", "playtime")
    ):
        return True
    return False


def _load_curation_overrides(
    dataset_id: str,
    curation_file: Path | None = None,
    schema_file: Path | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Load optional declarative curation JSON or DomainBlueprint JSON from disk."""
    import json

    work_dir = cwd or Path.cwd()
    candidates: list[Path] = []
    if curation_file:
        candidates.append(curation_file)
    if schema_file:
        candidates.append(schema_file)
    candidates.extend(
        [
            work_dir / f".demo-catalog-seed-{dataset_id}.json",
            work_dir / ".demo-catalog-seed.json",
            work_dir / "artifacts" / "generated_data" / "schema.json",
            work_dir / "schema.json",
        ]
    )

    raw_data: dict[str, Any] | None = None
    for path in candidates:
        if path and path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw_data = loaded
                    break
            except Exception as exc:
                print_warning(f"Could not parse curation file `{path}`: {exc}")

    if not raw_data:
        return {}

    # Normalize DomainBlueprint (`entities` list) into standard curation dict (`glossary`, `tables`)
    if "entities" in raw_data and isinstance(raw_data["entities"], list) and "tables" not in raw_data:
        tables_out: dict[str, Any] = {}
        for ent in raw_data["entities"]:
            if not isinstance(ent, dict) or not ent.get("table_name"):
                continue
            t_name = str(ent["table_name"])
            pk = [str(ent["primary_key"])] if ent.get("primary_key") else []
            fks = dict(ent.get("foreign_keys") or {})
            cols_out: dict[str, Any] = {}
            for fld in ent.get("fields", []):
                if not isinstance(fld, dict) or not fld.get("name"):
                    continue
                f_name = str(fld["name"])
                if fld.get("is_primary_key") and f_name not in pk:
                    pk.append(f_name)
                if fld.get("is_foreign_key") and fld.get("foreign_reference") and f_name not in fks:
                    fks[f_name] = str(fld["foreign_reference"])
                sample_vals = [str(v) for v in (fld.get("sample_values") or []) if v is not None]
                cols_out[f_name] = {
                    "business_label": fld.get("business_label"),
                    "description": fld.get("description"),
                    "synonyms": fld.get("synonyms") or [],
                    "allowed_values": fld.get("allowed_values") or (sample_vals if len(sample_vals) <= 15 else []),
                    "format_pattern": fld.get("format_pattern"),
                    "governance_tags": fld.get("governance_tags") or [],
                    "term_id": fld.get("term_id"),
                    "link_id": fld.get("link_id"),
                }
            tables_out[t_name] = {
                "role": ent.get("table_type", "dimension"),
                "business_label": ent.get("business_label"),
                "description": ent.get("description"),
                "governance_tags": ent.get("governance_tags") or [],
                "primary_key": pk,
                "foreign_keys": fks,
                "columns": cols_out,
            }
        return {
            "glossary": raw_data.get("glossary")
            or {
                "display_name": f"{_humanize_identifier(raw_data.get('domain_name', dataset_id))} Glossary",
                "description": raw_data.get("description") or f"Enterprise business glossary for {dataset_id}.",
                "terms": {},
            },
            "tables": tables_out,
        }

    return raw_data


def build_catalog_seed_plan(
    bq_client: BigQueryPort,
    dataset_id: str,
    location: str = "us",
    aspect_type_name: str = "semantic-curation",
    glossary_name: str | None = None,
    curation_file: Path | None = None,
    schema_file: Path | None = None,
    cwd: Path | None = None,
) -> CatalogSeedPlan:
    """Build a deterministic, dataset-agnostic CatalogSeedPlan by merging optional curation files with live BigQuery metadata."""
    project_id = bq_client.project_id
    loc = location.lower()
    resolved_glossary_id = glossary_name or f"{dataset_id.replace('_', '-')}-glossary"
    dataset_prefix = "".join(part[0] for part in dataset_id.split("_") if part)[:4] or "ds"

    overrides = _load_curation_overrides(
        dataset_id=dataset_id,
        curation_file=curation_file,
        schema_file=schema_file,
        cwd=cwd,
    )
    glossary_override = overrides.get("glossary", {}) if isinstance(overrides.get("glossary"), dict) else {}
    tables_override = overrides.get("tables", {}) if isinstance(overrides.get("tables"), dict) else {}

    glossary_display = glossary_override.get("display_name") or f"{_humanize_identifier(dataset_id)} Business Glossary"
    glossary_desc = (
        glossary_override.get("description")
        or f"Enterprise business glossary and curated KPI definitions for {dataset_id}."
    )

    terms_map: dict[str, GlossaryTermSeed] = {}
    raw_terms = glossary_override.get("terms", {})
    if isinstance(raw_terms, dict):
        for t_id, t_meta in raw_terms.items():
            if isinstance(t_meta, dict):
                terms_map[t_id] = GlossaryTermSeed(
                    term_id=t_id,
                    display_name=str(t_meta.get("display_name") or _humanize_identifier(t_id.replace("-", "_"))),
                    description=str(t_meta.get("description") or f"Business definition for {t_id}."),
                    category_id=t_meta.get("category_id"),
                )

    table_names = bq_client.list_tables(dataset_id)
    raw_tables: dict[str, Any] = {}
    dim_pk_lookup: dict[str, str] = {}

    # Pass 1: Read BigQuery table schemas & detect dimension primary keys
    for tbl_id in table_names:
        try:
            tbl = bq_client.get_table_metadata(dataset_id, tbl_id)
        except Exception:
            continue
        raw_tables[tbl_id] = tbl
        t_over = tables_override.get(tbl_id, {}) if isinstance(tables_override.get(tbl_id), dict) else {}

        pk_cols: list[str] = list(t_over.get("primary_key") or [])
        if not pk_cols:
            constraints = getattr(tbl, "table_constraints", None)
            if constraints:
                pk_info = getattr(constraints, "primary_key", None)
                if pk_info and getattr(pk_info, "columns", None):
                    pk_cols = list(pk_info.columns)
        if not pk_cols:
            schema_col_names = [f.name for f in getattr(tbl, "schema", [])]
            singular = tbl_id.replace("dim_", "").replace("fct_", "").rstrip("s")
            for cand in ("id", f"{tbl_id}_id", f"{singular}_id"):
                if cand in schema_col_names:
                    pk_cols = [cand]
                    break
            if not pk_cols and schema_col_names and schema_col_names[0].endswith("_id"):
                pk_cols = [schema_col_names[0]]

        is_dim = tbl_id.startswith("dim_") or (not tbl_id.startswith("fct_") and t_over.get("role") != "fact")
        if is_dim and len(pk_cols) == 1:
            dim_pk_lookup[pk_cols[0]] = tbl_id

    # Pass 2: Build TableSeedSpec and ColumnSeedSpec for every table
    tables_plan: dict[str, TableSeedSpec] = {}
    skip_val_suffixes = ("uuid", "hash", "token", "payload", "raw", "description", "content", "email", "name")

    for tbl_id in table_names:
        tbl = raw_tables.get(tbl_id)
        t_over = tables_override.get(tbl_id, {}) if isinstance(tables_override.get(tbl_id), dict) else {}
        cols_over = t_over.get("columns", {}) if isinstance(t_over.get("columns"), dict) else {}

        is_fact = (
            t_over.get("role") == "fact"
            or tbl_id.startswith("fct_")
            or any(
                k in tbl_id
                for k in ("fact", "order", "event", "transaction", "session", "purchase", "retention", "log")
            )
        )
        role = "fact" if is_fact else "dimension"

        # Primary keys
        pk_cols = list(t_over.get("primary_key") or [])
        if not pk_cols and tbl is not None:
            constraints = getattr(tbl, "table_constraints", None)
            if constraints:
                pk_info = getattr(constraints, "primary_key", None)
                if pk_info and getattr(pk_info, "columns", None):
                    pk_cols = list(pk_info.columns)
            if not pk_cols:
                schema_col_names = [f.name for f in getattr(tbl, "schema", [])]
                singular = tbl_id.replace("dim_", "").replace("fct_", "").rstrip("s")
                for cand in ("id", f"{tbl_id}_id", f"{singular}_id"):
                    if cand in schema_col_names:
                        pk_cols = [cand]
                        break
                if not pk_cols and schema_col_names and schema_col_names[0].endswith("_id"):
                    pk_cols = [schema_col_names[0]]

        # Foreign keys (normalized to `col -> target_table.target_col`)
        fks_dict: dict[str, str] = {}
        raw_fks = t_over.get("foreign_keys") or {}
        if isinstance(raw_fks, dict):
            for fk_col, ref in raw_fks.items():
                if isinstance(ref, list | tuple) and len(ref) == 2:
                    fks_dict[fk_col] = f"{ref[0]}.{ref[1]}"
                elif isinstance(ref, str) and "." in ref:
                    fks_dict[fk_col] = ref

        if tbl is not None:
            constraints = getattr(tbl, "table_constraints", None)
            if constraints:
                for fk in getattr(constraints, "foreign_keys", []) or []:
                    ref_tbl = getattr(fk, "referenced_table", None)
                    target_tbl_name = getattr(ref_tbl, "table_id", str(ref_tbl)) if ref_tbl else ""
                    for cr in getattr(fk, "column_references", []) or []:
                        src_col = getattr(cr, "referencing_column", None)
                        tgt_col = getattr(cr, "referenced_column", None)
                        if src_col and tgt_col and target_tbl_name:
                            fks_dict[src_col] = f"{target_tbl_name}.{tgt_col}"

            # Infer foreign keys from matching dimension primary keys
            for f in getattr(tbl, "schema", []):
                if f.name not in pk_cols and f.name not in fks_dict and f.name in dim_pk_lookup:
                    target_dim = dim_pk_lookup[f.name]
                    if target_dim != tbl_id:
                        fks_dict[f.name] = f"{target_dim}.{f.name}"

        # Sample rows for low-cardinality categorical allowed_values inference
        sample_rows: list[dict[str, Any]] = []
        if hasattr(bq_client, "preview_rows"):
            try:
                sample_rows = bq_client.preview_rows(dataset_id, tbl_id, limit=25) or []
            except Exception:
                sample_rows = []

        partition_field: str | None = None
        clustering_fields: list[str] = []
        if tbl is not None:
            time_part = getattr(tbl, "time_partitioning", None)
            if time_part and getattr(time_part, "field", None):
                partition_field = time_part.field
            clustering_fields = list(getattr(tbl, "clustering_fields", []) or [])

        tbl_label = str(t_over.get("business_label") or _humanize_identifier(tbl_id, strip_prefix=True))
        tbl_desc = str(
            t_over.get("description")
            or (getattr(tbl, "description", None) if tbl is not None else None)
            or f"Curated {tbl_label} {role} table in `{dataset_id}`."
        )
        tbl_tags = list(t_over.get("governance_tags") or [role, dataset_id])

        col_specs: dict[str, ColumnSeedSpec] = {}
        schema_fields = list(getattr(tbl, "schema", [])) if tbl is not None else []

        for f in schema_fields:
            col_name = f.name
            raw_type = (getattr(f, "field_type", None) or "STRING").upper()
            norm_type = TYPE_NORMALIZATION.get(raw_type, raw_type)
            c_over = cols_over.get(col_name, {}) if isinstance(cols_over.get(col_name), dict) else {}

            is_pk = col_name in pk_cols
            is_fk = col_name in fks_dict
            c_label = str(c_over.get("business_label") or _humanize_identifier(col_name))

            # Allowed values
            allowed_vals = [str(v) for v in (c_over.get("allowed_values") or []) if v is not None]
            if (
                not allowed_vals
                and norm_type == "STRING"
                and not is_pk
                and not is_fk
                and not col_name.endswith("_id")
                and not any(sfx in col_name.lower() for sfx in skip_val_suffixes)
                and sample_rows
            ):
                seen_vals: list[str] = []
                for row in sample_rows:
                    val = row.get(col_name)
                    if val is not None and str(val).strip() and str(val).strip() not in seen_vals:
                        seen_vals.append(str(val).strip())
                if 1 <= len(seen_vals) <= 12:
                    allowed_vals = seen_vals

            fmt_pattern = c_over.get("format_pattern") or _infer_format_pattern(col_name, norm_type)

            # Column description
            if c_over.get("description"):
                c_desc = str(c_over["description"])
            elif getattr(f, "description", None):
                c_desc = str(f.description)
            elif is_pk:
                c_desc = f"Unique primary key identifier for `{tbl_id}` ({c_label})."
            elif is_fk:
                c_desc = f"Foreign key linking `{tbl_id}.{col_name}` to `{fks_dict[col_name]}`."
            elif allowed_vals:
                c_desc = f"{c_label} classification ({', '.join(allowed_vals[:6])})."
            else:
                c_desc = f"Curated {c_label} ({norm_type}) attribute in `{tbl_id}`."

            # Governance tags
            c_tags: list[str] = list(c_over.get("governance_tags") or [])
            if not c_tags:
                if is_pk:
                    c_tags.append("primary_key")
                if is_fk:
                    c_tags.append("foreign_key")
                if col_name == partition_field:
                    c_tags.append("partition_key")
                if col_name in clustering_fields:
                    c_tags.append("cluster_key")
                if fmt_pattern == "usd":
                    c_tags.extend(["financial", "kpi"])
                elif norm_type in ("DATE", "TIMESTAMP", "DATETIME") or col_name.endswith(("_date", "_time")):
                    c_tags.append("temporal")
                elif allowed_vals:
                    c_tags.append("filter_dimension")
                else:
                    c_tags.append(role)

            # Glossary term assignment (explicit override OR auto-inferred domain dimension/KPI when no explicit terms exist)
            term_id = c_over.get("term_id")
            link_id = c_over.get("link_id")
            if (
                not term_id
                and not bool(raw_terms)
                and _should_auto_create_glossary_term(
                    col_name=col_name,
                    norm_type=norm_type,
                    is_pk=is_pk,
                    is_fk=is_fk,
                    allowed_values=allowed_vals,
                    format_pattern=fmt_pattern,
                )
            ):
                term_id = col_name.lower().replace("_", "-")

            if term_id:
                if not link_id:
                    link_id = f"{dataset_prefix}-link-{tbl_id[:6].replace('_', '-')}-{term_id}"[:63].rstrip("-")
                if term_id not in terms_map:
                    terms_map[term_id] = GlossaryTermSeed(
                        term_id=term_id,
                        display_name=c_label,
                        description=c_desc,
                    )

            col_specs[col_name] = ColumnSeedSpec(
                name=col_name,
                business_label=c_label,
                description=c_desc,
                synonyms=[str(s) for s in (c_over.get("synonyms") or [])],
                allowed_values=allowed_vals,
                format_pattern=fmt_pattern,
                governance_tags=c_tags,
                term_id=term_id,
                link_id=link_id,
            )

        tables_plan[tbl_id] = TableSeedSpec(
            name=tbl_id,
            role=role,
            business_label=tbl_label,
            description=tbl_desc,
            governance_tags=tbl_tags,
            primary_key=pk_cols,
            foreign_keys=fks_dict,
            columns=col_specs,
        )

    return CatalogSeedPlan(
        project_id=project_id,
        dataset_id=dataset_id,
        location=loc,
        aspect_type_id=aspect_type_name,
        glossary_id=resolved_glossary_id,
        glossary_display_name=glossary_display,
        glossary_description=glossary_desc,
        terms=terms_map,
        tables=tables_plan,
    )


def execute_catalog_seed_plan(
    bq_client: BigQueryPort,
    catalog_client: Any,
    plan: CatalogSeedPlan,
) -> dict[str, Any]:
    """Execute a CatalogSeedPlan against Dataplex Universal Catalog and BigQuery."""
    project_id = plan.project_id
    dataset_id = plan.dataset_id
    loc = plan.location.lower()
    aspect_type_name = plan.aspect_type_id
    glossary_name = plan.glossary_id

    stats: dict[str, Any] = {
        "glossary_created": False,
        "terms_seeded": 0,
        "tables_updated": 0,
        "aspects_attached": 0,
        "entry_links_created": 0,
    }

    if not hasattr(catalog_client, "ensure_glossary"):
        return stats

    # 1. Ensure Aspect Type & resolve numeric project number
    aspect_type_obj = catalog_client.ensure_aspect_type(aspect_type_name)
    project_number = (
        catalog_client.get_project_number() if hasattr(catalog_client, "get_project_number") else project_id
    )
    if not project_number.isdigit() and isinstance(aspect_type_obj, dict) and "name" in aspect_type_obj:
        parts = str(aspect_type_obj["name"]).split("/")
        if len(parts) >= 2 and parts[0] == "projects" and parts[1].isdigit():
            project_number = parts[1]

    # 2. Ensure Business Glossary & Terms
    glossary_obj = catalog_client.ensure_glossary(
        glossary_id=glossary_name,
        display_name=plan.glossary_display_name,
        description=plan.glossary_description,
    )
    if glossary_obj:
        stats["glossary_created"] = True

    for term_id, term_seed in plan.terms.items():
        term_res = catalog_client.ensure_glossary_term(
            glossary_id=glossary_name,
            term_id=term_id,
            display_name=term_seed.display_name,
            description=term_seed.description,
        )
        if term_res:
            stats["terms_seeded"] += 1

    aspect_key_base = f"{project_number}.{loc}.{aspect_type_name}"
    aspect_type_res = f"projects/{project_number}/locations/{loc}/aspectTypes/{aspect_type_name}"

    # 3. Sync BigQuery descriptions + PK/FK constraints (dimensions first so FK targets have PKs)
    sorted_tables = sorted(
        plan.tables.values(),
        key=lambda t: (1 if t.role == "fact" else 0, len(t.foreign_keys)),
    )

    for tbl_spec in sorted_tables:
        tbl_id = tbl_spec.name
        try:
            tbl = bq_client.get_table_metadata(dataset_id, tbl_id)
        except Exception:
            continue

        if not project_number.isdigit():
            entry_info = catalog_client.lookup_entry(dataset_id, tbl_id, view="ALL")
            if isinstance(entry_info, dict):
                parts = str(entry_info.get("name", "")).split("/")
                if len(parts) >= 2 and parts[0] == "projects" and parts[1].isdigit():
                    project_number = parts[1]
                else:
                    for a_key in entry_info.get("aspects") or {}:
                        a_prefix = str(a_key).split(".", 1)[0]
                        if a_prefix.isdigit() and a_prefix != "655216118709":
                            project_number = a_prefix
                            break
                aspect_key_base = f"{project_number}.{loc}.{aspect_type_name}"
                aspect_type_res = f"projects/{project_number}/locations/{loc}/aspectTypes/{aspect_type_name}"

        raw_bq = getattr(bq_client, "client", None)
        if raw_bq is not None and hasattr(raw_bq, "update_table"):
            try:
                from google.cloud import bigquery as gcp_bq

                new_schema = []
                for f in getattr(tbl, "schema", []):
                    c_spec = tbl_spec.columns.get(f.name)
                    c_desc = (
                        c_spec.description
                        if c_spec
                        else (getattr(f, "description", None) or f"Curated {f.name} field.")
                    )
                    new_schema.append(
                        gcp_bq.SchemaField(
                            name=f.name,
                            field_type=f.field_type,
                            mode=f.mode,
                            description=c_desc,
                            fields=f.fields,
                        )
                    )
                tbl.description = tbl_spec.description
                tbl.schema = new_schema
                raw_bq.update_table(tbl, ["description", "schema"])
            except Exception as exc:
                print_warning(f"Could not update BigQuery descriptions on {tbl_id}: {exc}")

            existing_constraints = getattr(tbl, "table_constraints", None)
            has_pk = bool(existing_constraints and getattr(existing_constraints, "primary_key", None))
            has_fks = bool(existing_constraints and getattr(existing_constraints, "foreign_keys", None))
            if tbl_spec.primary_key and not has_pk and hasattr(raw_bq, "query"):
                try:
                    pk_cols_sql = ", ".join(tbl_spec.primary_key)
                    raw_bq.query(
                        f"ALTER TABLE `{project_id}.{dataset_id}.{tbl_id}` ADD PRIMARY KEY ({pk_cols_sql}) NOT ENFORCED"
                    ).result()
                except Exception:
                    pass
            if tbl_spec.foreign_keys and not has_fks and hasattr(raw_bq, "query"):
                for fk_col, ref_target in tbl_spec.foreign_keys.items():
                    if "." not in ref_target:
                        continue
                    ref_tbl, ref_col = ref_target.split(".", 1)
                    try:
                        raw_bq.query(
                            f"ALTER TABLE `{project_id}.{dataset_id}.{tbl_id}` "
                            f"ADD CONSTRAINT fk_{tbl_id}_{fk_col} FOREIGN KEY ({fk_col}) "
                            f"REFERENCES `{project_id}.{dataset_id}.{ref_tbl}`({ref_col}) NOT ENFORCED"
                        ).result()
                    except Exception:
                        pass

        # 4. Patch Dataplex table + column semantic-curation aspects & create definition EntryLinks
        aspects_payload: dict[str, Any] = {
            aspect_key_base: {
                "aspectType": aspect_type_res,
                "data": {
                    "status": "CERTIFIED",
                    "business_label": tbl_spec.business_label,
                    "business_description": tbl_spec.description,
                    "governance_tags": tbl_spec.governance_tags,
                },
            }
        }

        source_entry_name = (
            f"projects/{project_number}/locations/{loc}/entryGroups/@bigquery/entries/"
            f"bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{tbl_id}"
        )

        for col_name, c_spec in tbl_spec.columns.items():
            col_data: dict[str, Any] = {
                "status": "CERTIFIED",
                "business_label": c_spec.business_label,
                "business_description": c_spec.description,
            }
            if c_spec.synonyms:
                col_data["synonyms"] = list(c_spec.synonyms)
            if c_spec.allowed_values:
                col_data["allowed_values"] = list(c_spec.allowed_values)
            if c_spec.format_pattern:
                col_data["format_pattern"] = str(c_spec.format_pattern)
            if c_spec.governance_tags:
                col_data["governance_tags"] = list(c_spec.governance_tags)

            aspects_payload[f"{aspect_key_base}@Schema.{col_name}"] = {
                "aspectType": aspect_type_res,
                "path": f"Schema.{col_name}",
                "data": col_data,
            }

            if c_spec.term_id:
                link_id = c_spec.link_id or f"link-{tbl_id[:6].replace('_', '-')}-{c_spec.term_id}"[:63].rstrip("-")
                target_term_entry = (
                    f"projects/{project_number}/locations/{loc}/entryGroups/@dataplex/entries/"
                    f"projects/{project_number}/locations/{loc}/glossaries/{glossary_name}/terms/{c_spec.term_id}"
                )
                link_res = catalog_client.create_entry_link(
                    entry_link_id=link_id,
                    source_entry=source_entry_name,
                    source_column=col_name,
                    target_term_entry=target_term_entry,
                )
                if link_res:
                    stats["entry_links_created"] += 1

        patched = catalog_client.patch_entry_aspects(dataset_id, tbl_id, aspects_payload)
        if patched:
            stats["tables_updated"] += 1
            stats["aspects_attached"] += len(aspects_payload)

    return stats
