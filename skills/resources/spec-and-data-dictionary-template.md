# Canonical `SPEC.md` & Data Dictionary Template

This shared resource defines the canonical structure for `./SPEC.md` (the living technical specification) and its `## Data Dictionary & Semantic Context` section, shared across [`demo-spec`](../demo-spec/SKILL.md), [`bigquery-metadata`](../bigquery-metadata/SKILL.md), [`knowledge-catalog-metadata`](../knowledge-catalog-metadata/SKILL.md), and [`looker-demo-orchestrator`](../looker-demo-orchestrator/SKILL.md).

---

## 1. Full `SPEC.md` Template Structure

```markdown
# {Demo Title} — Technical Specification (SPEC.md)

> **Status**: {Draft | In Review | Provisioned | Production Ready}
> **Last Updated**: {YYYY-MM-DD HH:MM:SS UTC}
> **Looker Project**: `{looker_project_name}` | **Model**: `{lookml_model_name}`
> **BigQuery Dataset**: `{gcp_project_id}.{bq_dataset_id}` ({gcp_location})
> **Associated Delivery Report**: [DELIVERY_REPORT.md](DELIVERY_REPORT.md)

---

## 1. Demo Metadata & Persona Alignment

- **Domain / Vertical**: {e.g., Fleet Logistics & IoT, FinTech Anti-Money Laundering, Healthcare Operations}
- **Target Persona**: {e.g., VP of Fleet Telematics, Head of Fraud Strategy, Chief Medical Officer}
- **Executive Value Proposition**: {Concise statement of what business value this demo proves}
- **Key User Journeys**:
  1. *Executive Journey*: High-level KPI visibility, cross-filtering, and anomaly detection.
  2. *Operational Journey*: Deep-dive granular exploration and root-cause diagnostics.
  3. *Conversational Journey*: Natural language ad-hoc question answering via CA AI Agent.

---

## 2. Business Scenario & Analytical Questions

### Business Problem Statement
{Clear narrative of the operational or strategic challenge the organization faces and how this analytics solution solves it.}

### Core Analytical Questions Answered
1. {Business question 1}
2. {Business question 2}
3. {Business question 3}

---

## 3. Relational Schema, Data Dictionary & Semantic Context

### Mermaid ERD
```mermaid
erDiagram
    {table_a} ||--o{ {table_b} : "{relationship_label} ({foreign_key})"
    {table_b} ||--o{ {table_c} : "{relationship_label} ({foreign_key})"
```

### Entity Relationship & Grain Summary (`bigquery-metadata`)
| Table Name | Grain / Primary Key | Table Type | Estimated Rows | Partition / Clustering |
| :--- | :--- | :--- | :--- | :--- |
| `fct_orders` | `order_id` (PK) | BASE TABLE | 1,250,000 | `order_date` (DAY) / `customer_id` |
| `dim_customers` | `customer_id` (PK) | BASE TABLE | 45,000 | None / `country_code` |

### Relationships & Joins
| Source Table | Foreign Key | Target Table | Primary Key | Join Relationship |
| :--- | :--- | :--- | :--- | :--- |
| `fct_orders` | `customer_id` | `dim_customers` | `customer_id` | `many_to_one` |

### Detailed Column Catalog & Dataplex Profiling Insights (`bigquery-metadata` + `knowledge-catalog-metadata`)
#### Table: `<TABLE_NAME>`
- **Catalog Status**: `{Active | Native BQ Metadata}` | **Data Quality Score**: `{98.4% | N/A}`

| Column | BigQuery Type | LookML Type | Null % | Distinct % | Top Frequent Values / Range | PK/FK & LookML Modeling Directive |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `order_id` | `STRING` | `string` | 0.0% | 100.0% | `1001`..`1251000` | **Validated PK**: Set `primary_key: yes`, `suggestable: no`. |
| `order_status` | `STRING` | `string` | 0.0% | 0.0004% | `COMPLETED` (72%), `PENDING` (18%) | **Low-Cardinality Categorical**: Add `suggestions: ['COMPLETED', 'PENDING', 'CANCELLED']`. |
| `total_amount` | `NUMERIC` | `number` | 0.2% | Continuous | `$4.50`..`$12,450.00` | **Continuous Metric**: Use `value_format_name: usd`; create `type: tier` dimension. |
| `ssn` | `STRING` | `string` | 0.0% | 100.0% | N/A | **PII Sensitive**: Set `hidden: yes` or `required_access_grants: [pii_access]`. |

---

## 4. Synthetic Data Generation Plan

- **Target Volumes**: `{table_1}`: {row_count} rows, `{table_2}`: {row_count} rows
- **Statistical Distributions & Business Logic**:
  - Pareto (80/20) foreign key sampling, tier-coupled log-normal metrics, and strict timestamp monotonicity.
- **Generation DAG Order**:
  1. `{root_dim_1}` (independent) -> 2. `{intermediate_dim_2}` -> 3. `{fct_table}`

---

## 5. Semantic Layer (LookML) Architecture

- **LookML Model**: `{lookml_model_name}.model.lkml` | **Connection**: `{looker_connection_name}`
- **Explores & Join Graph**:
  - `explore: {primary_explore}` (Base View: `{base_view_name}`):
    - `join: {joined_view_1}` (`type: left_outer`, `relationship: many_to_one`, `sql_on: ...`)
    - `join: {ndt_rollup_view}` (`type: left_outer`, `relationship: one_to_one`, `sql_on: ...`)
- **Chasm Trap & Fan Trap Mitigation Architecture**:
  - Document NDT rollups pre-aggregating 1:N child collections to parent grain, dedicated event-stream explores, and `primary_key: yes` symmetric aggregate safeguards.

---

## 6. Executive Dashboard & Visualization Layout

- **Dashboard File**: `{dashboard_name}.dashboard.lookml` (`crossfilter_enabled: true`)
- **Tabs Architecture**:
  - **Tab 1: Executive Pulse**: KPI Cards + Dual-axis trend + Donut share
  - **Tab 2: Entity Breakdown**: Ranked bar comparisons + Transparent Looker Grid (`table_theme: transparent`)
  - **Tab 3: Operational Diagnostics**: Throughput concentration + SLA target benchmarks

---

## 7. Conversational Analytics (CA) AI Agent Specification

- **Agent ID**: `{ca_agent_id}` | **Name**: `{ca_agent_name}` | **Explore Sources**: `[{explore_list}]`
- **Persona & Pre-Seeded Golden Queries**:
  1. *"{Golden Query 1 natural language question}"* -> `{lookml_model_name}/{explore_name}` (`[{field_list}]`)

---

## 8. External Embed Portal Specification (if applicable)

- **Workspace Directory**: `embed-portal/` (`frontend/` React 19 + Vite 6 + TanStack Router, `backend/` FastAPI + Cookieless SSO)
- **Branding & Theme Tokens**:
  - Brand Name: `{brand_name}` | Hub Title: `{portal_hub_title}`
  - HSL Tokens: `--color-primary-raw: {primary_hsl_raw};`, `--color-accent-raw: {accent_hsl_raw};`
  - Looker Themes: `<Brand>_Light` & `<Brand>_Dark`
- **Routes & Targets**:
  - `/` (Executive Hub), `/dashboard` (`VITE_DASHBOARD_ID`), `/conversational-analytics` (`VITE_CHAT_AGENT_ID`), `/explore` (`VITE_EXPLORE_PATH`)

---

## 9. Revision History & Change Log

| Timestamp (UTC) | Gate / Phase | Changed By | Summary of Changes |
| :--- | :--- | :--- | :--- |
| {YYYY-MM-DD HH:MM} | Gate 0 / Alignment | Looker Demo Orchestrator | Initialized SPEC.md with domain, persona, and core business questions. |
| {YYYY-MM-DD HH:MM} | Gate 1 / Data & Schema | Looker Demo Orchestrator | Hydrated relational schema, column definitions, Mermaid ERD, and volume plan. |
| {YYYY-MM-DD HH:MM} | Gate 2 / LookML & Dashboard | Looker Demo Orchestrator | Added LookML explores, NDT rollups, measures, and 3-tab dashboard specs. |
| {YYYY-MM-DD HH:MM} | Gate 3 / Production Release | Looker Demo Orchestrator | Recorded Looker deployment metadata, project name, and validation results. |
```

---

## 2. Dataplex Profile-to-LookML Decision Matrix (`knowledge-catalog-metadata`)

| Profile Indicator | Value / Threshold | LookML Modeling Directive |
| :--- | :--- | :--- |
| **Distinct Ratio** | `distinctRatio == 1.0` (Nulls = 0) | **Confirms Primary Key Grain**: Set `primary_key: yes` and `suggestable: no`. |
| **Distinct Ratio** | `distinctRatio < 0.05` | **Categorical Dimension**: Populate `suggestions: [...]` with top values; prime candidate for dashboard filters. |
| **Numeric Spread** | High skew (`max >> quartiles[2]`) | **Tiered Dimension**: Generate `type: tier` dimension with quartile intervals. |
| **Null Ratio** | `nullRatio > 0.10` | **Dimension Group / String**: Add `sql: COALESCE(...)` or note that measures must use `count` vs `count_distinct`. |
| **Quality Score** | `< 90%` or rule failure | **Explore Safeguard**: Add `sql_always_where` or `sql_always_having` to prune corrupt/incomplete rows. |
| **Sensitivity Tag**| `PII` / `CONFIDENTIAL` | **Access Control**: Set `hidden: yes` or attach `required_access_grants: [pii_access]`. |
