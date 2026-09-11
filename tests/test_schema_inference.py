"""Tests for :mod:`looker_demo_cli.schema_inference`.

Scope: the naming-convention heuristic that guesses column types, fact/dimension
classification, primary keys, and the foreign key join graph. Until Phase 4 this
logic lived inline in ``workflow/runner.py``, wrapped around a ``pyarrow`` read,
and was therefore untestable and untested -- while silently deciding the primary
key and join graph of every generated LookML view.

Phase 4 contract
----------------
These tests treat the extraction as **behavior-preserving**. Nothing here
asserts what the heuristic *should* do; it asserts what it *does*, so that the
move out of ``runner.py`` is provably a no-op and so that a later, deliberate
fix shows up as a failing test rather than as a silent change in a customer's
model.

Tests pinning behavior that is known to be wrong carry
``@pytest.mark.characterization`` and a ``# BUG:`` note naming the consequence
and the phase that owns the fix. Everything else is a plain ``unit`` assertion
of the intended contract.

Every test here is hermetic by construction: the module under test performs no
I/O at all, so no fixture, fake, or temporary directory is required.
"""

from __future__ import annotations

import inspect

import pytest

from looker_demo_cli import schema_inference
from looker_demo_cli.schema_inference import (
    classify_table,
    infer_foreign_keys,
    infer_primary_key,
    infer_table_specs,
    normalize_column_type,
)

pytestmark = pytest.mark.unit


# ===========================================================================
# Module purity
# ===========================================================================


def test_module_imports_nothing_that_touches_the_outside_world():
    """The whole point of the extraction is that this module cannot do I/O.

    If a later change reaches for ``pathlib`` or ``pyarrow`` here, the tests
    below stop being cheap plain-dict tests and start needing fixtures and
    temporary files -- which is exactly the state Phase 4 dug the heuristic out
    of. Failing at the import line keeps that regression obvious.
    """
    forbidden = {"pathlib", "pyarrow", "pandas", "os", "io", "shutil", "subprocess", "requests", "google"}

    source = inspect.getsource(schema_inference)
    imported_roots = set()
    for line in source.splitlines():
        if line.startswith("from "):
            imported_roots.add(line.split()[1].split(".")[0])
        elif line.startswith("import "):
            imported_roots.add(line.split()[1].split(".")[0])

    assert not (imported_roots & forbidden), f"schema_inference must stay pure; found {imported_roots & forbidden}"


# ===========================================================================
# normalize_column_type
# ===========================================================================


@pytest.mark.parametrize(
    ("arrow_type", "expected"),
    [
        ("int8", "INT64"),
        ("int16", "INT64"),
        ("int32", "INT64"),
        ("int64", "INT64"),
        ("uint8", "INT64"),
        ("uint64", "INT64"),
        ("float", "FLOAT64"),
        ("float32", "FLOAT64"),
        ("float64", "FLOAT64"),
        ("double", "FLOAT64"),
        ("bool", "BOOL"),
        ("timestamp[us]", "TIMESTAMP"),
        ("timestamp[us, tz=UTC]", "TIMESTAMP"),
        ("time32[s]", "TIMESTAMP"),
        ("time64[ns]", "TIMESTAMP"),
        ("date32[day]", "TIMESTAMP"),
        ("date64[ms]", "TIMESTAMP"),
        ("string", "STRING"),
        ("large_string", "STRING"),
    ],
    ids=[
        "int8",
        "int16",
        "int32",
        "int64",
        "uint8-unsigned-still-int",
        "uint64-unsigned-still-int",
        "bare-float",
        "float32",
        "float64",
        "double",
        "bool",
        "timestamp-us",
        "timestamp-with-tz",
        "time32",
        "time64",
        "date32",
        "date64",
        "string",
        "large_string",
    ],
)
def test_normalize_column_type_covers_every_branch(arrow_type: str, expected: str):
    """Each arm of the type map is what every LookML dimension's ``type`` is built from.

    A column normalized to ``STRING`` cannot be summed, averaged, or used as a
    ``dimension_group``, so an incorrect arm is not a cosmetic defect -- it
    removes the measure a demo dashboard was built to show.
    """
    assert normalize_column_type(arrow_type) == expected


