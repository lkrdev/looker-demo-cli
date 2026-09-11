"""Naming-convention heuristics that guess a relational model from bare column names.

The LookML generator needs three things a Parquet file cannot tell it: which
tables are facts, which column is each table's primary key, and how the tables
join. None of that is recorded anywhere in the source data, so this module
*guesses* it from table and column **names alone**.

> The output is a guess, not a fact.

Every guess here is load-bearing. ``primary_key`` becomes ``primary_key: yes``
on a LookML dimension, which is what Looker uses to decide whether symmetric
aggregates are needed; a wrong guess silently returns inflated sums rather than
raising. ``foreign_keys`` becomes the Explore's join graph; a wrong or missing
guess produces a model that validates cleanly and answers questions
incorrectly. There is no runtime signal that any of it went wrong, so the
heuristic is pinned by characterization tests rather than trusted.

This module is deliberately **pure**: it takes plain dicts of strings and
returns model objects. It performs no I/O -- no filesystem access, no Parquet
reading, no network -- so the caller owns every side effect and every branch
here is reachable from a unit test.

Typical use::

    raw = {"dim_users": {"user_id": "int64", "name": "string"}}
    specs = infer_table_specs(raw)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from looker_demo_cli.generators.lookml_generator import LookMLTableSpec

#: Prefixes stripped before a table name is compared to a column stem. Stripping
#: lets ``dim_users`` match a ``user_id`` column without the modelling
#: convention leaking into the column names.
_DIMENSION_PREFIX: Final[str] = "dim_"
_FACT_PREFIX: Final[str] = "fct_"

#: Substrings that mark a table as a fact even without the ``fct_`` prefix.
#: These cover the domains the synthetic data generator emits today.
_FACT_NAME_MARKERS: Final[tuple[str, ...]] = ("transaction", "alert", "order")

#: Suffix that identifies a key column. Also used as a slice width, so the two
#: cannot drift apart.
_KEY_SUFFIX: Final[str] = "_id"

#: Column name treated as a table's key with no further qualification.
_BARE_KEY_COLUMN: Final[str] = "id"

TABLE_TYPE_FACT: Final[str] = "fact"
TABLE_TYPE_DIMENSION: Final[str] = "dimension"


def _strip_table_prefix(table_name: str) -> str:
    """Remove the modelling prefixes from a table name.

    Args:
        table_name: A table name, with or without a ``dim_`` / ``fct_`` prefix.

    Returns:
        The table name with both prefixes removed.
    """
    # Substring replacement, not prefix stripping -- an interior occurrence is
    # removed too. Preserved deliberately; see the characterization tests.
    return table_name.replace(_DIMENSION_PREFIX, "").replace(_FACT_PREFIX, "")


def normalize_column_type(arrow_type: str) -> str:
    """Map an Arrow/Parquet type string onto the BigQuery type LookML expects.

    Matching is substring-based on the lowercased input rather than an exact
    lookup, which keeps parameterized types such as ``timestamp[us, tz=UTC]``
    and ``decimal128(38, 9)`` from needing an entry each. The trade-off is that
    an unrelated type whose name merely *contains* a marker is misclassified,
    and that anything unrecognized silently becomes ``STRING``.

    Args:
        arrow_type: The Arrow type rendered as a string, e.g. ``"int64"``.

    Returns:
        One of ``"INT64"``, ``"FLOAT64"``, ``"BOOL"``, ``"TIMESTAMP"`` or
        ``"STRING"``.
    """
    type_str = arrow_type.lower()
    if "int" in type_str:
        return "INT64"
    if "float" in type_str or "double" in type_str:
        return "FLOAT64"
    if "bool" in type_str:
        return "BOOL"
    # One arm for all three: a pure `date` is widened to a TIMESTAMP because
    # LookML's dimension_group renders both from the same type.
    if "timestamp" in type_str or "time" in type_str or "date" in type_str:
        return "TIMESTAMP"
    return "STRING"


def classify_table(table_name: str) -> str:
    """Decide whether a table is a fact or a dimension.

    The distinction drives which table becomes an Explore's base view and which
    tables are joined onto it, so a misclassification reshapes the whole model.

    Args:
        table_name: The table name, expected to be lowercase snake_case.

    Returns:
        ``"fact"`` or ``"dimension"``.
    """
    # Unanchored substring tests: any name *containing* a marker is a fact.
    is_fact = table_name.startswith(_FACT_PREFIX) or any(marker in table_name for marker in _FACT_NAME_MARKERS)
    return TABLE_TYPE_FACT if is_fact else TABLE_TYPE_DIMENSION


def infer_primary_key(table_name: str, columns: Sequence[str]) -> str | None:
    """Guess which column uniquely identifies a row.

    Three tiers are tried in order, each scanning ``columns`` front to back and
    taking the first hit, so column order breaks every tie:

    1. An exact match against the singularized table name, the table name
       as-is, or a bare ``id`` column.
    2. Any ``*_id`` column whose name *starts with* the last underscore-segment
       of the table name (or that segment minus its final character).
    3. Failing both, the first ``*_id`` column of any kind.

    Tier 3 is a last-resort guess and is frequently a foreign key rather than a
    primary key.

    Args:
        table_name: The table the columns belong to.
        columns: Column names in their physical order.

    Returns:
        The chosen column name, or ``None`` when no column ends in ``_id``.
    """
    base = _strip_table_prefix(table_name)
    # Singularization is a single-character chop, correct only for names whose
    # plural is a bare trailing "s".
    singular = base[:-1]
    exact_candidates = (f"{singular}{_KEY_SUFFIX}", f"{base}{_KEY_SUFFIX}", _BARE_KEY_COLUMN)
    for col in columns:
        if col in exact_candidates:
            return col

    name_stem = table_name.split("_")[-1]
    for col in columns:
        if col.endswith(_KEY_SUFFIX) and (col.startswith(name_stem[:-1]) or col.startswith(name_stem)):
            return col

    for col in columns:
        if col.endswith(_KEY_SUFFIX):
            return col

    return None


def infer_foreign_keys(
    table_name: str,
    columns: Sequence[str],
    primary_key: str | None,
    all_table_names: Sequence[str],
) -> dict[str, str]:
    """Guess the join graph by matching ``*_id`` columns to sibling table names.

    A column such as ``customer_id`` is matched against every other table,
    accepting the stem itself or one of three regular plural forms (``+s``,
    ``+es``, ``y -> ies``). A column with no matching table is dropped without
    comment, so a typo or an irregular plural costs a join rather than raising.

    Args:
        table_name: The table owning ``columns``; excluded from its own match
            set, so self-referencing keys are never resolved.
        columns: Column names in their physical order.
        primary_key: The column already claimed as the primary key, excluded
            from consideration. May be ``None``.
        all_table_names: Every table available to join against.

    Returns:
        Mapping of foreign key column to a ``"<table>.<column>"`` target.
    """
    foreign_keys: dict[str, str] = {}
    for col in columns:
        if not col.endswith(_KEY_SUFFIX) or col == primary_key:
            continue
        ref = col[: -len(_KEY_SUFFIX)]
        for candidate in all_table_names:
            if candidate == table_name:
                continue
            candidate_stem = _strip_table_prefix(candidate)
            if (
                candidate_stem == ref
                or candidate_stem == f"{ref}s"
                or candidate_stem == f"{ref}es"
                or (ref.endswith("y") and candidate_stem == f"{ref[:-1]}ies")
            ):
                # The target column is assumed to be spelled the same on the
                # parent, which holds only when the parent's key is prefixed
                # with its own singular name.
                foreign_keys[col] = f"{candidate}.{col}"
                break
    return foreign_keys


def infer_table_specs(raw_schemas: Mapping[str, Mapping[str, str]]) -> list[LookMLTableSpec]:
    """Build a LookML table spec for every table in ``raw_schemas``.

    Args:
        raw_schemas: Table name -> column name -> **raw** Arrow type string.
            Both levels are consumed in iteration order, so an insertion-ordered
            mapping (the caller's sorted glob) determines the order of the
            returned specs, of each spec's ``schema_fields``, and of every
            order-sensitive tie-break in key inference.

    Returns:
        One :class:`~looker_demo_cli.generators.lookml_generator.LookMLTableSpec`
        per input table, in input order.
    """
    # Materialized once: every table is matched against the full set, including
    # tables that appear later in the mapping.
    all_table_names = list(raw_schemas)

    specs: list[LookMLTableSpec] = []
    for table_name, raw_columns in raw_schemas.items():
        schema_fields = {col: normalize_column_type(raw_type) for col, raw_type in raw_columns.items()}
        columns = list(schema_fields)
        primary_key = infer_primary_key(table_name, columns)
        specs.append(
            LookMLTableSpec(
                table_name=table_name,
                table_type=classify_table(table_name),
                schema_fields=schema_fields,
                primary_key=primary_key,
                foreign_keys=infer_foreign_keys(table_name, columns, primary_key, all_table_names),
            )
        )
    return specs
