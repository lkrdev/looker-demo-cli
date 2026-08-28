# Looker Demo Creator (`demo-create`)

> **Automated, deterministic orchestrator for creating end-to-end Looker demos, synthetic BigQuery datasets, LookML semantic models, and Embedded Analytics portals.**

---

## Overview

`demo-create` is a developer and AI agent tool that unifies the end-to-end Looker demo creation lifecycle into a single workflow:
1. **Pre-flight & Environment Audit (`pre-check`)**: Verifies active GCP/ADC accounts, installs/patches global MCP tools (`data-designer`, `bigquery`, `lkr_codemode`), and organizes agent skills into intent-based subfolders.
2. **Dataset Decision & Synthesis**: Automatically checks for existing BigQuery datasets, enables data augmentation or green-field relational schema generation, and validates referential integrity.
3. **BigQuery Loading**: Creates datasets and partitioned/clustered tables in BigQuery.
4. **LookML Generation & Deployment**: Autogenerates production-ready views, explores, and executive dashboards, provisioning and deploying them directly to the Looker instance using `lkr-dev-cli`.
5. **Embedded Portal Scaffolding**: Clones and configures a clean, dedicated `looker-embed-demo` workspace for external client demos.

---

## Quickstart & Execution

### 1. Run Instantly with `uvx` (No Installation Required)

Once published to PyPI or your package index, you can execute the CLI on-demand in an ephemeral, isolated environment without creating a virtual environment or pre-installing dependencies:

```bash
# Run directly from the package name
uvx looker-demo-cli pre-check --fix

# Or invoke the `demo-create` binary explicitly
uvx --from looker-demo-cli demo-create pre-check --fix
```

#### Run with Options & Flags:
```bash
# Run pre-flight audit and auto-fix MCP / skills
uvx looker-demo-cli pre-check --fix

# Run end-to-end interactive demo creator
uvx looker-demo-cli run --project=retail_analytics --scope=internal
```

---

### 2. Install as a Persistent CLI Tool (`uv tool`)

To make `demo-create` / `looker-demo-cli` globally available on your shell's `PATH`:

```bash
# Install the published package globally in an isolated environment
uv tool install looker-demo-cli

# Now you can run it directly anywhere:
demo-create pre-check --fix
# or
looker-demo-cli pre-check --fix
```

To update to the latest version at any time:
```bash
uv tool upgrade looker-demo-cli
```

---

### 3. Running from Local Source or Git (Development)

If you are developing locally or testing before publishing:

#### Run directly from local source directory:
```bash
# Run from repository root
uvx --from . demo-create pre-check --fix

# Or run from anywhere by pointing to the repository path
uvx --from /path/to/looker-demo-cli demo-create pre-check --fix
```

#### Run directly from a Git repository:
```bash
uvx --from git+https://github.com/lkrdev/looker-demo-cli.git demo-create pre-check --fix
```

#### Local Editable Installation:
```bash
cd ~/looker-demo-cli
uv venv
uv pip install -e .
```

---

## Commands & Usage

### 1. Environment & Skill Audit (`pre-check`)
```bash
# Run visual audit of GCP credentials, MCP tools, and intent skills
demo-create pre-check

# Automatically install missing MCP configs and symlink skills into intent subfolders
demo-create pre-check --fix

# Emit raw JSON report for programmatic agent consumption
demo-create pre-check --json
```

### 2. End-to-End Demo Creation (`run`)
```bash
# Interactive wizard
demo-create run --project=retail_insights --scope=internal

# Non-interactive / Agent Mode
demo-create run \
  --project=logistics_analytics \
  --scope=external \
  --gcp-project=my-analytics-project \
  --gcp-account=user@example.com \
  --agent-mode
```

---

## Intent-Based Skill Organization

When you run `demo-create pre-check --fix`, skills are organized into `~/.gemini/config/skills/`:

```
~/.gemini/config/skills/
├── data-design/
│   ├── data-designer/
│   ├── data-designer-architect/
│   ├── data-designer-engineer/
│   └── data-designer-evaluator/
├── lookml/
│   ├── repo-lookml/
│   ├── lookml-model/
│   ├── lookml-explore/
│   ├── lookml-view/
│   ├── lookml-dashboard/
│   └── embed-themes/
└── embed-portal/
    ├── setup-embed-demo/
    ├── customize-frontend/
    ├── customize-frontend-branding/
    ├── customize-frontend-theme/
    └── sso-embed/
```

This guarantees that any Jetski agent in any directory can discover and execute Looker demo workflows.
