---
name: synthetic-data-authoring
description: Master skill for authoring realistic, high-throughput synthetic relational datasets using Modular DAG generation, non-uniform statistical distributions, cross-column business logic coupling, temporal growth/seasonality curves, and Looker Explore optimization.
---

# Skill: Synthetic Data Authoring (`synthetic-data-authoring`)

Use this skill whenever designing, scripting, or generating synthetic relational datasets for Looker demos at **Gate 1 (`gate_1_data`)**. It guides you in authoring high-fidelity data that passes `TableValidator` quality gates (`0` orphan foreign keys, `100%` primary key uniqueness, strict chronological ordering) and renders authentic executive visualizations in Looker dashboards.

---

## 1. The Four Mandatory Pillars of Realistic Synthetic Data

Never generate flat uniform random numbers (`random.uniform`, `random.randint`) or uncorrelated columns. Every dataset must implement four statistical and business logic pillars:

### Pillar 1: Non-Uniform Statistical Distributions
Real-world business metrics follow skewed power laws and bell curves, never flat distributions:
- **Pareto (80/20 Rule)**: Use `rng.pareto(a=1.5)` for tenant/customer revenue concentration, account activity, and product popularity so that the top 20% of entities drive ~80% of volume.
- **Log-Normal Distributions**: Use `rng.lognormal(mean, sigma)` for right-skewed positive continuous variables such as transaction amounts (`amount_usd`), API latencies (`latency_ms`), session durations, and cloud compute costs.
- **Beta / Normal Distributions**: Use `rng.beta(a, b)` for bounded rates/ratios (`0.0` to `1.0` such as conversion rates, discount percentages, CSAT scores) and `rng.normal(loc, scale)` for physical or demographic metrics.
- **Weighted Categoricals**: Always supply explicit probability vectors `p=[...]` when sampling categorical columns (e.g., `status` weights `[0.65, 0.20, 0.10, 0.05]` for `Completed`, `Active`, `Pending`, `Cancelled`).

### Pillar 2: Cross-Column Business Logic & Tier Coupling
Columns within a row must be mathematically and semantically coupled:
- **Tier-Dependent Distributions**: Sample the parent entity's tier (`Enterprise`, `Mid-Market`, `Growth`, `Standard`) first, then condition downstream metrics on that tier:
  - *Example*: `Enterprise` accounts have higher MRR (`lognormal(mean=9.5, sigma=0.6)`), higher SLA targets (`99.95%`), and lower churn probability (`2%`), whereas `Standard` accounts have lower MRR (`lognormal(mean=5.2, sigma=0.8)`) and higher churn (`12%`).
- **Mathematical Identity Columns**: Derived financial or operational columns must hold exact mathematical equality across every row:
  - `net_revenue_usd = gross_revenue_usd - discount_usd - tax_usd`
  - `total_tokens = prompt_tokens + completion_tokens`
  - `gross_margin_pct = (revenue_usd - cogs_usd) / revenue_usd`
- **Status-Conditional Nulls**: If `order_status == 'Pending'`, then `shipped_at` and `delivered_at` MUST be `NULL` (`pd.NaT`).

### Pillar 3: Temporal Trends, Seasonality & Strict Monotonicity
Time-series charts in Looker look artificial if row counts are flat across every month:
- **Organic Growth Curve (MoM Trend)**: Sample event timestamps using an exponential or power-law decay from `now` backward over 12–24 months (`rng.exponential(scale=120)`) so recent months show clear, realistic business growth in Looker line/area charts.
- **Diurnal & Day-of-Week Seasonality**: Weight intra-day hours around business peaks (e.g., normal distribution centered at `14:00 UTC`) and attenuate weekend volume by `40–60%` for B2B domains.
- **Strict Timestamp Monotonicity (`TableValidator` Gate)**: Every sequential timestamp pair within a table or across parent-child tables must satisfy chronological ordering:
  - `created_at <= updated_at <= resolved_at`
  - `session_start_time <= session_end_time`
  - `order_date <= ship_date <= delivery_date`
  - Add positive log-normal duration offsets (`start_time + pd.to_timedelta(rng.lognormal(...), unit='s')`) rather than sampling timestamps independently.

