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

## 2. Execution Workflows with `demo-create data generate`

You can synthesize datasets using either a **Declarative `DomainBlueprint` JSON (`--schema-file`)** or a **Vectorized Python Script (`--script`)**. Both routes execute through `ModularDAGSynthesizer`, run `TableValidator` in-memory assertions (`<50ms`), and upload via BigQuery ADC.

### Option A: Declarative `DomainBlueprint` JSON (`--schema-file`)
Write a `schema.json` specifying topological entities, primary/foreign keys, distributions, weights, and cross-column formulas:

```json
{
  "domain_name": "saas_finops",
  "description": "Cloud FinOps & Customer Telemetry Dataset",
  "entities": [
    {
      "table_name": "dim_organizations",
      "table_type": "dimension",
      "primary_key": "org_id",
      "row_count": 500,
      "fields": [
        {"name": "org_id", "type": "STRING", "is_primary_key": true},
        {"name": "org_name", "type": "STRING"},
        {
          "name": "tier",
          "type": "STRING",
          "sample_values": ["Enterprise", "Growth", "Mid-Market", "Standard"],
          "weights": [0.15, 0.25, 0.30, 0.30]
        },
        {"name": "region", "type": "STRING", "sample_values": ["North America", "EMEA", "APAC", "LATAM"]},
        {"name": "created_date", "type": "DATE"}
      ]
    },
    {
      "table_name": "fct_cloud_usage",
      "table_type": "fact",
      "primary_key": "usage_id",
      "foreign_keys": {"org_id": "dim_organizations.org_id"},
      "row_count": 15000,
      "fields": [
        {"name": "usage_id", "type": "STRING", "is_primary_key": true},
        {"name": "org_id", "type": "STRING", "is_foreign_key": true},
        {"name": "event_time", "type": "TIMESTAMP"},
        {"name": "compute_cost_usd", "type": "FLOAT64", "distribution": "lognormal", "mean": 4.2, "std": 0.9},
        {"name": "storage_cost_usd", "type": "FLOAT64", "distribution": "lognormal", "mean": 2.8, "std": 0.6},
        {"name": "total_cost_usd", "type": "FLOAT64", "formula": "compute_cost_usd + storage_cost_usd"}
      ]
    }
  ]
}
```

### Option B: Custom Vectorized Python Script (`--script`)
For complex multi-table tier coupling or custom domain logic, author a Python script defining `generate_tables(output_dir: Path, row_count: int) -> dict[str, pd.DataFrame]`:

```python
from pathlib import Path
import numpy as np
import pandas as pd

def generate_tables(output_dir: Path, row_count: int = 15000) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(42)
    n_orgs = max(200, row_count // 25)
    org_ids = [f"ORG-{i:05d}" for i in range(1, n_orgs + 1)]
    tiers = rng.choice(["Enterprise", "Growth", "Standard"], size=n_orgs, p=[0.2, 0.35, 0.45])
    
    df_orgs = pd.DataFrame({
        "org_id": org_ids,
        "tier": tiers,
        "region": rng.choice(["North America", "EMEA", "APAC"], size=n_orgs, p=[0.5, 0.3, 0.2]),
    })
    
    # Pareto weights for FK sampling so top orgs generate majority of usage events
    weights = rng.pareto(a=1.5, size=n_orgs) + 1.0
    weights /= weights.sum()
    sampled_indices = rng.choice(np.arange(n_orgs), size=row_count, p=weights)
    
    # Tier-coupled cost generation
    sampled_tiers = tiers[sampled_indices]
    base_mean = np.where(sampled_tiers == "Enterprise", 5.5, np.where(sampled_tiers == "Growth", 4.0, 2.8))
    compute_cost = np.round(rng.lognormal(mean=base_mean, sigma=0.7, size=row_count), 2)
    discount_usd = np.round(np.where(sampled_tiers == "Enterprise", compute_cost * 0.15, 0.0), 2)
    net_cost_usd = np.round(compute_cost - discount_usd, 2)
    
    now = pd.Timestamp.now(tz="UTC").floor("s")
    start_times = now - pd.to_timedelta(rng.exponential(scale=90, size=row_count).clip(0, 365), unit="D")
    end_times = start_times + pd.to_timedelta(rng.lognormal(mean=4.0, sigma=1.0, size=row_count), unit="s")
    
    df_usage = pd.DataFrame({
        "usage_id": [f"USG-{i:06d}" for i in range(1, row_count + 1)],
        "org_id": [org_ids[idx] for idx in sampled_indices],
        "session_start_time": start_times.floor("s"),
        "session_end_time": end_times.floor("s"),
        "compute_cost_usd": compute_cost,
        "discount_usd": discount_usd,
        "net_cost_usd": net_cost_usd,
    })
    return {"dim_organizations": df_orgs, "fct_cloud_usage": df_usage}
```

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
