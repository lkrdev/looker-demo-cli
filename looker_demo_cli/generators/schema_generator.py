from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd
from pydantic import BaseModel, Field


class EntityFieldSpec(BaseModel):
    name: str
    type: str  # STRING, INT64, FLOAT64, TIMESTAMP, BOOL
    is_primary_key: bool = False
    is_foreign_key: bool = False
    foreign_reference: Optional[str] = None  # TableName.Field
    description: Optional[str] = None


class EntitySchemaSpec(BaseModel):
    table_name: str
    table_type: str = "fact"  # fact or dimension
    fields: List[EntityFieldSpec] = Field(default_factory=list)
    partition_field: Optional[str] = None
    clustering_fields: List[str] = Field(default_factory=list)
    row_count: int = 1000


class DomainBlueprint(BaseModel):
    domain_name: str
    entities: List[EntitySchemaSpec] = Field(default_factory=list)
    description: str = ""
