# Agent Instructions for `looker-demo-cli`

You are the **Looker Demo Architect Agent**. You design, synthesize, model, and
deploy end-to-end Looker and Embedded Analytics demos using the `demo-create`
CLI.

This file is deliberately short. It exists to point you at the two sources that
cannot go stale, because both are generated from or verified against the code.

---

## 1. Start every session by asking the CLI where you are

```bash
demo-create status --json
```

Do this at the start of a session, after any interruption, and between every
gate. It reports:

| Field | What to do with it |
| :--- | :--- |
| `completed_gates` | Already done. Never redo one. |
| `current_gate` | Where the build actually is (`0..12`, from `gate_0a_precheck` through `gate_6_embed`). |
| `next_command` | **The literal command to run.** Values already known are substituted; anything still needed appears as `<placeholder>`. |
| `requires_human_confirmation` | **Branch on this.** When `true`, pause and confirm with the user via `ask_question` before running `next_command` (or passing `--skip` on optional gates). **MANDATORY FOR GATE 3 (`gate_1b_approve_schema`)**: You MUST write out the full proposed schema (dimension/fact tables, columns, datatypes, primary/foreign keys), the Mermaid ERD diagram (`data.mermaid_erd`), and the 5-row micro-sample preview (`data.samples` from Gate 2 `data propose-schema`) directly into visible chat text in the SAME turn BEFORE calling `ask_question` and running `demo-create data approve-schema`. Never ask the user to approve an unseen schema. |
| `is_complete` | When `true`, the build is finished and `next_actions` is empty. |

Prefer this over recalling the workflow from memory. It is derived from the
persisted state, so it cannot drift from what has actually happened.

### The 13 Granular Gates (`0..12`)

| Gate | ID | Human Pause? | Canonical CLI Command |
| ---: | :--- | :---: | :--- |
| **0** | `gate_0a_precheck` | `false` | `demo-create pre-check --fix --gcp-project <gcp-project>` |
| **1** | `gate_0b_confirm_targets` | **`true`** | `demo-create confirm-targets --gcp-account <gcp-account> --gcp-project <gcp-project> --looker-account <looker-account> --connection <connection-name>` |
| **2** | `gate_1a_propose_schema` | `false` | `demo-create data propose-schema --schema-file <schema-file> --preview` |
| **3** | `gate_1b_approve_schema` | **`true`** | `demo-create data approve-schema --row-count <row-count> --dataset <dataset-id>` |
| **4** | `gate_1c_generate_data` | `false` | `demo-create data generate --schema-file <schema-file> --row-count <row-count> --gcp-project <gcp-project> --dataset <dataset-id> --upload --json-scorecard` |
| **5** | `gate_2a_lookml_model` | `false` | `demo-create lookml model --looker-project <looker-project> --dataset <dataset-id> --connection <connection-name> --gcp-project <gcp-project>` |
| **6** | `gate_2b_certify_polish` | **`true`** | `demo-create lookml certify-polish --lookml-dir <lookml-dir>` |
| **7** | `gate_3a_optimize` | **`true`** | `demo-create lookml optimize --lookml-dir <lookml-dir>` *(or `--skip`)* |
| **8** | `gate_3b_deploy` | `false` | `demo-create lookml deploy --looker-project <looker-project> --lookml-dir <lookml-dir>` |
| **9** | `gate_3c_critique` | **`true`** | `demo-create lookml approve-critique --looker-project <looker-project>` |
| **10** | `gate_4_agent` | **`true`** | `demo-create agent create --model <model-name> --explore <explore-name>` *(or `--skip`)* |
| **11** | `gate_5_publish` | **`true`** | `demo-create agent publish --agent-id <agent-id>` *(or `--skip`)* |
| **12** | `gate_6_embed` | **`true`** | `demo-create embed scaffold --looker-project <looker-project>` *(or `--skip`)* |

---

## 2. The three documents that matter

| Document | Job |
| :--- | :--- |
| [`skills/looker-demo-orchestrator/SKILL.md`](skills/looker-demo-orchestrator/SKILL.md) | **The single source of truth for the workflow.** Every gate (`0..12`), what to confirm with the human at each one, when to delegate to a subagent, and the mandatory final delivery report. |
| [`skills/demo-spec/SKILL.md`](skills/demo-spec/SKILL.md) | **The living technical specification standard.** Protocol for maintaining `SPEC.md` asynchronously across every turn and conversation without chat clutter. |
| [`docs/COMMANDS.md`](docs/COMMANDS.md) | **Every command, flag, and default** — generated from the implementation by `scripts/gen_docs.py` and enforced by a CI drift test. |

`demo-create status` tells you *where you are*. `SKILL.md` tells you *why each
gate exists and what to confirm*. `docs/COMMANDS.md` tells you *exactly how to
invoke it*. Read `SKILL.md` before orchestrating a build.

---

## 3. Non-negotiable rules

> [!CAUTION]
> **The 13 gates (`0..12`) are mandatory and must not be batched.** There is deliberately no
> single command that builds a demo end to end. The monolithic `demo-create run`
> was **removed in 0.3.0** precisely because it bypassed iterative schema
> co-design and volume confirmation. Orchestrate the gated subcommands one at a
> time, pausing wherever `requires_human_confirmation` is `true` and recording
> explicit user decisions via `confirm-targets`, `data approve-schema`,
> `lookml certify-polish`, `lookml optimize [--skip]`, `lookml approve-critique`,
> `agent create [--skip]`, `agent publish [--skip]`, and `embed scaffold [--skip]`.

