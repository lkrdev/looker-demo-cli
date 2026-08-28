---
name: looker-demo-orchestrator
description: Master orchestration skill for designing, generating, modeling, and deploying end-to-end Looker and Embedded Analytics demos using the `demo-create` CLI, `lkr-dev-cli`, and Code Mode.
---

# Looker Demo Orchestrator (`demo-create`)

This skill defines the complete operational procedure for an AI agent or engineer creating full-stack data demos on Google Cloud BigQuery and Looker.

---

## 1. Pre-Flight Environment Inspection & Project Selection

Always execute the pre-check command first to verify GCP/ADC credentials, available projects, MCP servers, and organized skills:

```bash
demo-create pre-check --fix
```

To get structured machine-readable JSON status:
```bash
demo-create pre-check --json
```

### Authentication & Project Selection Rules:
1. **GCP Re-authentication Required**:
   If the pre-check output indicates that re-authentication is needed (e.g. `reauth_required: true`, token refresh error, or account restricted):
   - **Immediately prompt the user** to authenticate by running:
     ```bash
     gcloud auth login
     gcloud auth application-default login
     ```
   - Do NOT try to run BigQuery commands or create datasets until the user has re-authenticated.

2. **GCP Project Confirmation**:
   - The agent **MUST prompt the user to confirm/select which Google Cloud Project to use** from the list of available projects displayed in the pre-check summary before creating datasets or running data synthesis.
   - Example prompt: *"Please confirm which Google Cloud project you would like to use for the demo: 1) analytics-demo-prod, 2) customer-insights-dev, 3) financial-services-demo, or specify another project."*

---

## 2. Project Provisioning via Code Mode (`lkr_codemode` MCP or `lkr code-mode run`)

When initializing a new Looker project, execute the standard administrative snippet:

```python
# 1. Switch to dev mode
if session().get("workspace_id") != "dev":
    update_session(body={"workspace_id": "dev"})

project_name = "logistics_analytics"
connection_name = "default_bigquery_connection"

# 2. Create project & configure bare Git
create_project(body={"name": project_name})
update_project(project_id=project_name, body={"git_remote_url": None, "git_service_name": "bare"})

# 3. Register LookML Model
create_lookml_model(body={
    "name": project_name,
    "project_name": project_name,
    "allowed_db_connection_names": [connection_name],
    "unlimited_db_connections": False,
})
```

---

## 3. LookML Quality Standards, Validation & Production Deployment (`lkr-dev-cli`)

### A. Field Documentation Standards
Every view file (`views/*.view.lkml`) must follow LookML best practices:
- **Explicit `label:` and `description:`** parameters on every dimension, dimension group, and measure.
- Human-friendly Title Case labels (e.g. `label: "Patient Satisfaction Score"`).
- Explicit `type:`, `sql:`, and `value_format_name:` (e.g. `usd_0`, `percent_2`, `decimal_1`).
- Drill fields (`drill_fields: [...]`) on key primary measures.

### B. LookML Synchronization & Validation Gate (`lkr-dev-cli`)
Always use `lkr-dev-cli` to push files to the Looker developer workspace, validate syntax, and deploy to production:

```bash
# 1. Push local LookML files to the Looker dev branch
lkr --dev tools lookml push <lookml_folder_path> --project=<project_name>

# 2. Run LookML Validator (via Looker API or Code Mode) to catch syntax/join errors
# In Python / Code Mode:
# validation = sdk.validate_project("<project_name>")
# assert len(validation.errors) == 0, f"LookML errors found: {validation.errors}"

# 3. Commit and deploy to production once validation passes
lkr --dev tools lookml deploy --project=<project_name>

# Or execute one-step push, validation, commit, and deploy:
lkr --dev tools lookml push <lookml_folder_path> --project=<project_name> --deploy
```

- Local structure:
  - `views/*.view.lkml` (with labels and descriptions)
  - `models/*.model.lkml` (with explores and joins)
  - `dashboards/*.dashboard.lookml` (executive visualization layouts)

---

## 4. External Embedded Portal Scaffolding

When an external demo is required, scaffold a new dedicated workspace:

```bash
demo-create run --project=<project_name> --scope=external
```

This clones `looker-embed-demo`, updates `.env`, `src/constants.ts`, and applies custom brand themes.
