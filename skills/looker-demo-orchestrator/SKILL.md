---
name: looker-demo-orchestrator
description: Master orchestration skill for designing, generating, modeling, and deploying end-to-end Looker and Embedded Analytics demos using the `demo-create` CLI, `lkr-dev-cli`, and Code Mode.
---

# Looker Demo Orchestrator (`demo-create`)

This skill defines the complete operational procedure for an AI agent or engineer creating full-stack data demos on Google Cloud BigQuery and Looker.

---

## 1. Pre-Flight Environment Inspection, Account & Project Selection

Always execute the pre-check command first to verify GCP/ADC credentials, active configuration, available projects, MCP servers, and organized skills:

```bash
demo-create pre-check --fix
```

To get structured machine-readable JSON status:
```bash
demo-create pre-check --json
```

### Authentication, Account & Project Selection Rules:
1. **GCP Account & Project Confirmation**:
   - In environments with multiple Google accounts (e.g. corporate account alongside demo/personal accounts), the agent **MUST prompt the user to confirm/select both the GCP account and target Google Cloud Project** before creating datasets or running data synthesis.
   - The pre-check output displays the active `gcloud` account, active `gcloud` project, and Application Default Credentials (ADC) quota project, along with all authenticated accounts and accessible projects.
   - Example prompt: *"I detected the following GCP accounts: `admin@maluka.altostrat.com` (Active) and `maluka@google.com`. Please confirm which GCP account and which Google Cloud project (e.g., 1) luka-networking, 2) data-cloud-interactive-demo, 3) looker-demo-392616) you would like to use."*

2. **First-Time Setup or Missing Accounts/Projects**:
   - If no accounts or projects are configured in `gcloud`, instruct the user to run:
     ```bash
     gcloud auth login
     gcloud auth application-default login
     gcloud config set project <PROJECT_ID>
     ```

3. **GCP Re-authentication Required**:
   - If the pre-check output indicates that re-authentication is needed (e.g. `reauth_required: true`, token refresh error, or account restricted):
     - **Immediately prompt the user** to authenticate by running:
       ```bash
       gcloud auth login
       gcloud auth application-default login
       ```
     - Do NOT try to run BigQuery commands or create datasets until the user has re-authenticated.

---

## 2. Looker Authentication & Project Provisioning via Code Mode (`lkr-dev-cli code-mode`)

### A. Authentication Options & Validation
Looker authentication is managed directly via `lkr-dev-cli` without requiring an MCP server:

1. **OAuth Authentication (Interactive / Browser Flow)**:
   - Check current session:
     ```bash
     uvx lkr-dev-cli auth whoami
     uvx lkr-dev-cli auth list
     ```
   - If not authenticated, prompt for the Looker instance URL and perform pre-flight validation against `https://<instance_url>/auth?client_id=lkr-cli&...`.
   - Complete login:
     ```bash
     uvx --from "lkr-dev-cli[codemode]" lkr-dev-cli auth login
     ```

2. **API Key Authentication (`.env` or Environment Variables)**:
   - Provide credentials via `.env` file:
     ```bash
     uvx --env-file=.env --from "lkr-dev-cli[codemode]" lkr-dev-cli code-mode sandbox --code="..."
     ```

### B. Project Provisioning via Direct Code Mode CLI
Execute Python SDK commands directly in the Monty sandbox:

```bash
uvx --from "lkr-dev-cli[codemode]" lkr-dev-cli code-mode sandbox --code="
if session().get('workspace_id') != 'dev':
    update_session(body={'workspace_id': 'dev'})

project_name = 'logistics_analytics'
connection_name = 'default_bigquery_connection'

create_project(body={'name': project_name})
update_project(project_id=project_name, body={'git_remote_url': None, 'git_service_name': 'bare'})
create_lookml_model(body={
    'name': project_name,
    'project_name': project_name,
    'allowed_db_connection_names': [connection_name],
    'unlimited_db_connections': False,
})
"
```

---

## 3. Iterative Schema Co-Design & Micro-Sample Validation Gate

When generating greenfield datasets, the agent **MUST co-iterate with the user** across four deterministic phases:

1. **Phase 1 — Schema Proposal & Review**:
   - Propose the entity schema model (entities, fact vs dimension tables, field names, data types, primary keys, and foreign key relationships).
   - Solicit user confirmation and feedback before generating any data rows.

2. **Phase 2 — Micro-Sample Synthesis & Preview**:
   - Synthesize a micro-sample dataset (5–10 sample rows per table).
   - Present markdown tables showing the sample records, realistic data distributions, and referential integrity for user inspection.

3. **Phase 3 — Volume & Scale Confirmation**:
   - Prompt the user to confirm the target scale (e.g., Small ~1,000 rows, Medium ~10,000 rows, Large ~100,000 rows, or custom table sizes).

4. **Phase 4 — Execution & BigQuery Load**:
   - Only synthesize the full-volume dataset and create/load BigQuery tables **after explicit user acknowledgment**.

---

## 4. LookML Quality Standards, Validation & Production Deployment (`lkr-dev-cli`)

### A. Field Documentation & Flexible Dashboard Design Standards
Every view file (`views/*.view.lkml`) must follow LookML best practices:
- **Explicit `label:` and `description:`** parameters on every dimension, dimension group, and measure.
- Human-friendly Title Case labels (e.g. `label: "Patient Satisfaction Score"`).
- Explicit `type:`, `sql:`, and `value_format_name:` (e.g. `usd_0`, `percent_2`, `decimal_1`).
- Drill fields (`drill_fields: [...]`) on key primary measures.

**Flexible Dashboard Co-Design**:
- LookML dashboards are not confined to fixed 3-tab layouts.
- Incorporate user guidance, wireframes, image mockups, requested KPIs, tab structures (e.g., Executive Pulse, Operations, Drilldowns, Geography, Risk), and customized visual elements.

### B. LookML Synchronization & Comprehensive Validation Gate (`lkr-dev-cli`)
Always use `lkr-dev-cli` to push files to the Looker developer workspace, validate syntax, verify dashboard queries, and deploy to production:

```bash
# 1. Push local LookML files to the Looker dev branch
lkr --dev tools lookml push <lookml_folder_path> --project=<project_name>

# 2. Run LookML Validator (via Looker API or Code Mode) to catch syntax/join errors
# In Python / Code Mode:
# validation = sdk.validate_project("<project_name>")
# assert len(validation.errors) == 0, f"LookML errors found: {validation.errors}"

# 3. Exhaustive Dashboard Query Verification Gate:
# Execute every query element across all *.dashboard.lookml files via /api/4.0/queries/run/json or run_inline_query
# Confirm 100% of dashboard queries execute with HTTP 200 OK before deploying.

# 4. Commit and deploy to production once validation and query tests pass
lkr --dev tools lookml deploy --project=<project_name>

# Or execute one-step push, validation, commit, and deploy:
lkr --dev tools lookml push <lookml_folder_path> --project=<project_name> --deploy
```

- Local structure:
  - `views/*.view.lkml` (with labels and descriptions)
  - `models/*.model.lkml` (with explores and joins)
  - `dashboards/*.dashboard.lookml` (executive visualization layouts)

---

## 5. External Embedded Portal Scaffolding

When an external demo is required, scaffold a new dedicated workspace:

```bash
demo-create run --project=<project_name> --scope=external
```

This clones `looker-embed-demo`, updates `.env`, `src/constants.ts`, and applies custom brand themes.
