# Prerequisites, GCP IAM, and Looker Permissions Reference

This document is the complete setup and permissions reference for running `looker-demo-cli` (`demo-create`) across local development, Google Cloud (BigQuery, BigQuery Storage Write API, Dataplex Universal Catalog, Compute Engine + IAP, Vertex AI, and Discovery Engine / Gemini Enterprise), and Looker.

---

## 1. Local Tools & Cloud Infrastructure Checklist

| Requirement | Details & Verification |
| :--- | :--- |
| **Python & `uv`** | Python `>= 3.11` and [`uv`](https://docs.astral.sh/uv/) (`>= 0.4`) for isolated CLI installation (`uv tool install looker-demo-cli`) and `uvx` execution. |
| **`lkr-dev-cli` (`lkr`)** | Automatically installed alongside `looker-demo-cli` (`lkr-dev-cli[codemode] >= 0.2.3`). Used for OAuth login (`lkr auth login`), dev workspace sync (`lkr tools lookml push`), and production release. |
| **Google Cloud SDK (`gcloud` & `bq`)** | Authenticated via `gcloud auth login` and `gcloud auth application-default login` (ADC). Includes `bq` CLI for schema metadata inspection and Parquet table loading, plus `gcloud compute` for Gate 14 IAP tunneling. |
| **Go `>= 1.22`** *(Optional — Gate 14)* | Required when scaffolding, testing (`go test ./...`), and cross-compiling (`GOOS=linux GOARCH=amd64 go build`) the Gate 14 real-time BigQuery Storage Write API & CDC streaming daemon (`demo-create stream deploy`). |
| **Looker Instance** *(Required for `--mode full`)* | Looker (Google Cloud Core or Hosted) instance with **API 4.0** accessible and a pre-configured **BigQuery Database Connection** targeting your GCP project *(optional when running `--mode bq_only`)*. |
| **Dataplex Universal Catalog** *(Gate 5)* | Google Cloud Dataplex enabled (`dataplex.googleapis.com`) to seed or inspect Universal Catalog entries, aspect types (`semantic-curation`), Business Glossaries, Glossary Terms, and `definition` EntryLinks (`demo-create catalog seed` / `inspect`). |
| **Gemini Enterprise (GE) Instance** *(Gate 12)* | An active **Gemini Enterprise / Discovery Engine App** in Google Cloud Console (`global`, `us`, or `eu` region) to receive published Looker Conversational Analytics (CA) Agents. |
| **Node.js & `pnpm`** *(Optional — Gate 13)* | Required only when scaffolding and running the standalone React/Vite Embed Portal (`demo-create embed scaffold`). |

---

## 2. Google Cloud (GCP) Required APIs & IAM Roles

`demo-create` interacts with Google Cloud across **four distinct principals**.

### Required GCP APIs

Ensure the following APIs are enabled on your target GCP project:
- `bigquery.googleapis.com` — BigQuery API (dataset creation, Parquet loading, PK/FK constraints, and query validation)
- `bigquerystorage.googleapis.com` — BigQuery Storage Write API (`managedwriter` DefaultStream real-time CDC streaming at Gate 14)
- `dataplex.googleapis.com` — Dataplex API (Universal Catalog entries, `semantic-curation` aspect types, business glossaries, terms, `EntryLinks`, and Data Profile/Quality scans at Gate 5)
- `datacatalog.googleapis.com` — Data Catalog API (Legacy tag templates, policy tags, and catalog search fallback)
- `compute.googleapis.com` — Compute Engine API (Locked-down `e2-micro` VM with `--no-address`, subnet Private Google Access, Cloud Router/NAT, and IAP firewall rules at Gate 14)
- `iap.googleapis.com` — Cloud Identity-Aware Proxy API (`--tunnel-through-iap` SSH and SCP access to private GCE VMs at Gate 14)
- `logging.googleapis.com` — Cloud Logging v2 API (Structured JSON log emission and verification for the GCE streaming daemon at Gate 14)
- `secretmanager.googleapis.com` — Secret Manager API (Storing and accessing streamer dashboard secrets at Gate 14)
- `aiplatform.googleapis.com` — Vertex AI API (LLM/Gemini calls and AI-assisted synthesis/grounding)
- `discoveryengine.googleapis.com` — Discovery Engine API (Gemini Enterprise app discovery and CA Agent publishing at Gate 12)
- `cloudresourcemanager.googleapis.com` — Project number resolution, project listing, and automated IAM policy binding

### GCP IAM Matrix by Principal

| Principal | Where Used | Least-Privilege IAM Roles | Quick-Start / Sandbox Role | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **1. Developer / Local ADC User**<br/>*(Your `gcloud` user account)* | Gates 0–5, Gate 12 & Gate 14 (`pre-check`, `data`, `catalog`, `lookml`, `ge`, `stream`) | • `roles/bigquery.dataEditor`<br/>• `roles/bigquery.jobUser`<br/>• `roles/dataplex.admin` *(or `metadataEditor` + `taxonomyAdmin` for `catalog seed`; `metadataViewer` + `dataScanViewer` for `catalog inspect`)*<br/>• `roles/datacatalog.viewer`<br/>• `roles/compute.instanceAdmin.v1` *(Gate 14 GCE VM lifecycle)*<br/>• `roles/compute.networkAdmin` *(Gate 14 subnet Private Google Access, Cloud Router/NAT & IAP firewall rule)*<br/>• `roles/iap.tunnelResourceAccessor` *(Gate 14 `--tunnel-through-iap` SSH/SCP)*<br/>• `roles/iam.serviceAccountUser` *(Gate 14 attaching Compute SA to VM)*<br/>• `roles/secretmanager.admin` *(Gate 14 streamer secret creation/deletion)*<br/>• `roles/logging.viewer` *(Gate 14 Cloud Logging verification)*<br/>• `roles/aiplatform.user`<br/>• `roles/serviceusage.serviceUsageConsumer`<br/>• `roles/discoveryengine.viewer`<br/>• `roles/resourcemanager.projectIamAdmin` *(to auto-grant Looker GE & GCE Compute SA IAM)* | `roles/editor` + `roles/compute.networkAdmin` + `roles/iap.tunnelResourceAccessor` + `roles/dataplex.admin` + `roles/resourcemanager.projectIamAdmin` | Create BigQuery datasets/tables, load Parquet files, seed/inspect Dataplex Universal Catalog glossaries/aspects/links (`catalog seed`/`inspect`), provision locked-down GCE `e2-micro` streamer VMs (`--no-address` + IAP tunnel) (`stream deploy`/`delete`), discover GE apps, and bind IAM roles for service accounts. |
| **2. GCE Streamer VM Service Account**<br/>*(`<project-number>-compute@developer.gserviceaccount.com`)* | Gate 14 (`stream deploy` runtime on GCE `e2-micro` VM) | • `roles/bigquery.dataEditor`<br/>• `roles/bigquery.jobUser`<br/>• `roles/logging.logWriter`<br/>• `roles/secretmanager.secretAccessor` | Auto-bound by `stream deploy` (`cloud.go`) when Developer has `projectIamAdmin` | Allows the private GCE `e2-micro` daemon (`<dataset>-streamer.service`) to stream rows and CDC mutations (`_CHANGE_TYPE` = `UPSERT`/`DELETE`) via BigQuery Storage Write API over Private Google Access, emit structured JSON logs to Cloud Logging v2, and read Secret Manager configuration. |
| **3. Looker BigQuery Connection SA**<br/>*(Configured in Looker Admin > Connections)* | Gate 9 (`lookml deploy` & inline query validation) & Live Dashboards | • `roles/bigquery.dataEditor`<br/>• `roles/bigquery.jobUser` | `roles/bigquery.admin` | Execute live Looker Explore/Dashboard queries (`run_inline_query`) and materialize Native Derived Tables (NDTs) or Persistent Derived Tables (PDTs) in the scratch schema. |
| **4. Looker Gemini Service Account**<br/>*(`ai_ge_service_account_email` returned by `/api/4.0/gemini_enablement`)* | Gate 12 (`ge configure`, `agent publish`) | • `roles/discoveryengine.admin`<br/>• **Active Gemini Enterprise License** *(assigned in GCP / Workspace Admin)* | `roles/discoveryengine.admin` + **GE License** | Register, synchronize, and publish Looker Conversational Analytics (CA) Agents directly into your connected Gemini Enterprise app. *(Note: `demo-create ge configure` automatically runs the `roles/discoveryengine.admin` binding if your ADC user has `projectIamAdmin`.)* |

### Copy-Paste `gcloud` Setup Script

```bash
export PROJECT_ID="your-gcp-project-id"
export PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
export USER_EMAIL="$(gcloud config get-value account)"
export COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
# Optional: set if already known from Looker Connections / Admin > Gemini
export LOOKER_BQ_SA="your-looker-bq-sa@${PROJECT_ID}.iam.gserviceaccount.com"
export LOOKER_GE_SA="service-${PROJECT_NUMBER}@gcp-sa-looker.iam.gserviceaccount.com"

# 1. Enable required Google Cloud APIs (BigQuery, Storage Write API, Dataplex, GCE, IAP, Logging, Secret Manager, Vertex AI, Discovery Engine)
gcloud services enable \
  bigquery.googleapis.com \
  bigquerystorage.googleapis.com \
  dataplex.googleapis.com \
  datacatalog.googleapis.com \
  compute.googleapis.com \
  iap.googleapis.com \
  logging.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com \
  discoveryengine.googleapis.com \
  cloudresourcemanager.googleapis.com \
  --project="${PROJECT_ID}"

# 2. Grant Developer / Local ADC Roles (BigQuery + Dataplex Curation + Gate 14 GCE/IAP Streamer + Vertex AI + GE Discovery)
for ROLE in \
  roles/bigquery.dataEditor \
  roles/bigquery.jobUser \
  roles/dataplex.admin \
  roles/dataplex.dataScanViewer \
  roles/datacatalog.viewer \
  roles/compute.instanceAdmin.v1 \
  roles/compute.networkAdmin \
  roles/iap.tunnelResourceAccessor \
  roles/iam.serviceAccountUser \
  roles/secretmanager.admin \
  roles/logging.viewer \
  roles/aiplatform.user \
  roles/serviceusage.serviceUsageConsumer \
  roles/discoveryengine.viewer; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="user:${USER_EMAIL}" \
    --role="${ROLE}"
done

# 3. Grant GCE Streamer VM Service Account Roles (Gate 14 `demo-create stream deploy`)
# (Also executed automatically by `stream deploy` if your user has `roles/resourcemanager.projectIamAdmin`)
for ROLE in \
  roles/bigquery.dataEditor \
  roles/bigquery.jobUser \
  roles/logging.logWriter \
  roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${COMPUTE_SA}" \
    --role="${ROLE}" \
    --condition=None
done

# 4. Grant Looker BigQuery Connection SA Roles (if not already configured)
for ROLE in roles/bigquery.dataEditor roles/bigquery.jobUser; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${LOOKER_BQ_SA}" \
    --role="${ROLE}"
done

# 5. Grant Looker Gemini Service Account Discovery Engine Admin
# (Also executed automatically during `demo-create ge configure`)
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${LOOKER_GE_SA}" \
  --role="roles/discoveryengine.admin"
```

### Dataplex Universal Catalog (Knowledge Catalog) & DataScans Permissions Reference

When using `demo-create catalog seed`, `demo-create catalog inspect`, or generating LookML from Dataplex metadata via `demo-create lookml model --catalog <snapshot>`, Google Cloud credentials (ADC or Service Account) require specific permissions:

| Functional Area | Granular IAM Permissions | Pre-Built Role |
| :--- | :--- | :--- |
| **Universal Catalog Seeding (`catalog seed`)** | `dataplex.aspectTypes.create`, `dataplex.aspectTypes.get`, `dataplex.aspectTypes.update`<br/>`dataplex.entries.get`, `dataplex.entries.update`<br/>`dataplex.glossaries.create`, `dataplex.glossaries.get`<br/>`dataplex.glossaryTerms.create`, `dataplex.glossaryTerms.get`<br/>`dataplex.entryLinks.create`, `dataplex.entryLinks.get` | `roles/dataplex.admin` *(or `roles/dataplex.metadataEditor` + `roles/dataplex.taxonomyAdmin`)* |
| **Universal Catalog Entries & Aspects (`catalog inspect`)** | `dataplex.entries.get`<br/>`dataplex.entries.lookup`<br/>`dataplex.entryGroups.get`<br/>`dataplex.aspectTypes.get`<br/>`dataplex.entryTypes.get` | `roles/dataplex.metadataViewer` |
| **Business Glossaries & Terms (`catalog inspect`)** | `dataplex.glossaries.get`<br/>`dataplex.glossaryTerms.get`<br/>`dataplex.glossaryTerms.list` | `roles/dataplex.metadataViewer` |
| **Data Profiling & Quality Scans** | `dataplex.datascans.get`<br/>`dataplex.datascans.list`<br/>`dataplex.datascans.getData`<br/>`dataplex.datascans.listJobs`<br/>`dataplex.datascans.getJob` | `roles/dataplex.dataScanViewer` |
| **Data Catalog Fallback** | `datacatalog.entries.get`<br/>`datacatalog.tagTemplates.get` | `roles/datacatalog.viewer` |

#### OAuth 2.0 Scopes (ADC & Service Accounts)
- **Application Default Credentials (ADC)**: When running `gcloud auth application-default login`, the default `https://www.googleapis.com/auth/cloud-platform` scope grants full access to Dataplex APIs.
- **Dedicated Service Accounts**: If using a custom Service Account key or token with restricted scopes, ensure either:
  - `https://www.googleapis.com/auth/cloud-platform` (recommended)
  - `https://www.googleapis.com/auth/dataplex`

#### CLI Verification Commands
```bash
# Verify Dataplex, BigQuery Storage Write, GCE & IAP APIs are enabled
gcloud services list --enabled --filter="name:(dataplex.googleapis.com OR bigquerystorage.googleapis.com OR compute.googleapis.com OR iap.googleapis.com)"

# Test catalog seeding (dry-run plan) and inspection on a target dataset
demo-create catalog seed --dataset "${DATASET}" --gcp-project "${PROJECT_ID}" --mode plan --json
demo-create catalog inspect --dataset "${DATASET}" --gcp-project "${PROJECT_ID}" --json
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
> **Quick Path (Recommended)**: Assigning the built-in Looker **`Admin`** role to the authenticating user satisfies all Looker gates (Gates 0–13), including project creation, model registration, CA agent creation, Gemini Enterprise publishing, and Embed Portal instance provisioning.

If your organization enforces custom least-privilege Looker roles, grant the following permissions by workflow stage:

| Workflow Stage | Commands Executed | Required Looker Permissions / Role | Why It's Required |
| :--- | :--- | :--- | :--- |
| **Gates 0–1: Pre-Flight & Target Confirmation** | `demo-create pre-check`<br/>`demo-create confirm-targets` | `see_lookml`, `explore` *(or permission to list connections via `all_connections`)* | Verifies Looker OAuth/API session (`sdk.me()`) and enumerates available BigQuery connections. |
| **Gates 6–10: LookML Dev, Polish, Optimization & Production Release** | `demo-create lookml model`<br/>`demo-create lookml certify-polish`<br/>`demo-create lookml optimize`<br/>`demo-create lookml deploy`<br/>`demo-create lookml approve-critique` | **Developer Role** (`develop`, `deploy`, `see_lookml`, `see_lookml_dashboards`, `use_sql_runner`, `explore`, `see_queries`, `save_content`) **+** `manage_project_models` *(or `manage_models`)* | Switches API session to `dev` workspace (`update_session`), creates/updates the bare Git project (`create_project`), registers the LookML model and allowed DB connection (`create_lookml_model`), pushes files via `lkr`, runs `validate_project`, executes 100% of dashboard tile queries (`run_inline_query`), and deploys to production. |
| **Gate 11: Conversational Analytics (CA) Agent & Golden Queries** | `demo-create agent create`<br/>`demo-create agent golden-queries` | **CA Agent Author** (`create_agents` / `manage_agents` or Gemini CA access) **+** `explore`, `create_queries`, `save_content` | Creates base Looker queries (`POST /api/4.0/queries`) to generate deterministic `expanded_share_url` links, registers 1:1 Golden Queries (`POST /api/4.0/golden_queries`), provisions the CA Agent (`POST /api/4.0/agents`), and binds Golden Query IDs (`PATCH /api/4.0/agents/{id}`). |
| **Gate 12: Gemini Enterprise Configuration & Publishing** | `demo-create ge status`<br/>`demo-create ge configure`<br/>`demo-create agent publish` | **Looker `Admin` Role** | Required to read/update instance-level Gemini settings (`GET`/`PATCH /api/4.0/gemini_enablement`) and invoke the internal GE publishing endpoint (`POST /api/4.0/internal/agents/{id}/publish`). |
| **Gate 13: External Embedded Analytics Portal** | `demo-create embed scaffold` | **Looker `Admin` Role** | Provisions Looker embed allowlist, cookieless embed v2, `Embed Shared Users` group, shared folder permissions, `PUT /api/4.0/lookml_dashboards/move`, CA agent sharing, and brand themes. |

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
