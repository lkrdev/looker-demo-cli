---
name: knowledge-catalog-metadata
description: Inspects Google Cloud Dataplex Universal Catalog (Knowledge Catalog) via `demo-create catalog inspect` or `gcloud dataplex` CLI to extract business definitions, semantic descriptions, aspect types, column-level glossary terms, governance/sensitivity tags, data profiling statistics, data quality scan results, and key statistical insights. Enriches BigQuery schema context for LookML generation with graceful degradation.
---

# Knowledge Catalog & Data Quality Extraction Skill (`knowledge-catalog-metadata`)

## Role & Mission
You are the **Knowledge Catalog & Semantic Governance Specialist**. Your mission is to query Google Cloud Dataplex Universal Catalog and Dataplex DataScans—using the CLI-first `demo-create catalog` subcommands or native `gcloud dataplex` commands—to extract:
1. **Business Semantics**: Glossaries, descriptions, and user-friendly labels (including `semantic-curation` aspects).
2. **Governance & Sensitivity**: PII, security classifications, and access constraints.
3. **Data Profiling Metrics**: Null ratios, distinct cardinality ratios, top values, and numeric ranges.
4. **Data Quality Scans**: Overall quality scores, pass/fail status, and dimension evaluations (completeness, accuracy, freshness, uniqueness).
5. **Relationship Lookup Context**: Cross-table foreign key relationships and parent/child associations discovered by Dataplex.
6. **LookML Generation Mapping**: Feeding structured `CatalogSnapshot` metadata directly into `demo-create lookml model --catalog <snapshot> --profile <rich|hybrid|minimal>` to produce curated, production-ready LookML.

You enrich the technical schema produced by `bigquery-metadata` into a comprehensive **Data Dictionary & Semantic Context** within `SPEC.md`.

> [!IMPORTANT]
> **Cardinal Rules**:
> 1. **CLI-First Architecture**: Always prefer `demo-create catalog inspect` to automatically extract and format a complete `CatalogSnapshot`. Use native `gcloud dataplex` commands only for ad-hoc inspection, manual debugging, or when targeting custom aspects.
> 2. **Zero Hardcoded Python Scripts**: Never write ad-hoc Python scripts in the workspace to parse or query Dataplex APIs.
> 3. **Graceful Fallback**: If Dataplex is disabled, or if a table has no catalog entry, data profile, or data quality scan, **do not fail or block the workflow**. Gracefully record that profiling/catalog data is unavailable and retain technical BigQuery metadata.
> 4. **Non-Destructive & Read-Only**: Use read-only inspection commands (`inspect`, `lookup`, `describe`, `list`). Never trigger unwanted scan executions or mutate catalog configurations.

---

## 1. Environment & Target Resolution

Infer the target parameters from Gate 0 / Gate 1 or `SPEC.md`:

```bash
# 1. Active project and region
PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
LOCATION=$(gcloud config get-value compute/region 2>/dev/null || echo "us-central1")

# 2. Check SPEC.md if available
if [ -f "SPEC.md" ]; then
  PROJECT_ID=$(grep -E "gcp_project_id" SPEC.md | head -n 1 | awk -F': ' '{print $2}' | tr -d '"' | tr -d "'" | tr -d ' ' || echo "$PROJECT_ID")
  DATASET=$(grep -E "bigquery_dataset" SPEC.md | head -n 1 | awk -F': ' '{print $2}' | tr -d '"' | tr -d "'" | tr -d ' ' || echo "$DATASET")
fi
```

---

## 2. CLI-First Catalog Inspection (`demo-create catalog`)

The primary method for extracting Dataplex metadata is the unified CLI command `demo-create catalog inspect`.

### A. Introspecting Dataset Catalog Metadata
```bash
demo-create catalog inspect \
  --dataset "${DATASET}" \
  --gcp-project "${PROJECT_ID}" \
  --location "${LOCATION}" \
  --output-file ".demo-catalog-${DATASET}.json" \
  --json
```

This single command:
1. Discovers all BigQuery tables in the dataset.
2. Queries Dataplex Universal Catalog entries via resource URIs (`//bigquery.googleapis.com/...`).
3. Fetches attached aspect types—notably `google-cloud-dataplex/aspects/semantic-curation` containing curated table display names, column descriptions, and glossary term links.
4. Aggregates the latest Dataplex Data Profile and Data Quality scan results.
5. Captures entry lookup context (discovered join relationships and foreign keys).
6. Persists the output as a structured `CatalogSnapshot` JSON (`.demo-catalog-${DATASET}.json`) and sets `catalog_snapshot_path` in `.demo-state.json`.

