---
name: lookml-filtered-measures
description: Mandatory SELECT DISTINCT grounding protocol and Looker filter expression standards for authoring accurate, non-null LookML filtered measures and derived ratios.
---

# LookML Filtered Measures & Grounding Protocol (`lookml-filtered-measures`)

Looker compiles `filters: [dimension: "value"]` inside a `measure` block into a SQL `CASE WHEN` expression:

```sql
COUNT(CASE WHEN table.dimension = 'value' THEN 1 ELSE NULL END)
```

If the string literal in `filters: [...]` does not match the exact casing, formatting, or naming of values stored in the underlying BigQuery table or Parquet file, the filtered measure evaluates to **`0`** (for `type: count`) or **`NULL`** (for `type: sum`, `average`, `count_distinct`). Any downstream derived ratio measure (e.g., `availability_rate`, `error_rate`, `conversion_rate`) referencing that measure will silently return `0` or `NULL` across all dashboards and explores.

---

## 1. Mandatory `SELECT DISTINCT` Grounding Protocol

> [!CAUTION]
> **NEVER GUESS OR ASSUME CATEGORICAL FILTER STRINGS.**
> Do not assume shorthand strings like `"2xx"`, `"active"`, `"Enterprise"`, or `"tier_1"` without inspecting the data first. Synthetic data generators and production schemas frequently use descriptive labels (e.g., `'2xx Success'`, `'Active Subscription'`, `'Enterprise Tier'`).

Before writing any `filters: [...]` block in a `.view.lkml` file, you **MUST** verify the exact distinct values present in the column using one of the following methods:

### Priority 1: Local Parquet Inspection (Zero-Latency)
If local Parquet files exist in `scratch/` or the workspace output directory, inspect distinct values using Python or `duckdb` / `pyarrow`:

```bash
python3 -c "
import pyarrow.parquet as pq
import pandas as pd
df = pq.read_table('scratch/parquet/fct_api_requests.parquet').to_pandas()
print(df['status_class'].value_counts(dropna=False))
"
```

### Priority 2: BigQuery `SELECT DISTINCT` Query
If data is already loaded in BigQuery or modeling from an existing dataset, query the distinct values and their row counts directly:

```bash
bq query --use_legacy_sql=false --format=prettyjson \
  "SELECT status_class, COUNT(*) AS row_count FROM \`project.dataset.fct_api_requests\` GROUP BY 1 ORDER BY 2 DESC LIMIT 20"
```
*(Or via the `bigquery` MCP tool `execute_sql_readonly`.)*

---

## 2. Looker Filter Expression Syntax Reference

When authoring `filters: [dimension_name: "expression"]`, choose the appropriate Looker filter expression pattern based on the verified distinct values:

### A. Exact Literal Match
Use when matching a single, verified categorical string value:
```lookml
measure: successful_request_count {
  label: "Successful Requests (2xx)"
  description: "Count of requests completing with 2xx HTTP status codes"
  type: count
  filters: [status_class: "2xx Success"]
}
```

### B. Prefix / Substring Wildcard (`%`)
Use Looker's `%` wildcard when a categorical column contains multiple descriptive variants sharing a common prefix or substring (compiles to SQL `LIKE '2xx%'`):
```lookml
measure: successful_request_count {
  label: "Successful Requests (2xx)"
  type: count
  filters: [status_class: "2xx%"]
}
```

### C. Multi-Value `OR` Lists (Comma-Separated)
Use comma-separated values inside a single string to match any of several values (compiles to SQL `IN (...)`):
```lookml
measure: server_fault_count {
  label: "Server Faults (5xx)"
  type: count
  filters: [http_status_code: "500, 502, 503, 504"]
}
```

### D. Negation & Exclusion (`-`)
Prefix a value with `-` to exclude it. Combine multiple conditions across separate filter entries (compiled with `AND`) or within the same dimension:
```lookml
measure: client_error_excl_ratelimit_count {
  label: "Client Errors (4xx excl. 429)"
  description: "Count of client-side error responses excluding HTTP 429 rate limits"
  type: count
  filters: [
    status_class: "4xx Client Error",
    http_status_code: "-429"
  ]
}
```

### E. Null & Empty Exclusions
To exclude nulls or empty strings in string dimensions:
```lookml
measure: valid_error_event_count {
  label: "Valid Error Events"
  type: count
  filters: [error_code: "-NULL,-EMPTY"]
}
```

### F. Numeric & Boolean Filters
- **Boolean (`type: yesno`)**: Use `"Yes"` or `"No"` (Looker standard boolean filter syntax):
  ```lookml
  filters: [is_billable: "Yes"]
  ```
- **Numeric comparisons**:
  ```lookml
  filters: [latency_ms: "> 1000", response_bytes: ">= 0"]
  ```

---

## 3. Safe Derived Ratio Measures (Preventing Divide-by-Zero)

When combining filtered measures into rate, ratio, or percentage measures (`type: number`), always guard against division by zero using `SAFE_DIVIDE` or `NULLIF` in the `sql:` block, and apply explicit `value_format_name`:

```lookml
measure: availability_rate {
  label: "API Availability Rate (%)"
  description: "Percentage of total API requests succeeding without 5xx server errors"
  type: number
  sql: SAFE_DIVIDE(${successful_request_count}, NULLIF(${count}, 0)) ;;
  value_format_name: percent_2
}

measure: error_5xx_rate {
  label: "5xx Server Error Rate (%)"
  description: "Percentage of total requests failing with 5xx server faults"
  type: number
  sql: SAFE_DIVIDE(${server_error_count}, NULLIF(${count}, 0)) ;;
  value_format_name: percent_2
}
```

---

## 4. Automated Pre-Deployment Verification

The `demo-create lookml deploy` command automatically runs the **Filtered Measure Distinct-Value Auditor** (`validator_service.py`), which:
1. Parses all `filters: [...]` blocks across every `.view.lkml` file.
2. Evaluates each filter expression against local Parquet files or the target BigQuery table (`sql_table_name`).
3. **Hard-fails deployment** if any filtered measure matches `0` rows, outputting the exact distinct values found in the column so you can immediately fix the LookML filter literal.
