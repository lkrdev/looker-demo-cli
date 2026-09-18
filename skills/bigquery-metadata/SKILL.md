---
name: bigquery-metadata
description: Inspects BigQuery datasets and tables via Google Cloud SDK `bq` CLI to extract schema, column descriptions, primary/foreign key constraints, and partition information without custom Python scripts. Synthesizes findings into a structured Data Dictionary & Relationship Context for LookML modeling skills.
---

# BigQuery Metadata Extraction Skill (`bigquery-metadata`)

## Role & Mission
You are the **BigQuery Schema & Metadata Extraction Specialist**. Your mission is to inspect BigQuery datasets, tables, and views using the native Google Cloud SDK `bq` CLI tool to extract technical schemas, column descriptions, primary/foreign key relationships, and table properties.

You produce a clean, deterministic **Data Dictionary & Relationship Graph** that is recorded in `SPEC.md` to feed directly into downstream LookML modeling skills (`lookml-view`, `lookml-explore`, `lookml-model`, and `lookml-snowflake-modeler`).

> [!IMPORTANT]
> **Cardinal Rules**:
> 1. **Zero Hardcoded Python Scripts**: Use only standard Google Cloud SDK CLI commands (`bq`, `gcloud`, `jq`). Do not create or run ad-hoc `.py` files in the workspace.
> 2. **Gate 0 / Gate 1 Alignment**: Infer the target `PROJECT_ID`, `DATASET`, and `LOCATION` from the active Gate 0 alignment or `SPEC.md`. If missing, verify against `gcloud config get-value project`.
> 3. **Non-Destructive & Read-Only**: Only execute read-only metadata inspections (`bq show`, `INFORMATION_SCHEMA` queries). Never execute DDL/DML changes.

---

## 1. Environment & Target Discovery

Before querying metadata, resolve and verify the target parameters:

```bash
# 1. Check active GCP project
PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
echo "Active Project: ${PROJECT_ID}"

# 2. If SPEC.md exists, inspect confirmed Gate 0 / Gate 1 targets
if [ -f "SPEC.md" ]; then
  grep -E "gcp_project_id|bigquery_dataset|region|dataset" SPEC.md || true
fi
```

If `PROJECT_ID` or `DATASET` is undefined, prompt the user or consult `demo-create status --json` or `SPEC.md`.

---

## 2. Metadata Extraction Recipes (CLI-Only)

### Recipe A: Table Inventory & Classification
Lists all tables, views, and materialized views in the dataset:

```bash
bq query --use_legacy_sql=false --format=prettyjson \
  "SELECT table_catalog AS project_id,
          table_schema AS dataset_id,
          table_name,
          table_type,
          creation_time,
          ddl
   FROM \`${PROJECT_ID}.${DATASET}.INFORMATION_SCHEMA.TABLES\`
   ORDER BY table_type, table_name;"
```

*Classification Key*:
- `BASE TABLE`: Physical fact or dimension table.
- `VIEW` / `MATERIALIZED VIEW`: Pre-aggregated or transformed layer.

---

### Recipe B: Column Schema, Types & Existing Descriptions
Retrieves full column-level metadata, including nested/repeated fields and descriptions:

```bash
# Option 1: Dataset-wide column field paths and descriptions
bq query --use_legacy_sql=false --format=prettyjson \
  "SELECT table_name,
          field_path AS column_name,
          data_type,
          description,
          collation_name,
          rounding_mode
   FROM \`${PROJECT_ID}.${DATASET}.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS\`
   ORDER BY table_name, field_path;"
```

```bash
# Option 2: Table-specific schema in native JSON
bq show --schema --format=prettyjson "${PROJECT_ID}:${DATASET}.${TABLE}"
```

---

### Recipe C: Primary Keys, Foreign Keys & Constraints
BigQuery supports unenforced informational constraints. Querying `TABLE_CONSTRAINTS` and `KEY_COLUMN_USAGE` reveals the declared entity relationships critical for LookML explore join cardinality:

