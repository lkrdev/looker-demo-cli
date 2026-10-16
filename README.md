# Looker Demo Creator (`demo-create`)

> **From zero to production Looker, Dataplex Knowledge Catalog, and Real-Time BigQuery CDC demo in minutes.**
> Automated, deterministic 15-gate orchestrator for creating end-to-end Looker demos, synthetic or adopted BigQuery datasets, Dataplex Universal Catalog (Knowledge Catalog) semantic curations & business glossaries, 3NF Snowflake LookML semantic models, 100% test-verified executive dashboards, Conversational Analytics (CA) Agents published to Gemini Enterprise (GE), private-labeled Embedded Analytics portals, and real-time BigQuery Storage Write API CDC streaming pipelines on locked-down GCE VMs.

---

## Overview: How `demo-create` Operates

`demo-create` unifies the full-stack data & analytics demo lifecycle into a deterministic **15-Gate State Machine (`Gates 0..14`)** persisted in `.demo-state.json`. It supports two primary deployment modes at **Gate 1 (`confirm-targets`)**:
- **`--mode full` (Default — End-to-End Looker + BigQuery + Catalog + Stream)**: Executes all 15 gates (`0..14`) across BigQuery, Dataplex Knowledge Catalog, LookML modeling/polish/optimization/deployment, Conversational Analytics Agent, Gemini Enterprise, Embedded Portal, and Live BigQuery Streaming.
- **`--mode bq_only` (BigQuery + Knowledge Catalog + Stream Only)**: Executes Gates `0..5` (BigQuery dataset synthesis/adoption + Dataplex Knowledge Catalog curation/seeding), automatically marks Looker Gates `6..13` satisfied, and routes directly to **Gate 14 (`stream`)** without requiring Looker credentials.

### Core Lifecycle Stages
1. **Pre-Flight & Target Confirmation (`pre-check`, `confirm-targets` — Gates 0–1)**: Audits GCP/Looker credentials, prunes deprecated MCP servers/skills, syncs CLI-first skills into `~/.gemini/config/skills/`, and records human-confirmed targets (`--mode full|bq_only`).
2. **Schema Co-Design, Modular DAG Synthesis or Dataset Adoption (`data` — Gates 2–4)**: Designs 3NF relational schemas (`propose-schema --preview` + `approve-schema`) and synthesizes Parquet tables (`>7,500 rows/sec`) loaded into BigQuery with Day Partitioning & Clustering (`generate --upload`), **or** adopts an existing BigQuery dataset (`data adopt`).
3. **Dataplex Knowledge Catalog Semantic Curation (`catalog` — Gate 5)**: Seeds or inspects live Dataplex Universal Catalog `AspectTypes` (`semantic-curation`), Business Glossaries (`<dataset>-glossary`), Glossary Terms, `definition` EntryLinks, and BigQuery `PRIMARY KEY` / `FOREIGN KEY` constraints (`catalog seed` / `inspect` / `sync`), persisting `.demo-catalog-<dataset>.json`.
4. **LookML Modeling, 3-Pass Polish, Optimization & QA Release (`lookml` — Gates 6–10)**: Generates catalog-enriched LookML views/explores/dashboards (`lookml model`), enforces 3-Pass Executive Dashboard Polish & `SELECT DISTINCT` filtered measure grounding (`lookml certify-polish`), applies server caching/partition optimizations (`lookml optimize`), validates 100% of dashboard queries before production deployment (`lookml deploy`), and records post-deploy visual critique approval (`lookml approve-critique`).
5. **Conversational Analytics & Gemini Enterprise (`agent` / `ge` — Gates 11–12)**: Grounds 1:1 Golden Queries (`expanded_share_url`) from dashboard tiles, links them to a Looker CA Agent (`agent create`), and publishes to Gemini Enterprise (`agent publish`).
6. **Private-Labeled Embed Portal (`embed` — Gate 13)**: Provisions Looker instance embed settings (Service Account auth, allowlist, cookieless v2, 2-step shared folder inheritance, LookML dashboard move, CA agent sharing, brand themes) and scaffolds a React/Vite + FastAPI portal (`embed scaffold`).
7. **Real-Time BigQuery Storage Write API & CDC Streaming (`stream` — Gate 14)**: Plans (`stream plan`), scaffolds & customizes a Go `managedwriter` DefaultStream CDC service (`_CHANGE_TYPE` = `UPSERT`/`DELETE`, `max_staleness = INTERVAL 0 SECOND`) driven by live Dataplex Knowledge Catalog metadata, and deploys it to a locked-down GCE `e2-micro` VM (`--no-address` private IP + IAP tunnel + `<dataset>-streamer.service`) (`stream deploy --execute-infra`), with single-command teardown (`stream delete`).

