<!--
  GENERATED FILE -- DO NOT EDIT BY HAND.

  Produced by `python scripts/gen_docs.py` from the live Typer application.
  `tests/test_docs_drift.py` regenerates this file in CI and fails if it
  differs, so an edit here will be reverted by the next run. To change this
  document, change the command's `help=` text or its signature.
-->

# Command Reference

Every command, option, and default below is read directly from the
implementation. The CLI is designed to be driven by an AI agent, so two
conventions hold everywhere:

- **`--json` emits a machine-readable envelope on stdout**, and *only* that.
  All human-facing output goes to stderr, so `demo-create ... --json | jq`
  is always safe.
- **`--state-file` points at an explicit `.demo-state.json`** instead of the
  discovered one, so concurrent builds cannot read each other's progress.

Failures are reported by exit code, so a caller never has to parse prose:

| Exit | Meaning |
| ---: | :--- |
| `0` | Success |
| `2` | Usage error (unknown flag, missing argument) -- raised by Click |
| `3` | `AuthError` -- credentials missing, expired, or insufficient |
| `4` | `ConfigError` -- a required value was not supplied and could not be resolved |
| `5` | `RemoteApiError` -- Looker, BigQuery, or Google Cloud rejected the call |
| `6` | `ValidationError` -- generated artifacts failed validation |
| `7` | `StateError` -- `.demo-state.json` is unreadable, corrupt, or a newer schema |

