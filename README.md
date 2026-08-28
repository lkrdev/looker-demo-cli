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

## Installation

```bash
cd /usr/local/google/home/maluka/looker-demo-cli
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
  --gcp-project=looker-demo-392616 \
  --gcp-account=admin@maluka.altostrat.com \
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