---

## What Gets Automated: Production Deliverables Inventory

| Production Asset | What Gets Automated | Exact Output Format |
| :--- | :--- | :--- |
| **BigQuery Data Warehouse** | 3NF relational schema synthesis (Modular DAG with Pareto/Log-Normal distributions, PK/FK referential integrity, and in-memory `TableValidator` scorecard), batch Parquet upload, or live dataset adoption (`data adopt`) | Clean BigQuery dataset with partitioned/clustered tables & `NOT ENFORCED` PK/FK constraints |
| **Dataplex Knowledge Catalog** | Hybrid curation (`--curation-file` / `--schema-file` + live BigQuery auto-inference): creates custom `semantic-curation` `AspectType`, Business Glossary & Terms, column-to-term `definition` `EntryLinks`, and table/column aspects | Live Dataplex resources + `.demo-catalog-<dataset>.json` (`rich` / `hybrid` / `minimal` profiles) |
| **LookML 3NF Semantic Model (Layered)** | Catalog-driven layered LookML generation (`SPEC.md`, `--layered` default), Day-2 non-destructive `catalog sync --layered [--dry-run]`, Explore Base View selection, Chasm Trap elimination with Native Derived Table (NDT) rollups joined `one_to_one`, role-playing diamond joins, and curated field metadata | Physical `views/base/*.view.lkml`, semantic `views/refinements/*.view.lkml` (`+view_name`), `explores/*.explore.lkml`, and `models/*.model.lkml` |
| **Executive Tabbed Dashboard** | Mandatory `lookml-dashboard-designer` subagent handoff via `invoke_subagent` (loading `looker-visualizations` skills), 3-Pass Executive Dashboard Polish, Highcharts `advanced_vis_config` geometry, `SELECT DISTINCT`-grounded filtered measures in `views/refinements/*.view.lkml`, and `lookml certify-polish` gate verification | Production `dashboards/*.dashboard.lookml` deployed via API |
| **LookML Performance Optimization** | Static `suggestions: [...]` on low-cardinality dims, `suggestable: no` on unique IDs/text, model datagroup caching, BigQuery partition pruning filters, and headless `.backup_pre_opt` rollback snapshots (`lookml restore`) | Production-hardened LookML avoiding database query spikes |
| **Pre-Deployment QA Audit** | Dev branch push, LookML project validator, 100% test execution of all dashboard queries via Looker API, and bounded self-healing (max 3 iterations via `lookml-qa-validator`) | 100% HTTP 200 OK query pass certificate before production release |
| **Conversational Analytics (CA) Agent** | Auto-generated domain persona and query rules, extraction of dashboard tiles into 1:1 Looker 4.0 Golden Queries with `expanded_share_url` grounding | Live AI Agent in Looker with natural language chat UI |
| **Gemini Enterprise (GE) Integration** | Automated registration, IAM binding (`roles/discoveryengine.admin`), and one-click publishing to connected Gemini Enterprise apps | Natural language querying across enterprise Gemini apps in minutes |
| **Private-Labeled Embed Portal (Optional)** | 6-point Looker instance provisioning (group, folder, `PUT /api/4.0/lookml_dashboards/move`, themes) + React/Vite & FastAPI scaffolding (`looker-embed-demo`) | Complete web application ready to run (`pnpm install && pnpm dev`) |
| **Real-Time BigQuery CDC Streamer (Optional)** | Catalog-aware Go `managedwriter` Storage Write API streamer (`insert_only`, `upsert_cdc`, `full_crud`), `max_staleness = INTERVAL 0 SECOND` DDL, and locked-down GCE `e2-micro` VM (`--no-address` + Cloud NAT + `--tunnel-through-iap`) | Live `<dataset>-streamer.service` systemd daemon on private GCE VM + `stream delete` teardown |