@pytest.mark.parametrize(
    ("arrow_type", "expected"),
    [
        ("INT64", "INT64"),
        ("Float64", "FLOAT64"),
        ("BOOL", "BOOL"),
        ("TIMESTAMP[us]", "TIMESTAMP"),
        ("STRING", "STRING"),
    ],
    ids=["upper-int", "mixed-float", "upper-bool", "upper-timestamp", "upper-string"],
)
def test_normalize_column_type_is_case_insensitive(arrow_type: str, expected: str):
    """Callers may pass a BigQuery-style uppercase type, not only an Arrow repr.

    The I/O layer feeds this ``str(arrow_type)``, but the same function is the
    natural place for a BigQuery introspection path to normalize through later.
    Lowercasing up front is what makes both callers safe.
    """
    assert normalize_column_type(arrow_type) == expected


@pytest.mark.parametrize(
    ("arrow_type", "expected"),
    [
        ("datetime64[ns]", "TIMESTAMP"),
        ("date", "TIMESTAMP"),
        ("timestamp[ns]", "TIMESTAMP"),
    ],
    ids=["datetime-matches-via-time", "bare-date", "timestamp-not-shadowed"],
)
def test_normalize_column_type_collapses_the_shared_temporal_arm(arrow_type: str, expected: str):
    """``timestamp``/``time``/``date`` share one arm, so their ordering cannot matter.

    This is the trap the arm exists to defuse: ``timestamp`` *contains*
    ``time``, and ``datetime`` contains it too, so any attempt to split them
    into separately-ordered branches later must keep every one of these mapping
    to the same result or the dimension_group timeframes change underneath
    existing dashboards.
    """
    assert normalize_column_type(arrow_type) == expected


@pytest.mark.parametrize(
    ("arrow_type", "expected"),
    [
        ("binary", "STRING"),
        ("large_binary", "STRING"),
        ("null", "STRING"),
        ("list<item: string>", "STRING"),
        ("duration[s]", "STRING"),
    ],
    ids=[
        "binary",
        "large_binary",
        "null",
        "list-of-string",
        "duration",
    ],
)
def test_normalize_column_type_falls_back_to_string_for_unknown_types(arrow_type: str, expected: str):
    """Unrecognized types degrade to ``STRING`` rather than raising.

    Silent degradation is the deliberate behavior here: a demo that models one
    exotic column as text still ships, whereas a generator that aborts on an
    unexpected Arrow type strands the user after the BigQuery load has already
    run. The cost is that a genuinely wrong mapping looks identical to a
    deliberate one.
    """
    assert normalize_column_type(arrow_type) == expected


@pytest.mark.characterization
@pytest.mark.parametrize(
    ("arrow_type", "expected"),
    [
        ("int96", "INT64"),
        ("interval[dt]", "INT64"),
        ("point", "INT64"),
        ("struct<a: int32>", "INT64"),
        ("decimal128(38, 9)", "STRING"),
        ("decimal256(76, 38)", "STRING"),
    ],
    ids=[
        "int96-legacy-parquet-timestamp",
        "interval-contains-int",
        "geo-point-contains-int",
        "nested-struct-leaks-inner-int",
        "decimal128-has-no-arm",
        "decimal256-has-no-arm",
    ],
)
def test_normalize_column_type_misclassifies_types_by_substring(arrow_type: str, expected: str):
    """Substring matching and a missing decimal arm both produce wrong LookML types.

    # BUG: two distinct defects are pinned here.
    #  1. The ``"int" in type_str`` test is an unanchored substring match, so
    #     ``int96`` (Parquet's legacy timestamp encoding), ``interval``, a
    #     GeoArrow ``point``, and any nested type whose *rendered* name quotes
    #     an inner ``int32`` all become INT64. A timestamp modeled as INT64
    #     loses its dimension_group entirely -- every date filter and time
    #     series tile silently disappears from the generated dashboard.
    #  2. There is no decimal/numeric arm at all, so BigQuery NUMERIC columns
    #     fall through to STRING. Revenue and currency columns therefore cannot
    #     be summed: the measure generator skips them and the demo ships
    #     without its headline KPI.
    # Owner: Phase 5 (replace substring matching with an anchored type table).
    """
    assert normalize_column_type(arrow_type) == expected


