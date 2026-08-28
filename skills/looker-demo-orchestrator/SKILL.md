---
name: looker-demo-orchestrator
description: Master orchestration skill for designing, generating, modeling, and deploying end-to-end Looker and Embedded Analytics demos using the `demo-create` CLI, `lkr-dev-cli`, and Code Mode.
---

# Looker Demo Orchestrator (`demo-create`)

This skill defines the complete operational procedure for an AI agent or engineer creating full-stack data demos on Google Cloud BigQuery and Looker.

---

## 1. Pre-Flight Environment Inspection

Always execute the pre-check command to verify GCP/ADC credentials, MCP servers, and organized skills:

```bash
demo-create pre-check --fix
```

To get structured machine-readable JSON status:
```bash
demo-create pre-check --json
```

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

## 3. LookML Synchronization & Production Deployment (`lkr-dev-cli`)

Always use `lkr-dev-cli` to push local LookML files, validate syntax, commit, and deploy to production:

```bash
lkr tools lookml push <lookml_folder_path> --project=<project_name> --deploy
```

- Local structure:
  - `views/*.view.lkml`
  - `models/*.model.lkml`
  - `dashboards/*.dashboard.lookml`

---

## 4. External Embedded Portal Scaffolding

When an external demo is required, scaffold a new dedicated workspace:

```bash
demo-create run --project=<project_name> --scope=external
```

This clones `looker-embed-demo`, updates `.env`, `src/constants.ts`, and applies custom brand themes.