### B. Catalog Mapping Profiles
Run `demo-create catalog profiles` to review available mapping profiles for downstream LookML generation:
```bash
demo-create catalog profiles --json
```

| Profile | Behavior & LookML Impact |
|---|---|
| **`rich`** | Curated Dataplex display labels and business descriptions on all views, dimensions, and measures. Generates derived filtered measures from glossary terms, and maps custom aspect types to LookML tags / dimension groups. |
| **`hybrid`** *(default)* | Uses curated descriptions and labels where present, falling back gracefully to schema-derived identifiers. |
| **`minimal`** | Pure BigQuery schema-derived fields with column descriptions preserved. |

### C. Downstream Consumption by `demo-create lookml model`
At Gate 5 (`gate_2a_lookml_model`), pass the snapshot to `demo-create lookml model` (or let the CLI auto-discover `.demo-catalog-${DATASET}.json`):
```bash
demo-create lookml model \
  --looker-project "${LOOKER_PROJECT}" \
  --dataset "${DATASET}" \
  --connection "${CONNECTION}" \
  --gcp-project "${PROJECT_ID}" \
  --catalog ".demo-catalog-${DATASET}.json" \
  --profile rich
```

### D. Mock/Testbed Seeding (`demo-create catalog seed`)
When working in sandboxes or CI environments without live Dataplex aspect types, seed mock catalog metadata from a `DomainBlueprint` schema file:
```bash
demo-create catalog seed \
  --dataset "${DATASET}" \
  --schema-file ./artifacts/generated_data/schema.json \
  --output-file ".demo-catalog-${DATASET}.json"
```

---

## 3. Low-Level Native `gcloud dataplex` Recipes (Fallback & Ad-Hoc Inspection)

When debugging specific aspects or querying Dataplex directly without the CLI wrapper, use the native recipes below:

### Recipe A: Table Entry & Aspect Lookup
Dataplex Universal Catalog links BigQuery tables via their standard Google Cloud resource URI. When data scans publish to catalog, profile and quality metrics appear directly in entry `aspects`:

```bash
RESOURCE_URI="//bigquery.googleapis.com/projects/${PROJECT_ID}/datasets/${DATASET}/tables/${TABLE}"

ENTRY_JSON=$(gcloud dataplex entries lookup \
  --resource="${RESOURCE_URI}" \
  --location="${LOCATION}" \
  --project="${PROJECT_ID}" \
  --format="json" 2>/dev/null || echo "{}")
```

*Extracting Semantic Glossaries & Descriptions*:
```bash
echo "$ENTRY_JSON" | jq '
  .aspects | to_entries[] | {
    aspect_type: .key,
    data: .value.data
  }'
```

---

### Recipe B: Data Profiling Metrics (From Entry Aspects or DataScans)

Dataplex publishes column-level statistical profiles into entry aspects or via dedicated DataScan jobs.

#### 1. Checking Entry Aspects for Profile Data:
```bash
echo "$ENTRY_JSON" | jq '
  .aspects | to_entries[] | select(.key | test("profile|data-profile"; "i")) | .value.data'
```

#### 2. Querying Dataplex DataScans Directly:
If profile data is not in catalog aspects, check if an explicit Data Profile scan exists for the table:

```bash
# Find scans targeting the table
SCAN_ID=$(gcloud dataplex datascans list \
  --location="${LOCATION}" \
  --project="${PROJECT_ID}" \
  --filter="data.resource://bigquery.googleapis.com/projects/${PROJECT_ID}/datasets/${DATASET}/tables/${TABLE} AND type=DATA_PROFILE" \
  --format="value(name)" 2>/dev/null | head -n 1)

if [ -n "$SCAN_ID" ]; then
  # Fetch full profile spec and latest execution results
  gcloud dataplex datascans describe "${SCAN_ID}" \
    --location="${LOCATION}" \
    --project="${PROJECT_ID}" \
    --view=full \
    --format="json" 2>/dev/null | jq '{
      state: .state,
      dataProfileResult: .dataProfileResult
    }'
fi
```

