"""Data models for Dataplex Universal Catalog metadata and snapshots.

Provides strongly typed schemas for tables, columns, relationships, governance tags,
and coverage reports captured from Google Cloud Knowledge Catalog (Dataplex) and BigQuery.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class SourceStatus(BaseModel):
    """Availability and connectivity status of an upstream metadata source."""

    source_name: str
    available: bool
    details: str = ""


class ColumnMeta(BaseModel):
    """Enriched column metadata from BigQuery and Dataplex Universal Catalog."""

    name: str
    data_type: str
    mode: str = "NULLABLE"
    description: str | None = None
    business_label: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    allowed_values: list[str] = Field(default_factory=list)
    format_pattern: str | None = None
    governance_tags: list[str] = Field(default_factory=list)
    policy_tags: list[str] = Field(default_factory=list)
    is_primary_key: bool = False
    is_foreign_key: bool = False
    foreign_key_target: str | None = None
    linked_glossary_term: str | None = None


class TableMeta(BaseModel):
    """Enriched table metadata including constraints, partition/cluster keys, and column dictionary."""

    name: str
    role: str = "dimension"
    num_rows: int = 0
    description: str | None = None
    business_label: str | None = None
    governance_tags: list[str] = Field(default_factory=list)
    partition_field: str | None = None
    clustering_fields: list[str] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: dict[str, str] = Field(default_factory=dict)
    columns: dict[str, ColumnMeta] = Field(default_factory=dict)


class Relationship(BaseModel):
    """Detected or documented entity relationship between two tables."""

    source_table: str
    source_column: str
    target_table: str
    target_column: str
    relationship_type: str = "many_to_one"
    confidence: float = 1.0
    source_type: str = "TABLE_CONSTRAINTS"


class CoverageReport(BaseModel):
    """Catalog metadata enrichment coverage statistics and recommended modeling profile."""

    total_columns: int = 0
    columns_with_descriptions: int = 0
    columns_with_labels: int = 0
    columns_with_glossary: int = 0
    columns_with_formats: int = 0
    coverage_percentage: float = 0.0
    recommended_profile: str = "auto"

    @classmethod
    def compute(cls, tables: dict[str, TableMeta]) -> CoverageReport:
        """Calculate coverage statistics and recommended profile from a dictionary of TableMeta."""
        total_columns = 0
        columns_with_descriptions = 0
        columns_with_labels = 0
        columns_with_glossary = 0
        columns_with_formats = 0
        enriched_columns = 0

        for table in tables.values():
            for col in table.columns.values():
                total_columns += 1
                has_enrichment = False
                if col.description and col.description.strip():
                    columns_with_descriptions += 1
                    has_enrichment = True
                if col.business_label and col.business_label.strip():
                    columns_with_labels += 1
                    has_enrichment = True
                if col.linked_glossary_term and col.linked_glossary_term.strip():
                    columns_with_glossary += 1
                    has_enrichment = True
                if col.format_pattern and col.format_pattern.strip():
                    columns_with_formats += 1
                    has_enrichment = True
                if has_enrichment:
                    enriched_columns += 1

        coverage_percentage = round((enriched_columns / total_columns) * 100.0, 2) if total_columns > 0 else 0.0

        if coverage_percentage >= 70.0:
            recommended = "rich"
        elif coverage_percentage >= 30.0:
            recommended = "hybrid"
        else:
            recommended = "auto"

        return cls(
            total_columns=total_columns,
            columns_with_descriptions=columns_with_descriptions,
            columns_with_labels=columns_with_labels,
            columns_with_glossary=columns_with_glossary,
            columns_with_formats=columns_with_formats,
            coverage_percentage=coverage_percentage,
            recommended_profile=recommended,
        )


class CatalogSnapshot(BaseModel):
    """Serializable snapshot of dataset metadata from BigQuery and Knowledge Catalog."""

    dataset_id: str
    project_id: str
    location: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    sources: dict[str, SourceStatus] = Field(default_factory=dict)
    tables: dict[str, TableMeta] = Field(default_factory=dict)
    relationships: list[Relationship] = Field(default_factory=list)
    coverage: CoverageReport | None = None
    raw_aspects: dict[str, Any] = Field(default_factory=dict)

    def to_json(self, indent: int = 2) -> str:
        """Serialize the snapshot to a formatted JSON string."""
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, s: str) -> CatalogSnapshot:
        """Deserialize a snapshot from a JSON string."""
        return cls.model_validate_json(s)

    def save(self, path: Path | str) -> None:
        """Persist snapshot JSON to disk atomically."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_file = target.with_suffix(target.suffix + ".tmp")
        try:
            temp_file.write_text(self.to_json(), encoding="utf-8")
            temp_file.replace(target)
        except Exception:
            if temp_file.exists():
                temp_file.unlink()
            raise

    @classmethod
    def load(cls, path: Path | str) -> CatalogSnapshot:
        """Load and deserialize snapshot JSON from disk."""
        target = Path(path)
        return cls.from_json(target.read_text(encoding="utf-8"))

    def compute_coverage(self) -> CoverageReport:
        """Compute and update the coverage report from the snapshot's tables."""
        report = CoverageReport.compute(self.tables)
        self.coverage = report
        return report
