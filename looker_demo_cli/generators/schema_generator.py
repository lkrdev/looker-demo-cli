from __future__ import annotations

import datetime
from pathlib import Path
import random
from typing import Any
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from looker_demo_cli.generators.lookml_generator import LookMLTableSpec


class EntityFieldSpec(BaseModel):
    name: str
    type: str  # STRING, INT64, FLOAT64, TIMESTAMP, DATE, BOOL
    is_primary_key: bool = False
    is_foreign_key: bool = False
    foreign_reference: str | None = None  # TableName.Field
    description: str | None = None
    sample_values: list[Any] | None = None


class EntitySchemaSpec(BaseModel):
    table_name: str
    table_type: str = "dimension"  # fact or dimension
    fields: list[EntityFieldSpec] = Field(default_factory=list)
    primary_key: str | None = None
    foreign_keys: dict[str, str] = Field(default_factory=dict)  # fk_col -> ParentTable.ParentCol
    row_count: int = 1000


class DomainBlueprint(BaseModel):
    domain_name: str
    entities: list[EntitySchemaSpec] = Field(default_factory=list)
    description: str = ""


class DynamicDataSynthesizer:
    """Generates realistic relational Parquet datasets dynamically from DomainBlueprints."""

    def synthesize_dataset(
        self,
        blueprint: DomainBlueprint,
        output_dir: Path,
        micro_sample_only: bool = False,
    ) -> list[LookMLTableSpec]:
        """Synthesize Parquet tables from blueprint entities and return LookMLTableSpecs."""
        output_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now(datetime.UTC)

        # Store generated primary key pools for foreign key referential integrity
        generated_pk_pools: dict[str, list[str]] = {}
        table_specs: list[LookMLTableSpec] = []

        # Sort entities topologically: dimensions first, then fact tables
        sorted_entities = sorted(
            blueprint.entities,
            key=lambda e: (1 if e.table_type == "fact" else 0, len(e.foreign_keys)),
        )

        for entity in sorted_entities:
            row_count = 10 if micro_sample_only else entity.row_count
            df = self._generate_entity_dataframe(
                entity=entity,
                row_count=row_count,
                pk_pools=generated_pk_pools,
                current_time=now,
            )

            # Write parquet
            parquet_path = output_dir / f"{entity.table_name}.parquet"
            df.to_parquet(parquet_path, index=False)

            # Record PK pool for child FK references
            pk_col = entity.primary_key
            if not pk_col:
                for f in entity.fields:
                    if f.is_primary_key:
                        pk_col = f.name
                        break
            if pk_col and pk_col in df.columns:
                generated_pk_pools[entity.table_name] = df[pk_col].astype(str).tolist()

            # Build LookMLTableSpec
            schema_fields: dict[str, str] = {}
            for col_name, dtype in df.dtypes.items():
                if "int" in str(dtype).lower():
                    schema_fields[str(col_name)] = "INT64"
                elif "float" in str(dtype).lower():
                    schema_fields[str(col_name)] = "FLOAT64"
                elif "bool" in str(dtype).lower():
                    schema_fields[str(col_name)] = "BOOL"
                elif "datetime" in str(dtype).lower():
                    schema_fields[str(col_name)] = "TIMESTAMP"
                elif str(col_name).endswith(("_date", "_day")) or str(col_name).startswith("date_"):
                    schema_fields[str(col_name)] = "DATE"
                else:
                    schema_fields[str(col_name)] = "STRING"

            table_specs.append(
                LookMLTableSpec(
                    table_name=entity.table_name,
                    table_type=entity.table_type,
                    schema_fields=schema_fields,
                    primary_key=pk_col,
                    foreign_keys=entity.foreign_keys,
                )
            )

        return table_specs

    def _generate_entity_dataframe(
        self,
        entity: EntitySchemaSpec,
        row_count: int,
        pk_pools: dict[str, list[str]],
        current_time: datetime.datetime,
    ) -> pd.DataFrame:
        """Generate a realistic synthetic DataFrame for a single entity spec."""
        data: dict[str, list[Any]] = {}
        pk_col = entity.primary_key
        prefix = entity.table_name.replace("dim_", "").replace("fct_", "")[:3].upper()

        if pk_col:
            data[pk_col] = [f"{prefix}-{i:05d}" for i in range(1, row_count + 1)]

        for field in entity.fields:
            if field.name == pk_col:
                continue

            # 1. Foreign Key resolution
            if field.name in entity.foreign_keys or field.is_foreign_key:
                ref = entity.foreign_keys.get(field.name) or field.foreign_reference or ""
                parent_table = ref.split(".")[0] if "." in ref else ""
                if parent_table in pk_pools and pk_pools[parent_table]:
                    data[field.name] = [random.choice(pk_pools[parent_table]) for _ in range(row_count)]
                else:
                    data[field.name] = [f"{field.name[:3].upper()}-{random.randint(1, max(10, row_count // 5)):05d}" for _ in range(row_count)]
                continue

            # 2. Date / Timestamp generation
            if field.type == "DATE" or field.name.endswith(("_date", "_day")) or field.name.startswith("date_"):
                data[field.name] = [
                    (current_time - datetime.timedelta(days=random.randint(1, 730))).date()
                    for _ in range(row_count)
                ]
            elif field.type == "TIMESTAMP" or field.name.endswith(("_time", "_at")):
                data[field.name] = [
                    current_time - datetime.timedelta(days=random.randint(1, 730), seconds=random.randint(0, 86400))
                    for _ in range(row_count)
                ]
            # 3. Numeric values
            elif field.type in ("FLOAT64", "NUMERIC", "DOUBLE"):
                if any(k in field.name.lower() for k in ["usd", "amount", "price", "cost", "revenue", "payout", "premium", "income", "limit", "value"]):
                    data[field.name] = [round(random.uniform(50.0, 5000.0), 2) for _ in range(row_count)]
                elif any(k in field.name.lower() for k in ["rate", "pct", "discount", "margin", "ratio", "score"]):
                    data[field.name] = [round(random.uniform(0.01, 0.95), 3) for _ in range(row_count)]
                else:
                    data[field.name] = [round(random.uniform(1.0, 100.0), 2) for _ in range(row_count)]
            elif field.type in ("INT64", "INTEGER"):
                if "age" in field.name.lower():
                    data[field.name] = [random.randint(18, 75) for _ in range(row_count)]
                elif "credit" in field.name.lower():
                    data[field.name] = [random.randint(580, 850) for _ in range(row_count)]
                elif "nps" in field.name.lower() or "rating" in field.name.lower():
                    data[field.name] = [random.randint(1, 10) for _ in range(row_count)]
                elif "count" in field.name.lower() or "days" in field.name.lower():
                    data[field.name] = [random.randint(1, 30) for _ in range(row_count)]
                else:
                    data[field.name] = [random.randint(1, 1000) for _ in range(row_count)]
            # 4. Booleans
            elif field.type in ("BOOL", "BOOLEAN"):
                data[field.name] = [bool(random.random() < 0.7) for _ in range(row_count)]
            # 5. Strings / Categorical pools
            else:
                if field.sample_values:
                    data[field.name] = [random.choice(field.sample_values) for _ in range(row_count)]
                elif "status" in field.name.lower():
                    data[field.name] = [random.choice(["Active", "Completed", "Pending", "Cancelled"]) for _ in range(row_count)]
                elif "segment" in field.name.lower() or "tier" in field.name.lower():
                    data[field.name] = [random.choice(["Standard", "Preferred", "Enterprise", "High-Growth"]) for _ in range(row_count)]
                elif "type" in field.name.lower() or "category" in field.name.lower():
                    data[field.name] = [random.choice(["Category A", "Category B", "Category C", "Category D"]) for _ in range(row_count)]
                elif "channel" in field.name.lower():
                    data[field.name] = [random.choice(["Direct Online", "Mobile App", "Partner API", "Broker Referral"]) for _ in range(row_count)]
                elif "state" in field.name.lower():
                    data[field.name] = [random.choice(["CA", "NY", "TX", "FL", "IL", "WA", "CO", "MA"]) for _ in range(row_count)]
                elif "name" in field.name.lower():
                    firsts = ["Jordan", "Taylor", "Morgan", "Alex", "Casey", "Riley", "Cameron", "Avery"]
                    data[field.name] = [f"{random.choice(firsts)} {i}" for i in range(1, row_count + 1)]
                elif "email" in field.name.lower():
                    data[field.name] = [f"user_{i:04d}@example.com" for i in range(1, row_count + 1)]
                else:
                    data[field.name] = [f"{field.name.title()} {i}" for i in range(1, row_count + 1)]

        return pd.DataFrame(data)


