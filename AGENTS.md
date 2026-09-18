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
| `current_gate` | Where the build actually is. |
| `next_command` | **The literal command to run.** Values already known are substituted; anything still needed appears as `<placeholder>`. |
| `requires_human_confirmation` | **Branch on this.** When `true`, pause and confirm with the user before running `next_command`. **MANDATORY FOR GATE 1 (`gate_1_data`)**: You MUST write out the full proposed schema (dimension/fact tables, columns, datatypes, primary/foreign keys) and Mermaid ERD diagram directly into visible chat text in the SAME turn BEFORE calling `ask_question`. Never ask the user to approve an unseen schema. |
| `is_complete` | When `true`, the build is finished and `next_actions` is empty. |

Prefer this over recalling the workflow from memory. It is derived from the
persisted state, so it cannot drift from what has actually happened.

---

## 2. The three documents that matter

| Document | Job |
| :--- | :--- |
| [`skills/looker-demo-orchestrator/SKILL.md`](skills/looker-demo-orchestrator/SKILL.md) | **The single source of truth for the workflow.** Every gate, what to confirm with the human at each one, when to delegate to a subagent, and the mandatory final delivery report. |
| [`skills/demo-spec/SKILL.md`](skills/demo-spec/SKILL.md) | **The living technical specification standard.** Protocol for maintaining `SPEC.md` asynchronously across every turn and conversation without chat clutter. |
| [`docs/COMMANDS.md`](docs/COMMANDS.md) | **Every command, flag, and default** — generated from the implementation by `scripts/gen_docs.py` and enforced by a CI drift test. |

`demo-create status` tells you *where you are*. `SKILL.md` tells you *why each
gate exists and what to confirm*. `docs/COMMANDS.md` tells you *exactly how to
invoke it*. Read `SKILL.md` before orchestrating a build.

---

## 3. Non-negotiable rules

> [!CAUTION]
> **The gates are mandatory and must not be batched.** There is deliberately no
> single command that builds a demo end to end. The monolithic `demo-create run`
> was **removed in 0.3.0** precisely because it bypassed iterative schema
> co-design and volume confirmation. Orchestrate the gated subcommands one at a
> time, pausing wherever `requires_human_confirmation` is `true`.

> [!CAUTION]
> **Never call `ask_question` for schema approval without printing the schema in chat first.**
> At Gate 1 (`gate_1_data`), you MUST output the complete proposed relational schema (all tables, columns, types, primary and foreign keys) and a full Mermaid ERD diagram in visible chat text in the EXACT SAME TURN before calling `ask_question`. Calling `ask_question` with an empty chat body or asking the user to approve an unseen schema is strictly forbidden.

> [!CAUTION]
> **Never silently retarget a different GCP project or dataset.** If a
> permissions error occurs (`403`, `bigquery.datasets.create`, expired token),
> **block and prompt the user** to refresh credentials
> (`gcloud auth application-default login`) or grant the required role. A
> fallback project is never the right recovery.

> [!CAUTION]
> **Never treat `demo-create lookml model` dashboard output as finished or rely solely on HTTP 200 query validation — ALWAYS trigger `looker-visualizations` skills before Gate 3 (`deploy`).**
> The CLI's built-in dashboard generator produces raw functional scaffolding only, and Looker's `validate_project` / `run_inline_query` (`HTTP 200 OK`) only checks LookML and SQL syntax — NOT client-side Highcharts rendering (e.g., `series_types: { ...: looker_column }` passes SQL/LookML validation with `HTTP 200 OK` yet crashes Highcharts in the browser because Highcharts requires bare `column`). Between Gate 2 (`lookml model`) and Gate 3 (`lookml deploy`), and whenever iterating on any `.dashboard.lookml` file, you **MUST** consult the visualization skills ([`looker-visualizations`](skills/looker-visualizations/SKILL.md), [`looker-vis-advanced-config`](skills/looker-visualizations/looker-vis-advanced-config/SKILL.md), [`looker-vis-cartesian`](skills/looker-visualizations/looker-vis-cartesian/SKILL.md), [`looker-vis-tabular-kpi`](skills/looker-visualizations/looker-vis-tabular-kpi/SKILL.md), [`looker-vis-specialty-maps`](skills/looker-visualizations/looker-vis-specialty-maps/SKILL.md)) and apply the 4 default polish rules:
> 1. **Audit chart types and `series_types` against Highcharts specs** (avoid invalid wrapper names like `looker_column` inside `series_types`; use bare Highcharts types `column`, `line`, `area`, `bar`, `scatter`).
> 2. **Inject modern geometry tokens via `advanced_vis_config`** (rounded bar corners `borderRadius: 4`, chart `borderRadius: 8`, transparent chart surfaces `"backgroundColor": "transparent"`, and shadow `tooltip`).
> 3. **Convert default pie charts to donuts** (`type: looker_pie`, `show_donut: true`, `inner_radius: 50`) with curated palettes.
> 4. **Upgrade tables to `table_theme: transparent`** with in-cell data bars (`series_cell_visualizations`).

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