---

## Subagent Invocation Matrix (`looker-demo-orchestrator`)

`demo-create` follows a **CLI-First Architecture with Explicit & Conditional Subagents**. Deterministic operations run directly via compiled CLI subcommands in the parent session, while specialized creative, modeling, healing, and multi-file Go engineering tasks delegate to isolated subagents under [`skills/looker-demo-orchestrator/subagents/`](skills/looker-demo-orchestrator/subagents/):

| Gate | Stage | Subagent(s) | Trigger Condition (Mandatory vs. Conditional) | Owned Files & Responsibility |
| :--- | :--- | :--- | :--- | :--- |
| **Gate 4**<br/>(`gate_1c_generate_data`) | Modular DAG Synthesis & BigQuery Load | [`data-engineer`](skills/looker-demo-orchestrator/subagents/data-engineer.md) | **On-Demand / Optional**<br/>*(Parent CLI `demo-create data generate --upload` is default; spawn subagent when custom domain synthesis scripts or complex repair are needed)* | Synthesizes Parquet datasets (`>7,500 rows/sec`), validates PK/FK invariants via `TableValidator`, and uploads to BigQuery with Day Partitioning & Clustering. |
| **Gate 6**<br/>(`gate_2a_lookml_model`) | Semantic LookML Modeling | [`lookml-snowflake-modeler`](skills/looker-demo-orchestrator/subagents/lookml-snowflake-modeler.md) | **Conditional**<br/>*(Spawned **ONLY** when the schema contains 3NF Snowflake structures with Chasm Traps—multiple `1:N` child fanouts—or diamond role-playing joins)* | Runs `schema_graph_analyzer.py`, sets Explore Base Views on leaf facts, builds Native Derived Table (NDT) rollups joined `one_to_one`, and resolves diamond joins in `lookml/`. |
| **Gate 7**<br/>(`gate_2b_certify_polish`) | 3-Pass Executive Dashboard Polish | [`lookml-dashboard-designer`](skills/looker-demo-orchestrator/subagents/lookml-dashboard-designer.md) | **Mandatory at Gate 7 (`invoke_subagent`)**<br/>*(Must be invoked via `invoke_subagent` to load `looker-visualizations` skills, author domain KPIs in `views/refinements/*.view.lkml`, and transform raw `LookMLGenerator` draft scaffolding into a bespoke, 3-pass polished dashboard before `demo-create lookml certify-polish` will pass)* | Customizes `lookml/dashboards/*.dashboard.lookml` and `lookml/views/refinements/*.view.lkml` with domain KPIs, Highcharts `advanced_vis_config`, `SELECT DISTINCT` filtered measures, and transparent grids, then runs `lookml certify-polish`. |
| **Gate 9**<br/>(`gate_3b_deploy`) | Pre-Deployment QA & Production Release | [`lookml-qa-validator`](skills/looker-demo-orchestrator/subagents/lookml-qa-validator.md) | **Conditional (Self-Healing)**<br/>*(Spawned **ONLY** if `demo-create lookml deploy` fails LookML validation or any dashboard tile query test fails)* | Runs up to 3 bounded self-healing loops using `lookml-dashboard-to-query` to patch broken LookML fields/tiles and returns a deploy readiness certificate to the parent orchestrator. |
| **Gate 13**<br/>(`gate_6_embed`) | External Embed Portal Scaffolding | [`embed-portal-engineer`](skills/looker-demo-orchestrator/subagents/embed-portal-engineer.md) | **Conditional (When Gate 13 Confirmed)**<br/>*(Spawned **ONLY** when the user confirms external embed portal creation rather than `--skip`)* | Audits `group_id`/`folder_id` sync after `demo-create embed scaffold`, adapts domain copy (`Sidebar.tsx`, `LoginPage.tsx`, `Home.tsx`, `SalesActivityFeed.tsx`), customizes `styles.css`, and verifies `pnpm run build`. |
| **Gate 14**<br/>(`gate_7_stream`) | Live BigQuery Streaming & CDC (Phase A $\to$ Phase B) | 1. [`bq-profiling-researcher`](skills/looker-demo-orchestrator/subagents/bq-profiling-researcher.md)<br/>2. [`catalog-spec-architect`](skills/looker-demo-orchestrator/subagents/catalog-spec-architect.md)<br/>3. [`go-bq-cdc-engineer`](skills/looker-demo-orchestrator/subagents/go-bq-cdc-engineer.md)<br/>4. [`go-sim-cli-engineer`](skills/looker-demo-orchestrator/subagents/go-sim-cli-engineer.md) | **Mandatory 4-Agent Parallel Wave (When Gate 14 Confirmed)**<br/>*(Dispatched concurrently right after Phase A `demo-create stream deploy --scaffold-only` and before Phase B `demo-create stream deploy --execute-infra`)* | • **Agent 1 (`bq-profiling-researcher`)**: `.demo-stream-profile.json`, `streamer/seed_data.json`<br/>• **Agent 2 (`catalog-spec-architect`)**: `SPEC.md` (Section 4B), `.demo-stream-plan.json`, catalog metadata alignment<br/>• **Agent 3 (`go-bq-cdc-engineer`)**: `streamer/internal/bq/*`, `streamer/internal/model/rows.go`, `streamer/bq.go`, `streamer/bq_test.go`, `streamer/schema.sql`<br/>• **Agent 4 (`go-sim-cli-engineer`)**: `streamer/internal/sim/*`, `streamer/main.go`, `streamer/cloud.go`, `streamer/sim.go`, `streamer/deploy-bq.sh`, `streamer/<dataset>-streamer.service` |