# ===========================================================================
# classify_table
# ===========================================================================


@pytest.mark.parametrize(
    ("table_name", "expected"),
    [
        ("fct_orders", "fact"),
        ("fct_anything_at_all", "fact"),
        ("transactions", "fact"),
        ("payment_transaction_log", "fact"),
        ("alerts", "fact"),
        ("security_alerts", "fact"),
        ("orders", "fact"),
        ("dim_users", "dimension"),
        ("users", "dimension"),
        ("products", "dimension"),
        ("inventory_snapshot", "dimension"),
    ],
    ids=[
        "fct-prefix",
        "fct-prefix-unknown-domain",
        "transaction-marker",
        "transaction-marker-embedded",
        "alert-marker",
        "alert-marker-embedded",
        "order-marker",
        "dim-prefix",
        "plain-noun",
        "plain-noun-products",
        "no-marker",
    ],
)
def test_classify_table_recognizes_prefix_and_markers(table_name: str, expected: str):
    """Fact/dimension decides which table becomes an Explore's base view.

    Getting this wrong does not fail validation -- it produces an Explore
    anchored on the wrong table, which changes the grain of every measure in
    it and quietly changes the answer to every question asked of the demo.
    """
    assert classify_table(table_name) == expected


@pytest.mark.characterization
@pytest.mark.parametrize(
    ("table_name", "expected"),
    [
        ("reorder_events", "fact"),
        ("border_crossings", "fact"),
        ("alert_severity_levels", "fact"),
        ("dim_transaction_types", "fact"),
        ("FCT_Orders", "dimension"),
        ("Transactions", "dimension"),
    ],
    ids=[
        "reorder-contains-order",
        "border-contains-order",
        "alert-lookup-table",
        "dim-prefix-loses-to-marker",
        "uppercase-prefix-missed",
        "uppercase-marker-missed",
    ],
)
def test_classify_table_matches_markers_anywhere_and_only_in_lowercase(table_name: str, expected: str):
    """Unanchored, case-sensitive substring matching misclassifies real table names.

    # BUG: three defects share one root -- the markers are tested with a bare
    # ``in`` against the raw name.
    #  1. Any name *containing* a marker is a fact: ``border_crossings`` and
    #     ``reorder_events`` both trip the ``order`` marker, and
    #     ``alert_severity_levels`` -- a tiny lookup table -- trips ``alert``.
    #     Each becomes an Explore base view instead of a joined dimension.
    #  2. An explicit ``dim_`` prefix does not win: ``dim_transaction_types``
    #     is classified as a fact, overriding the author's own declaration.
    #  3. Matching is case-sensitive, so a table exported with any uppercase in
    #     its name is always a dimension, ``fct_`` prefix or not.
    # Owner: Phase 5 (anchor the prefix test and casefold the name).
    """
    assert classify_table(table_name) == expected


# ===========================================================================
# infer_primary_key -- tier 1: exact name match
# ===========================================================================