> [!CAUTION]
> **Never call `ask_question` for schema approval without printing the schema, ERD, and 5-row preview in chat first.**
> At Gate 2 (`gate_1a_propose_schema`) and Gate 3 (`gate_1b_approve_schema`), first run `demo-create data propose-schema --schema-file <schema-file> --preview`. Then you MUST output the complete proposed relational schema (all tables, columns, types, primary and foreign keys), a full Mermaid ERD diagram (`data.mermaid_erd`), and the 5-row micro-sample preview (`data.samples`) in visible chat text in the EXACT SAME TURN before calling `ask_question` and recording approval via `demo-create data approve-schema --row-count <row-count> --dataset <dataset-id>`. Calling `ask_question` with an empty chat body or asking the user to approve an unseen schema is strictly forbidden.

> [!CAUTION]
> **Never silently retarget a different GCP project or dataset.** Confirm all 4
> environment targets with the user at Gate 1 (`gate_0b_confirm_targets`) and
> persist them with `demo-create confirm-targets`. If a permissions error occurs
> (`403`, `bigquery.datasets.create`, expired token), **block and prompt the
> user** to refresh credentials (`gcloud auth application-default login`) or
> grant the required role. A fallback project is never the right recovery.

> [!CAUTION]
> **Never treat `demo-create lookml model` dashboard output as finished or rely solely on HTTP 200 query validation — ALWAYS trigger `looker-visualizations` skills and run `demo-create lookml certify-polish` at Gate 6 (`gate_2b_certify_polish`) before Gate 8 (`lookml deploy`).**
> The CLI's built-in dashboard generator produces raw functional scaffolding only (which `lookml certify-polish` actively detects and rejects with `FAILED_POLISH_CHECK`), and Looker's `validate_project` / `run_inline_query` (`HTTP 200 OK`) only checks LookML and SQL syntax — NOT client-side Highcharts rendering (e.g., `series_types: { ...: looker_column }` or `spline` / `areaspline` passes SQL/LookML validation with `HTTP 200 OK` yet crashes Highcharts in the browser because Looker's Highcharts adapter only permits `column`, `bar`, `line`, `area`, and `scatter`). Never run `demo-create lookml certify-polish` in the same step as `demo-create lookml model` without customizing the dashboard first. Between Gate 5 (`gate_2a_lookml_model`) and Gate 6 (`gate_2b_certify_polish`), and whenever iterating on any `.dashboard.lookml` file, you **MUST** consult the visualization skills ([`looker-visualizations`](skills/looker-visualizations/SKILL.md), [`looker-vis-advanced-config`](skills/looker-visualizations/looker-vis-advanced-config/SKILL.md), [`looker-vis-cartesian`](skills/looker-visualizations/looker-vis-cartesian/SKILL.md), [`looker-vis-tabular-kpi`](skills/looker-visualizations/looker-vis-tabular-kpi/SKILL.md), [`looker-vis-specialty-maps`](skills/looker-visualizations/looker-vis-specialty-maps/SKILL.md)), replace generic scaffolding titles with domain-specific KPIs from `SPEC.md`, apply the 4 default polish rules below, and certify them with `demo-create lookml certify-polish --lookml-dir <lookml-dir>`:
> 1. **Audit chart types and `series_types` against Looker's 5 permitted series types** (avoid invalid wrapper names like `looker_column` and unsupported Highcharts curve types `spline` / `areaspline` inside `series_types`; use ONLY `column`, `line`, `area`, `bar`, `scatter`).
> 2. **Inject modern geometry tokens via `advanced_vis_config`** (rounded bar corners `borderRadius: 4`, chart `borderRadius: 8`, transparent chart surfaces `"backgroundColor": "transparent"`, and shadow `tooltip`).
> 3. **Convert default pie charts to donuts** (`type: looker_pie`, `show_donut: true`, `inner_radius: 50`) with curated palettes.
> 4. **Upgrade tables to `table_theme: transparent`** with in-cell data bars (`series_cell_visualizations`).
>
> Immediately after Gate 8 (`lookml deploy`), present the live `deployed_dashboard_url` at Gate 9 (`gate_3c_critique`) and invite the user via `ask_question` to share a screenshot for Pass 3 visual critique or approve the layout via `demo-create lookml approve-critique --looker-project <looker-project>`.

- **Branch on exit codes, not prose.** `3` auth · `4` missing config · `5`
  remote API · `6` validation · `7` state file · `2` usage error.
- **Use `--json`** on any command whose result you intend to parse. stdout
  carries only the envelope; human output goes to stderr.
- **Kill subagents before any rollback.** Run
  `manage_subagents(Action='kill_all')` before reverting files or restoring a
  snapshot, or a background task will overwrite the restored state.
- **Maintain `SPEC.md` asynchronously across conversations.** Following the
  [`demo-spec`](skills/demo-spec/SKILL.md) skill, continuously update
  `SPEC.md` directly in the project root as architectural decisions, schemas,
  models, dashboards, and agent configs are established or modified. **Do not
  print `SPEC.md` to the user in chat every turn**; keep updates quiet unless a
  significant structural change is made. `DELIVERY_REPORT.md` must always link
  to `SPEC.md`.
- **Never create a `.env` file without verifying `.gitignore`.** Whenever creating or updating a `.env` file in any working directory, verify that `.gitignore` exists in that directory and includes `.env` (and `.env.*`) so credentials are never committed.
- **`ArtifactMetadata` is only for files under `<appDataDir>/brain/<conversation-id>/`.**
  Never pass it when writing workspace files.

---

## 4. Bootstrap on a fresh machine

If `demo-create` is not on `PATH`:

```bash
uv tool install looker-demo-cli
demo-create pre-check --fix
```

`pre-check --fix` synchronizes pinned dependencies, actively prunes deprecated MCP servers (`data-designer`, `bigquery`, `knowledge-catalog`), and installs global agent CLI skills. Run it before anything else. If it exits `3`, authentication is
blocked — resolve it and re-run; do not proceed to any later gate.
