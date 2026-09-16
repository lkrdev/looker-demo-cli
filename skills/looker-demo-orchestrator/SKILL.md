---
name: looker-demo-orchestrator
description: Master orchestration skill for designing, generating, modeling, and deploying end-to-end Looker and Embedded Analytics demos using the `demo-create` CLI, `lkr-dev-cli`, and Code Mode.
---

# Looker Demo Orchestrator (`demo-create`)

This skill defines the mandatory operational procedure for an AI agent or engineer creating full-stack data demos on Google Cloud BigQuery and Looker.

> [!CAUTION]
> **CRITICAL RULE: THE GATES ARE MANDATORY AND MUST NOT BE BATCHED.**
> There is deliberately no single command that builds a demo end to end -- the monolithic `demo-create run` was removed in 0.3.0 precisely because it bypassed iterative schema co-design and volume validation. The agent **MUST** orchestrate the gated subcommands interactively stage-by-stage as detailed below, pausing for human confirmation where required.

---

## Core Execution Architecture: CLI-First with On-Demand Subagents

To eliminate subagent initialization drag, serialization latency, and background file-write race conditions, the workflow follows a **CLI-First Architecture with On-Demand Subagents**:

```mermaid
graph TD
    Start([User Request]) --> PreCheck["Orchestrator: Turn-1 Pre-Check<br/>(demo-create pre-check --json)"]
    PreCheck --> Gate0{"Human Gate 0: Instant 4-Target Alignment<br/>(ask_question - NO plan before this!)"}
    Gate0 -->|Co-Design Phase 1| Phase1["Orchestrator: Schema & ERD Proposal<br/>(Mermaid ERD in Chat)"]
    Phase1 -->|Co-Design Phase 2| Phase2["Orchestrator: Micro-Sample Preview<br/>(5-10 Sample Rows in Chat)"]
    Phase2 --> Gate1{"Human Gate 1: Scale & Volume Confirmation<br/>(ask_question)"}
    Gate1 -->|Fast-Path CLI| GenBQ["Orchestrator: Batch Synthesis & BQ Upload<br/>(demo-create data generate + upload)"]
    GenBQ --> ModelCLI["Gate 2A: Semantic Modeling & SELECT DISTINCT Grounding<br/>(demo-create lookml model + lookml-filtered-measures)"]
    ModelCLI --> SnowflakeBranch{"Is Schema 3NF Snowflake<br/>with Chasm Traps?"}
    SnowflakeBranch -->|Yes: Spawn Subagent| S_Snowflake["Subagent: lookml-snowflake-modeler<br/>(NDT Rollups & Chasm Trap Elimination)"]
    SnowflakeBranch -->|No: Standard Star Schema| DashDesign["Gate 2B: Mandatory Dashboard Polish<br/>(lookml-dashboard-designer 3-Pass Protocol)"]
    S_Snowflake --> DashDesign
    DashDesign --> CleanRoot["Gate 3A: Clean Root Orphan Duplicates<br/>(demo-create lookml clean-root)"]
    CleanRoot --> GateOpt{"Gate 3B: Run Performance Optimizer?<br/>(Guarded ask_question)"}
    GateOpt -->|Yes: Confirmed| OptCLI["Orchestrator: Performance Optimizer<br/>(demo-create lookml optimize --backup)"]
    GateOpt -->|No: Skipped| DeployCLI["Gate 3C: Pre-Deployment QA & Release<br/>(demo-create lookml deploy + Filtered Measure Auditor)"]
    OptCLI --> DeployCLI
    DeployCLI --> QAStatus{"LookML & Query Validation<br/>Passed 100%?"}
    QAStatus -->|Fail: Spawn Subagent| S_QA["Subagent: lookml-qa-validator<br/>(Max 3 Self-Healing Loops)"]
    S_QA -->|Certified Ready| DeployProd["Orchestrator: Production Release<br/>(lkr tools lookml deploy)"]
    QAStatus -->|Pass 100%| DeployProd
    DeployProd --> GateCritique{"Gate 3D: Post-Deploy Screenshot Critique<br/>(ask_question: Approve or Share Screenshot)"}
    GateCritique -->|User Shares Screenshot| Pass3Refine["Pass 3 Visual Critique & Refinement<br/>(view_file -> Refine LookML -> Redeploy)"]
    Pass3Refine --> GateCritique
    GateCritique -->|Approved| GateCA{"Human Gate 4: Provision CA Agent?<br/>(ask_question)"}
    GateCA -->|Yes| CA_CLI["Orchestrator: CA Agent & Golden Queries<br/>(demo-create agent create)"]
    GateCA -->|No| DeliveryReport["Orchestrator: Final Delivery Report<br/>(DELIVERY_REPORT.md)"]
    CA_CLI --> GateGE{"Human Gate 5: Publish to Gemini Enterprise?<br/>(ask_question)"}
    GateGE -->|Yes| GE_CLI["Orchestrator: Publish to GE<br/>(demo-create agent publish)"]
    GateGE -->|No| DeliveryReport
    GE_CLI --> DeliveryReport
```