@pytest.mark.parametrize(
    ("table_name", "columns", "expected"),
    [
        ("dim_users", ["user_id", "user_name"], "user_id"),
        ("fct_orders", ["order_id", "amount"], "order_id"),
        ("users", ["user_id", "user_name"], "user_id"),
        ("dim_inventory", ["inventory_id", "qty"], "inventory_id"),
        ("dim_users", ["id", "user_name"], "id"),
        ("fct_transactions", ["transaction_id", "amount"], "transaction_id"),
    ],
    ids=[
        "singularized-dim",
        "singularized-fct",
        "singularized-unprefixed",
        "unsingularized-name-matches-as-is",
        "bare-id-column",
        "prefix-stripped-before-match",
    ],
)
def test_infer_primary_key_tier_one_matches_the_table_name(table_name: str, columns: list[str], expected: str):
    """Tier 1 is the only tier that is actually evidence-based.

    It matches a column against the table's own name, which is the one signal
    that genuinely implies uniqueness. Every tier below it is a guess, so
    keeping tier 1 broad is what keeps the guessing tiers from being reached.
    """
    assert infer_primary_key(table_name, columns) == expected


@pytest.mark.parametrize(
    ("table_name", "columns", "expected"),
    [
        ("dim_users", ["id", "user_id"], "id"),
        ("dim_users", ["user_id", "id"], "user_id"),
        ("fct_orders", ["customer_id", "order_id"], "order_id"),
        ("dim_users", ["users_id", "user_id"], "users_id"),
    ],
    ids=[
        "bare-id-first-wins",
        "singular-first-wins",
        "tier-one-beats-tier-three",
        "plural-form-first-wins",
    ],
)
def test_infer_primary_key_tie_breaks_on_column_order(table_name: str, columns: list[str], expected: str):
    """When several columns qualify, physical column order decides -- nothing else.

    The caller builds ``schema_fields`` from an insertion-ordered dict, so the
    Parquet file's field order silently determines the primary key. Two exports
    of the same logical table with reordered columns produce different LookML.
    Pinning it here means a future dict-ordering change cannot slip through.
    """
    assert infer_primary_key(table_name, columns) == expected


# ===========================================================================
# infer_primary_key -- tier 2: prefix match on the trailing name segment
# ===========================================================================


@pytest.mark.parametrize(
    ("table_name", "columns", "expected"),
    [
        ("dim_users", ["created_at", "user_key_id"], "user_key_id"),
        ("dim_users", ["created_at", "users_natural_id"], "users_natural_id"),
        ("fct_order_events", ["ts", "event_seq_id"], "event_seq_id"),
    ],
    ids=[
        "singular-stem-prefix",
        "full-stem-prefix",
        "trailing-segment-only",
    ],
)
def test_infer_primary_key_tier_two_falls_back_to_a_prefix_match(table_name: str, columns: list[str], expected: str):
    """Tier 2 catches surrogate keys that decorate the entity name.

    Real generated schemas use ``<entity>_key_id`` and ``<entity>_seq_id``
    often enough that without this tier they would fall to tier 3 and pick an
    arbitrary column. Note it compares only the *last* underscore segment of
    the table name, which is why ``fct_order_events`` matches on ``event``.
    """
    assert infer_primary_key(table_name, columns) == expected


@pytest.mark.characterization
def test_infer_primary_key_tier_two_can_select_a_foreign_key():
    """A prefix match on a chopped stem claims a non-unique column as the key.

    # BUG: tier 2 accepts any ``*_id`` column that merely *starts with* the
    # table's stem minus one character. For ``fct_sales`` the stem becomes
    # ``sale``, and ``salesperson_id`` -- a foreign key with many rows per
    # value -- matches. The generated view marks it ``primary_key: yes``, which
    # tells Looker no symmetric aggregate is needed; every sum and count over a
    # joined Explore is then silently inflated by the fan-out.
    # Owner: Phase 5 (require the stem to be followed by ``_`` and end in _id).
    """
    assert infer_primary_key("fct_sales", ["sold_at", "salesperson_id", "amount"]) == "salesperson_id"


