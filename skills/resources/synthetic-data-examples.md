# Synthetic Data Authoring Reference Templates (`synthetic-data-authoring`)

This shared resource contains the complete `DomainBlueprint` JSON (`--schema-file`), custom vectorized Python generator script (`--script`), and `--json-scorecard` output contract used by [`synthetic-data-authoring`](../synthetic-data-authoring/SKILL.md) and [`data-engineer`](../looker-demo-orchestrator/subagents/data-engineer.md).

---

## 1. Declarative `DomainBlueprint` JSON (`--schema-file`)

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

---

## 2. Custom Vectorized Python Generator Script (`--script`)

Author a script defining `generate_tables(output_dir: Path, row_count: int) -> dict[str, pd.DataFrame]` when custom multi-table tier coupling is needed:

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

    # Pareto weights (80/20 rule) for FK sampling so top orgs generate majority of usage events
    weights = rng.pareto(a=1.5, size=n_orgs) + 1.0
    weights /= weights.sum()
    sampled_indices = rng.choice(np.arange(n_orgs), size=row_count, p=weights)

    # Tier-coupled cost generation & exact mathematical identity columns
    sampled_tiers = tiers[sampled_indices]
    base_mean = np.where(sampled_tiers == "Enterprise", 5.5, np.where(sampled_tiers == "Growth", 4.0, 2.8))
    compute_cost = np.round(rng.lognormal(mean=base_mean, sigma=0.7, size=row_count), 2)
    discount_usd = np.round(np.where(sampled_tiers == "Enterprise", compute_cost * 0.15, 0.0), 2)
    net_cost_usd = np.round(compute_cost - discount_usd, 2)

    # Exponential growth curve + strict timestamp monotonicity (start_time <= end_time)
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

## 3. Verification Scorecard JSON Contract (`--json-scorecard`)

```json
{
  "status": "SUCCESS",
  "domain": "telemetry_analytics",
  "execution_time_seconds": 1.84,
  "records_per_second": 8695.6,
  "tables": {
    "dim_systems": {"rows": 50, "pk_uniqueness": 1.0, "orphan_fks": 0},
    "fct_sessions": {"rows": 15000, "pk_uniqueness": 1.0, "orphan_fks": 0, "temporal_valid": true}
  },
  "bigquery_load": {
    "dataset": "telemetry_analytics",
    "auth": "ADC",
    "uploaded": true,
    "partitioned_tables": ["fct_sessions"],
    "clustered_tables": ["fct_sessions"]
  },
  "sample_rows_markdown": "| session_id | system_id | duration_ms | status |\n|---|---|---|---|\n| SES-000001 | SYS-00012 | 342.1 | Completed |"
}
```
