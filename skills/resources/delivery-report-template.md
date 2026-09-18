# Canonical `DELIVERY_REPORT.md` Template

This shared resource defines the mandatory final delivery report structure emitted at the end of the `looker-demo-orchestrator` workflow and saved to `./DELIVERY_REPORT.md`.

> [!IMPORTANT]
> **Mandatory Link to `SPEC.md`**: Both the chat delivery report and the persisted `DELIVERY_REPORT.md` **MUST prominently link to `[SPEC.md](SPEC.md)`** in the Quick Access Links table.

```markdown
# {Domain Name} — Final Delivery Report

> [!NOTE]
> **Production Deployment Status: Active & Operational**
> - **Looker Instance**: [{looker_instance_host}]({looker_instance_url})
> - **Looker Project & Model**: `{looker_project_name}`
> - **BigQuery Dataset**: `{gcp_project_id}.{bq_dataset_id}` ({gcp_location})
> - **Looker Database Connection**: `{looker_connection_name}`
> - **Validation Gate**: 0 LookML errors, {queries_passed}/{queries_tested} (100%) Dashboard Queries Passed

---

## 1. Quick Access Links

| Asset | Direct URL / Access Path | Description |
| :--- | :--- | :--- |
| **Technical Architecture Spec** | [SPEC.md](SPEC.md) | Living technical specification, relational schema & modeling architecture |
| **Executive Dashboard** | [{dashboard_title}]({looker_instance_url}/dashboards/{lookml_model_name}::{dashboard_name}) | {tabs_count}-tab executive command center with cross-filtering |
| **Conversational Analytics Agent** | [{agent_name}]({looker_instance_url}/conversational-analytics/agents/{ca_agent_id}) | AI Data Agent with {gq_count} pre-seeded Golden Queries *(if provisioned)* |
| **{Primary Explore} Explore** | [Explore: {Primary Explore Label}]({looker_instance_url}/explore/{lookml_model_name}/{primary_explore}) | Primary domain entity, metrics & dimension analysis |
| **{Event Stream} Explore** | [Explore: {Event Stream Label}]({looker_instance_url}/explore/{lookml_model_name}/{event_explore}) | Granular event/telemetry audit stream |
| **Embed Analytics Portal** | [External Embed Portal]({embed_portal_url}) | White-labeled external embed application *(if scaffolded)* |

---

## 2. BigQuery Data Warehouse Summary

All {table_count} relational tables were synthesized with realistic domain distributions, strict referential integrity, and uploaded to BigQuery:

```{gcp_project_id}.{bq_dataset_id}
├── {table_name_1}  ({rows_1} rows)  - {table_1_description}
├── {table_name_2}  ({rows_2} rows)  - {table_2_description}
└── {table_name_n}  ({rows_n} rows)  - {table_n_description}
```

Total dataset volume: **{total_rows} rows**.

---

## 3. Relational Architecture & ERD

```mermaid
erDiagram
    {table_a} ||--o{ {table_b} : "{relationship_label} ({foreign_key})"
```

*(Optional — Include `### Chasm Trap Mitigation Architecture` below ONLY if snowflake modeling was required / 1:N child collections were detected)*

---

## 4. LookML Dashboard Layout & Tabbed Architecture

The dashboard (`{lookml_model_name}::{dashboard_name}`) is structured into **{tab_count} functional operational tabs** with universal cross-filtering (`crossfilter_enabled: true`) and popover filters:

### Tab 1: {Tab 1 Name}
- **KPI Banners**: {List of primary single-value metrics}.
- **{Chart 1 Title}**: {Chart visualization type and business question answered}.
- **{Chart 2 Title}**: {Chart visualization type and business question answered}.

### Tab 2: {Tab 2 Name}
- **KPI Banners**: {List of secondary single-value metrics}.
- **{Chart 1 Title}**: {Chart visualization type and business question answered}.

---

## 5. Pre-Deployment Validation Audit Record

```
[Phase 1] Code Push to Dev Branch:             100% COMPLETE ({files_count} LookML files pushed)
[Phase 2] LookML Validator (validate_project):   0 ERRORS DETECTED
[Phase 3] Exhaustive Dashboard Query Tests:      {queries_passed} / {queries_tested} (100%) QUERIES PASSED
[Phase 4] Production Deployment:                SUCCESS (Deployed to Production at {timestamp})
```

### Detailed Query Test Results ({queries_passed}/{queries_tested} HTTP 200 OK)
1. `{query_tile_1}` (Explore: `{explore_1}`) ➔ **PASS**
2. `{query_tile_2}` (Explore: `{explore_2}`) ➔ **PASS**

---

## 6. Conversational Analytics (CA) AI Agent Configuration *(if provisioned)*

- **Agent ID**: `{ca_agent_id}` | **Agent Name**: `{ca_agent_name}`
- **Explore Sources**: `{explore_sources_list}` | **Code Interpreter**: Enabled
- **Direct Agent Chat URL**: [Open {ca_agent_name}]({looker_instance_url}/conversational-analytics/agents/{ca_agent_id})
- **Pre-Seeded Golden Queries**:
  1. *"{Natural language business question 1}"*
  2. *"{Natural language business question 2}"*

---

## 7. Gemini Enterprise (GE) / Embed Portal Status

*(If published to Gemini Enterprise)*:
- **Publish State**: `published` (HTTP 200 OK) — `Successfully published Agent {ca_agent_id} to GEMINI_ENTERPRISE.`

*(If external embed portal was scaffolded)*:
- **Workspace Directory**: `{embed_workspace_dir}` (`frontend/` React 19 + Vite 6 + TanStack Router, `backend/` FastAPI + Cookieless SSO)
- **Local Dev Command**: `cd {embed_workspace_dir}/frontend && pnpm install && pnpm dev`
- **Portal Routes**: `/` (Executive Hub), `/dashboard` (`{deployed_dashboard_id}`), `/conversational-analytics` (`{ca_agent_id}`), `/explore` (`{explore_path}`)
- **Branding & Themes**: `{brand_name}`, `--color-primary-raw: {primary_hsl};`, Looker Themes `<Brand>_Light` & `<Brand>_Dark`, verified `0` errors via `pnpm run build`.
```