@pytest.mark.characterization
def test_infer_primary_key_naive_singularization_breaks_on_es_plurals():
    """A table whose plural is ``-es`` loses its real key to an arbitrary one.

    # BUG: singularization is ``name[:-1]``. For ``dim_addresses`` that yields
    # ``addresse``, so neither ``addresse_id`` nor ``addresses_id`` exists and
    # tier 1 misses the obvious ``address_id``. Tier 2's stem is chopped the
    # same way and also misses. Tier 3 then takes the *first* ``_id`` column,
    # which here is the foreign key ``customer_id``. The view gets a primary
    # key on a column with many rows per value, and because a primary key is
    # excluded from foreign key inference, the ``customer`` join disappears
    # too -- one naive slice costs both the key and an Explore join.
    # Owner: Phase 5 (use a real singularization table, or require an
    # explicit key declaration).
    """
    assert infer_primary_key("dim_addresses", ["customer_id", "address_id", "street"]) == "customer_id"


@pytest.mark.parametrize(
    ("table_name", "columns", "expected"),
    [
        ("dim_person", ["person_id", "name"], "person_id"),
        ("dim_status", ["status_id", "label"], "status_id"),
        ("dim_inventory", ["inventory_id", "qty"], "inventory_id"),
    ],
    ids=["singular-noun", "s-ending-singular-noun", "y-ending-noun"],
)
def test_infer_primary_key_unsingularized_alternative_rescues_singular_names(
    table_name: str, columns: list[str], expected: str
):
    """Tier 1 tests both the chopped and the unchopped name, which is what saves singular tables.

    ``dim_person`` chops to the meaningless ``perso``; the match only succeeds
    because the raw name is tried as well. Removing that second alternative
    while "fixing" singularization would break every singular-named table at
    once, so it is pinned separately.
    """
    assert infer_primary_key(table_name, columns) == expected


# ===========================================================================
# infer_primary_key -- tier 3 and the empty case
# ===========================================================================


@pytest.mark.characterization
@pytest.mark.parametrize(
    ("table_name", "columns", "expected"),
    [
        ("fct_alerts", ["created_at", "device_id", "user_id"], "device_id"),
        ("fct_alerts", ["created_at", "user_id", "device_id"], "user_id"),
        ("bridge_tagging", ["tag_id", "article_id"], "tag_id"),
    ],
    ids=[
        "first-fk-wins",
        "same-table-reordered-picks-differently",
        "join-table-has-no-key-at-all",
    ],
)
def test_infer_primary_key_tier_three_grabs_an_arbitrary_id_column(table_name: str, columns: list[str], expected: str):
    """The last resort picks whichever ``_id`` column happens to come first.

    # BUG: tier 3 has no evidence behind it whatsoever -- it takes the first
    # ``*_id`` column in physical order. For an event table like ``fct_alerts``
    # that is a foreign key, so the view claims a non-unique column is unique
    # (breaking symmetric aggregates) *and* loses that column's join, because
    # the primary key is excluded from foreign key inference. The second case
    # shows the same logical table producing a different model purely from
    # column reordering. A many-to-many bridge table has no single-column key
    # at all, yet one is asserted regardless.
    # Owner: Phase 5 (emit no primary key and warn, rather than guessing).
    """
    assert infer_primary_key(table_name, columns) == expected


@pytest.mark.parametrize(
    ("table_name", "columns"),
    [
        ("dim_regions", ["region_name", "country"]),
        ("dim_regions", []),
        ("dim_regions", ["identifier", "id_code", "paid"]),
    ],
    ids=["no-id-columns", "no-columns-at-all", "id-substring-is-not-a-suffix"],
)
def test_infer_primary_key_returns_none_when_nothing_ends_in_id(table_name: str, columns: list[str]):
    """No key is better than a fabricated one, and the generator must handle ``None``.

    The last case matters most: matching is a strict ``_id`` *suffix* test, so
    ``identifier``, ``id_code`` and ``paid`` are all correctly ignored. A
    looser ``"id" in col`` test would claim ``paid`` as a primary key.
    """
    assert infer_primary_key(table_name, columns) is None


# ===========================================================================
# infer_foreign_keys
# ===========================================================================


