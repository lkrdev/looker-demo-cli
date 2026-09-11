"""Read table schemas off disk and hand them to the pure inference layer.

This module is deliberately thin. It exists only to separate *reading parquet
files* from *guessing a relational model*, which used to be one 75-line function
in the deleted ``workflow/runner`` module and was therefore impossible to test
without writing real parquet files to a temp directory.

All the interesting logic -- primary key detection, foreign key matching, type
normalisation -- lives in :mod:`looker_demo_cli.schema_inference`, which touches
no filesystem and is covered by table-driven tests.
"""

from __future__ import annotations

from pathlib import Path

from looker_demo_cli.generators.lookml_generator import LookMLTableSpec
from looker_demo_cli.schema_inference import infer_table_specs


def read_parquet_schemas(parquet_dir: Path) -> dict[str, dict[str, str]]:
    """Read the column names and Arrow types of every parquet file in a directory.

    Files are read in sorted order and the returned mapping preserves it, because
    the inference layer resolves foreign keys against the set of sibling table
    names and downstream LookML generation renders views in iteration order --
    so a stable order means a stable diff between runs.

    Args:
        parquet_dir: Directory containing one ``*.parquet`` file per table. The
            file stem becomes the table name.

    Returns:
        Mapping of table name to a mapping of column name to raw Arrow type
        string (for example ``"int64"`` or ``"timestamp[us]"``).
    """
    # Imported lazily: pyarrow is a heavy import and only the parquet path needs
    # it, so `demo-create --help` should not pay for it.
    import pyarrow.parquet as pq

    schemas: dict[str, dict[str, str]] = {}
    for parquet_file in sorted(parquet_dir.glob("*.parquet")):
        table = pq.read_table(parquet_file)
        schemas[parquet_file.stem] = {
            name: str(arrow_type) for name, arrow_type in zip(table.schema.names, table.schema.types, strict=True)
        }
    return schemas


def extract_table_specs_from_parquet_dir(parquet_dir: Path) -> list[LookMLTableSpec]:
    """Infer LookML table specifications from a directory of parquet files.

    Args:
        parquet_dir: Directory containing one ``*.parquet`` file per table.

    Returns:
        One spec per parquet file, in sorted filename order, each carrying the
        inferred table type, column types, primary key, and foreign keys.
    """
    return infer_table_specs(read_parquet_schemas(parquet_dir))