| Component | Execution Mode | Responsibility & Scope |
|---|---|---|
| **Parent Orchestrator** | Direct Parent Turn | Interactive co-design gates (`ask_question`), fast CLI subcommands (`demo-create data`, `lookml model`, `lookml optimize`, `lookml deploy`, `agent create`), state machine, post-deploy screenshot critique loop, and final delivery report. |
| [`demo-spec`](../demo-spec/SKILL.md) | **Companion Core Skill** | Asynchronously creates and maintains `SPEC.md` as the living technical architecture document across all gates, updating quietly on disk without chat dumping. |
| [`lookml-filtered-measures`](../lookml-filtered-measures/SKILL.md) | **Companion Core Skill** | Enforces mandatory `SELECT DISTINCT` grounding before writing any `filters: [...]` in LookML measures, preventing `0`/`NULL` ratios. |
| [`data-engineer`](subagents/data-engineer.md) | **On-Demand Subagent** | Synthesizes full-volume Parquet datasets via local subagent-authored Python (zero Vertex AI dependency) and loads tables into BigQuery. |
| [`lookml-snowflake-modeler`](subagents/lookml-snowflake-modeler.md) | **On-Demand Subagent** | Spawned ONLY when schemas contain complex 3NF snowflake structures with Chasm Traps (multiple 1:N children), diamond joins, or require Native Derived Table (NDT) rollups. |
| [`lookml-dashboard-designer`](subagents/lookml-dashboard-designer.md) | **Mandatory Gate 2 Subagent** | Executes the 3-Pass Executive Dashboard Polish protocol (theme-inheriting `type: text` headers, centered legends, independent dual-axis formatting, transparent grids, and screenshot critique). |
| [`lookml-qa-validator`](subagents/lookml-qa-validator.md) | **On-Demand Subagent** | Spawned ONLY when `demo-create lookml deploy` encounters validation errors or failing queries; runs up to 3 self-healing loops via `lookml-dashboard-to-query`. |
| [`embed-portal-engineer`](subagents/embed-portal-engineer.md) | **On-Demand Subagent** | Spawned ONLY if external embed demo portal is requested by user. |

> [!IMPORTANT]
> ### Subagent Lifecycle Kill-Fence & Headless Rollback Protocol
> 1. **Subagent Kill-Fence**: Before any file revert, rollback, or manual code restoration, the orchestrator **MUST kill all running subagents** via `manage_subagents(Action='kill_all')` (or `manage_subagents(Action='kill', ConversationIds=[...])`) to prevent background tasks from overwriting restored files.
> 2. **Headless Directory Snapshots**: In headless environments without Git tracking, `demo-create lookml optimize` automatically snapshots pre-optimization files to `lookml/.backup_pre_opt`. If the user rejects optimization or requests a rollback, execute `demo-create lookml restore --lookml-dir <dir>` to cleanly restore files in 1 command.
> 3. **Artifact Boundaries**: `ArtifactMetadata` in `write_to_file` is strictly for files inside `<appDataDir>/brain/<conversation-id>/`. For workspace files, never supply `ArtifactMetadata`.
> 4. **Living `SPEC.md` Quiet Maintenance**: Throughout every conversation and across all gates, the orchestrator quietly maintains `SPEC.md` in the workspace root following [`demo-spec`](../demo-spec/SKILL.md). Never dump `SPEC.md` into chat unless a significant structural change was made. `DELIVERY_REPORT.md` must always link to `SPEC.md`.
> 5. **Mandatory `.gitignore` Verification for `.env` Files**: Whenever creating or modifying a `.env` file (e.g., Looker API credentials, GCP settings, or Embed Portal environment), confirm that `.gitignore` exists in the working directory and includes `.env` (and `**/.env`) before writing credentials to disk.

---

## Orientation: `demo-create status` Is the Source of Truth

Before deciding what to do next -- at the start of a session, after any
interruption, and between every gate -- run:

```bash
demo-create status --json
```

The envelope reports:

| Field | Use |
| :--- | :--- |
| `completed_gates` | Which stages are already done; never redo one. |
| `current_gate` | Where the build actually is. |
| `next_command` | The literal command to run, with every known value substituted and `<placeholder>` where a value is still needed. |
| `requires_human_confirmation` | **Branch on this.** When `true`, call `ask_question` with the gate's `human_checkpoint` *before* running `next_command`. |
| `is_complete` | When `true`, the build is finished and `next_actions` is empty. |

The gate sections below explain *why* each gate exists and precisely what to
confirm with the user. `demo-create status` tells you *where you are*. Prefer the
command for state and this document for intent -- the command is generated from
the implementation and cannot drift from it.

Every command also accepts `--state-file` to target an explicit
`.demo-state.json`, and `--json` to emit the machine-readable envelope. All
failures carry a distinct exit code (`AuthError`=3, `ConfigError`=4,
`RemoteApiError`=5, `ValidationError`=6, `StateError`=7), so branch on the exit
code rather than parsing prose.

## 0. Bootstrap on Fresh Machines (Mandatory Step 0)

If `demo-create` is not available on `PATH`, the agent **MUST immediately run**:
```bash
uv tool install looker-demo-cli
```
Immediately after installation, the agent **MUST run**:
```bash
demo-create pre-check --fix
```
This guarantees all pinned dependencies, MCP servers (`data-designer`, `bigquery`, `knowledge-catalog`), and global agent skills (`~/.gemini/config/skills/`) are synchronized before executing any other commands.

---

## 1. Pre-Flight Environment Inspection & Interactive Confirmation Gate

Always execute the pre-check inspection first to inspect GCP credentials, available projects, Looker OAuth sessions, and MCP tools:

```bash
demo-create pre-check --json
lkr auth list
```

> [!CAUTION]
> ### 🛑 Mandatory Pre-Flight Hard Stop & Immediate Auth Fail Gate
> 1. **Immediate Fail on Missing Auth**: If `demo-create pre-check` exits with code **3** (`AUTH_ERROR`) or reports `data.is_blocked: true`:
>    - **GCP Missing / Unauthenticated**: STOP immediately. Prompt the user to run:
>      ```bash
>      gcloud auth login
>      gcloud auth application-default login
>      gcloud config set project <PROJECT_ID>
>      ```
>    - **Looker Unauthenticated**: Prompt the user to run `lkr auth login` (or configure API keys).
>      If `lkr-cli` OAuth client is not registered on the Looker instance, provide:
>      - **API Explorer URL**: `https://<your-instance>/extensions/marketplace_extension_api_explorer::api-explorer/4.0/methods/Auth/register_oauth_client_app`
>      - **Client ID**: `lkr-cli`
>      - **Request Body JSON**:
>        ```json
>        {
>          "redirect_uri": "http://localhost:8000/callback",
>          "display_name": "LKR",
>          "description": "lkr.dev language server, MCP and CLI",
>          "enabled": true
>        }
>        ```
>      - **Remote Host / SSH Tunneling**: If operating on a remote machine /   / VM, remind the user to forward port 8000:
>        ```bash
>        ssh -L 8000:localhost:8000 <remote-host>
>        ```
>      - **Port 8000 Conflict Cleanup**: Free port 8000 before running `lkr auth login`:
>        ```bash
>        lsof -ti:8000 | xargs kill -9   # (or: fuser -k 8000/tcp)
>        ```
>      - **Headless / Agent OAuth Callback Fallback**: If the user's browser redirects to `http://localhost:8000/callback?code=...` and cannot load the page, the user can paste the full callback URL into the chat so the agent can curl it locally.
>    - **DO NOT proceed** until authentication is re-verified via `demo-create pre-check --fix`.
> 2. **DO NOT execute any further tool calls** (e.g. do not probe database connections, inspect models, or test SDK commands).
> 3. **IMMEDIATELY invoke `ask_question`** in the very next step to prompt the user to confirm all 4 targets below.
> 4. If `available_connections` is empty in `pre-check`, provide standard recommendations (e.g. `looker_demo_bigquery`, `default_bigquery_connection`) along with a write-in option rather than trying to query Looker first.
> 5. Only proceed to Phase 1 (Schema Proposal) after the user has explicitly submitted their answers.