### Pillar 4: Looker Explore & Symmetric Aggregate Optimization
Synthetic data must be engineered specifically for Looker's SQL generation engine and BigQuery cost optimization:
- **100% Primary Key Uniqueness**: Every table's primary key (`_id`) must be non-null and 100% unique so Looker's symmetric aggregates (`SUM DISTINCT`, `AVG DISTINCT`) never suffer fanout inflation.
- **Pareto Foreign Key Sampling (Realistic Join Fanouts)**: When generating fact rows (`fct_*`), sample foreign keys from the parent dimension's primary key pool using Pareto weights rather than uniform sampling. This ensures top customers have hundreds of orders while long-tail customers have 1–2 orders.
- **Clean Categorical Domains for Filtered Measures**: Ensure categorical columns used in LookML filtered measures (`status`, `tier`, `region`, `channel`, `severity`) use clean, consistent title-case or uppercase strings without trailing whitespace or stray typos.
- **BigQuery Day Partitioning & Clustering Alignment**: Include a primary `TIMESTAMP` or `DATE` column (`event_time`, `created_at`, `order_date`) on every fact table and standard foreign key / filter columns (`*_id`, `*_type`, `*_status`, `tier`, `region`) so `BigQueryOptimizationAdvisor` automatically applies Day Partitioning and Clustering.

---

## 2. Execution Workflows & Reference Templates (`demo-create data generate`)

You can synthesize datasets using either a **Declarative `DomainBlueprint` JSON (`--schema-file`)** or a **Custom Vectorized Python Script (`--script`)**. Both routes execute through `ModularDAGSynthesizer`, run `TableValidator` in-memory assertions (`<50ms`), and upload via BigQuery ADC.

Consult **[`synthetic-data-examples.md`](../resources/synthetic-data-examples.md)** for full copy-pasteable implementations of:
1. **Option A: Declarative `DomainBlueprint` JSON (`--schema-file`)** — topological entities, primary/foreign keys, non-uniform distributions (`lognormal`, `pareto`), weighted categoricals, and cross-column formulas.
2. **Option B: Custom Vectorized Python Script (`--script`)** — `generate_tables(output_dir: Path, row_count: int) -> dict[str, pd.DataFrame]` with Pareto 80/20 FK sampling, tier-coupled log-normal metrics, exact mathematical identity columns, and strict timestamp monotonicity.
3. **Verification Scorecard Contract (`--json-scorecard`)** — structure of the in-memory `TableValidator` and BigQuery ADC upload report.

---

## 3. CLI Execution Commands (`demo-create data generate`)

1. **Instant Sample Preview (`--preview -n 5`)**:
   Inspect 5 sampled rows across all tables without writing Parquet files or uploading to BigQuery:
   ```bash
   demo-create data generate --schema-file schema.json --preview -n 5
   # or with custom script:
   demo-create data generate --script generate_data.py --preview -n 5
   ```

2. **In-Memory Validation Audit (`--validate-only`)**:
   Execute the topological DAG and run `TableValidator` assertions (`PK uniqueness`, `0 orphan FKs`, `temporal monotonicity`) locally:
   ```bash
   demo-create data generate --schema-file schema.json --row-count 15000 --validate-only --json-scorecard
   ```

3. **Full Synthesis + Resilient ADC BigQuery Upload (`--upload --json-scorecard`)**:
   Generate Snappy-compressed Parquet tables, validate in memory, automatically configure BigQuery Day Partitioning & Clustering via `BigQueryOptimizationAdvisor`, and upload using Google Cloud Python SDK Application Default Credentials (ADC):
   ```bash
   demo-create data generate \
     --schema-file schema.json \
     --row-count 15000 \
     --output-dir ./artifacts/generated_data \
     --gcp-project <GCP_PROJECT_ID> \
     --dataset <DATASET_ID> \
     --upload \
     --json-scorecard
   ```