@pytest.mark.parametrize(
    ("column", "parent_table", "expected_target"),
    [
        ("user_id", "dim_user", "dim_user.user_id"),
        ("user_id", "dim_users", "dim_users.user_id"),
        ("box_id", "dim_boxes", "dim_boxes.box_id"),
        ("company_id", "dim_companies", "dim_companies.company_id"),
        ("user_id", "users", "users.user_id"),
    ],
    ids=[
        "exact-stem-match",
        "plural-s",
        "plural-es",
        "plural-y-to-ies",
        "unprefixed-parent",
    ],
)
def test_infer_foreign_keys_matches_regular_plural_forms(column: str, parent_table: str, expected_target: str):
    """The join graph is reconstructed entirely from these four spelling rules.

    Each rule that fails to match costs a join, and a missing join means a
    dashboard tile that was supposed to break revenue down by customer instead
    cannot reference the customer table at all.
    """
    result = infer_foreign_keys("fct_orders", ["order_id", column], "order_id", ["fct_orders", parent_table])
    assert result == {column: expected_target}


def test_infer_foreign_keys_preserves_column_order_for_multiple_parents():
    """A fact table normally joins several dimensions, and their order is the tile order downstream.

    The generator writes joins in mapping order, so pinning the order keeps the
    generated Explore byte-stable across runs -- otherwise every regeneration
    produces a spurious diff in the LookML project.
    """
    result = infer_foreign_keys(
        "fct_orders",
        ["order_id", "user_id", "product_id", "store_id"],
        "order_id",
        ["fct_orders", "dim_users", "dim_products", "dim_stores"],
    )

    assert result == {
        "user_id": "dim_users.user_id",
        "product_id": "dim_products.product_id",
        "store_id": "dim_stores.store_id",
    }
    assert list(result) == ["user_id", "product_id", "store_id"]


def test_infer_foreign_keys_excludes_the_primary_key():
    """A table's own key must not be joined back to a same-named parent.

    ``fct_orders.order_id`` alongside a ``dim_orders`` table would otherwise
    generate a self-defeating join of the fact to a dimension on its own grain.
    """
    result = infer_foreign_keys(
        "fct_orders",
        ["order_id", "user_id"],
        "order_id",
        ["fct_orders", "dim_orders", "dim_users"],
    )

    assert "order_id" not in result
    assert result == {"user_id": "dim_users.user_id"}


def test_infer_foreign_keys_accepts_a_null_primary_key():
    """With no primary key inferred, every ``*_id`` column is join-eligible.

    Bridge tables reach this state routinely, and it is the one case where the
    heuristic behaves well: both sides of the many-to-many get joined.
    """
    result = infer_foreign_keys(
        "bridge_user_tags",
        ["user_id", "tag_id"],
        None,
        ["bridge_user_tags", "dim_users", "dim_tags"],
    )

    assert result == {"user_id": "dim_users.user_id", "tag_id": "dim_tags.tag_id"}


def test_infer_foreign_keys_never_matches_a_bare_id_column():
    """``id`` is a primary key spelling, not a foreign key spelling.

    Matching is a strict ``_id`` suffix test, so a table with a bare ``id``
    column and a same-named sibling table cannot accidentally join to itself.
    """
    result = infer_foreign_keys("dim_users", ["id", "name"], "id", ["dim_users", "dim_ids"])

    assert result == {}


def test_infer_foreign_keys_stops_at_the_first_matching_parent():
    """Two tables can satisfy the same stem, and the first one listed wins.

    Table order comes from a sorted directory glob, so with both ``users`` and
    ``dim_users`` present the alphabetically-earlier file silently becomes the
    join target for the entire model.
    """
    result = infer_foreign_keys("fct_orders", ["order_id", "user_id"], "order_id", ["users", "dim_users", "fct_orders"])

    assert result == {"user_id": "users.user_id"}