### Mandatory Interactive Confirmation Checklist:
Before designing schemas, creating BigQuery datasets, or touching Looker, the agent **MUST explicitly prompt the user** (via `ask_question` or interactive prompt) to confirm all four environment targets:

1. **GCP User Account**: (e.g. `admin@example.com` vs `analyst@company.com`)
2. **Target Google Cloud Project ID**: (e.g. `my-analytics-gcp-project`, `demo-data-warehouse`)
3. **Target Looker Instance / OAuth Account**: (e.g. `my-company.looker.com` vs `demo-instance` from `lkr auth list` or `available_oauth_instances`)
4. **Target Looker Database Connection**: (e.g. `looker_demo_bigquery` or `default_bigquery_connection`)

> [!IMPORTANT]
> **NEVER assume or default the Looker instance or GCP project** without explicit user confirmation, even if an active session exists in `pre-check`.
>
> ### 🛑 Strict Anti-Planning Rule (NO `implementation_plan.md` Before Gate 0)
> The agent **MUST NEVER** generate an `implementation_plan.md` artifact or start drafting detailed plans before Gate 0 has completed and the user has confirmed all 4 environment targets via `ask_question`. Writing a planning artifact at Turn 1 buries the mandatory Gate 0 questions and forces premature assumptions about GCP projects and Looker instances.
>
> ### 🛑 Strict Pre-Flight GCP Account Activation & Validation
> Immediately upon user selection of the GCP User Account in Step 1:
> 1. Set the active gcloud CLI account: `gcloud config set account <selected_account>`
> 2. Verify token validity: `gcloud auth print-access-token --account=<selected_account>`
> 3. If token check fails, exits non-zero, or prompts for re-authentication, **STOP IMMEDIATELY**. Prompt the user to run:
>    ```bash
>    gcloud auth login <selected_account>
>    gcloud auth application-default login
>    ```
>    DO NOT fall back silently to another account or ambient ADC. Confirm credentials before proceeding.

---

## 2. Iterative Schema Co-Design & Micro-Sample Validation Gate

When creating demo datasets, the agent **MUST co-iterate with the user** across four deterministic phases. Do not write full tables or load BigQuery until all phases are complete:

```mermaid
graph TD
    A[Phase 1: Schema & ERD Proposal] -->|User Approval| B[Phase 2: Micro-Sample Preview]
    B -->|User Validation| C[Phase 3: Scale & Volume Confirmation]
    C -->|User Scale Selection| D[Phase 4: Full Synthesis & BigQuery Load]
```

> [!CAUTION]
> ### 🛑 Strict 2-Step Sequential Visible Presentation Rule (Anti-Zero-Length Turn)
> **NEVER call `ask_question` in an empty or content-free message turn.**
> 1. **Phase 1 Must Render ERD & Schema in Chat First**: In the exact same response turn, the agent MUST write the full Markdown ERD diagram (`mermaid`), dimension/fact tables, column datatypes, primary/foreign keys, and target domain metrics in visible chat BEFORE calling `ask_question` to approve the schema.
> 2. **Phase 2 Must Render Data Tables in Chat First**: Once Phase 1 is approved, the agent MUST output complete Markdown preview tables (5–10 rows per table demonstrating parent/child referential integrity and realistic distributions) in visible chat BEFORE calling `ask_question` to validate the sample and select the volume scale.
> 3. Bypassing visible preview rendering in chat blinds the user and is strictly forbidden.

### Phase 1 — Schema Proposal & Review (Human-in-the-Loop)
**MANDATORY Single-Turn Message Structure**:
Your response turn in this phase MUST contain visible Markdown content before calling `ask_question`. Calling `ask_question` with an empty chat body is strictly prohibited. Structure your turn as follows:
1. **Domain Overview**: 2–3 sentences explaining the scenario, key operational and analytical entities, and business goals.
2. **Mermaid ERD Diagram**: A full `mermaid` diagram showing entities, primary/foreign keys, and cardinality (e.g. `||--o{`).
3. **Relational Schema Specification**: Markdown tables listing each table, its columns, data types, key constraints (PK, FK), and business definitions.
4. **Key Metrics Highlight**: List the primary business and analytics metrics enabled by this schema (e.g., MRR/ARR, churn, latency, conversion).
5. **Approval Question**: Only after rendering steps 1–4 in visible chat, call the `ask_question` tool asking the user to approve the schema or request adjustments.

### Phase 2 — Micro-Sample Synthesis & Preview (Human-in-the-Loop)
- **Execution Priority (Mandatory)**:
  1. **Priority 1 (Primary)**: Author DataDesigner builder scripts (`data_designer.config`) and execute validation/preview using the `data-designer` MCP tools (`call_mcp_tool(ServerName="data-designer", ToolName="validate_builder", ...)` and `preview_dataset`).
  2. **Priority 2 (Fallback Only)**: Only if the `data-designer` MCP server is unavailable or fails, fall back to `demo-create data generate --engine fallback` or standard Python scripts.