*Key Profiling Fields to Extract for LookML*:
- `rowCount`: Total table record volume.
- `profile.fields[].name`: Column name.
- `profile.fields[].profile.nullRatio`: Null ratio (0.0 to 1.0).
- `profile.fields[].profile.distinctRatio`: Uniqueness ratio (0.0 to 1.0).
- `profile.fields[].profile.topNValues`: Top frequent values (values + counts).
- `profile.fields[].profile.min` / `.max` / `.quartiles`: Numeric boundary ranges.

---

### Recipe C: Data Quality Scans & Dimension Health
Inspect Data Quality scans to identify table reliability, failed validation rules, and freshness:

```bash
# Find Data Quality scan for the table
DQ_SCAN_ID=$(gcloud dataplex datascans list \
  --location="${LOCATION}" \
  --project="${PROJECT_ID}" \
  --filter="data.resource://bigquery.googleapis.com/projects/${PROJECT_ID}/datasets/${DATASET}/tables/${TABLE} AND type=DATA_QUALITY" \
  --format="value(name)" 2>/dev/null | head -n 1)

if [ -n "$DQ_SCAN_ID" ]; then
  gcloud dataplex datascans describe "${DQ_SCAN_ID}" \
    --location="${LOCATION}" \
    --project="${PROJECT_ID}" \
    --view=full \
    --format="json" 2>/dev/null | jq '{
      passed: .dataQualityResult.passed,
      score: .dataQualityResult.score,
      dimensions: .dataQualityResult.dimensions,
      failed_rules: [.dataQualityResult.rules[]? | select(.passed == false) | {rule: .rule.dimension, column: .rule.column, details: .rule.description}]
    }'
fi
```

*LookML Modeling Impact*:
- **Failed Completeness Rules**: Add `sql: COALESCE(...)` or filter invalid records in LookML explores (`sql_always_where`).
- **Uniqueness Violations**: If a key fails uniqueness rules, flag that symmetric aggregates are required or that duplicate rows must be resolved before setting `primary_key: yes`.

---

### Recipe D: Governance, Sensitivity & PII Tags
Inspect entry aspects for privacy and security tags:

```bash
echo "$ENTRY_JSON" | jq '
  .aspects | to_entries[] | select(.key | test("security|governance|privacy|pii"; "i")) | {
    tag_aspect: .key,
    details: .value.data
  }'
```

---

## 4. Key Insights Generation Framework

Translate raw profiling and quality statistics into concrete LookML modeling decisions:

```mermaid
graph TD
    Profile[Data Profile & Quality Scan] --> DistinctRatio{Distinct Ratio}
    DistinctRatio -->|1.0 & 0% Nulls| PK[Validated Primary Key<br/>primary_key: yes]
    DistinctRatio -->|< 0.05| LowCard[Categorical Dimension<br/>suggestions: [...] or parameter]
    DistinctRatio -->|High & Continuous| Metric[Numeric Metric<br/>type: tier / measure type: average]

    Profile --> NullRatio{Null Ratio}
    NullRatio -->|> 0.0| Coalesce[Coalesce / Fallback<br/>sql: COALESCE(${TABLE}.col, 'Unknown')]
    NullRatio -->|0.0| SafeGrain[Mandatory Field / Clean Grain]

    Profile --> DQ{Data Quality Score}
    DQ -->|Pass >= 95%| Trusted[Certified Production Explore]
    DQ -->|Fail / Quality Warnings| Sanitize[Sanitization Filter<br/>sql_always_where: valid_flag = 1]
```

### Decision Matrix & `SPEC.md` Enrichment Protocol

Apply the **Dataplex Profile-to-LookML Decision Matrix** and update the `### Detailed Column Catalog & Dataplex Profiling Insights` table in `SPEC.md` using the canonical template in **[`spec-and-data-dictionary-template.md`](../resources/spec-and-data-dictionary-template.md)**.

---

## 5. Graceful Fallback Protocol
If Dataplex or DataScans are unavailable:
```bash
if [ -z "$ENTRY_JSON" ] || echo "$ENTRY_JSON" | grep -qiE "NOT_FOUND|PERMISSION_DENIED|disabled"; then
  echo "Dataplex catalog entry or datascans not found. Falling back to native BigQuery metadata."
  # Proceed using bq metadata without interrupting LookML modeling
fi
```
