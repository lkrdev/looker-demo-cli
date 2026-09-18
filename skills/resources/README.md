# Shared Skills Resources Catalog (`skills/resources/`)

This directory contains shared reference documentation, templates, guardrails, and code recipes imported across the `looker-demo-cli` skills and subagents to keep individual `SKILL.md` files concise and DRY.

| Resource File | Imported By Skills / Subagents | Contents |
| :--- | :--- | :--- |
| [`spec-and-data-dictionary-template.md`](spec-and-data-dictionary-template.md) | `demo-spec`, `bigquery-metadata`, `knowledge-catalog-metadata`, `looker-demo-orchestrator` | Canonical 9-section `SPEC.md` template, `## Data Dictionary & Semantic Context` schema, and Dataplex Profile-to-LookML Decision Matrix |
| [`delivery-report-template.md`](delivery-report-template.md) | `looker-demo-orchestrator`, `demo-spec` | Canonical 7-section `DELIVERY_REPORT.md` template with mandatory `[SPEC.md](SPEC.md)` link |
| [`dashboard-polish-standards.md`](dashboard-polish-standards.md) | `looker-demo-orchestrator`, `lookml-dashboard-designer`, `looker-visualizations` | 4 Mandatory Default Polish Rules, Highcharts `series_types` contract, `advanced_vis_config` dual-axis & `type: text` templates, and 9-Point Executive UI Quality Checklist |
| [`auth-and-guardrails.md`](auth-and-guardrails.md) | `looker-demo-orchestrator`, `data-engineer`, `bigquery-metadata`, `knowledge-catalog-metadata` | GCP/Looker OAuth & ADC recovery recipes, `lkr-cli` registration JSON, SSH port 8000 forwarding, Target Project Integrity guardrail, and Subagent Kill-Fence |
| [`synthetic-data-examples.md`](synthetic-data-examples.md) | `synthetic-data-authoring`, `data-engineer` | Declarative `DomainBlueprint` `schema.json`, custom vectorized `generate_tables()` Python script, and `--json-scorecard` contract |
| [`lkr-code-mode-reference.md`](lkr-code-mode-reference.md) | `looker-demo-orchestrator`, `lookml-qa-validator` | `lkr code-mode sandbox` Monty Sandbox function cheat-sheet, bare git project initialization, and inline query verification recipes |