- Display Markdown preview tables directly in chat demonstrating:
  - Referential integrity across parent/child IDs.
  - Realistic domain-specific values and categorical distributions.
- Prompt the user to inspect and validate the sample records.

### Phase 3 — Volume & Scale Confirmation (Human-in-the-Loop)
- Prompt the user to select the target scale:
  - **Small** (~1,000–5,000 rows across tables) — Quick testing
  - **Medium** (~10,000–50,000 rows) — Standard demo
  - **Large** (~100,000–500,000+ rows) — High-volume enterprise demo
  - **Custom** table-specific sizing

### Phase 4 — Batch Synthesis & BigQuery Load (Delegate to Subagent)
- Only after Phases 1–3 are explicitly acknowledged by the user, delegate batch synthesis and BigQuery ingestion to the **[`data-engineer`](subagents/data-engineer.md)** subagent (or run the MCP / CLI commands).
- **DataDesigner MCP Tools First & Zero Vertex AI Dependency**:
  - **Priority 1 (Primary)**: Execute batch generation via `data-designer` MCP tools (`validate_builder` ➔ `generate_dataset` ➔ `export_to_bigquery`) or `demo-create data generate --builder-script <path> --engine data-designer`.
  - **Priority 2 (Fallback Only)**: Only if DataDesigner MCP/runtime is unavailable, fall back to `demo-create data generate --engine fallback`.
  - It does **NOT** use Vertex AI, Google Cloud AI APIs, or `roles/aiplatform.user` IAM permissions.
```yaml
subagent:
  type: "skills/looker-demo-orchestrator/subagents/data-engineer.md"
  prompt: "Synthesize full volume Parquet data for {confirmed_scale} rows and upload to BigQuery project {confirmed_gcp_project} dataset {dataset_name} using DataDesigner MCP tools."
  inputs:
    gcp_project_id: "{confirmed_gcp_project}"
    dataset_id: "{dataset_name}"
    location: "US"
    schema_spec: "{approved_schema_json}"
    scale: "{confirmed_scale}"
    output_dir: "scratch/parquet"
```

> [!CAUTION]
> ### 🛑 Strict Target Project Integrity & ADC Refresh Gate
> 1. **NEVER silently fall back or divert to an alternate Google Cloud Project or dataset** if permissions errors (e.g. `403 Access Denied`, `bigquery.datasets.create`, or expired ADC tokens) occur during dataset creation or table loading.
> 2. If `data-engineer` returns status `PERMISSION_DENIED` or fails on the confirmed project, **the pipeline MUST BLOCK IMMEDIATELY and prompt the user** (via `ask_question` or terminal instruction) to refresh their ADC credentials (`gcloud auth application-default login`) or grant the necessary BigQuery IAM roles on the confirmed project.
> 3. Under no circumstances should the agent create or load tables into a different project than the one explicitly confirmed by the user in Step 1.

---

## 3. Looker Project & Model Provisioning (`lkr-dev-cli`)

Looker authentication is managed directly via `lkr-dev-cli` using the confirmed OAuth account:

### A. Project & Bare Git Initialization
Ensure the project exists on the target Looker instance with a bare Git repository:

```bash
lkr --oauth-account=<oauth_account> code-mode sandbox --code="
if session().get('workspace_id') != 'dev':
    update_session(body={'workspace_id': 'dev'})

project_name = '<project_name>'
connection_name = '<connection_name>'

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

## 4. LookML Quality Standards & 4-Stage Semantic Pipeline

### A. Semantic Modeling, Triage & Filtered Measure Grounding (Delegate to Modeler Subagent)

Delegate semantic modeling to the front-door **[`lookml-modeler`](subagents/lookml-modeler.md)** subagent:

```yaml
subagent:
  type: "skills/looker-demo-orchestrator/subagents/lookml-modeler.md"
  prompt: "Model LookML views, explores, and measures for {project_name}. Enforce mandatory SELECT DISTINCT grounding via lookml-filtered-measures before writing any filters: [...] blocks. If normalized 3NF structures with Chasm Traps or diamond joins exist, delegate to lookml-snowflake-modeler."
  inputs:
    project_name: "{looker_project_name}"
    connection_name: "{looker_connection_name}"
    lookml_dir: "lookml/"
    table_specs: "{extracted_table_specs}"
    domain_metrics: "{domain_metrics_list}"
