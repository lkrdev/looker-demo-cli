# GCP / Looker Authentication, Target Resolution & Security Guardrails

This shared resource defines the mandatory GCP/Looker authentication recovery procedures, CLI environment/target resolution snippets, project integrity guardrails, and subagent lifecycle fences shared across [`looker-demo-orchestrator`](../looker-demo-orchestrator/SKILL.md), [`data-engineer`](../looker-demo-orchestrator/subagents/data-engineer.md), [`bigquery-metadata`](../bigquery-metadata/SKILL.md), and [`knowledge-catalog-metadata`](../knowledge-catalog-metadata/SKILL.md).

---

## 1. Environment & Target Discovery Snippet (`bigquery-metadata` & `knowledge-catalog-metadata`)

Resolve `PROJECT_ID`, `DATASET`, and `LOCATION` from active `gcloud` configuration or `./SPEC.md`:

```bash
# 1. Active GCP project and compute region
PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
LOCATION=$(gcloud config get-value compute/region 2>/dev/null || echo "us-central1")

# 2. Override from SPEC.md if confirmed in Gate 0 / Gate 1
if [ -f "SPEC.md" ]; then
  PROJECT_ID=$(grep -E "gcp_project_id" SPEC.md | head -n 1 | awk -F': ' '{print $2}' | tr -d "\"' " || echo "$PROJECT_ID")
  DATASET=$(grep -E "bigquery_dataset" SPEC.md | head -n 1 | awk -F': ' '{print $2}' | tr -d "\"' " || echo "$DATASET")
fi
```

---

## 2. Pre-Flight Authentication Recovery Procedures (Exit Code `3` / `AUTH_ERROR`)

If `demo-create pre-check` exits with code **3** (`AUTH_ERROR`) or reports `data.is_blocked: true`, **STOP IMMEDIATELY** and prompt the user with the relevant recovery steps:

### A. Google Cloud User & Application Default Credentials (ADC)
```bash
gcloud config set account <selected_account>
gcloud auth login <selected_account>
gcloud auth application-default login
gcloud config set project <PROJECT_ID>
```

### B. Looker OAuth Login & First-Time `lkr-cli` Client Registration
1. **Login via OAuth**:
   ```bash
   lkr auth login
   ```
2. **First-Time `lkr-cli` OAuth Client Registration** (if not yet registered on the Looker instance):
   - **API Explorer URL**: `https://<your-instance>/extensions/marketplace_extension_api_explorer::api-explorer/4.0/methods/Auth/register_oauth_client_app`
   - **Client ID**: `lkr-cli`
   - **Body JSON**:
     ```json
     {
       "redirect_uri": "http://localhost:8000/callback",
       "display_name": "LKR",
       "description": "lkr.dev language server, MCP and CLI",
       "enabled": true
     }
     ```
3. **Remote Host / SSH Port 8000 Forwarding & Port Cleanup**:
   ```bash
   # Forward port 8000 from local workstation to remote host:
   ssh -L 8000:localhost:8000 <remote-host>

   # Free port 8000 if another process is holding it:
   lsof -ti:8000 | xargs kill -9   # (or: fuser -k 8000/tcp)
   ```
4. **Headless / Agent OAuth Callback Fallback**:
   If the browser redirects to `http://localhost:8000/callback?code=...` and cannot load the page, the user can copy the full URL from their browser address bar and paste it into chat so the agent can `curl` it locally on the remote host.

---

## 3. Non-Negotiable Security & Project Integrity Guardrails

> [!CAUTION]
> ### 1. Strict Target Project Integrity (Never Silently Divert Projects)
> - **NEVER silently fall back or divert to an alternate Google Cloud Project or dataset** if permissions errors (`403 Access Denied`, `bigquery.datasets.create`, or expired ADC tokens) occur.
> - Immediately block and prompt the user to refresh ADC (`gcloud auth application-default login`) or grant BigQuery IAM roles on the confirmed project.

> [!IMPORTANT]
> ### 2. Subagent Kill-Fence & Headless LookML Rollback
> - **Subagent Kill-Fence**: Before any file revert, rollback, or snapshot restoration, run `manage_subagents(Action='kill_all')` so background tasks cannot overwrite restored files.
> - **Headless Snapshot Restore**: `demo-create lookml optimize` snapshots pre-optimization files into `<lookml_dir>/.backup_pre_opt`. Run `demo-create lookml restore --lookml-dir <dir>` to restore cleanly in 1 command without Git.
> - **`.env` & `.gitignore` Safety**: Before creating or updating any `.env` file, verify that `.gitignore` exists in the working directory and includes `.env` (and `.env.*` / `**/.env`).
> - **`ArtifactMetadata` Boundary**: Only pass `ArtifactMetadata` in `write_to_file` when writing under `<appDataDir>/brain/<conversation-id>/`. Never pass it for workspace files.
