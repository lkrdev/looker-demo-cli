---
name: data-designer-engineer
description: Engineering skill for authoring, validating, and debugging DataDesigner Python builder scripts (PEP 723), Jinja2 expressions, custom column generators, and direct CLI operations (uvx).
argument-hint: "[describe the schema columns, custom logic, or builder script to create]"
license: Apache-2.0
metadata:
  owner: lkr.dev
---

# Role: DataDesigner Engineer

You are a Data Engineering Specialist who writes clean, robust Python generation scripts using NVIDIA NeMo DataDesigner.

## The Script Standard (PEP 723)

All generation logic must be encapsulated in a single Python script with inline PEP 723 dependency metadata and a `load_config_builder()` entry point:

```python
# /// script
# dependencies = [
#   "data-designer",
#   "pydantic",
# ]
# ///
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import data_designer.config as dd
from pydantic import BaseModel, Field


def load_config_builder() -> dd.DataDesignerConfigBuilder:
    builder = dd.DataDesignerConfigBuilder()

    # 1. UUID Primary Key
    builder.add_column(
        dd.SamplerColumnConfig(
            name="user_id",
            sampler_type="uuid",
            params=dd.UUIDSamplerParams(),
        )
    )

    # 2. Faker Person Generator (drop=True helper)
    builder.add_column(
        dd.SamplerColumnConfig(
            name="person",
            drop=True,
            sampler_type="person_from_faker",
            params=dd.PersonFromFakerSamplerParams(locale="en_US"),
        )
    )

    # 3. Derived Jinja2 Expressions
    builder.add_column(
        dd.ExpressionColumnConfig(
            name="full_name",
            expr="{{ person.first_name }} {{ person.last_name }}",
        )
    )
    builder.add_column(
        dd.ExpressionColumnConfig(
            name="email",
            expr="{{ person.first_name | lower }}.{{ person.last_name | lower }}@example.com",
        )
    )

    # 4. Categorical with Weights
    builder.add_column(
        dd.SamplerColumnConfig(
            name="subscription_tier",
            sampler_type="category",
            params=dd.CategorySamplerParams(
                values=["FREE", "PRO", "ENTERPRISE"],
                weights=[0.60, 0.35, 0.05],
            ),
        )
    )

    # 5. Continuous Distribution
    builder.add_column(
        dd.SamplerColumnConfig(
            name="monthly_spend",
            sampler_type="uniform",
            params=dd.UniformSamplerParams(low=0.0, high=500.0, decimal_places=2),
        )
    )

    return builder
```

## Custom Column Generators

When built-in samplers are not enough, attach custom Python generator functions:

```python
@dd.custom_column_generator(
    required_columns=["monthly_spend", "subscription_tier"],
    side_effect_columns=["is_high_value_customer"],
)
def compute_customer_tier(row: dict) -> dict:
    spend = row.get("monthly_spend", 0.0)
    tier = row.get("subscription_tier", "FREE")
    row["is_high_value_customer"] = spend > 300.0 or tier == "ENTERPRISE"
    return row
```

## Jinja2 Expression Rules & Pitfalls

1. **Direct Column Access**: Use `{{ column_name }}`.
2. **Object/Field Access**: For dictionary columns (e.g. `person`), use `{{ person.first_name }}` or `{{ person.city }}`.
3. **LLM Judge Score Access**: Judge columns produce `{score_name: {reasoning: str, score: int}}`.
   - Correct: `{{ quality.correctness.score }}`
   - Incorrect: `{{ quality.correctness }}` (returns raw dict)
4. **Column Dependencies**: A column evaluated via Jinja2 MUST appear AFTER all columns it references in `builder.add_column(...)`.

## Engineering Workflow with CLI (`uvx`)

1. **Author the script**:
   Save your generator logic to a local script (e.g. `builder.py`) with PEP 723 metadata and a `load_config_builder()` function.

2. **Validate syntax and schema**:
   Run sub-second static analysis and schema validation:
   ```bash
   uvx --from git+https://github.com/lkrdev/synthetic-data-generator.git data-designer validate builder.py
   ```

3. **Preview a sample**:
   Generate an immediate sample in non-interactive mode:
   ```bash
   uvx --from git+https://github.com/lkrdev/synthetic-data-generator.git data-designer preview builder.py -n 5 --non-interactive
   ```

4. **Run batch generation**:
   Generate full dataset files (Parquet/JSONL) into the output directory:
   ```bash
   uvx --from git+https://github.com/lkrdev/synthetic-data-generator.git data-designer create builder.py -n <NUM_RECORDS> -o artifacts/
   ```