```

- **Triage Protocol**:
  - **Existing BigQuery Dataset & Knowledge Catalog**: If modeling from an existing dataset (`dataset_id` provided or running `demo-create lookml model --dataset <id>`), introspect BigQuery table schema, primary/foreign key constraints (`INFORMATION_SCHEMA.TABLE_CONSTRAINTS`), and Google Cloud Data Catalog / Dataplex metadata (`@bigquery` entry group). If the `knowledge-catalog` MCP server is installed, invoke it to retrieve business glossaries and column tags to enrich LookML descriptions.
  - **Standard / Star Schemas**: `lookml-modeler` writes `.view.lkml`, primary keys, formatted measures (`usd_0`, `percent_2`, `decimal_1`), drill fields, and `.explore.lkml` directly.
  - **Mandatory `SELECT DISTINCT` Grounding ([`lookml-filtered-measures`](../lookml-filtered-measures/SKILL.md))**: Never guess categorical filter strings (e.g., `"2xx"`, `"active"`). Before writing any `filters: [...]` block inside a measure, inspect the actual distinct values in the local Parquet file or run `SELECT DISTINCT` against BigQuery so filtered measures and derived ratios (`SAFE_DIVIDE(${num}, NULLIF(${den}, 0))`) never evaluate to `0` or `NULL`.
  - **Normalized 3NF / Snowflake Schemas**: If multiple 1:N child collections or diamond joins are detected, hand off to **[`lookml-snowflake-modeler`](subagents/lookml-snowflake-modeler.md)**:
    - Runs `schema_graph_analyzer.py` on the schema DAG.
    - Sets Explore Base Views on leaf event facts ($d_{\text{in}} = 0$).
    - Pre-aggregates child collections into **Native Derived Tables (NDTs)** and joins them **`relationship: one_to_one`** onto the parent Explore (eliminates Chasm Traps).
    - Resolves diamond joins with role-playing aliases (`from: users`) and explicit `view_label:` headers.
- **Mandatory Field Standards**: Explicit `label:` and `description:` parameters on EVERY dimension, dimension group, and measure (Title Case, e.g. `label: "Monthly Recurring Revenue"`).

---

### B. Mandatory Executive Dashboard Polish (Delegate to Dashboard Designer Subagent)

After `demo-create lookml model` scaffolds the baseline LookML project, **automatically delegate dashboard polish and domain customization** to the **[`lookml-dashboard-designer`](subagents/lookml-dashboard-designer.md)** subagent executing the **3-Pass Iterative Design & Screenshot Critique Protocol**:

```yaml
subagent:
  type: "skills/looker-demo-orchestrator/subagents/lookml-dashboard-designer.md"
  prompt: "Execute the 3-Pass Executive Dashboard Polish protocol for {project_name} grounded strictly in staged explores and views. Enforce: (1) Theme-inheriting type: text section headers (no hardcoded HTML background gradients), (2) Centered legends (legend_position: center), (3) Dual-axis charts with independent value ranges (y_axis_combined: false, y_axis_unpinned: true) and explicit numeric axis label formatting, and (4) Transparent data grids (table_theme: transparent) with inline cell visualizations."
  inputs:
    project_name: "{looker_project_name}"
    model_name: "{looker_model_name}"
    primary_explore: "{primary_explore_name}"
    lookml_dir: "lookml/"
    domain_theme: "{domain_theme}"