def create_dynamic_blueprint_from_name(domain_name: str) -> DomainBlueprint:
    """Dynamically construct a sensible relational blueprint for any domain name."""
    clean_name = domain_name.lower().replace("-", "_").replace(" ", "_")
    entity_name = clean_name.replace("dim_", "").replace("fct_", "")

    return DomainBlueprint(
        domain_name=clean_name,
        description=f"Dynamic relational dataset for {domain_name}.",
        entities=[
            EntitySchemaSpec(
                table_name=f"dim_{entity_name}_entities",
                table_type="dimension",
                primary_key="entity_id",
                row_count=1000,
                fields=[
                    EntityFieldSpec(name="entity_id", type="STRING", is_primary_key=True),
                    EntityFieldSpec(name="entity_name", type="STRING"),
                    EntityFieldSpec(name="category", type="STRING", sample_values=["Tier 1", "Tier 2", "Tier 3", "Enterprise"]),
                    EntityFieldSpec(name="status", type="STRING", sample_values=["Active", "Pending", "Archived"]),
                    EntityFieldSpec(name="region", type="STRING", sample_values=["North America", "EMEA", "APAC", "LATAM"]),
                    EntityFieldSpec(name="created_date", type="DATE"),
                ],
            ),
            EntitySchemaSpec(
                table_name=f"fct_{entity_name}_events",
                table_type="fact",
                primary_key="event_id",
                foreign_keys={"entity_id": f"dim_{entity_name}_entities.entity_id"},
                row_count=5000,
                fields=[
                    EntityFieldSpec(name="event_id", type="STRING", is_primary_key=True),
                    EntityFieldSpec(name="entity_id", type="STRING", is_foreign_key=True),
                    EntityFieldSpec(name="event_date", type="DATE"),
                    EntityFieldSpec(name="event_type", type="STRING", sample_values=["Type A", "Type B", "Type C", "Type D"]),
                    EntityFieldSpec(name="amount_usd", type="FLOAT64"),
                    EntityFieldSpec(name="fee_usd", type="FLOAT64"),
                    EntityFieldSpec(name="net_value_usd", type="FLOAT64"),
                    EntityFieldSpec(name="status", type="STRING", sample_values=["Success", "Processing", "Flagged", "Refunded"]),
                    EntityFieldSpec(name="channel", type="STRING", sample_values=["Web", "Mobile", "API", "Partner"]),
                ],
            ),
        ],
    )


def generate_domain_dataset(
    target: str | DomainBlueprint,
    output_dir: Path,
    micro_sample_only: bool = False,
) -> list[LookMLTableSpec]:
    """Generate dynamic synthetic dataset from a DomainBlueprint or dynamic domain name."""
    if isinstance(target, DomainBlueprint):
        blueprint = target
    else:
        blueprint = create_dynamic_blueprint_from_name(target)

    synthesizer = DynamicDataSynthesizer()
    return synthesizer.synthesize_dataset(blueprint=blueprint, output_dir=output_dir, micro_sample_only=micro_sample_only)
