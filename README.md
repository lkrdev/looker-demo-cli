# Looker Demo Creator (`demo-create`)

> **Automated, deterministic orchestrator for creating end-to-end Looker demos, synthetic BigQuery datasets, LookML semantic models, and Embedded Analytics portals.**

---

## Overview

`demo-create` is a developer and AI agent tool that unifies the end-to-end Looker demo creation lifecycle into a single workflow:
1. **Pre-flight & Environment Audit (`pre-check`)**: Verifies active GCP/ADC accounts, installs/patches global MCP tools (`data-designer`, `bigquery`, `knowledge-catalog`), checks Looker authentication (OAuth / API keys), and organizes agent skills (including `lkr-code-mode`) into intent-based subfolders.
2. **Dataset Decision & Synthesis**: Automatically checks for existing BigQuery datasets, enables data augmentation or green-field relational schema generation, and validates referential integrity.
3. **BigQuery Loading**: Creates datasets and partitioned/clustered tables in BigQuery.
4. **LookML Generation & Direct Code-Mode Deployment**: Autogenerates production-ready views, explores, and executive dashboards, provisioning and deploying them directly to the Looker instance using `lkr-dev-cli code-mode` without requiring an MCP server.
5. **Embedded Portal Scaffolding**: Clones and configures a clean, dedicated `looker-embed-demo` workspace for external client demos.

---

## Quickstart & Installation

### 1. Install as a Persistent CLI Tool (`uv tool` - Recommended)

To make `demo-create`, `looker-demo-cli`, AND `lkr` (`lkr-dev-cli`) globally available on your shell's `PATH` in a persistent, isolated environment:

```bash
# Install the published package globally
uv tool install looker-demo-cli

# Now all tools execute directly with zero startup latency and pre-pinned dependencies:
demo-create pre-check --fix
lkr auth list
```

To update to the latest version at any time:
```bash
uv tool upgrade looker-demo-cli
```

---

### 2. Run Ephemerally with `uvx` (Zero-Install Alternative)

You can also execute the CLI on-demand in an ephemeral cache without pre-installing:

```bash
# Run pre-flight audit and auto-fix MCP / skills
uvx looker-demo-cli pre-check --fix

# Or invoke the demo-create binary explicitly
uvx --from looker-demo-cli demo-create pre-check --fix

# Run end-to-end interactive demo creator
uvx looker-demo-cli run --project=retail_analytics --scope=internal
```

---

### 3. Workspace Virtual Environment & Script Runner

To eliminate missing dependency errors across agent scratch scripts or data synthesis pipelines:

```bash
# Initialize a local .venv with all demo packages pre-installed:
demo-create env init
source .venv/bin/activate

# Execute ad-hoc scratch scripts using the CLI's bundled Python environment:
demo-create run-script scratch/generate_data.py

# Run one-off Python commands in the demo environment:
demo-create python -c "import pandas, pyarrow, google.cloud.bigquery; print('Ready!')"
```

---

### 4. Running from Local Source or Git (Development)

If developing locally or testing from a Git repository:

#### Run directly from local source directory:
```bash
uvx --from . demo-create pre-check --fix
```

#### Run directly from Git:
```bash
uvx --from git+https://github.com/lkrdev/looker-demo-cli.git demo-create pre-check --fix
```

#### Local Editable Installation:
```bash
cd ~/looker-demo-cli
demo-create env init
source .venv/bin/activate
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

When you run `demo-create pre-check --fix`, skills are automatically pulled from remote repositories and organized into `~/.gemini/config/skills/`:

```
~/.gemini/config/skills/
├── data-design/
│   ├── data-designer/
│   ├── data-designer-architect/
│   ├── data-designer-engineer/
│   ├── data-designer-evaluator/
│   └── vertex-ai/
├── lookml/
│   ├── lkr-code-mode/
│   ├── repo-lookml/
│   ├── lookml-model/
│   ├── lookml-explore/
│   ├── lookml-view/
│   ├── lookml-dashboard/
│   ├── lookml-dashboard-to-query/
│   └── embed-themes/
└── embed-portal/
    ├── looker-demo-orchestrator/
    ├── setup-embed-demo/
    ├── customize-frontend/
    ├── customize-frontend-branding/
    ├── customize-frontend-theme/
    └── sso-embed/
```

This guarantees that any Jetski agent in any directory can discover and execute Looker demo workflows.
