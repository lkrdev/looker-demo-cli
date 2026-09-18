# Prerequisites, GCP IAM, and Looker Permissions Reference

This document is the complete setup and permissions reference for running `looker-demo-cli` (`demo-create`) across local development, Google Cloud (BigQuery, Vertex AI, Dataplex, and Discovery Engine / Gemini Enterprise), and Looker.

---

## 1. Local Tools & Cloud Infrastructure Checklist

| Requirement | Details & Verification |
| :--- | :--- |
| **Python & `uv`** | Python `>= 3.11` and [`uv`](https://docs.astral.sh/uv/) (`>= 0.4`) for isolated CLI installation (`uv tool install looker-demo-cli`) and `uvx` execution. |
| **`lkr-dev-cli` (`lkr`)** | Automatically installed alongside `looker-demo-cli` (`lkr-dev-cli[codemode] >= 0.2.3`). Used for OAuth login (`lkr auth login`), dev workspace sync (`lkr tools lookml push`), and production release. |
| **Google Cloud SDK (`gcloud` & `bq`)** | Authenticated via `gcloud auth login` and `gcloud auth application-default login` (ADC). Includes `bq` CLI for schema metadata inspection and Parquet table loading. |
| **Looker Instance** | Looker (Google Cloud Core or Hosted) instance with **API 4.0** accessible and a pre-configured **BigQuery Database Connection** targeting your GCP project. |
| **Gemini Enterprise (GE) Instance** *(Gate 5)* | An active **Gemini Enterprise / Discovery Engine App** in Google Cloud Console (`global`, `us`, or `eu` region) to receive published Looker Conversational Analytics (CA) Agents. |
| **Node.js & `pnpm`** *(Optional)* | Required only when scaffolding and running the standalone React/Vite Embed Portal (`demo-create embed scaffold`). |

---

## 2. Google Cloud (GCP) Required APIs & IAM Roles

`demo-create` interacts with Google Cloud across **three distinct principals**.

### Required GCP APIs

Ensure the following APIs are enabled on your target GCP project:
- `bigquery.googleapis.com` — BigQuery API (dataset creation, Parquet loading, and query validation)
- `aiplatform.googleapis.com` — Vertex AI API (LLM/Gemini calls and AI-assisted synthesis/grounding)
- `discoveryengine.googleapis.com` — Discovery Engine API (Gemini Enterprise app discovery and CA Agent publishing)
- `dataplex.googleapis.com` & `datacatalog.googleapis.com` — *(Optional)* Knowledge Catalog / Dataplex metadata enrichment
- `cloudresourcemanager.googleapis.com` — Project listing and automated IAM policy binding

### GCP IAM Matrix by Principal

| Principal | Where Used | Least-Privilege IAM Roles | Quick-Start / Sandbox Role | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **1. Developer / Local ADC User**<br/>*(Your `gcloud` user account)* | Gates 0–2 & Gate 5 (`pre-check`, `data`, `lookml`, `ge`) | • `roles/bigquery.dataEditor`<br/>• `roles/bigquery.jobUser`<br/>• `roles/aiplatform.user`<br/>• `roles/serviceusage.serviceUsageConsumer`<br/>• `roles/dataplex.viewer` *(optional)*<br/>• `roles/discoveryengine.viewer`<br/>• `roles/resourcemanager.projectIamAdmin` *(to auto-grant Looker SA IAM)* | `roles/editor` + `roles/resourcemanager.projectIamAdmin` | Create BigQuery datasets/tables, load Parquet files, run `SELECT DISTINCT` measure grounding queries, invoke Vertex AI / Gemini models, inspect Dataplex metadata, discover GE apps, and bind IAM roles for the Looker GE Service Account. |
| **2. Looker BigQuery Connection SA**<br/>*(Configured in Looker Admin > Connections)* | Gate 3 (`lookml deploy` & inline query validation) & Live Dashboards | • `roles/bigquery.dataEditor`<br/>• `roles/bigquery.jobUser` | `roles/bigquery.admin` | Execute live Looker Explore/Dashboard queries (`run_inline_query`) and materialize Native Derived Tables (NDTs) or Persistent Derived Tables (PDTs) in the scratch schema. |
| **3. Looker Gemini Service Account**<br/>*(`ai_ge_service_account_email` returned by `/api/4.0/gemini_enablement`)* | Gate 5 (`ge configure`, `agent publish`) | • `roles/discoveryengine.admin`<br/>• **Active Gemini Enterprise License** *(assigned in GCP / Workspace Admin)* | `roles/discoveryengine.admin` + **GE License** | Register, synchronize, and publish Looker Conversational Analytics (CA) Agents directly into your connected Gemini Enterprise app. *(Note: `demo-create ge configure` automatically runs the `roles/discoveryengine.admin` binding if your ADC user has `projectIamAdmin`.)* |

### Copy-Paste `gcloud` Setup Script

```bash
export PROJECT_ID="your-gcp-project-id"
export USER_EMAIL="$(gcloud config get-value account)"
# Optional: set if already known from Looker Connections / Admin > Gemini
export LOOKER_BQ_SA="your-looker-bq-sa@${PROJECT_ID}.iam.gserviceaccount.com"
export LOOKER_GE_SA="service-<project-number>@gcp-sa-looker.iam.gserviceaccount.com"

# 1. Enable required Google Cloud APIs
gcloud services enable \
  bigquery.googleapis.com \
  aiplatform.googleapis.com \
  discoveryengine.googleapis.com \
  dataplex.googleapis.com \
  datacatalog.googleapis.com \
  cloudresourcemanager.googleapis.com \
  --project="${PROJECT_ID}"

# 2. Grant Developer / Local ADC Roles (BigQuery + Vertex AI LLM + Dataplex + GE Discovery)
for ROLE in \
  roles/bigquery.dataEditor \
  roles/bigquery.jobUser \
  roles/aiplatform.user \
  roles/serviceusage.serviceUsageConsumer \
  roles/dataplex.viewer \
  roles/discoveryengine.viewer; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="user:${USER_EMAIL}" \
    --role="${ROLE}"
done

# 3. Grant Looker BigQuery Connection SA Roles (if not already configured)
for ROLE in roles/bigquery.dataEditor roles/bigquery.jobUser; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${LOOKER_BQ_SA}" \
    --role="${ROLE}"
done

# 4. Grant Looker Gemini Service Account Discovery Engine Admin
# (Also executed automatically during `demo-create ge configure`)
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${LOOKER_GE_SA}" \
  --role="roles/discoveryengine.admin"
```

---

## 3. Looker Instance Configuration & User Permissions

### A. Instance-Level Prerequisites (Looker Admin)

1. **OAuth Client Registration (`lkr-cli`)**:
   If `lkr-cli` has not yet been registered on your Looker instance, an admin must register it once via the Looker API Explorer:
   - Open: `https://<your-looker-instance>/extensions/marketplace_extension_api_explorer::api-explorer/4.0/methods/Auth/register_oauth_client_app`
   - Set **`client_id`**: `lkr-cli`
   - Request body JSON:
     ```json
     {
       "redirect_uri": "http://localhost:8000/callback",
       "display_name": "LKR",
       "description": "lkr.dev language server, MCP and CLI",
       "enabled": true
     }
     ```
   - Check **"I Understand"** and click **"Run"**.
2. **BigQuery Connection**:
   Configure a BigQuery connection in **Admin > Database > Connections** pointing to your target GCP project (with PDTs enabled if using materialized derived tables).
3. **Gemini & Conversational Analytics Enablement**:
   In **Admin > Platform > Gemini** (or via `PATCH /api/4.0/gemini_enablement`):
   - **Conversational Analytics (CA)** enabled (`ai_ca_enabled: true`).
   - **Publish to Gemini Enterprise** enabled (`ai_ge_publish_enabled: true`) with your GE GCP Project ID, Location (`global`/`us`/`eu`), and GE App/Engine ID configured (automated by `demo-create ge configure`).

### B. Looker User Role & Permission Matrix

> [!TIP]
> **Quick Path (Recommended)**: Assigning the built-in Looker **`Admin`** role to the authenticating user satisfies all gates (Gates 0–5), including project creation, model registration, CA agent creation, and internal Gemini Enterprise publishing endpoints.

If your organization enforces custom least-privilege Looker roles, grant the following permissions by workflow stage:

| Workflow Stage | Commands Executed | Required Looker Permissions / Role | Why It's Required |
| :--- | :--- | :--- | :--- |
| **Gate 0: Pre-Flight & Connection Audit** | `demo-create pre-check` | `see_lookml`, `explore` *(or permission to list connections via `all_connections`)* | Verifies Looker OAuth/API session (`sdk.me()`) and enumerates available BigQuery connections. |
| **Gates 2–3: LookML Dev, Validation & Production Release** | `demo-create lookml model`<br/>`demo-create lookml clean-root`<br/>`demo-create lookml deploy` | **Developer Role** (`develop`, `deploy`, `see_lookml`, `see_lookml_dashboards`, `use_sql_runner`, `explore`, `see_queries`, `save_content`) **+** `manage_project_models` *(or `manage_models`)* | Switches API session to `dev` workspace (`update_session`), creates/updates the bare Git project (`create_project`), registers the LookML model and allowed DB connection (`create_lookml_model`), pushes files via `lkr`, runs `validate_project`, executes 100% of dashboard tile queries (`run_inline_query`), and deploys to production. |
| **Gate 4: Conversational Analytics (CA) Agent & Golden Queries** | `demo-create agent create`<br/>`demo-create agent golden-queries` | **CA Agent Author** (`create_agents` / `manage_agents` or Gemini CA access) **+** `explore`, `create_queries`, `save_content` | Creates base Looker queries (`POST /api/4.0/queries`) to generate deterministic `expanded_share_url` links, registers 1:1 Golden Queries (`POST /api/4.0/golden_queries`), provisions the CA Agent (`POST /api/4.0/agents`), and binds Golden Query IDs (`PATCH /api/4.0/agents/{id}`). |
| **Gate 5: Gemini Enterprise Configuration & Publishing** | `demo-create ge status`<br/>`demo-create ge configure`<br/>`demo-create agent publish` | **Looker `Admin` Role** | Required to read/update instance-level Gemini settings (`GET`/`PATCH /api/4.0/gemini_enablement`) and invoke the internal GE publishing endpoint (`POST /api/4.0/internal/agents/{id}/publish`). |

---

## 4. Remote Hosts & SSH Port Forwarding (`lkr auth login`)

The Looker OAuth callback redirects your browser to `http://localhost:8000/callback`.
- **SSH Port Forwarding**: If developing on a remote machine or VM, forward port `8000` through SSH:
  ```bash
  ssh -L 8000:localhost:8000 <remote-host>
  ```
- **Clearing Port 8000**: If port `8000` is occupied by an existing process, terminate it before logging in:
  ```bash
  lsof -ti:8000 | xargs kill -9   # (or: fuser -k 8000/tcp)
  ```
- **Headless / Agent Fallback**: If your browser redirects to `http://localhost:8000/callback?code=...` and displays a connection error, copy the entire URL from your browser address bar and paste it into chat. The AI agent will `curl` the callback URL locally on the remote host to complete authentication.

---

## 5. Gemini Enterprise (GE) Provisioning Lifecycle

When deploying Conversational Analytics (CA) Agents into Gemini Enterprise (`demo-create ge configure` and `demo-create agent publish`), the CLI performs four automated steps:

1. **Automated Inspection (`GET /api/4.0/gemini_enablement`)**: Inspects active Looker Gemini enablement settings and retrieves the Looker Service Account (`ai_ge_service_account_email`).
2. **Automated GCP Discovery (`discoveryengine.googleapis.com`)**: Scans the target GCP project for active Discovery Engine apps across `global`, `us`, and `eu` regions.
3. **Looker Configuration (`PATCH /api/4.0/gemini_enablement`)**: Updates Looker GE settings with `ai_ge_project_id`, `ai_ge_location`, `ai_ge_instance_id`, and `ai_ge_publish_enabled: true`.
4. **Automated Looker SA IAM Binding**: Grants `roles/discoveryengine.admin` to `ai_ge_service_account_email` via `gcloud projects add-iam-policy-binding`. *(Note: Ensure the Looker Service Account also has an active Gemini Enterprise license assigned in your Google Cloud / Workspace Admin console.)*