> Start with [`demo-create status`](#demo-create-status). It reports which gates
> are complete and prints the exact next command to run.

## Commands at a glance

| Command | Description |
| :--- | :--- |
| [`status`](#demo-create-status) | Report the completed gates, the current gate, and the exact next command. |
| [`pre-check`](#demo-create-pre-check) | Audit GCP/ADC credentials, MCP server definitions, and agent skill folders. |
| [`skills`](#demo-create-skills) | View and manage intent-based global agent skills. |
| [`run-script`](#demo-create-run-script) | Execute a Python script using the CLI's bundled runtime and dependencies. |
| [`python`](#demo-create-python) | Execute Python within the CLI's environment (e.g. `demo-create python -c '...'`). |
| [`agent`](#demo-create-agent) | Provision Looker Conversational Analytics AI agents, ground golden queries, and publish to GE. |
| [`ge`](#demo-create-ge) | Inspect and configure Looker Gemini Enterprise (GE) integration. |
| [`env`](#demo-create-env) | Manage local demo workspace virtual environment and runtime health. |
| [`data`](#demo-create-data) | Design, synthesize, inspect, and upload BigQuery demo datasets. |
| [`lookml`](#demo-create-lookml) | Generate LookML models from BigQuery/Knowledge Catalog or Parquet, and deploy. |
| [`embed`](#demo-create-embed) | Scaffold standalone Embedded Analytics web applications and portals. |

---

## `demo-create status`

Report the completed gates, the current gate, and the exact next command.

Read-only: it loads `.demo-state.json` and writes nothing back, so it is
safe to call between every other command.

```bash
demo-create status [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

---

## `demo-create pre-check`

Audit GCP/ADC credentials, MCP server definitions, and agent skill folders.

This is Gate 0: no other command should run until this reports unblocked.
The verdict is persisted to the state file as `precheck_passed` so that
`demo-create status` can report gate 0 as cleared without re-running the
(slow, network-bound) audit.

```bash
demo-create pre-check [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--fix` | Automatically install missing MCP configs and organize global skills |  |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--gcp-project` | Target Google Cloud Project ID |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

---

## `demo-create skills`

View and manage intent-based global agent skills.

```bash
demo-create skills [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--fix` | Symlink and organize skills into intent subfolders |  |

---

## `demo-create run-script`

Execute a Python script using the CLI's bundled runtime and dependencies.

```bash
demo-create run-script [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `script_path` | Path to Python script to execute with CLI environment **(required)** |  |

---

## `demo-create python`

Execute Python within the CLI's environment (e.g. `demo-create python -c '...'`).

```bash
demo-create python [OPTIONS]
```

---

## `demo-create agent`

Provision Looker Conversational Analytics AI agents, ground golden queries, and publish to GE.

| Subcommand | Description |
| :--- | :--- |
| [`create`](#demo-create-agent-create) | Create a Conversational Analytics agent and ground it with golden queries. |
| [`golden-queries`](#demo-create-agent-golden-queries) | Extract dashboard queries and link them as Golden Queries to an existing agent. |
| [`publish`](#demo-create-agent-publish) | Verify Gemini Enterprise configuration and publish a CA agent to it (Gate 5). |

### `demo-create agent create`

Create a Conversational Analytics agent and ground it with golden queries.

```bash
demo-create agent create [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--model` | LookML model name |  |
| `--explore` | Primary explore name |  |
| `--dashboards-dir` | Directory with *.dashboard.lookml files |  |
| `--dashboard-file` | Specific *.dashboard.lookml file |  |
| `--dashboard-id` | Deployed Looker dashboard ID for query extraction |  |
| `--name` | Custom Assistant name |  |
| `--instructions` | Custom system prompt instructions |  |
| `--publish-ge` | Automatically configure and publish to Gemini Enterprise |  |
| `--non-interactive` | Run non-interactively without prompting for GE reconfigurations |  |
| `--looker-account` | Saved Looker OAuth account alias |  |
| `--instance` | Looker instance base URL |  |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

### `demo-create agent golden-queries`

Extract dashboard queries and link them as Golden Queries to an existing agent.

```bash
demo-create agent golden-queries [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--agent-id` | Target CA Agent ID |  |
| `--dashboard-id` | Deployed Looker dashboard ID |  |
| `--dashboards-dir` | Directory with *.dashboard.lookml files |  |
| `--dashboard-file` | Specific *.dashboard.lookml file |  |
| `--model` | LookML model the extracted queries run against |  |
| `--explore` | Explore the extracted queries run against |  |
| `--looker-account` | Saved Looker OAuth account alias |  |
| `--instance` | Looker instance base URL |  |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

### `demo-create agent publish`

Verify Gemini Enterprise configuration and publish a CA agent to it (Gate 5).

```bash
demo-create agent publish [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--agent-id` | Target CA Agent ID to publish |  |
| `--non-interactive` | Run non-interactively without prompting for GE reconfigurations |  |
| `--looker-account` | Saved Looker OAuth account alias |  |
| `--instance` | Looker instance base URL |  |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

---

## `demo-create ge`

Inspect and configure Looker Gemini Enterprise (GE) integration.

| Subcommand | Description |
| :--- | :--- |
| [`configure`](#demo-create-ge-configure) | Configure Gemini Enterprise settings in Looker and grant the required IAM role. |
| [`publish`](#demo-create-ge-publish) | Verify Gemini Enterprise configuration and publish a CA agent to it (Gate 5). |
| [`status`](#demo-create-ge-status) | Fetch and display current Looker Gemini enablement and GE configuration. |

### `demo-create ge configure`

Configure Gemini Enterprise settings in Looker and grant the required IAM role.

Discovers Discovery Engine apps in the target project, patches Looker's
`gemini_enablement` settings, and grants the Looker service account
`roles/discoveryengine.admin`.

```bash
demo-create ge configure [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--gcp-project` | Target Google Cloud Project ID |  |
| `--instance` | Looker instance base URL |  |
| `--looker-account` | Saved Looker OAuth account alias |  |
| `--app-id` | Gemini Enterprise App/Engine ID |  |
| `--location` | Gemini Enterprise Location/Region | `global` |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

### `demo-create ge publish`

Verify Gemini Enterprise configuration and publish a CA agent to it (Gate 5).

An alias for `looker_demo_cli.commands.agent.agent_publish`, kept
because Gate 5 of the orchestration flow is about Gemini Enterprise and
looking for the verb under `ge` is the natural instinct.

Both spellings now share one implementation rather than one calling the
other as a Python function -- an arrangement that held together only
because the two declared identical options in identical order, and that
reported `"agent publish"` in this command's *success* envelope while its
*failure* envelope said `"ge publish"`.

```bash
demo-create ge publish [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--agent-id` | Target CA Agent ID to publish |  |
| `--non-interactive` | Run non-interactively without prompting for GE reconfigurations |  |
| `--looker-account` | Saved Looker OAuth account alias |  |
| `--instance` | Looker instance base URL |  |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

### `demo-create ge status`

Fetch and display current Looker Gemini enablement and GE configuration.

```bash
demo-create ge status [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--instance` | Looker instance base URL |  |
| `--looker-account` | Saved Looker OAuth account alias |  |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

---

## `demo-create env`

Manage local demo workspace virtual environment and runtime health.

| Subcommand | Description |
| :--- | :--- |
| [`info`](#demo-create-env-info) | Display runtime environment details and critical dependency pin health. |
| [`init`](#demo-create-env-init) | Initialize a dedicated .venv in the target directory with all pinned tools. |

### `demo-create env info`

Display runtime environment details and critical dependency pin health.

```bash
demo-create env info [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

### `demo-create env init`

Initialize a dedicated .venv in the target directory with all pinned tools.

```bash
demo-create env init [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--dir` | Directory where .venv will be created. Defaults to the current directory. |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

---

## `demo-create data`

Design, synthesize, inspect, and upload BigQuery demo datasets.

| Subcommand | Description |
| :--- | :--- |
| [`generate`](#demo-create-data-generate) | Synthesize high-fidelity relational Parquet dataset tables locally. |
| [`inspect`](#demo-create-data-inspect) | Inspect tables, schemas, and metadata in a BigQuery dataset. |
| [`upload`](#demo-create-data-upload) | Upload local Parquet tables into a BigQuery dataset. |

### `demo-create data generate`

Synthesize high-fidelity relational Parquet dataset tables locally.

```bash
demo-create data generate [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--domain` | Domain theme name (e.g. supply_chain, trucking_iot) | `logistics_analytics` |
| `--row-count` | Target fact row count | `1000` |
| `--output-dir` | Local directory to write Parquet files |  |
| `--upload` | Automatically upload synthesized Parquet tables to BigQuery |  |
| `--gcp-project` | Target GCP Project ID if uploading |  |
| `--dataset` | Target BigQuery dataset ID if uploading |  |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

### `demo-create data inspect`

Inspect tables, schemas, and metadata in a BigQuery dataset.

```bash
demo-create data inspect [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--dataset` | BigQuery dataset ID |  |
| `--gcp-project` | Target GCP Project ID |  |
| `--location` | BigQuery dataset location | `US` |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

### `demo-create data upload`

Upload local Parquet tables into a BigQuery dataset.

```bash
demo-create data upload [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--parquet-dir` | Directory containing Parquet files |  |
| `--dataset` | Target BigQuery dataset ID |  |
| `--gcp-project` | Target GCP Project ID |  |
| `--location` | BigQuery dataset location | `US` |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

---

## `demo-create lookml`

Generate LookML models from BigQuery/Knowledge Catalog or Parquet, and deploy.

| Subcommand | Description |
| :--- | :--- |
| [`clean-root`](#demo-create-lookml-clean-root) | Delete duplicate root-level LookML files that shadow their subfolder copies. |
| [`deploy`](#demo-create-lookml-deploy) | Push local LookML to the dev workspace, validate it, and deploy to production. |
| [`model`](#demo-create-lookml-model) | Generate LookML views, explores, and dashboards from BigQuery or Parquet. |
| [`optimize`](#demo-create-lookml-optimize) | Patch staged LookML in place with Google Cloud server performance best practices. |
| [`restore`](#demo-create-lookml-restore) | Restore LookML files from the `.backup_pre_opt` snapshot. |

### `demo-create lookml clean-root`

Delete duplicate root-level LookML files that shadow their subfolder copies.

For example `users.view.lkml` at the project root alongside
`views/users.view.lkml`. These orphans desync the remote master branch.

```bash
demo-create lookml clean-root [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--looker-project` | Looker project name to audit and clean |  |
| `--looker-account` | Saved Looker OAuth account alias |  |
| `--instance` | Looker instance base URL |  |
| `--dry-run` | Report root duplicates without deleting them |  |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

### `demo-create lookml deploy`

Push local LookML to the dev workspace, validate it, and deploy to production.

```bash
demo-create lookml deploy [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--lookml-dir` | Directory containing LookML files |  |
| `--looker-project` | Looker project name |  |
| `--looker-account` | Saved Looker OAuth account alias |  |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

### `demo-create lookml model`

Generate LookML views, explores, and dashboards from BigQuery or Parquet.

```bash
demo-create lookml model [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--dataset` | Existing BigQuery dataset ID to model |  |
| `--tables` | Comma-separated list of table names to include |  |
| `--parquet-dir` | Directory containing Parquet files |  |
| `--looker-project` | Looker project and model name |  |
| `--connection` | Looker database connection name; reused from prior state if omitted |  |
| `--output-dir` | Directory to output generated LookML files |  |
| `--gcp-project` | Target GCP Project ID |  |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

### `demo-create lookml optimize`

Patch staged LookML in place with Google Cloud server performance best practices.

```bash
demo-create lookml optimize [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--lookml-dir` | Directory containing LookML files |  |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--backup`, `--no-backup` | Snapshot LookML files into .backup_pre_opt before patching | `true` |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

### `demo-create lookml restore`

Restore LookML files from the `.backup_pre_opt` snapshot.

```bash
demo-create lookml restore [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--lookml-dir` | Directory containing LookML files |  |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |

---

## `demo-create embed`

Scaffold standalone Embedded Analytics web applications and portals.

| Subcommand | Description |
| :--- | :--- |
| [`scaffold`](#demo-create-embed-scaffold) | Scaffold a full-stack React/Vite analytics embed portal workspace. |

### `demo-create embed scaffold`

Scaffold a full-stack React/Vite analytics embed portal workspace.

```bash
demo-create embed scaffold [OPTIONS]
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `--looker-project` | Looker/demo project name |  |
| `--target-dir` | Directory where the web app will be scaffolded |  |
| `--dashboard-id` | Looker dashboard ID to embed |  |
| `--brand-name` | Customer brand display name |  |
| `--instance` | Looker instance URL |  |
| `--json` | Emit the result envelope as JSON on stdout |  |
| `--state-file` | Path to .demo-state.json. Defaults to discovering it in the current directory. |  |