```

- **Pass 1 (Explore-Grounded Architecture & Distinct Value Discovery)**: Inspects staged `explores/*.explore.lkml` and `views/*.view.lkml` files (never invents fields) and runs `SELECT DISTINCT` on categorical dimensions used in custom `series_colors:` so color keys match exact data literals.
- **Pass 2 (Executive Visual Polish Standards)**:
  - **Theme-Inheriting Section Headers**: Native `type: text` tiles (`row: 0, width: 24, height: 2`) at the top of each tab using `title_text` and `subtitle_text` without hardcoded HTML background gradients or fixed hex text colors.
  - **KPI Scorecards**: `single_value` stat banners (`row: 2, height: 4`) with `single_value_title` and change comparisons (`show_comparison: true`). Never attach `advanced_vis_config` to `single_value` tiles.
  - **Centered Legends**: Every chart with a legend (`looker_area`, `looker_column`, `looker_bar`, `looker_line`, `looker_pie`) explicitly sets `legend_position: center` and `"legend": {"align": "center", "verticalAlign": "bottom"}` in `advanced_vis_config`.
  - **Independent Dual-Axis Ranges & Numeric Formatting**: Dual-axis charts set `y_axis_combined: false`, `y_axis_unpinned: true`, and map series to independent left/right axes with explicit numeric format strings (`"${value:,.0f}"`, `"{value:,.0f}"`, `"{value:.1f} ms"`, `"{value:.1f}%"`).
  - **Transparent Data Grids**: `looker_grid` tiles set `table_theme: transparent`, `show_view_names: false`, and inline `series_cell_visualizations` bars on primary measures.
- **Pre-Push Visual & Filtered Measure Auditor**: `demo-create lookml deploy` automatically executes static visualization contract checks, Executive Polish warnings, and the **Filtered Measure Distinct-Value Auditor** against local Parquet/BigQuery before pushing to Looker.

---

### C. LookML Server Performance Optimization Gate (Strictly Guarded Interactive Gate)

> [!CAUTION]
> ### 🛑 STRICT CONDITIONAL BRANCH FENCE: NEVER AUTO-RUN OPTIMIZER
> Spawning the optimizer subagent or running `demo-create lookml optimize` without prior user confirmation violates the co-design contract. The orchestrator **MUST pause and prompt the user via `ask_question`**:
> - **Question**: "Would you like to run the LookML Performance Optimizer to audit and apply Google Cloud Looker Server Optimization best practices?"
> - **Options**:
>   - `(Recommended) Yes: Apply Google Cloud performance optimizations (datagroup caching, partition pruning filters, static suggestions, foreign key hiding)`
>   - `No: Skip performance optimization and proceed directly to QA validation`
>
> **Execution Branches**:
> - **If user selects "Yes"**: Execute directly in the parent session via fast-path CLI:
>   ```bash
>   demo-create lookml optimize --lookml-dir <lookml_dir>
>   ```
>   The CLI automatically snapshots current LookML files into `<lookml_dir>/.backup_pre_opt` before modifying any files.
> - **If user selects "No"**: Advance immediately to Phase D without touching LookML files.
> - **If user requests a Rollback**:
>   1. Enforce the **Kill-Fence**: Immediately terminate any active subagents: `manage_subagents(Action='kill_all')`.
>   2. Atomically restore the pre-optimization snapshot with 1 command (headless, zero git requirement):
>      ```bash
>      demo-create lookml restore --lookml-dir <lookml_dir>
>      ```

- **Optimizations Applied by the CLI Engine**:
  - **Static Suggestions on Low-Cardinality Dims**: Injects `suggestions: ["val1", "val2", ...]` on categorical fields with $\le 15$ distinct values to eliminate database roundtrips when filters open.
  - **Disable Suggestions on Unique Keys**: Injects `suggestable: no` on primary keys, foreign key UUIDs, timestamps, and free text.
  - **Model Datagroup Caching**: Configures production datagroups (`max_cache_age: "4 hours"`) and applies `persist_with: default_caching_policy`.
  - **Partition Pruning**: Enforces `always_filter` or `conditionally_filter` on BigQuery partitioned date columns.
  - **Field Pruning**: Sets `hidden: yes` on raw foreign key IDs and asserts `primary_key: yes` on unique grains.

---

### D. Mandatory Pre-Deployment Validation Gate (CLI Fast-Path with On-Demand QA Healing)

1. **Direct Fast-Path Deploy & Query Test**:
   Execute pre-flight filtered measure audit, dev push, project validator, and dashboard query verification directly in the parent session:
   ```bash
   demo-create lookml deploy --looker-project <looker_project_name> --lookml-dir <lookml_dir> --looker-account <oauth_account>
   ```
   If all LookML checks, filtered measure distinct-value checks, and dashboard query tests return 100% HTTP 200 OK, the CLI automatically deploys to production and outputs the live dashboard URL.

2. **On-Demand QA Healing Subagent (Triggered ONLY on Validation / Query Failure)**:
   If validation fails or any dashboard query encounters an error, spawn the **[`lookml-qa-validator`](subagents/lookml-qa-validator.md)** subagent:

```yaml
subagent:
  type: "skills/looker-demo-orchestrator/subagents/lookml-qa-validator.md"
  prompt: "Investigate LookML validator errors or query failures, run bounded self-healing (max 3 attempts) using lookml-dashboard-to-query, and certify deploy readiness."
  inputs:
    project_name: "{looker_project_name}"
    oauth_account: "{oauth_account}"
    lookml_dir: "lookml/"
    dashboard_files: ["dashboards/*.dashboard.lookml"]
```

The `lookml-qa-validator` subagent executes bounded self-healing:

```mermaid
graph LR
    Step1[1. Push to Dev Branch] --> Step2[2. Run LookML Validator]
    Step2 --> Step3[3. Run Dashboard Query Tests]
    Step3 -->|Errors Found| Heal{Self-Heal Loop<br/>Max 3 Attempts}
    Heal -->|Patch Applied| Step1
    Heal -->|Exceeded 3| Fail[Report Failure to Parent]
    Step3 -->|100% Pass| Step4[Return Deploy Certificate]
```

> [!CAUTION]
> **Production Deployment Authority Remains with Parent Orchestrator**:
> The `lookml-qa-validator` subagent is strictly an auditing/healing worker and cannot release to production. Once it returns `{ready_to_deploy: true}`, the **Parent Orchestrator** executes production release:
> ```bash
> lkr --oauth-account=<oauth_account> tools lookml deploy --project=<project_name>
> ```

---

### E. Interactive Post-Deploy Screenshot Critique Checkpoint (Pass 3 Visual Critique)

Immediately after `demo-create lookml deploy` succeeds and outputs the live Looker dashboard URL, the orchestrator **MUST present the URL in chat and pause with `ask_question`** before advancing to Gate 4 (`gate_4_agent`):

- **Question**: "The dashboard is live at `{deployed_dashboard_url}`. Would you like to share a screenshot of the rendered dashboard for visual layout critique & Pass 3 refinement, or approve as-is?"
- **Options**:
  - `(Recommended) Approve dashboard layout as-is and proceed to Gate 4 (Conversational Analytics Agent)`
  - `I will share/upload a screenshot in chat for visual critique and refinement`

**If the user shares a screenshot path or image**:
1. Inspect the rendered image via `view_file`.
2. Audit typography hierarchy, axis label spacing, legend alignment, dual-axis balance, and color contrast.
3. Apply targeted LookML adjustments to `dashboards/*.dashboard.lookml` (via direct edit or `lookml-dashboard-designer`).
4. Re-deploy via `demo-create lookml deploy` and confirm visual satisfaction before advancing to Gate 4.

---

## 5. Provision Conversational Analytics Data Agent & Gemini Enterprise (GE) Publishing (Delegate to Subagent)

> [!IMPORTANT]
> **Conditional Subagent Trigger**:
> The **[`ca-agent-provisioner`](subagents/ca-agent-provisioner.md)** subagent is **ONLY spawned if the user explicitly confirms CA Agent creation** in the interactive gate below.

> [!CAUTION]
> ### 🛑 Strict Sequential Gate Isolation: NEVER Bundle CA, GE, and Embed Gates
> The orchestrator **MUST present each post-deployment gate sequentially in its own discrete step**:
> 1. **Phase 1: LookML Model & Dashboard Deployed to Production**
> 2. **Phase 2: CA Agent Confirmation Gate** (`ask_question`)
> 3. **Phase 3: GE Verification & Publishing Gate** (`ask_question`, if CA Agent created)
> 4. **Phase 4: External Embed Portal Gate** (`ask_question`, ONLY after Looker assets, CA Agent, and GE status are completely finished)
> Under NO circumstances may the agent bundle these questions into a single multi-question modal.

### A. Interactive CA Agent Confirmation Gate (Parent Orchestrator)
Prompt the user via `ask_question`:
- **Question**: "Would you like to provision a Looker Conversational Analytics (CA) Agent for the `{model_name}` model?"
- **Options**:
  - `(Recommended) Provision CA Agent with default domain instructions and dashboard golden queries`
  - `Provide custom system instructions before provisioning`
  - `Skip Conversational Analytics Agent creation`

### B. Interactive Gemini Enterprise (GE) Verification & Publishing Gate (Parent Orchestrator)
If CA Agent creation is selected, the orchestrator/CLI checks Looker GE settings via `GET /api/4.0/gemini_enablement`:

- **Case 1: GE is already configured** (`ai_ge_project_id`, `ai_ge_instance_id`, `ai_ge_location` populated):
  - Displays the active GE app ID, location, and GCP project.
  - Prompts user: "Gemini Enterprise is configured for app `{ge_instance_id}`. Publish CA Agent to this GE app?"
  - Options:
    - `(Recommended) Yes, publish agent to existing Gemini Enterprise app`
    - `Reconfigure Looker with a different Gemini Enterprise app`
    - `Skip Gemini Enterprise publishing (internal Looker only)`

- **Case 2: GE is not configured** (or user requested reconfigure):
  - Automatically queries active GCP project for available GE apps via Discovery Engine API / `gcloud`.
  - Prompts user to select from discovered GE apps (or enter custom App ID / Region).
  - Updates Looker settings via `PATCH /api/4.0/gemini_enablement` sending the full payload with `ai_ge_publish_enabled: true`.
  - Grants `roles/discoveryengine.admin` to the Looker SA email via `gcloud projects add-iam-policy-binding`.
  - Confirms the Looker SA has a Gemini Enterprise license before proceeding to publish.

### C. Procedural Delegation: `ca-agent-provisioner` Subagent
Once confirmed, delegate Golden Query extraction, agent creation, and GE publishing to **[`ca-agent-provisioner`](subagents/ca-agent-provisioner.md)**:

```yaml
subagent:
  type: "skills/looker-demo-orchestrator/subagents/ca-agent-provisioner.md"
  prompt: "Provision Looker CA Agent for model {lookml_model_name} on explore {primary_explore}, extract dashboard tile golden queries, and publish to Gemini Enterprise if confirmed."
  inputs:
    project_name: "{looker_project_name}"
    model_name: "{lookml_model_name}"
    primary_explore: "{primary_explore}"
    oauth_account: "{oauth_account}"
    dashboard_files: ["dashboards/*.dashboard.lookml"]
    system_instructions: "{system_instructions_or_default_template}"
    publish_ge: "{publish_ge_boolean}"
```

The subagent follows the strict Looker 4.0 Golden Query rules:
1. `create_agent(body={...})` with persona, query patterns, and domain rules.
2. For each dashboard tile: `create_query` $\to$ get `expanded_share_url` $\to$ `create_golden_query` with exactly **ONE question** $\to$ `update_agent` linking all IDs.
3. If `publish_ge: true`: executes `POST /api/4.0/internal/agents/{agent_id}/publish` with body `{}` and verifies publication state via `GET /api/4.0/internal/agents/{agent_id}` with automatic retries (up to 3 attempts).
4. **Re-Publishing Guarantee**: If any LookML self-healing or dashboard corrections occurred during QA validation, the orchestrator/subagent **MUST re-extract golden queries, update the agent, and re-publish to GE** so that the agent is guaranteed to be operational in Gemini Enterprise.

---

## 6. External Embedded Portal Scaffolding (Delegate to Subagent)

> [!IMPORTANT]
> **Conditional Subagent Trigger**:
> The **[`embed-portal-engineer`](subagents/embed-portal-engineer.md)** subagent is **ONLY spawned if the user explicitly confirms external embed portal creation** in the interactive gate below.

### A. Interactive External Embed Confirmation Gate (Parent Orchestrator)
Prompt the user via `ask_question`:
- **Question**: "Would you like to scaffold an external branded embedded analytics portal (`looker-embed-demo`)?"
- **Options**:
  - `(Recommended) Scaffold external embed portal with custom brand theme and embedded chat`
  - `Skip external portal scaffolding (internal Looker only)`

### B. Procedural Delegation: `embed-portal-engineer` Subagent
If confirmed, delegate frontend scaffolding, environment configuration, brand tokens, and build verification to **[`embed-portal-engineer`](subagents/embed-portal-engineer.md)**:

```yaml
subagent:
  type: "skills/looker-demo-orchestrator/subagents/embed-portal-engineer.md"
  prompt: "Scaffold external embed demo for {project_name}, configure .env (VITE_CHAT_AGENT_ID={ca_agent_id}, dashboard ID={dashboard_id}), customize brand styling in styles.css, and verify build."
  inputs:
    project_name: "{looker_project_name}"
    looker_instance_url: "{looker_instance_url}"
    dashboard_id: "{deployed_dashboard_id}"
    ca_agent_id: "{ca_agent_id}"
    brand_name: "{brand_name}"
    theme_colors: "{brand_theme_colors}"
    target_dir: "embed-portal/"
```

The subagent:
1. Clones/scaffolds `looker-embed-demo`.
2. Configures `.env` with `VITE_LOOKER_HOST`, `VITE_DEFAULT_DASHBOARD_ID`, and `VITE_CHAT_AGENT_ID`.
3. Customizes `src/constants.ts` and CSS variables in `src/styles.css`.
4. Installs dependencies (`pnpm install` or `npm install`) in `frontend/` and runs `pnpm build` (or `npm run build`) to verify clean compilation.

---

## 7. Mandatory Final Delivery Report Protocol

Upon completing the demo creation pipeline (production deployment, plus optional CA Agent or Embed Portal steps), the Parent Orchestrator **MUST synthesize all subagent outputs and emit a comprehensive Executive Delivery Report**.

The report must be emitted directly in chat as the final deliverable and saved to the project directory as `DELIVERY_REPORT.md` (or artifact).

> [!IMPORTANT]
> **Mandatory Link to `SPEC.md`**: The delivery report **MUST** prominently link to `[SPEC.md](SPEC.md)` in its Quick Access Links table. `SPEC.md` serves as the project's living architectural and technical specification, continuously updated in the background across all turns and conversations via [`demo-spec`](../demo-spec/SKILL.md).

### Mandatory Report Structure & Template:

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

*(Optional — Include `### Chasm Trap Mitigation Architecture` below ONLY if snowflake modeling was required / 1:N child collections were detected)*:
<!--
### Chasm Trap Mitigation Architecture
- Document NDT rollups pre-aggregating child 1:N metrics at the parent grain.
- Document one_to_one joins onto parent table eliminating Cartesian products.
- Document dedicated Event Stream Explores with event leaf as Base View.
-->

---

## 4. LookML Dashboard Layout & Tabbed Architecture

The dashboard (`{lookml_model_name}::{dashboard_name}`) is structured into **{tab_count} functional operational tabs** with universal cross-filtering and popover filters:

### Tab 1: {Tab 1 Name}
- **KPI Banners**: {List of primary single-value metrics}.
- **{Chart 1 Title}**: {Chart visualization type and business question answered}.
- **{Chart 2 Title}**: {Chart visualization type and business question answered}.

### Tab 2: {Tab 2 Name}
- **KPI Banners**: {List of secondary single-value metrics}.
- **{Chart 1 Title}**: {Chart visualization type and business question answered}.

---

## 5. Pre-Deployment Validation Audit Record

In strict compliance with the **Looker Demo Orchestrator** pre-deployment gate, all validation checks passed before production release:

```
[Phase 1] Code Push to Dev Branch:             100% COMPLETE ({files_count} LookML files pushed)
[Phase 2] LookML Validator (validate_project):   0 ERRORS DETECTED
[Phase 3] Exhaustive Dashboard Query Tests:      {queries_passed} / {queries_tested} (100%) QUERIES PASSED
[Phase 4] Production Deployment:                SUCCESS (Deployed to Production at {timestamp})
```

### Detailed Query Test Results ({queries_passed}/{queries_tested} HTTP 200 OK)
1. `{query_tile_1}` (Explore: `{explore_1}`) ➔ **PASS**
2. `{query_tile_2}` (Explore: `{explore_2}`) ➔ **PASS**
3. `{query_tile_n}` (Explore: `{explore_n}`) ➔ **PASS**

---

## 6. Conversational Analytics (CA) AI Agent Configuration *(if provisioned)*

- **Agent ID**: `{ca_agent_id}`
- **Agent Name**: `{ca_agent_name}`
- **Explore Sources**: `{explore_sources_list}`
- **Code Interpreter**: Enabled
- **Direct Agent Chat URL**: [Open {ca_agent_name}]({looker_instance_url}/conversational-analytics/agents/{ca_agent_id})

### Pre-Seeded Golden Queries
1. *"{Natural language business question 1}"*
2. *"{Natural language business question 2}"*
3. *"{Natural language business question n}"*

---

## 7. Gemini Enterprise (GE) / Embed Portal Status

*(If published to Gemini Enterprise)*:
- **Publish State**: `published` (HTTP 200 OK)
- **Status Message**: `Successfully published Agent {ca_agent_id} to GEMINI_ENTERPRISE.`
- **Capabilities**: Full natural language synthesis over `{lookml_model_name}`, golden query semantic routing, and code interpretation within Gemini Enterprise apps.

*(If external embed portal was scaffolded)*:
- **Workspace Directory**: `{embed_workspace_dir}` (Full-stack: `frontend/` React 19 + TypeScript + Vite 6 + TanStack Router, `backend/` FastAPI + Cookieless Embed SSO)
- **Local Dev Command**: `cd {embed_workspace_dir}/frontend && pnpm install && pnpm dev` (or `./start.sh` from portal root)
- **Portal Views & Navigation (`customize-frontend-looker-config`)**:
  - **Home Hub (`/`)**: Hero header (`{portal_hub_title}`), Live Operational/Financial Summary KPI cards, Live Operational Ticker (streaming events), AI Strategic Executive Briefing.
  - **Embedded Dashboard (`/dashboard`)**: `{deployed_dashboard_id}` with universal date filters (`{dashboard_date_filter_names}`).
  - **Embedded AI Assistant (`/conversational-analytics`)**: Connected CA Agent `{ca_agent_id}`.
  - **Embedded Explorer (`/explore`)**: Grounded in `{explore_path}`.
- **Branding & CSS Theming (`customize-frontend-branding` & `customize-frontend-theme`)**:
  - Brand Header Name: `{brand_name}` (in `Sidebar.tsx` & `constants.ts`)
  - Primary HSL Palette: `--color-primary-raw: {primary_hsl};`
  - Typography: `--font-heading: 'Outfit'`, `--font-sans: 'Inter'`
  - Dark Mode: Native `html.dark` surface tokens
  - Looker Themes (`embed-themes`): `<Brand>_Light` & `<Brand>_Dark`
- **Role-Based Access Control (`ROLE_PERMISSIONS`)**:
  - Simple User (`viewer`) vs Advanced User (`explorer`) profiles with group assignment `{group_id}`.
- **Build Status**: Verified 0 TypeScript / compilation errors via `pnpm run build`.
```

---

## 8. Modular CLI Execution & State Persistence

When performing isolated operations or delegating granular tasks to specialized subagents, use the modular CLI subcommands. Execution state is persisted across invocations in `.demo-state.json` (auto-loaded and updated with CLI option overrides):

| Command Group | Subcommand | Purpose | Key Flags |
|---|---|---|---|
| **`demo-create data`** | `generate` | Synthesizes local Parquet dataset tables | `--domain`, `--row-count`, `--output-dir` |
| | `upload` | Creates dataset and uploads Parquet tables to BigQuery | `--parquet-dir`, `--gcp-project`, `--dataset`, `--location` |
| | `inspect` | Introspects tables and schema in existing BigQuery dataset | `--gcp-project`, `--dataset` |
| **`demo-create lookml`** | `model` | Generates LookML views, explores, and models from BigQuery (with Knowledge Catalog & PK/FK constraints) or Parquet | `--looker-project`, `--dataset`, `--parquet-dir`, `--connection`, `--output-dir` |
| | `deploy` | Pushes staged LookML to dev workspace, validates, runs query tests, and deploys to production | `--looker-project`, `--lookml-dir`, `--looker-account` |
| **`demo-create agent`** | `create` | Creates CA Agent, grounds golden queries, and optionally publishes to GE | `--model`, `--explore`, `--dashboard-id`, `--publish-ge` |
| | `golden-queries` | Extracts queries from dashboard files/IDs and links as Golden Queries | `--agent-id`, `--dashboard-id`, `--dashboards-dir` |
| | `publish` | Verifies GE config and publishes agent to connected GE apps | `--agent-id`, `--looker-account` |
| **`demo-create embed`** | `scaffold` | Scaffolds React/Vite embed portal workspace with `.env` and theme tokens | `--looker-project`, `--dashboard-id`, `--agent-id`, `--brand-name`, `--target-dir` |
| **`demo-create ge`** | `status` | Displays current Looker Gemini enablement and GE config | `--looker-account` |
| | `configure` | Discovers GE apps on GCP, configures Looker GE settings, and grants IAM roles | `--instance-id`, `--location`, `--gcp-project` |