---

## Prerequisites

Before running `demo-create`, ensure the following tools, cloud roles, and Looker settings are in place. For the full **IAM Principal Matrix**, **copy-paste `gcloud` setup script**, **granular Looker role permissions**, **OAuth client registration**, and **SSH port-forwarding guide**, see **[`docs/PREREQUISITES.md`](docs/PREREQUISITES.md)**.

| Category | Required Setup |
| :--- | :--- |
| **1. Local CLI & Infrastructure** | • **Python `>= 3.11` & [`uv`](https://docs.astral.sh/uv/)** (`uv tool install looker-demo-cli` installs both `demo-create` and `lkr-dev-cli`).<br/>• **Google Cloud SDK (`gcloud` & `bq`)** authenticated via `gcloud auth login` and `gcloud auth application-default login`.<br/>• **Go `>= 1.22`** *(required only when deploying the Gate 14 BigQuery CDC streamer)*.<br/>• **Looker Instance** (API 4.0) with a pre-configured **BigQuery Connection** *(required for `--mode full`, optional for `--mode bq_only`)*. |
| **2. Google Cloud APIs & IAM** | • **APIs**: `bigquery`, `bigquerystorage`, `dataplex`, `datacatalog`, `compute`, `iap`, `logging`, `secretmanager`, `aiplatform`, `discoveryengine`, `cloudresourcemanager`.<br/>• **Developer / Local ADC**: `roles/bigquery.dataEditor`, `roles/bigquery.jobUser`, `roles/dataplex.admin` (or `metadataEditor` + `taxonomyAdmin` for `catalog seed`), `roles/compute.instanceAdmin.v1`, `roles/compute.networkAdmin`, `roles/iap.tunnelResourceAccessor`, `roles/iam.serviceAccountUser`, `roles/secretmanager.admin` (for Gate 14 GCE streamer), `roles/discoveryengine.viewer`, and `roles/resourcemanager.projectIamAdmin`.<br/>• **GCE Streamer VM SA** (`<project-number>-compute@developer.gserviceaccount.com`, Gate 14): `roles/bigquery.dataEditor`, `roles/bigquery.jobUser`, `roles/logging.logWriter`, `roles/secretmanager.secretAccessor` *(auto-bound by `stream deploy` when caller has `projectIamAdmin`)*.<br/>• **Looker BigQuery SA**: `roles/bigquery.dataEditor` + `roles/bigquery.jobUser`.<br/>• **Looker Gemini SA** (`ai_ge_service_account_email`): `roles/discoveryengine.admin` + an assigned **Gemini Enterprise License**. |
| **3. Looker Permissions** | • **Instance Settings**: `lkr-cli` OAuth app registered (`register_oauth_client_app`) and **Admin > Platform > Gemini** enabled (CA + Publish to GE).<br/>• **User Role**: Looker **`Admin`** role *(Recommended for all gates)*. |

👉 **[Full Setup, IAM Matrix & `gcloud` Bootstrap Script → `docs/PREREQUISITES.md`](docs/PREREQUISITES.md)**

---

## Getting Started: Two Ways to Build

### 🚀 Mode 1: AI Agent Pair-Programmer (Primary Hero Flow)

Run interactively with your AI coding assistant using the **[`looker-demo-orchestrator`](skills/looker-demo-orchestrator/SKILL.md)** skill.

#### Step 1: Install Persistent CLI Tools
```bash
uv tool install looker-demo-cli
# Or from a local checkout during development:
uv tool install --editable /path/to/looker-demo-cli --force
```
Installs `demo-create`, `looker-demo-cli`, and `lkr` (`lkr-dev-cli`) globally across terminal sessions.

#### Step 2: Run Pre-Flight Audit & Auto-Fix
```bash
demo-create pre-check --fix
```
Verifies GCP/Looker credentials, prunes deprecated MCP servers (`data-designer`, `bigquery`, `knowledge-catalog`), and syncs CLI-first skills.

#### Step 3: Launch the AI Demo Creation Flow
Instruct your AI assistant in chat:
> *"Create an end-to-end Looker demo for Mobile Gaming Telemetry (or IoT Fleet Analytics, SaaS ARR, Retail, Fintech)."*

---

### 💻 Mode 2: Standalone CLI (15-Gate Headless Engine)

Execute `demo-create` directly from your terminal or CI/CD pipeline, one gate at a time. State is persisted to `.demo-state.json` between calls:

```bash
# Gates 0–1: Pre-check & Target Confirmation
demo-create pre-check --fix --gcp-project my-gcp-project
demo-create confirm-targets --mode full --gcp-account me@example.com --gcp-project my-gcp-project \
  --looker-account prod --connection my_bq_connection --dataset mobile_gaming_analytics

# Gates 2–4: Propose/Approve Schema & Generate/Upload Data (or `demo-create data adopt --dataset ...`)
demo-create data propose-schema --schema-file schema.json --preview
demo-create data approve-schema --row-count 25000 --dataset mobile_gaming_analytics
demo-create data generate --schema-file schema.json --row-count 25000 --gcp-project my-gcp-project \
  --dataset mobile_gaming_analytics --upload --json-scorecard

# Gate 5: Seed or Inspect Dataplex Knowledge Catalog (Glossaries, Terms, Aspects, EntryLinks)
demo-create catalog seed --dataset mobile_gaming_analytics --gcp-project my-gcp-project --mode execute

# Gates 6–10: LookML Model, 3-Pass Polish Certification, Optimizer, QA Deploy & Visual Critique
demo-create lookml model --looker-project mobile_gaming --dataset mobile_gaming_analytics \
  --connection my_bq_connection --gcp-project my-gcp-project --profile rich
demo-create lookml certify-polish --lookml-dir lookml
demo-create lookml optimize --lookml-dir lookml
demo-create lookml deploy --looker-project mobile_gaming --lookml-dir lookml
demo-create lookml approve-critique --looker-project mobile_gaming

# Gates 11–13: Conversational Analytics Agent, Gemini Enterprise Publish & Embed Portal
demo-create agent create --model mobile_gaming --explore fct_game_sessions --dashboards-dir lookml/dashboards
demo-create agent publish --agent-id <agent-id>
demo-create embed scaffold --looker-project mobile_gaming --skip

# Gate 14: Real-Time BigQuery Storage Write API & CDC Streamer on Locked-Down GCE (--no-address + IAP)
demo-create stream plan --gcp-project my-gcp-project --dataset mobile_gaming_analytics \
  --target-table fct_game_sessions --frequency 1m --crud-mode upsert_cdc --backfill-mode skip
demo-create stream deploy --gcp-project my-gcp-project --dataset mobile_gaming_analytics --scaffold-only
# (After 4-agent customization of ./streamer)
demo-create stream deploy --gcp-project my-gcp-project --dataset mobile_gaming_analytics --execute-infra
```

---

## The 15-Stage Gate Reference (`demo-create status`)

Use `demo-create status` (or `demo-create status --json`) at any point to inspect completed gates and get the literal next command to run:

<!-- BEGIN GENERATED GATES TABLE -->
| Gate | ID | Stage | Human Pause? | Canonical CLI Command |
| ---: | :--- | :--- | :---: | :--- |
| **0** | `gate_0a_precheck` | Environment, credential & skill-tree audit | `false` | `demo-create pre-check --fix --gcp-project <gcp-project>` |
| **1** | `gate_0b_confirm_targets` | 4-target environment confirmation | **`true`** | `demo-create confirm-targets --gcp-account <gcp-account> --gcp-project <gcp-project> --looker-account <looker-account> --connection <connection-name>` *([--mode full\|bq_only])* |
| **2** | `gate_1a_propose_schema` | Schema blueprint authoring & 5-row micro-sample preview | `false` | `demo-create data propose-schema --schema-file <schema-file> --preview` |
| **3** | `gate_1b_approve_schema` | Schema ERD & target row volume approval | **`true`** | `demo-create data approve-schema --row-count <row-count> --dataset <dataset-id>` |
| **4** | `gate_1c_generate_data` | Modular DAG synthesis & BigQuery load | `false` | `demo-create data generate --schema-file <schema-file> --row-count <row-count> --gcp-project <gcp-project> --dataset <dataset-id> --upload --json-scorecard` |
| **5** | `gate_1d_catalog` | Knowledge Catalog semantic curation & enrichment | **`true`** | `demo-create catalog seed --dataset <dataset-id> --gcp-project <gcp-project>` *(or `catalog inspect` / `catalog sync` / `--skip`)* |
| **6** | `gate_2a_lookml_model` | Semantic LookML modeling & draft dashboard scaffolding | `false` | `demo-create lookml model --looker-project <looker-project> --dataset <dataset-id> --connection <connection-name> --gcp-project <gcp-project> --layered` |
| **7** | `gate_2b_certify_polish` | 3-Pass Executive Dashboard Polish & filtered measure audit | **`true`** | `demo-create lookml certify-polish --lookml-dir <lookml-dir>` |
| **8** | `gate_3a_optimize` | LookML Server Performance Optimizer gate | **`true`** | `demo-create lookml optimize --lookml-dir <lookml-dir>` *(or `--skip`)* |
| **9** | `gate_3b_deploy` | Pre-deployment LookML validation, query testing & production release | `false` | `demo-create lookml deploy --looker-project <looker-project> --lookml-dir <lookml-dir>` |
| **10** | `gate_3c_critique` | Post-deploy dashboard screenshot critique (Pass 3) | **`true`** | `demo-create lookml approve-critique --looker-project <looker-project>` |
| **11** | `gate_4_agent` | Conversational Analytics agent provisioning & golden queries | **`true`** | `demo-create agent create --model <model-name> --explore <explore-name>` *(or `--skip`)* |
| **12** | `gate_5_publish` | Gemini Enterprise publishing | **`true`** | `demo-create agent publish --agent-id <agent-id>` *(or `--skip`)* |
| **13** | `gate_6_embed` | External Embedded Analytics portal scaffolding | **`true`** | `demo-create embed scaffold --looker-project <looker-project>` *(or `--skip`)* |
| **14** | `gate_7_stream` | Live BigQuery streaming & CDC GCE deployment (optional) | **`true`** | `demo-create stream plan --gcp-project <gcp-project> --dataset <dataset-id>` *(or `stream deploy` / `stream delete` / `--skip`)* |
<!-- END GENERATED GATES TABLE -->

---

## Commands Quick Reference

| Command Group | Key Subcommands | Purpose |
| :--- | :--- | :--- |
| **`status`** | `demo-create status [--json]` | Reports `completed_gates`, `current_gate` (`0..14`), `requires_human_confirmation`, and `next_command`. |
| **`pre-check`** | `demo-create pre-check [--fix] [--json]` | Audits GCP/Looker auth, prunes deprecated MCP servers/skills, and syncs skills into `~/.gemini/config/skills/`. |
| **`confirm-targets`** | `demo-create confirm-targets [--mode full\|bq_only]` | Records human-confirmed GCP account, GCP project, Looker account, DB connection, and deployment mode. |
| **`data`** | `propose-schema`, `approve-schema`, `generate`, `upload`, `adopt`, `inspect` | Co-designs 3NF schemas, synthesizes Parquet files, loads BigQuery datasets with Day Partitioning & Clustering, or adopts existing BigQuery datasets. |
| **`catalog`** | `seed`, `inspect`, `sync`, `profiles` | Seeds live Dataplex Knowledge Catalog `AspectTypes`, Glossaries, Terms, and `EntryLinks` (hybrid `--curation-file` + BigQuery auto-inference), inspects existing catalog metadata into `.demo-catalog-<dataset>.json`, or syncs catalog metadata into LookML refinements. |
| **`lookml`** | `model`, `certify-polish`, `optimize`, `restore`, `clean-root`, `deploy`, `approve-critique` | Generates catalog-aware LookML, certifies 3-Pass Dashboard Polish, applies performance optimizations (with `.backup_pre_opt` snapshots), validates 100% of queries, and deploys to production. |
| **`agent`** | `create`, `golden-queries`, `publish` | Provisions Looker CA AI Agents, extracts & links 1:1 Golden Queries (`expanded_share_url`), and publishes to Gemini Enterprise. |
| **`ge`** | `status`, `configure`, `publish` | Inspects Looker Gemini enablement, discovers GCP Discovery Engine apps, binds `roles/discoveryengine.admin` to the Looker SA, and publishes agents. |
| **`embed`** | `scaffold` | Provisions Looker embed settings (group, folder, dashboard move, CA agent share, themes) and scaffolds a branded React/Vite + FastAPI `looker-embed-demo` portal. |
| **`stream`** | `plan`, `deploy`, `delete` | Plans, scaffolds (`--scaffold-only`), deploys (`--execute-infra`), and tears down (`delete`) a catalog-driven Go BigQuery Storage Write API & CDC streamer on a locked-down GCE `e2-micro` VM (`--no-address` + IAP + `<dataset>-streamer.service`). |
| **`env`** | `init`, `run-script`, `python` | Initializes a workspace `.venv` or executes ad-hoc Python scripts inside the CLI's bundled environment. |

---

## Documentation

| Document | Purpose |
| :--- | :--- |
| **[`docs/PREREQUISITES.md`](docs/PREREQUISITES.md)** | **Prerequisites, GCP IAM & Looker Permissions** — Principal IAM matrix, copy-paste `gcloud` setup script, Looker role permissions by gate, OAuth setup, and GE provisioning. |
| **[`docs/COMMANDS.md`](docs/COMMANDS.md)** | **Full command reference** — Every command, flag, and default, generated from the implementation and enforced by a CI drift test. |
| **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** | **Architecture & Invariants** — How the CLI is layered, skill organization, and the invariants a change must preserve. |
| **[`CONTRIBUTING.md`](CONTRIBUTING.md)** | Setup, the local gate, and how to add a command. |
| **[`AGENTS.md`](AGENTS.md)** | Instructions for an AI agent orchestrating a build. |