@pytest.mark.characterization
def test_infer_foreign_keys_cannot_resolve_a_self_reference():
    """A hierarchy within one table is dropped rather than aliased.

    # BUG: the candidate loop skips ``cand == t_name`` outright, so a
    # self-referencing key can never resolve. Combined with the ``_id`` stem
    # rules, a ``dim_users`` table carrying both ``id`` and ``user_id`` loses
    # ``user_id`` entirely. Self-joins genuinely do need a distinct alias
    # (``from: dim_users``), so the exclusion is not wrong so much as
    # unfinished -- the relationship is discarded silently instead of being
    # emitted as an aliased join, and org-chart style demos come out flat.
    # Owner: Phase 5 (emit a role-playing alias join instead of skipping).
    """
    result = infer_foreign_keys("dim_users", ["id", "user_id"], "id", ["dim_users"])

    assert result == {}


@pytest.mark.characterization
@pytest.mark.parametrize(
    ("column", "table_names"),
    [
        ("person_id", ["fct_orders", "dim_people"]),
        ("child_id", ["fct_orders", "dim_children"]),
        ("promo_id", ["fct_orders", "dim_promotions"]),
        ("customer_id", ["fct_orders", "dim_customer_accounts"]),
        ("warehouse_id", ["fct_orders", "eu_dim_warehouses"]),
    ],
    ids=[
        "irregular-plural-people",
        "irregular-plural-children",
        "abbreviated-column-stem",
        "qualified-parent-name",
        "prefix-stripped-mid-name",
    ],
)
def test_infer_foreign_keys_silently_drops_unresolvable_columns(column: str, table_names: list[str]):
    """Any relationship the four spelling rules miss vanishes without a warning.

    # BUG: an unmatched ``*_id`` column produces no entry, no log line, and no
    # error. The generated Explore is missing a join, so the dimension it would
    # have exposed simply is not in the field picker -- and because the model
    # still validates, the gap is only ever noticed by whoever builds the demo
    # dashboard. Irregular plurals (``people``, ``children``), abbreviated
    # column stems (``promo_id`` -> ``promotions``) and qualified parent names
    # (``dim_customer_accounts``) are all common enough to hit routinely. The
    # last case is a second defect: prefix stripping uses an unanchored
    # ``str.replace``, so an interior ``dim_`` is deleted too --
    # ``eu_dim_warehouses`` is rewritten to ``eu_warehouses``, which still
    # matches nothing, and any table legitimately containing ``dim_`` mid-name
    # is silently renamed before comparison.
    # Owner: Phase 5 (warn on unresolved keys; anchor the prefix strip).
    """
    result = infer_foreign_keys("fct_orders", ["order_id", column], "order_id", table_names)

    assert result == {}


# ===========================================================================
# infer_table_specs -- end to end
# ===========================================================================


def test_infer_table_specs_models_a_star_schema():
    """The canonical shape the generator is built for: one fact, two conformed dimensions.

    This is the whole contract in one assertion -- ordering, type
    normalization, classification, key inference and the join graph -- so a
    regression anywhere in the module surfaces here even if a unit test above
    was missed.
    """
    raw_schemas = {
        "dim_users": {"user_id": "int64", "user_name": "string", "signed_up_at": "timestamp[us]"},
        "dim_products": {"product_id": "int64", "product_name": "string", "list_price": "double"},
        "fct_orders": {
            "order_id": "int64",
            "user_id": "int64",
            "product_id": "int64",
            "amount": "double",
            "is_gift": "bool",
            "ordered_at": "timestamp[us]",
        },
    }

    users, products, orders = infer_table_specs(raw_schemas)

    assert [s.table_name for s in (users, products, orders)] == ["dim_users", "dim_products", "fct_orders"]

    assert users.table_type == "dimension"
    assert users.primary_key == "user_id"
    assert users.foreign_keys == {}
    assert users.schema_fields == {"user_id": "INT64", "user_name": "STRING", "signed_up_at": "TIMESTAMP"}

    assert products.table_type == "dimension"
    assert products.primary_key == "product_id"
    assert products.foreign_keys == {}
    assert products.schema_fields == {"product_id": "INT64", "product_name": "STRING", "list_price": "FLOAT64"}

    assert orders.table_type == "fact"
    assert orders.primary_key == "order_id"
    assert orders.foreign_keys == {"user_id": "dim_users.user_id", "product_id": "dim_products.product_id"}
    assert orders.schema_fields["is_gift"] == "BOOL"
    assert orders.schema_fields["ordered_at"] == "TIMESTAMP"