```bash
bq query --use_legacy_sql=false --format=prettyjson \
  "SELECT
     tc.table_name,
     tc.constraint_name,
     tc.constraint_type,
     kcu.column_name,
     kcu.ordinal_position,
     ccu.table_name AS referenced_table_name,
     ccu.column_name AS referenced_column_name
   FROM \`${PROJECT_ID}.${DATASET}.INFORMATION_SCHEMA.TABLE_CONSTRAINTS\` tc
   JOIN \`${PROJECT_ID}.${DATASET}.INFORMATION_SCHEMA.KEY_COLUMN_USAGE\` kcu
     ON tc.constraint_name = kcu.constraint_name
    AND tc.table_schema = kcu.table_schema
   LEFT JOIN \`${PROJECT_ID}.${DATASET}.INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE\` ccu
     ON tc.constraint_name = ccu.constraint_name
    AND tc.table_schema = ccu.table_schema
   ORDER BY tc.table_name, tc.constraint_type, kcu.ordinal_position;"
```

*Inference Rule*:
- If BigQuery constraints are not declared, inspect column naming conventions:
  - Columns named `id`, `<table_singular>_id`, or `uuid` indicate candidate **Primary Keys**.
  - Columns matching `<foreign_table>_id` indicate candidate **Foreign Keys**.

---

### Recipe D: Partitioning, Clustering & Row Counts
Inspects physical layout for LookML query optimization (`sql_always_where` partition filters):

```bash
bq show --format=prettyjson "${PROJECT_ID}:${DATASET}.${TABLE}" | jq '{
  table: .tableReference.tableId,
  numRows: .numRows,
  numBytes: .numBytes,
  partitioning: (.timePartitioning // .rangePartitioning // "None"),
  clustering: (.clustering.fields // "None")
}'
```

---

## 3. Data Dictionary Synthesis Protocol

Once metadata is collected, synthesize findings into a structured markdown block and insert or update it in `SPEC.md` under `## Data Dictionary & Semantic Context` (and optionally in `docs/DATA_DICTIONARY.md` if requested):

```markdown
## Data Dictionary & Semantic Context

### Summary
- **GCP Project**: `<PROJECT_ID>`
- **Dataset**: `<DATASET>`
- **Total Tables**: `<COUNT>`

### Entity Relationship & Grain Summary
| Table Name | Grain / Primary Key | Table Type | Estimated Rows | Partition / Clustering |
| :--- | :--- | :--- | :--- | :--- |
| `fct_orders` | `order_id` (PK) | BASE TABLE | 1,250,000 | `order_date` (DAY) / `customer_id` |
| `dim_customers` | `customer_id` (PK) | BASE TABLE | 45,000 | None / `country_code` |

### Relationships & Joins
| Source Table | Foreign Key | Target Table | Primary Key | Join Relationship |
| :--- | :--- | :--- | :--- | :--- |
| `fct_orders` | `customer_id` | `dim_customers` | `customer_id` | `many_to_one` |

### Detailed Column Catalog
#### Table: `<TABLE_NAME>`
| Column | BigQuery Type | LookML Dimension Type | Nullable | Primary/Foreign Key | Description / Semantic Note |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `order_id` | `STRING` | `string` | NO | PK (`primary_key: yes`) | Unique order identifier |
| `customer_id`| `STRING` | `string` | NO | FK -> `dim_customers` | Customer reference |
| `total_amount`| `NUMERIC` | `number` | YES | None | Gross transaction amount |
| `created_at` | `TIMESTAMP`| `time` | NO | None | Order creation timestamp |
```

---

## 4. Downstream LookML Handoff Checklist

Before passing context to LookML generation skills:
1. Verify every table has an identified **Primary Key** (mark `primary_key: yes`).
2. Verify all **Foreign Keys** have explicit `many_to_one` or `one_to_one` relationships.
3. Highlight timestamp/date fields for LookML `dimension_group` generation (types: `time`, `date`).
4. Note partitioned date fields so LookML explores can suggest or enforce partition filters.