def test_infer_table_specs_resolves_a_parent_declared_later():
    """Join targets are resolved against all tables, not only ones seen so far.

    The table list is materialized before the loop, so a fact file that sorts
    ahead of its dimensions (``fct_`` before ``dim_`` never happens, but
    ``bridge_`` before ``dim_`` does) still joins correctly.
    """
    raw_schemas = {
        "fct_orders": {"order_id": "int64", "user_id": "int64"},
        "dim_users": {"user_id": "int64", "user_name": "string"},
    }

    orders, _users = infer_table_specs(raw_schemas)

    assert orders.foreign_keys == {"user_id": "dim_users.user_id"}


def test_infer_table_specs_returns_empty_for_empty_input():
    """An empty directory must produce an empty list, not an error.

    The caller checks ``if not table_specs`` and raises a ``ConfigError`` with
    remediation text; returning ``[]`` is what lets that friendly message win
    over a traceback.
    """
    assert infer_table_specs({}) == []


def test_infer_table_specs_handles_a_lone_table_with_no_keys():
    """A single flat table still yields a usable spec with a null primary key.

    Flat-file demos (one wide table, no joins) are a legitimate input, and the
    generator must receive ``primary_key=None`` rather than a fabricated value.
    """
    (spec,) = infer_table_specs({"metrics_daily": {"metric_name": "string", "value": "double", "day": "date32[day]"}})

    assert spec.table_name == "metrics_daily"
    assert spec.table_type == "dimension"
    assert spec.primary_key is None
    assert spec.foreign_keys == {}
    assert spec.schema_fields == {"metric_name": "STRING", "value": "FLOAT64", "day": "TIMESTAMP"}


@pytest.mark.characterization
def test_infer_table_specs_targets_a_column_the_parent_does_not_have():
    """The join target reuses the child's column name, which need not exist upstream.

    # BUG: the target is built as ``f"{parent_table}.{col}"``, assuming the
    # parent's key is spelled exactly like the child's foreign key. Here
    # ``dim_users`` keys on a bare ``id``, so the inferred target
    # ``dim_users.user_id`` names a column that does not exist. The generator
    # emits a join predicate against a non-existent field, which LookML
    # validation rejects -- and the failure surfaces at deploy time, several
    # minutes and one BigQuery load after the mistake was made.
    # Owner: Phase 5 (resolve the target against the parent's inferred key).
    """
    raw_schemas = {
        "dim_users": {"id": "int64", "user_name": "string"},
        "fct_orders": {"order_id": "int64", "user_id": "int64"},
    }

    users, orders = infer_table_specs(raw_schemas)

    assert users.primary_key == "id"
    assert "user_id" not in users.schema_fields
    assert orders.foreign_keys == {"user_id": "dim_users.user_id"}


@pytest.mark.characterization
def test_infer_table_specs_normalizes_types_before_inferring_keys():
    """Key inference reads the normalized mapping, so type mapping order is load-bearing.

    # BUG: keys are inferred from ``list(schema_fields)`` rather than from the
    # raw column list. The two are identical today only because normalization
    # preserves insertion order and never drops a column. Any future
    # normalization that skips an unsupported type would also silently change
    # which column becomes the primary key -- an invisible coupling between
    # the type map and the join graph.
    # Owner: Phase 5 (infer keys from the raw column names directly).
    """
    (spec,) = infer_table_specs({"dim_users": {"user_id": "decimal128(38, 9)", "user_name": "string"}})

    assert spec.schema_fields["user_id"] == "STRING"
    assert spec.primary_key == "user_id"
